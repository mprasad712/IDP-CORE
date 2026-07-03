import pytest
from uuid import uuid4
from datetime import datetime, timezone


@pytest.fixture
def anyio_backend():
    return "asyncio"


class _Root:
    """Stub user; role 'root' bypasses _can_access_document org-scoping."""
    def __init__(self):
        self.id = uuid4()
        self.role = "root"


async def _make_doc(session, *, status="pending_review"):
    """Create a base Agent + IdpAgent(has_processed_docs_output) + IdpDocument + one job. Returns (idp_agent_id, doc_id, job_id)."""
    from agentcore.services.database.models.agent.model import Agent
    from agentcore.services.database.models.idp.config import IdpAgent
    from agentcore.services.database.models.idp.documents import IdpDocument, IdpProcessingJob
    base = Agent(id=uuid4(), name="RS Test Agent", data={"nodes": []})
    session.add(base); await session.flush()
    idp = IdpAgent(id=uuid4(), agent_id=base.id, extraction_mode="dynamic_prompting",
                   default_rule_action="pending_review", extra={"has_processed_docs_output": "true"})
    session.add(idp); await session.flush()
    doc = IdpDocument(id=uuid4(), agent_id=idp.id, original_filename="t.png", file_path=f"{idp.id}/t.png",
                      file_type="png", file_size_bytes=10, source="upload", status=status)
    session.add(doc)
    job = IdpProcessingJob(id=uuid4(), document_id=doc.id, agent_id=idp.id, status="completed")
    session.add(job); await session.commit()
    return idp.id, doc.id, job.id


@pytest.mark.anyio
async def test_soft_delete_hides_doc_but_keeps_row(monkeypatch):
    from agentcore.services.deps import session_scope
    from agentcore.api.idp import processed_docs as pd
    from agentcore.services.database.models.idp.documents import IdpDocument
    # avoid real storage calls during delete
    class _S:  # no-op storage
        async def delete_file(self, *a, **k): return None
    monkeypatch.setattr(pd, "get_storage_service", lambda: _S())
    async with session_scope() as s:
        _agent, doc_id, _job = await _make_doc(s)
    try:
        async with session_scope() as s:
            await pd.delete_processed_doc(session=s, id=doc_id, current_user=_Root())
        async with session_scope() as s:
            doc = await s.get(IdpDocument, doc_id)
            assert doc is not None                    # row retained (soft delete)
            assert doc.deleted_at is not None         # marked deleted
    finally:
        async with session_scope() as s:
            doc = await s.get(IdpDocument, doc_id)
            if doc: await s.delete(doc); await s.commit()


@pytest.mark.anyio
async def test_soft_deleted_doc_blocks_export_patch_review_approve():
    """A soft-deleted doc (deleted_at set) must 404 from export / patch-fields / review / approve,
    matching the detail/DELETE/line-item guards. Root stub bypasses org scope."""
    from fastapi import HTTPException
    from agentcore.services.deps import session_scope
    from agentcore.api.idp import processed_docs as pd
    from agentcore.services.database.models.idp.documents import IdpDocument, IdpProcessingJob

    async with session_scope() as s:
        _agent, doc_id, job_id = await _make_doc(s)
    try:
        # soft-delete the doc directly
        async with session_scope() as s:
            doc = await s.get(IdpDocument, doc_id)
            doc.deleted_at = datetime.now(timezone.utc)
            s.add(doc); await s.commit()

        # export → 404
        async with session_scope() as s:
            with pytest.raises(HTTPException) as ei:
                await pd.export_processed_doc(session=s, id=doc_id, current_user=_Root())
            assert ei.value.status_code == 404

        # patch fields → 404
        async with session_scope() as s:
            with pytest.raises(HTTPException) as ei:
                await pd.update_extracted_fields(
                    session=s, id=doc_id,
                    payload=pd.HumanFieldsUpdateRequest(), current_user=_Root())
            assert ei.value.status_code == 404

        # review → 404
        async with session_scope() as s:
            with pytest.raises(HTTPException) as ei:
                await pd.review_processed_doc(
                    session=s, id=doc_id,
                    payload=pd.DocumentReviewRequest(), current_user=_Root())
            assert ei.value.status_code == 404

        # approve → 404
        async with session_scope() as s:
            with pytest.raises(HTTPException) as ei:
                await pd.approve_processed_doc(session=s, id=doc_id, current_user=_Root())
            assert ei.value.status_code == 404
    finally:
        async with session_scope() as s:
            j = await s.get(IdpProcessingJob, job_id); d = await s.get(IdpDocument, doc_id)
            if j: await s.delete(j)
            if d: await s.delete(d)
            await s.commit()


@pytest.mark.anyio
async def test_add_and_delete_line_item_row(monkeypatch):
    from agentcore.services.deps import session_scope
    from agentcore.api.idp import processed_docs as pd
    from agentcore.services.database.models.idp.documents import IdpExtractedLineItem
    from sqlmodel import select
    async with session_scope() as s:
        _agent, doc_id, job_id = await _make_doc(s)
        # seed an existing row 0 with 2 columns
        for col, val in (("item", "Widget"), ("amount", "10")):
            s.add(IdpExtractedLineItem(id=uuid4(), document_id=doc_id, job_id=job_id,
                                       row_index=0, column_name=col, extracted_value=val))
        await s.commit()
    try:
        # ADD a row
        async with session_scope() as s:
            res = await pd.add_line_item_row(
                session=s, id=doc_id,
                payload=pd.LineItemRowCreate(columns={"item": "Gadget", "amount": "20"}),
                current_user=_Root())
        assert res["row_index"] == 1
        async with session_scope() as s:
            rows = (await s.exec(select(IdpExtractedLineItem).where(
                IdpExtractedLineItem.document_id == doc_id, IdpExtractedLineItem.row_index == 1))).all()
            assert {r.column_name for r in rows} == {"item", "amount"}
            assert {r.extracted_value for r in rows} == {"Gadget", "20"}
        # DELETE row 0
        async with session_scope() as s:
            await pd.delete_line_item_row(session=s, id=doc_id, row_index=0, current_user=_Root())
        async with session_scope() as s:
            left = (await s.exec(select(IdpExtractedLineItem).where(
                IdpExtractedLineItem.document_id == doc_id))).all()
            assert {r.row_index for r in left} == {1}   # only the added row remains
    finally:
        async with session_scope() as s:
            from agentcore.services.database.models.idp.documents import IdpDocument, IdpProcessingJob
            for r in (await s.exec(select(IdpExtractedLineItem).where(IdpExtractedLineItem.document_id == doc_id))).all():
                await s.delete(r)
            j = await s.get(IdpProcessingJob, job_id);  d = await s.get(IdpDocument, doc_id)
            if j: await s.delete(j)
            if d: await s.delete(d)
            await s.commit()
