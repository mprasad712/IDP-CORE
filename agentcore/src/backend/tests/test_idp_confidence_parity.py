"""The number that ROUTES a document must be the number STORED on it and SHOWN in the UI — and it must be
the MODEL's confidence, not a re-derivation of it.

Three separate bugs converged here.

1. **Divergence.** The Confidence Router averaged the LLM's self-reported per-field confidence; the DB
   stored the mean of an OCR-substring score. A scanned invoice the UI showed as 83% was routed on 0.75,
   fell below a 0.8 threshold, took the Low Confidence branch, and never reached the Rules (``gte 0.6``) or
   Approval Gate nodes at all — so it landed in Pending Review despite passing every rule configured.

2. **The "LLM confidence" was a constant.** ``build_compact_extraction_messages`` forbade the model from
   returning one, and ``_expand_value`` stamped a hardcoded ``0.75`` on every populated field.

3. **The OCR score measured tokenization, not correctness.** ``(0.70 + ratio × 0.17)`` divides by token
   length, so the same perfect extraction scored 1.0 from a digital PDF (word tokens) and 0.746 from a
   docx (paragraph tokens). The file format decided the confidence.

Now: ``confidence_score`` is the model's number, everywhere; whether a value is actually on the page is an
independent boolean (``grounded``); and both sides call ``extraction.compute_overall_confidence``.
"""

from uuid import uuid4

import pytest
from sqlmodel import select

from agentcore.schema.message import Message
from agentcore.services.database.models.agent.model import Agent
from agentcore.services.database.models.idp.config import IdpAgent
from agentcore.services.database.models.idp.documents import (
    IdpDocument,
    IdpExtractedHeader,
    IdpExtractedLineItem,
    IdpProcessingJob,
)
from agentcore.services.deps import session_scope
from agentcore.services.idp.extraction import (
    compute_overall_confidence,
    field_confidences,
    save_extraction_results,
)
from agentcore.services.idp.graph_native.payload import overall_confidence, ungrounded_fields


@pytest.fixture
def anyio_backend():
    return "asyncio"


def _tok(text, conf=0.99):
    return {"text": text, "page_number": 1, "bounding_box": [[61, 64], [289, 64], [289, 85], [61, 85]], "confidence": conf}


#: One token per WORD, as `text_layer.extract_native_text` emits for a digital PDF / PaddleOCR.
WORD_TOKENS = [_tok(w) for w in "ACME CORPORATION INV-2026-0042 Widget Pro".split()]
#: One token per PARAGRAPH, as `_office_docx` emits. Same document, same content, coarser tokenization.
PARAGRAPH_TOKENS = [_tok("Supplier: ACME CORPORATION"), _tok("Invoice no. INV-2026-0042"), _tok("Item: Widget Pro")]

#: Model-reported confidences — varied, because the model is now actually asked for them.
EXTRACTION = {
    "headers": {
        "supplier_name": {"value": "ACME CORPORATION", "confidence": 0.98, "reasoning": None},
        "invoice_number": {"value": "INV-2026-0042", "confidence": 0.91, "reasoning": None},
        # Absent from the document: stored at 0.0 for the UI, but MUST NOT drag the mean down.
        "supplier_gstn": {"value": None, "confidence": 0.0, "reasoning": None},
    },
    "line_items": [
        {"row_index": 0, "columns": [
            {"column_name": "description", "value": "Widget Pro", "confidence": 0.87},
            {"column_name": "hsn_code", "value": None, "confidence": 0.0},
        ]},
    ],
}
MEAN = (0.98 + 0.91 + 0.87) / 3          # 0.92 — three populated fields, two nulls excluded


def _msg(extracted, tokens):
    """A Message shaped exactly as the extractor emits it: the working set rides in additional_kwargs."""
    return Message(text="", additional_kwargs={"idp": {"extracted": extracted, "tokens": tokens}})


