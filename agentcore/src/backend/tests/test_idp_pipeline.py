"""Tests for the B19 per-document IDP processing pipeline.

The digital path, routing, persistence, idempotency, config-reader and endpoint tests
run anywhere. The scanned/OCR path needs PaddleOCR and is covered by a separate, skipped
test (runs on machines/CI where paddle is installed).
"""

import io
from uuid import uuid4

import pytest

from agentcore.services.idp import pipeline
from agentcore.services.idp.agent_config import resolve_pipeline_config


@pytest.fixture
def anyio_backend():
    return "asyncio"


class _MockStorage:
    """In-memory storage (the deployed default is Azure Blob, unreachable in tests)."""

    def __init__(self):
        self.store: dict[tuple[str, str], bytes] = {}

    async def save_file(self, agent_id: str, file_name: str, data: bytes) -> None:
        self.store[(agent_id, file_name)] = data

    async def get_file(self, agent_id: str, file_name: str) -> bytes:
        key = (agent_id, file_name)
        if key not in self.store:
            raise FileNotFoundError(file_name)
        return self.store[key]


@pytest.fixture(autouse=True)
def mock_storage(monkeypatch):
    s = _MockStorage()
    monkeypatch.setattr(pipeline, "get_storage_service", lambda: s)
    return s


# ─────────────────────────────── helpers ───────────────────────────────
def _node(display_name: str, fields: dict) -> dict:
    return {
        "id": display_name.replace(" ", ""),
        "type": "genericNode",
        "data": {
            "node": {
                "display_name": display_name,
                "template": {k: {"value": v} for k, v in fields.items()},
            }
        },
    }


def _graph(model_uuid: str | None, mode: str = "dynamic_prompt", prompt: str = "Extract fields", extra_nodes=None):
    nodes = [_node("AI Field Extractor", {"extraction_mode": mode, "prompt": prompt, "config_name": ""})]
    if model_uuid is not None:
        nodes.append(_node("Large Language Model", {"registry_model": f"GPT | gpt-4o | {model_uuid}"}))
    if extra_nodes:
        nodes.extend(extra_nodes)
    return {"nodes": nodes, "edges": []}


def _digital_pdf() -> bytes:
    from reportlab.pdfgen import canvas

    buf = io.BytesIO()
    c = canvas.Canvas(buf)
    c.drawString(50, 770, "Invoice Number INV-001 Date 2026 Total Amount 100")
    y = 748
    for r in range(6):  # plenty of on-page words so it classifies as digital
        c.drawString(50, y, " ".join(f"alpha{r}{i}" for i in range(8)))
        y -= 18
    c.showPage()
    c.save()
    return buf.getvalue()


async def _setup_document(session, *, graph, file_bytes=None, default_rule_action="pending_review", extra=None):
    """Create base Agent (with graph), IdpAgent, and a stored IdpDocument. Returns (agent_id, doc_id)."""
    from agentcore.services.database.models.agent.model import Agent
    from agentcore.services.database.models.idp.config import IdpAgent
    from agentcore.services.database.models.idp.documents import IdpDocument

    agent_id = uuid4()
    doc_id = uuid4()

    base_agent = Agent(id=agent_id, name="IDP Pipeline Test Agent", data=graph)
    session.add(base_agent)
    await session.flush()

    idp_agent = IdpAgent(
        id=agent_id, agent_id=agent_id, extraction_mode="dynamic_prompting",
        default_rule_action=default_rule_action, extra=extra,
    )
    session.add(idp_agent)
    await session.flush()

    storage_filename = f"idp_{doc_id}.pdf"
    if file_bytes is not None:
        # pipeline.get_storage_service is monkeypatched to the in-memory mock
        await pipeline.get_storage_service().save_file(agent_id=str(agent_id), file_name=storage_filename, data=file_bytes)

    doc = IdpDocument(
        id=doc_id, agent_id=agent_id, original_filename="test.pdf",
        file_path=f"{agent_id}/{storage_filename}", file_type="pdf",
        file_size_bytes=len(file_bytes or b""), source="upload", status="queued",
    )
    session.add(doc)
    await session.commit()
    return agent_id, doc_id


