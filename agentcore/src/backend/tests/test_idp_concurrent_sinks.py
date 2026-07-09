"""Two `Processed Docs Output` nodes can persist the same run concurrently. They must not collide.

A native graph usually wires a sink to BOTH branches of the Confidence Router, so both sinks execute and
each opens its own ``session_scope()``. ``save_extraction_results`` begins with
``DELETE ... WHERE job_id = …``, which is idempotent only when serialized: run it twice in parallel and
both DELETEs see nothing, both INSERT, and the second dies with

    duplicate key value violates unique constraint "uq_idp_ext_header_job_field"

Observed on a scanned PDF (slower OCR changed the interleaving); the same graph survived on digital PDFs
purely by timing — and there the *second* sink silently replaced the evidence-scored rows with flat
LLM confidences, because the losing branch carries no OCR tokens.

Fix: a ``FOR UPDATE`` lock on the document row serializes writers, and ``skip_if_already_saved`` makes an
opportunistic writer NEVER DOWNGRADE what is already there. Note it is *not* "first writer wins" — which
sink reaches the lock first is arbitrary, so that rule would discard the OCR evidence half the time.
"""

import asyncio
from uuid import uuid4

import pytest
from sqlmodel import select

from agentcore.services.database.models.agent.model import Agent
from agentcore.services.database.models.idp.config import IdpAgent
from agentcore.services.database.models.idp.documents import (
    IdpDocument,
    IdpExtractedHeader,
    IdpExtractedLineItem,
    IdpProcessingJob,
)
from agentcore.services.deps import session_scope
from agentcore.services.idp.extraction import save_extraction_results


@pytest.fixture
def anyio_backend():
    return "asyncio"


# The real sink's payload: OCR tokens present -> per-field evidence + source_location.
OCR_TOKENS = [
    {"text": "ACME CORPORATION", "page_number": 1, "bbox": [[61, 64], [289, 64], [289, 85], [61, 85]], "confidence": 0.99},
    {"text": "INV-2026-0042", "page_number": 1, "bbox": [[61, 102], [322, 102], [322, 125], [61, 125]], "confidence": 0.99},
]
EXTRACTION = {
    "headers": {
        "supplier_name": {"value": "ACME CORPORATION", "confidence": 0.9, "reasoning": "compact extraction"},
        "invoice_number": {"value": "INV-2026-0042", "confidence": 0.9, "reasoning": "compact extraction"},
    },
    "line_items": [
        {"row_index": 0, "columns": [{"column_name": "description", "value": "Widget Pro", "confidence": 0.9}]},
    ],
}


async def _seed():
    agent_id, doc_id = uuid4(), uuid4()
    async with session_scope() as session:
        session.add(Agent(id=agent_id, name=f"sink-{agent_id.hex[:8]}", data={}))
        await session.flush()
        idp = IdpAgent(agent_id=agent_id, extraction_mode="named_config", is_active=True)
        session.add(idp)
        await session.flush()
        session.add(IdpDocument(
            id=doc_id, agent_id=idp.id, original_filename="scan.pdf", file_path="mock/scan.pdf",
            file_type="pdf", file_size_bytes=1, source="upload", status="processing",
        ))
        job = IdpProcessingJob(document_id=doc_id, agent_id=idp.id, status="running")
        session.add(job)
        await session.commit()
        await session.refresh(job)
        return agent_id, doc_id, job.id


async def _cleanup(agent_id, doc_id):
    async with session_scope() as session:
        for model in (IdpExtractedHeader, IdpExtractedLineItem):
            for row in (await session.exec(select(model).where(model.document_id == doc_id))).all():
                await session.delete(row)
        for job in (await session.exec(
            select(IdpProcessingJob).where(IdpProcessingJob.document_id == doc_id)
        )).all():
            await session.delete(job)
        doc = await session.get(IdpDocument, doc_id)
        if doc:
            await session.delete(doc)
        for ia in (await session.exec(select(IdpAgent).where(IdpAgent.agent_id == agent_id))).all():
            await session.delete(ia)
        agent = await session.get(Agent, agent_id)
        if agent:
            await session.delete(agent)
        await session.commit()


async def _save(doc_id, job_id, *, tokens, skip):
    """One sink: its own session, exactly as `processed_docs_output.finalize` does."""
    async with session_scope() as session:
        return await save_extraction_results(
            session=session, document_id=doc_id, job_id=job_id,
            extraction_result=EXTRACTION, ocr_tokens=tokens, skip_if_already_saved=skip,
        )


async def _headers(doc_id):
    async with session_scope() as session:
        return (await session.exec(
            select(IdpExtractedHeader).where(IdpExtractedHeader.document_id == doc_id)
        )).all()


@pytest.mark.anyio
async def test_two_concurrent_sinks_do_not_violate_the_unique_constraint():
    """THE regression: `uq_idp_ext_header_job_field` on (job_id, field_name).

    Whether the two coroutines actually interleave inside the delete-then-insert window is up to the event
    loop and the driver, so this alone is a safety net rather than a reproduction. The deterministic guards
    are the two order-explicit tests below, plus `test_the_document_row_is_locked_before_the_delete`.
    """
    agent_id, doc_id, job_id = await _seed()
    try:
        # Both sinks fire at once, exactly as the two Processed Docs Output nodes do. Only one carries OCR
        # tokens — the other is on the router branch the document did not take.
        results = await asyncio.gather(
            _save(doc_id, job_id, tokens=OCR_TOKENS, skip=True),
            _save(doc_id, job_id, tokens=[], skip=True),
            return_exceptions=True,
        )
        for r in results:
            assert not isinstance(r, Exception), f"a concurrent sink raised: {r!r}"

        headers = await _headers(doc_id)
        assert len(headers) == 2, f"expected one row per field, got {len(headers)} (duplicates or clobber)"
        assert {h.field_name for h in headers} == {"supplier_name", "invoice_number"}
        # Whichever order they ran in, the evidence-bearing save must be the one on disk.
        assert all(h.source_location is not None for h in headers), "the token-less sink won the race"
    finally:
        await _cleanup(agent_id, doc_id)