# ───────────────────────── the parity contract ─────────────────────────
def test_the_router_and_the_db_compute_the_same_number():
    """`overall_confidence` (router/rules/gate) must equal `compute_overall_confidence` (what gets stored)."""
    assert compute_overall_confidence(EXTRACTION) == pytest.approx(MEAN)
    assert overall_confidence(_msg(EXTRACTION, WORD_TOKENS)) == pytest.approx(MEAN)


def test_the_score_is_the_models_number_and_the_token_stream_cannot_move_it():
    """THE invariant. The same extraction scores identically as a digital PDF, a docx, and a vision run.

    It did not. Word tokens gave `invoice_number` an exact match (1.0) while the docx's paragraph token gave
    it `(0.70 + 6/22 × 0.17) = 0.746` — for byte-identical extracted values. Tokenization is a property of
    the file format, not of how confident we should be.
    """
    scores = {
        "digital pdf (word tokens)": overall_confidence(_msg(EXTRACTION, WORD_TOKENS)),
        "docx (paragraph tokens)": overall_confidence(_msg(EXTRACTION, PARAGRAPH_TOKENS)),
        "vision (no tokens)": overall_confidence(_msg(EXTRACTION, [])),
    }
    assert set(pytest.approx(v) == MEAN for v in scores.values()) == {True}, scores


def test_the_mean_covers_headers_and_line_items_and_skips_absent_fields():
    confs = field_confidences(EXTRACTION)
    assert sorted(confs) == [0.87, 0.91, 0.98], "2 populated headers + 1 populated line-item cell"
    assert compute_overall_confidence(EXTRACTION) == pytest.approx(sum(confs) / 3)

    # A line-item cell that scores DIFFERENTLY must move the mean — otherwise "included" is unfalsifiable.
    headers_only = {"headers": EXTRACTION["headers"], "line_items": []}
    with_weak_cell = {
        "headers": EXTRACTION["headers"],
        "line_items": [{"row_index": 0, "columns": [{"column_name": "amount", "value": "1250.00", "confidence": 0.10}]}],
    }
    assert compute_overall_confidence(with_weak_cell) < compute_overall_confidence(headers_only), (
        "a low-confidence line-item cell did not pull the mean down — line items are being ignored"
    )


def test_no_extracted_fields_scores_zero_so_it_routes_to_review():
    assert compute_overall_confidence({"headers": {}, "line_items": []}) == 0.0
    empty = {"headers": {"a": {"value": None, "confidence": 0.9}}, "line_items": []}
    assert compute_overall_confidence(empty) == 0.0


def test_fields_the_model_never_scored_are_excluded_and_an_all_unknown_document_routes_to_review():
    """`confidence: None` means the model ignored the schema. Unknown is not 0.75 and it is not 0.0.

    Excluded from the mean, so one unscored field cannot poison a good document. But if EVERY populated
    field is unscored there is nothing to average, the document scores 0.0, and a human looks at it.
    """
    partly = {
        "headers": {
            "a": {"value": "x", "confidence": 0.9},
            "b": {"value": "y", "confidence": None},   # unscored -> dropped, not averaged as 0.0
        },
        "line_items": [],
    }
    assert compute_overall_confidence(partly) == pytest.approx(0.9)

    all_unknown = {"headers": {"a": {"value": "x", "confidence": None}}, "line_items": []}
    assert compute_overall_confidence(all_unknown) == 0.0


def test_a_stamped_overall_conf_wins_so_downstream_nodes_reuse_the_routers_number():
    """The Confidence Router stamps `overall_conf`; Rules / Approval Gate must read that, not recompute."""
    msg = Message(text="", additional_kwargs={"idp": {"extracted": EXTRACTION, "tokens": WORD_TOKENS, "overall_conf": 0.42}})
    assert overall_confidence(msg) == pytest.approx(0.42)