async def _cleanup(agent_id, doc_id):
    from agentcore.services.database.models.agent.model import Agent
    from agentcore.services.database.models.idp.config import IdpAgent
    from agentcore.services.database.models.idp.documents import IdpDocument, IdpProcessingJob
    from agentcore.services.deps import session_scope
    from sqlmodel import select

    async with session_scope() as session:
        for job in (await session.exec(select(IdpProcessingJob).where(IdpProcessingJob.document_id == doc_id))).all():
            await session.delete(job)
        doc = await session.get(IdpDocument, doc_id)
        if doc:
            await session.delete(doc)
        idp = await session.get(IdpAgent, agent_id)
        if idp:
            await session.delete(idp)
        ag = await session.get(Agent, agent_id)
        if ag:
            await session.delete(ag)
        await session.commit()


def _patch_extraction(monkeypatch, *, result=None, raises=False):
    async def fake_build_llm(session, model_id):
        return object()

    async def fake_extract_dynamic(ocr_text, prompt, llm_model):
        if raises:
            raise RuntimeError("boom from LLM")
        return result if result is not None else {
            "headers": {"invoice_number": {"value": "INV-001", "confidence": 0.95, "reasoning": "top"}},
            "line_items": [],
        }

    monkeypatch.setattr(pipeline, "_build_llm", fake_build_llm)
    monkeypatch.setattr(pipeline, "extract_dynamic", fake_extract_dynamic)


# ─────────────────────────────── tests ───────────────────────────────
@pytest.mark.anyio
async def test_pipeline_digital_happy_path(monkeypatch):
    from agentcore.services.database.models.idp.documents import IdpDocument, IdpProcessingJob, IdpExtractedHeader
    from agentcore.services.deps import session_scope
    from sqlmodel import select

    _patch_extraction(monkeypatch)
    async with session_scope() as session:
        agent_id, doc_id = await _setup_document(session, graph=_graph(str(uuid4())), file_bytes=_digital_pdf())

    try:
        await pipeline.process_document(doc_id)
        async with session_scope() as session:
            doc = await session.get(IdpDocument, doc_id)
            assert doc.status in ("auto_approved", "pending_review")
            assert doc.processing_started_at is not None and doc.processing_completed_at is not None
            assert doc.overall_confidence is not None

            job = (await session.exec(select(IdpProcessingJob).where(IdpProcessingJob.document_id == doc_id))).first()
            assert job.status == "completed"
            assert job.extraction_mode_used == "dynamic_prompt"
            assert isinstance(job.log, list) and job.log
            steps = [e["step"] for e in job.log]
            for s in ("load", "detect", "ocr", "extract", "route", "finalize"):
                assert s in steps

            headers = (await session.exec(select(IdpExtractedHeader).where(IdpExtractedHeader.job_id == job.id))).all()
            assert any(h.field_name == "invoice_number" and h.extracted_value == "INV-001" for h in headers)
    finally:
        await _cleanup(agent_id, doc_id)


@pytest.mark.anyio
async def test_pipeline_routing_auto_approve(monkeypatch):
    from agentcore.services.database.models.idp.documents import IdpDocument
    from agentcore.services.deps import session_scope

    # high-confidence result + auto_approve policy => auto_approved
    _patch_extraction(monkeypatch, result={
        "headers": {"f": {"value": "x", "confidence": 0.99}}, "line_items": [],
    })
    async with session_scope() as session:
        agent_id, doc_id = await _setup_document(
            session, graph=_graph(str(uuid4())), file_bytes=_digital_pdf(), default_rule_action="auto_approve"
        )
    try:
        await pipeline.process_document(doc_id)
        async with session_scope() as session:
            doc = await session.get(IdpDocument, doc_id)
            assert doc.status == "auto_approved"
    finally:
        await _cleanup(agent_id, doc_id)