@pytest.mark.anyio
async def test_the_token_less_sink_never_clobbers_the_evidence_sink():
    """Evidence sink first, token-less sink second: the second must not replace those rows."""
    agent_id, doc_id, job_id = await _seed()
    try:
        conf_real = await _save(doc_id, job_id, tokens=OCR_TOKENS, skip=True)
        headers = await _headers(doc_id)
        assert all(h.source_location is not None for h in headers), "the evidence-bearing save didn't land"

        conf_loser = await _save(doc_id, job_id, tokens=[], skip=True)
        assert conf_loser == pytest.approx(conf_real), "the second sink recomputed the overall confidence"

        after = await _headers(doc_id)
        assert {h.id for h in after} == {h.id for h in headers}, "rows were deleted and re-inserted"
        assert all(h.source_location is not None for h in after), "OCR evidence was clobbered"
        assert {h.confidence_score for h in after} == {h.confidence_score for h in headers}
    finally:
        await _cleanup(agent_id, doc_id)


@pytest.mark.anyio
async def test_the_evidence_sink_upgrades_rows_the_token_less_sink_wrote_first():
    """The OTHER order — the one that broke "first writer wins".

    Which sink reaches the row lock first is arbitrary. If the token-less one gets there first, a naive
    "skip when rows exist" rule would discard the OCR evidence entirely. The rule must be "never downgrade",
    so the evidence-bearing save UPGRADES the evidence-less rows.
    """
    agent_id, doc_id, job_id = await _seed()
    try:
        await _save(doc_id, job_id, tokens=[], skip=True)             # loser lands first
        first = await _headers(doc_id)
        assert all(h.source_location is None for h in first)

        await _save(doc_id, job_id, tokens=OCR_TOKENS, skip=True)     # the real sink upgrades them
        after = await _headers(doc_id)
        assert len(after) == 2
        assert all(h.source_location is not None for h in after), "the evidence sink was wrongly skipped"
        assert {h.id for h in after}.isdisjoint({h.id for h in first}), "rows were not replaced"
    finally:
        await _cleanup(agent_id, doc_id)


@pytest.mark.anyio
async def test_two_evidence_less_sinks_do_not_fight():
    """Vision runs have no OCR tokens at all — two such sinks must settle, not ping-pong."""
    agent_id, doc_id, job_id = await _seed()
    try:
        await _save(doc_id, job_id, tokens=[], skip=True)
        first_ids = {h.id for h in await _headers(doc_id)}
        await _save(doc_id, job_id, tokens=[], skip=True)
        assert {h.id for h in await _headers(doc_id)} == first_ids, "the second evidence-less sink rewrote rows"
    finally:
        await _cleanup(agent_id, doc_id)


@pytest.mark.anyio
async def test_reprocessing_still_replaces_rows_when_the_caller_owns_the_save():
    """`pipeline._run` (fixed engine) leaves skip_if_already_saved=False and keeps replace semantics."""
    agent_id, doc_id, job_id = await _seed()
    try:
        await _save(doc_id, job_id, tokens=OCR_TOKENS, skip=True)
        first_ids = {h.id for h in await _headers(doc_id)}

        await _save(doc_id, job_id, tokens=OCR_TOKENS, skip=False)   # the default: delete + insert
        second_ids = {h.id for h in await _headers(doc_id)}

        assert len(second_ids) == 2
        assert first_ids.isdisjoint(second_ids), "delete-then-insert should have produced fresh rows"
    finally:
        await _cleanup(agent_id, doc_id)


@pytest.mark.anyio
async def test_a_new_job_for_the_same_document_still_saves():
    """Idempotency is per JOB. Reprocessing mints a new job_id, which must not be skipped."""
    agent_id, doc_id, job_id = await _seed()
    try:
        await _save(doc_id, job_id, tokens=OCR_TOKENS, skip=True)

        async with session_scope() as session:
            doc = await session.get(IdpDocument, doc_id)
            job2 = IdpProcessingJob(document_id=doc_id, agent_id=doc.agent_id, status="running")
            session.add(job2)
            await session.commit()
            await session.refresh(job2)
            job2_id = job2.id

        await _save(doc_id, job2_id, tokens=OCR_TOKENS, skip=True)
        headers = await _headers(doc_id)
        assert len(headers) == 4, "the second job's rows were skipped as if they were the first job's"
        assert {h.job_id for h in headers} == {job_id, job2_id}
    finally:
        await _cleanup(agent_id, doc_id)


def test_the_document_row_is_locked_before_the_delete():
    """Without the FOR UPDATE, two sinks both DELETE (seeing nothing) and both INSERT."""
    import inspect

    from agentcore.services.idp import extraction

    src = inspect.getsource(extraction.save_extraction_results)
    lock = src.index("with_for_update()")
    delete_at = src.index("delete(IdpExtractedHeader)")
    assert lock < delete_at, "the row lock must precede the delete-then-insert"


def test_both_opportunistic_savers_opt_into_never_downgrade():
    """The sink and the safety net must not downgrade each other; only the owning caller may replace."""
    import inspect

    from agentcore.components.IDP import processed_docs_output
    from agentcore.services.idp.graph_native import process

    for mod in (processed_docs_output, process):
        src = inspect.getsource(mod)
        assert "skip_if_already_saved=True" in src, f"{mod.__name__} can clobber another sink's rows"