# ───────────────────────── grounding is the OTHER signal ─────────────────────────
def test_a_confident_hallucination_scores_high_and_is_flagged_ungrounded():
    """Confidence cannot catch a hallucination — a hallucinated value is a CONFIDENT one. Grounding can.

    The old code damped the score instead (`llm_conf × 0.38`), which hid the model's claim AND mislabelled
    every correctly reformatted date as invented. Two signals, reported separately.
    """
    hallucinated = {
        "headers": {
            "supplier_name": {"value": "ACME CORPORATION", "confidence": 0.98},
            "invoice_date": {"value": "2099-01-01", "confidence": 0.99},   # not on the page
        },
        "line_items": [],
    }
    assert compute_overall_confidence(hallucinated) == pytest.approx(0.985)   # the score is NOT damped
    assert ungrounded_fields(_msg(hallucinated, WORD_TOKENS)) == ["invoice_date"]


def test_grounding_is_unknown_not_false_when_there_is_no_token_stream():
    """A vision run has nothing to check against. Reporting every field as ungrounded would route them all
    to review — the opposite of the intent."""
    assert ungrounded_fields(_msg(EXTRACTION, [])) == []


def test_a_reformatted_date_is_grounded_against_the_prose_it_came_from():
    """The prompt mandates YYYY-MM-DD; the page says "30 April 2022". That is obedience, not invention.

    This exact case scored 0.285 — the band reserved for hallucinated values — and dragged the document
    below its auto-approve threshold.
    """
    tokens = [_tok(w) for w in "Invoice Date : 30 April 2022".split()]
    extracted = {"headers": {"invoice_date": {"value": "2022-04-30", "confidence": 0.95}}, "line_items": []}
    assert ungrounded_fields(_msg(extracted, tokens)) == []


# ───────────────────────── end-to-end against the DB ─────────────────────────
async def _seed():
    agent_id, doc_id = uuid4(), uuid4()
    async with session_scope() as session:
        session.add(Agent(id=agent_id, name=f"conf-{agent_id.hex[:8]}", data={}))
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


@pytest.mark.anyio
async def test_the_stored_and_displayed_confidence_equals_what_the_router_saw():
    """`idp_documents.overall_confidence` (the UI's %) == the router's score for the same payload."""
    agent_id, doc_id, job_id = await _seed()
    try:
        routed = overall_confidence(_msg(EXTRACTION, WORD_TOKENS))

        async with session_scope() as session:
            returned = await save_extraction_results(
                session=session, document_id=doc_id, job_id=job_id,
                extraction_result=EXTRACTION, ocr_tokens=WORD_TOKENS,
            )
        assert returned == pytest.approx(routed)

        async with session_scope() as session:
            doc = await session.get(IdpDocument, doc_id)
            assert float(doc.overall_confidence) == pytest.approx(routed, abs=1e-4)
    finally:
        await _cleanup(agent_id, doc_id)


@pytest.mark.anyio
async def test_the_persisted_row_carries_the_models_confidence_and_a_separate_grounded_flag():
    agent_id, doc_id, job_id = await _seed()
    try:
        extraction = {
            "headers": {
                "supplier_name": {"value": "ACME CORPORATION", "confidence": 0.98},
                "invoice_date": {"value": "2099-01-01", "confidence": 0.99},   # confident, but not on the page
                "supplier_gstn": {"value": None, "confidence": 0.0},
            },
            "line_items": [],
        }
        async with session_scope() as session:
            await save_extraction_results(
                session=session, document_id=doc_id, job_id=job_id,
                extraction_result=extraction, ocr_tokens=WORD_TOKENS,
            )

        async with session_scope() as session:
            rows = {
                h.field_name: h
                for h in (await session.exec(
                    select(IdpExtractedHeader).where(IdpExtractedHeader.job_id == job_id)
                )).all()
            }

        assert float(rows["supplier_name"].confidence_score) == pytest.approx(0.98)
        assert rows["supplier_name"].grounded is True

        # The hallucination keeps its high confidence AND is marked ungrounded. Both, or the split is a lie.
        assert float(rows["invoice_date"].confidence_score) == pytest.approx(0.99)
        assert rows["invoice_date"].grounded is False
        assert rows["invoice_date"].source_location is None

        # A field with no value was never checked -> grounded is NULL, not False.
        assert rows["supplier_gstn"].grounded is None
    finally:
        await _cleanup(agent_id, doc_id)