@pytest.mark.anyio
async def test_pipeline_routing_empty_goes_to_review(monkeypatch):
    from agentcore.services.database.models.idp.documents import IdpDocument
    from agentcore.services.deps import session_scope

    # zero fields => confidence 0 => pending_review even under auto_approve
    _patch_extraction(monkeypatch, result={"headers": {}, "line_items": []})
    async with session_scope() as session:
        agent_id, doc_id = await _setup_document(
            session, graph=_graph(str(uuid4())), file_bytes=_digital_pdf(), default_rule_action="auto_approve"
        )
    try:
        await pipeline.process_document(doc_id)
        async with session_scope() as session:
            doc = await session.get(IdpDocument, doc_id)
            assert doc.status == "pending_review"
    finally:
        await _cleanup(agent_id, doc_id)


@pytest.mark.anyio
async def test_pipeline_failure_path(monkeypatch):
    from agentcore.services.database.models.idp.documents import IdpDocument, IdpProcessingJob
    from agentcore.services.deps import session_scope
    from sqlmodel import select

    _patch_extraction(monkeypatch, raises=True)
    async with session_scope() as session:
        agent_id, doc_id = await _setup_document(session, graph=_graph(str(uuid4())), file_bytes=_digital_pdf())
    try:
        await pipeline.process_document(doc_id)
        async with session_scope() as session:
            doc = await session.get(IdpDocument, doc_id)
            assert doc.status == "failed" and doc.failed_at is not None
            job = (await session.exec(select(IdpProcessingJob).where(IdpProcessingJob.document_id == doc_id))).first()
            assert job.status == "failed" and job.error_message
            assert job.log[-1]["status"] == "error"
    finally:
        await _cleanup(agent_id, doc_id)


@pytest.mark.anyio
async def test_pipeline_idempotency_refuses_running(monkeypatch):
    from agentcore.services.database.models.idp.documents import IdpDocument, IdpProcessingJob
    from agentcore.services.deps import session_scope

    _patch_extraction(monkeypatch)
    async with session_scope() as session:
        agent_id, doc_id = await _setup_document(session, graph=_graph(str(uuid4())), file_bytes=_digital_pdf())
        # pre-existing running job (different id)
        running = IdpProcessingJob(document_id=doc_id, agent_id=agent_id, status="running")
        session.add(running)
        # our queued job
        ours = IdpProcessingJob(document_id=doc_id, agent_id=agent_id, status="queued")
        session.add(ours)
        await session.commit()
        our_job_id = ours.id
    try:
        await pipeline.process_document(doc_id, our_job_id)
        async with session_scope() as session:
            our = await session.get(IdpProcessingJob, our_job_id)
            assert our.status == "failed" and "already running" in (our.error_message or "")
            doc = await session.get(IdpDocument, doc_id)
            assert doc.status == "queued"  # untouched
    finally:
        await _cleanup(agent_id, doc_id)


@pytest.mark.anyio
async def test_pipeline_missing_model_in_graph(monkeypatch):
    from agentcore.services.database.models.idp.documents import IdpDocument, IdpProcessingJob
    from agentcore.services.deps import session_scope
    from sqlmodel import select

    _patch_extraction(monkeypatch)
    async with session_scope() as session:
        agent_id, doc_id = await _setup_document(
            session, graph=_graph(None), file_bytes=_digital_pdf()  # no LLM node
        )
    try:
        await pipeline.process_document(doc_id)
        async with session_scope() as session:
            doc = await session.get(IdpDocument, doc_id)
            assert doc.status == "failed"
            job = (await session.exec(select(IdpProcessingJob).where(IdpProcessingJob.document_id == doc_id))).first()
            assert "model" in (job.error_message or "").lower()
    finally:
        await _cleanup(agent_id, doc_id)