@pytest.mark.anyio
async def test_the_reported_scenario_now_routes_high():
    """A document the UI shows above the threshold must take the High Confidence branch.

    Reproduces the report: threshold 0.8, UI shows 83%, but the router saw 0.75 and routed Low, so the
    Rules (gte 0.6) and Approval Gate nodes were never reached.
    """
    from agentcore.components.IDP.confidence_router import IDPConfidenceRouter

    agent_id, doc_id, job_id = await _seed()
    try:
        async with session_scope() as session:
            stored = await save_extraction_results(
                session=session, document_id=doc_id, job_id=job_id,
                extraction_result=EXTRACTION, ocr_tokens=WORD_TOKENS,
            )
        assert stored >= 0.8, f"fixture must sit above the threshold to exercise the bug (got {stored:.3f})"

        router = IDPConfidenceRouter()
        router.data = _msg(EXTRACTION, WORD_TOKENS)
        router.confidence_field = "confidence"
        router.threshold = 0.8
        assert router._get_score() == pytest.approx(stored), "the router scored a different number than the DB"
    finally:
        await _cleanup(agent_id, doc_id)


# ───────────────────────── structural guards ─────────────────────────
def test_both_sides_call_one_function():
    """Neither side may grow its own averaging loop again."""
    import inspect

    from agentcore.services.idp import extraction
    from agentcore.services.idp.graph_native import payload

    assert "compute_overall_confidence(" in inspect.getsource(payload.overall_confidence)
    assert "compute_overall_confidence(" in inspect.getsource(extraction.save_extraction_results)


def test_the_score_function_cannot_see_the_token_stream():
    """Structural, because a comment saying "don't blend grounding in" is not enforcement.

    `compute_overall_confidence` takes the extraction and nothing else. There is no OCR token stream in
    scope for it to be re-derived from, so the docx-vs-pdf divergence cannot come back by accident.
    """
    import ast
    import inspect
    import textwrap

    from agentcore.services.idp.extraction import compute_overall_confidence, field_confidences

    assert list(inspect.signature(compute_overall_confidence).parameters) == ["extraction_result"]
    assert list(inspect.signature(field_confidences).parameters) == ["extraction_result"]

    # Match the AST, not the text. A substring scan of the source matches the docstring — which explains
    # at length why grounding and tokens must stay out of this function, and would "fail" the guard.
    fn = ast.parse(textwrap.dedent(inspect.getsource(field_confidences))).body[0]
    if isinstance(fn.body[0], ast.Expr) and isinstance(fn.body[0].value, ast.Constant):
        fn.body.pop(0)                                   # drop the docstring
    code = ast.dump(ast.Module(body=fn.body, type_ignores=[])).lower()
    assert "grounding" not in code and "token" not in code, "field_confidences reached for the token stream"


# ───────────────────────── the router gates on BOTH signals ─────────────────────────
def _router(msg, threshold=0.8, require_grounding=None):
    from agentcore.components.IDP.confidence_router import IDPConfidenceRouter

    r = IDPConfidenceRouter()
    r.data = msg
    r.confidence_field = "confidence"
    r.threshold = threshold
    if require_grounding is not None:
        r.require_grounding = require_grounding
    return r


HALLUCINATED = {
    "headers": {
        "supplier_name": {"value": "ACME CORPORATION", "confidence": 0.98},
        "po_number": {"value": "PO-9999999", "confidence": 0.99},   # confident, and not on the page
    },
    "line_items": [],
}


def test_a_confident_hallucination_does_not_take_the_high_confidence_branch():
    """0.985 clears a 0.8 threshold. It must still go to review, because a value was invented.

    Confidence alone can never catch this — the model is confident *because* it believes the invention.
    """
    r = _router(_msg(HALLUCINATED, WORD_TOKENS), require_grounding=True)
    score, ungrounded, takes_high = r._decide()
    assert score == pytest.approx(0.985)      # the score is NOT damped...
    assert ungrounded == ["po_number"]        # ...and the hallucination is caught separately
    assert takes_high is False


def test_the_two_branches_are_exactly_complementary():
    """`high_confidence` and `low_confidence` both run (group_outputs). Exactly one may pass data through."""
    for extracted, tokens in ((EXTRACTION, WORD_TOKENS), (HALLUCINATED, WORD_TOKENS), (EXTRACTION, [])):
        for require in (True, False):
            r = _router(_msg(extracted, tokens), require_grounding=require)
            _s, _u, takes_high = r._decide()
            hi, lo = _router(_msg(extracted, tokens), require_grounding=require), _router(_msg(extracted, tokens), require_grounding=require)
            hi_stopped, lo_stopped = [], []
            hi.stop = lambda name: hi_stopped.append(name)
            lo.stop = lambda name: lo_stopped.append(name)
            hi.high_confidence()
            lo.low_confidence()
            assert bool(hi_stopped) != bool(lo_stopped), (extracted is HALLUCINATED, require, takes_high)
            assert bool(hi_stopped) is not takes_high


def test_grounding_never_blocks_a_vision_run():
    """No token stream -> nothing to check -> the gate must not fire, or every vision doc routes to review."""
    _s, ungrounded, takes_high = _router(_msg(HALLUCINATED, []), require_grounding=True)._decide()
    assert ungrounded == []
    assert takes_high is True


def test_the_grounding_gate_is_off_by_default_so_published_agents_do_not_change_behavior():
    """`Node` materializes every declared input as an attribute holding its class default.

    So a Confidence Router rebuilt from a snapshot published BEFORE `require_grounding` existed is
    indistinguishable from a fresh one — `getattr(self, "require_grounding", False)` reads the class value,
    not the absent template key. A `value=True` default would therefore reroute every already-deployed
    agent's documents with no republish. It must be opt-in.
    """
    from agentcore.components.IDP.confidence_router import IDPConfidenceRouter

    bare = IDPConfidenceRouter()
    assert bare.require_grounding is False, (
        "a default-on grounding gate silently changes routing for every published snapshot"
    )
    assert {i.name: i.value for i in IDPConfidenceRouter.inputs}["require_grounding"] is False

    r = _router(_msg(HALLUCINATED, WORD_TOKENS))          # left at the default
    _s, ungrounded, takes_high = r._decide()
    assert ungrounded == []
    assert takes_high is True, "an existing published agent changed behavior without being republished"


def test_no_code_path_can_invent_a_confidence():
    """A repo-wide guard, because this bug had FOUR copies and I found them one at a time.

    `_DEFAULT_FIELD_CONFIDENCE = 0.75` in `_expand_value`, `0.8` twice in `extract_dynamic`'s structured
    path, and `0.8` twice more in `extract_multimodal`'s vision path. Each silently replaced the model's
    judgement with a constant, which downstream then overwrote with an OCR score. Grepping for the names I
    expected is what let three of them survive the first pass.

    Every `confidence` a field can carry must come from `_model_confidence`, whose only sources are the
    model's own float, `0.0` (no value), or `None` (unknown).
    """
    import ast
    import inspect
    import textwrap

    from agentcore.services.idp import extraction

    tree = ast.parse(textwrap.dedent(inspect.getsource(extraction)))
    offenders = []
    for node in ast.walk(tree):
        # A dict literal `{"confidence": <number>}` -- a constant reaching the canonical shape.
        if isinstance(node, ast.Dict):
            for key, value in zip(node.keys, node.values):
                if isinstance(key, ast.Constant) and key.value == "confidence":
                    if isinstance(value, ast.Constant) and isinstance(value.value, (int, float)):
                        if value.value != 0.0:
                            offenders.append(f"line {value.lineno}: {{'confidence': {value.value}}}")
        # A `.get("confidence", <non-zero number>)` default.
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "get":
            if len(node.args) == 2 and isinstance(node.args[0], ast.Constant) and node.args[0].value == "confidence":
                default = node.args[1]
                if isinstance(default, ast.Constant) and isinstance(default.value, (int, float)) and default.value != 0.0:
                    offenders.append(f"line {default.lineno}: .get('confidence', {default.value})")

    assert not offenders, "a constant confidence can reach the database:\n  " + "\n  ".join(offenders)