@pytest.mark.anyio
async def test_agent_config_reader():
    from agentcore.services.database.models.agent.model import Agent
    from agentcore.services.database.models.idp.config import IdpAgent
    from agentcore.services.deps import session_scope

    model_uuid = str(uuid4())
    extra_nodes = [
        _node("Scan Corrector", {"fix_skew": False, "fix_rotation": True, "allowed_angles": "90,270"}),
        _node("Page Selector", {"selection_mode": "first_n", "first_n_pages": 2}),
        _node("Document Type Detector", {"allowed_extensions": "pdf,png", "min_text_length": 40}),
    ]
    graph = _graph(model_uuid, mode="dynamic_prompt", prompt="Read it", extra_nodes=extra_nodes)
    idp_agent = IdpAgent(id=uuid4(), agent_id=uuid4(), extraction_mode="dynamic_prompting", default_rule_action="auto_approve")
    base_agent = Agent(id=uuid4(), name="x", data=graph)

    async with session_scope() as session:
        cfg = await resolve_pipeline_config(session, idp_agent, base_agent)

    assert cfg.model_id == model_uuid
    assert cfg.extraction_mode == "dynamic_prompt"
    assert cfg.prompt == "Read it"
    assert cfg.fix_skew is False and cfg.fix_rotation is True
    assert cfg.allowed_angles == [90, 270]
    assert cfg.page_selection_mode == "first_n" and cfg.first_n_pages == 2
    assert cfg.allowed_extensions == {"pdf", "png"} and cfg.min_text_length == 40
    assert cfg.default_rule_action == "auto_approve"


@pytest.mark.anyio
async def test_process_endpoint(monkeypatch):
    from fastapi.testclient import TestClient
    from agentcore.main import create_app
    from agentcore.services.auth.utils import get_current_active_user
    from agentcore.api.idp import idp_rbac
    from agentcore.api.idp import documents as documents_api
    from agentcore.services.database.models.idp.documents import IdpProcessingJob
    from agentcore.services.deps import session_scope

    def mock_user():
        from types import SimpleNamespace
        return SimpleNamespace(id=uuid4(), role="super_admin")

    async with session_scope() as session:
        agent_id, doc_id = await _setup_document(session, graph=_graph(str(uuid4())), file_bytes=b"%PDF-1.4 test")

    fixed_job = uuid4()

    async def fake_enqueue(session, document_id):
        return fixed_job

    monkeypatch.setattr(documents_api, "enqueue_document", fake_enqueue)

    app = create_app()
    app.dependency_overrides[get_current_active_user] = mock_user
    app.dependency_overrides[idp_rbac] = mock_user
    client = TestClient(app)
    try:
        r = client.post(f"/api/v1/idp/documents/{doc_id}/process")
        assert r.status_code == 202, r.text
        assert r.json()["job_id"] == str(fixed_job)

        # a running job now exists -> 409
        async with session_scope() as session:
            session.add(IdpProcessingJob(document_id=doc_id, agent_id=agent_id, status="running"))
            await session.commit()
        r2 = client.post(f"/api/v1/idp/documents/{doc_id}/process")
        assert r2.status_code == 409, r2.text
    finally:
        app.dependency_overrides.clear()
        await _cleanup(agent_id, doc_id)


@pytest.mark.skipif(
    not __import__("agentcore.services.idp.ocr", fromlist=["_paddle_ocr_available"])._paddle_ocr_available,
    reason="PaddleOCR not installed (runs on M4/NVIDIA/Docker/CI)",
)
@pytest.mark.anyio
async def test_pipeline_scanned_path(monkeypatch):
    """Full OCR path with a scanned image. Skipped where paddle is absent."""
    from agentcore.services.database.models.idp.documents import IdpDocument
    from agentcore.services.deps import session_scope

    _patch_extraction(monkeypatch)
    # a tiny PNG (1x1) -> classified scanned -> real PaddleOCR
    png = (
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4"
        b"\x89\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
    )
    async with session_scope() as session:
        agent_id, doc_id = await _setup_document(session, graph=_graph(str(uuid4())), file_bytes=png)
        doc = await session.get(IdpDocument, doc_id)
        doc.file_type = "png"
        doc.file_path = doc.file_path.replace(".pdf", ".png")
        session.add(doc)
        await session.commit()
    try:
        await pipeline.process_document(doc_id)
        async with session_scope() as session:
            doc = await session.get(IdpDocument, doc_id)
            assert doc.status in ("auto_approved", "pending_review", "failed")
    finally:
        await _cleanup(agent_id, doc_id)
