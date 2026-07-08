"""`POST /api/run/{agent_id}` with a document attachment runs a PUBLISHED IDP agent.

The endpoint is shared with chat agents, so the most important test here is the FIRST one: a chat agent
must still take the chat path, untouched. Everything else pins the IDP arm's contract.

The pipeline itself is stubbed — it has its own suites (`test_idp_pipeline.py`, `test_idp_published_snapshot.py`).
What is tested here is the API contract: guards, the row that gets written, the response shape, and recovery.
"""

import base64
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlmodel import select

from agentcore.api.idp import run_api
from agentcore.helpers.agent import get_agent_by_id_or_endpoint_name
from agentcore.main import create_app
from agentcore.services.auth.utils import validate_agent_api_key
from agentcore.services.database.models.agent.model import Agent
from agentcore.services.database.models.idp.config import IdpAgent
from agentcore.services.database.models.idp.documents import (
    IdpDocument,
    IdpExtractedHeader,
    IdpProcessingJob,
)
from agentcore.services.deps import session_scope

PDF_BYTES = b"%PDF-1.4\n%%EOF\n"
PDF_B64 = base64.b64encode(PDF_BYTES).decode()


@pytest.fixture
def anyio_backend():
    return "asyncio"


# ───────────────────────────── graphs ─────────────────────────────
def _node(display_name: str) -> dict:
    return {
        "id": display_name.replace(" ", ""),
        "data": {"type": display_name.replace(" ", ""), "node": {"display_name": display_name, "template": {}}},
    }


IDP_GRAPH = {"nodes": [_node("AI Field Extractor"), _node("Processed Docs Output")], "edges": []}
CHAT_GRAPH = {"nodes": [_node("Chat Input"), _node("Chat Output")], "edges": []}


# ───────────────────────────── fixtures ─────────────────────────────
async def _seed_agent(graph: dict, *, with_idp_agent: bool, multi_doc_split: bool = False):
    agent_id = uuid4()
    async with session_scope() as session:
        session.add(Agent(id=agent_id, name=f"run-api-{agent_id.hex[:8]}", data=graph))
        await session.flush()
        if with_idp_agent:
            session.add(IdpAgent(
                agent_id=agent_id, extraction_mode="named_config",
                is_active=True, multi_doc_split=multi_doc_split,
            ))
        await session.commit()
    return agent_id


async def _cleanup(agent_id):
    async with session_scope() as session:
        for ia in (await session.exec(select(IdpAgent).where(IdpAgent.agent_id == agent_id))).all():
            for doc in (await session.exec(
                select(IdpDocument).where(IdpDocument.agent_id == ia.id)
            )).all():
                for h in (await session.exec(
                    select(IdpExtractedHeader).where(IdpExtractedHeader.document_id == doc.id)
                )).all():
                    await session.delete(h)
                for job in (await session.exec(
                    select(IdpProcessingJob).where(IdpProcessingJob.document_id == doc.id)
                )).all():
                    await session.delete(job)
                await session.delete(doc)
            await session.delete(ia)
        agent = await session.get(Agent, agent_id)
        if agent:
            await session.delete(agent)
        await session.commit()


class _MockStorage:
    def __init__(self):
        self.saved: dict[str, bytes] = {}
        self.deleted: list[str] = []

    async def save_file(self, agent_id: str, file_name: str, data: bytes) -> None:
        self.saved[file_name] = data

    async def delete_file(self, agent_id: str, file_name: str) -> None:
        self.deleted.append(file_name)


def _client(agent_id, graph, monkeypatch, *, api_key=True, storage=None, chat_spy=None):
    """A TestClient whose /api/run resolves `agent_id` with `graph`, bypassing deployment + key lookups."""
    import agentcore.api.endpoints as endpoints

    agent = SimpleNamespace(id=agent_id, name="test-agent", data=graph, org_id=None, dept_id=None)

    # `_resolve_agent_data_for_env` would hit agent_deployment_{uat,prod}. Its own parity is covered by
    # test_idp_published_snapshot.py; here we just hand back the snapshot for the requested env.
    async def _fake_resolve(agent_id, env, version=None):
        return graph, None, None

    async def _fake_enforce(*a, **k):
        return None

    monkeypatch.setattr(endpoints, "_resolve_agent_data_for_env", _fake_resolve)
    monkeypatch.setattr(endpoints, "_enforce_agent_api_key", _fake_enforce)
    if chat_spy is not None:
        monkeypatch.setattr(endpoints, "simple_run_agent", chat_spy)
    monkeypatch.setattr(run_api, "get_storage_service", lambda: (storage or _MockStorage()))

    app = create_app()
    app.dependency_overrides[get_agent_by_id_or_endpoint_name] = lambda: agent
    key = SimpleNamespace(agent_id=agent_id, environment="uat", deployment_id=None, created_by=uuid4())
    app.dependency_overrides[validate_agent_api_key] = (lambda: key) if api_key else (lambda: None)
    return TestClient(app), agent


def _url(agent_id, env=1, version="v1"):
    return f"/api/run/{agent_id}?env={env}&version={version}"


def _doc_payload(**over):
    body = {"filename": "invoice.pdf", "content_base64": PDF_B64}
    body.update(over)
    return {"document": body}


# ═════════════════════════ 1. the chat regression guard ═════════════════════════
@pytest.mark.anyio
async def test_chat_agent_is_untouched_by_the_idp_branch(monkeypatch):
    """THE guard for "existing agent calls must not be affected". A chat graph never enters run_api."""
    called = {}

    async def _spy(*args, **kwargs):
        called["yes"] = True
        return {"outputs": []}

    agent_id = await _seed_agent(CHAT_GRAPH, with_idp_agent=False)
    try:
        client, _ = _client(agent_id, CHAT_GRAPH, monkeypatch, chat_spy=_spy)
        resp = client.post(_url(agent_id), json={"input_value": "Hello!"})
        assert resp.status_code == 200, resp.text
        assert called.get("yes") is True, "the chat agent did not reach simple_run_agent"
    finally:
        await _cleanup(agent_id)


@pytest.mark.anyio
async def test_chat_agent_with_a_document_still_takes_the_chat_path(monkeypatch):
    """The branch is GRAPH-gated, not payload-gated: a stray `document` must not divert a chat agent."""
    called = {}

    async def _spy(*args, **kwargs):
        called["yes"] = True
        return {"outputs": []}

    agent_id = await _seed_agent(CHAT_GRAPH, with_idp_agent=False)
    try:
        client, _ = _client(agent_id, CHAT_GRAPH, monkeypatch, chat_spy=_spy)
        resp = client.post(_url(agent_id), json={"input_value": "Hi", **_doc_payload()})
        assert resp.status_code == 200, resp.text
        assert called.get("yes") is True
    finally:
        await _cleanup(agent_id)


# ═════════════════════════ 2. request guards (nothing touches storage) ═════════════════════════
@pytest.mark.anyio
async def test_idp_rejects_dev_env(monkeypatch):
    agent_id = await _seed_agent(IDP_GRAPH, with_idp_agent=True)
    try:
        client, _ = _client(agent_id, IDP_GRAPH, monkeypatch)
        resp = client.post(_url(agent_id, env=0), json=_doc_payload())
        assert resp.status_code == 400
        assert "published UAT or PROD" in resp.json()["detail"]
    finally:
        await _cleanup(agent_id)


@pytest.mark.anyio
async def test_idp_requires_an_attachment(monkeypatch):
    agent_id = await _seed_agent(IDP_GRAPH, with_idp_agent=True)
    try:
        client, _ = _client(agent_id, IDP_GRAPH, monkeypatch)
        resp = client.post(_url(agent_id), json={"input_value": "no file here"})
        assert resp.status_code == 400
        assert "a document is required" in resp.json()["detail"]
    finally:
        await _cleanup(agent_id)


@pytest.mark.anyio
async def test_idp_rejects_streaming(monkeypatch):
    agent_id = await _seed_agent(IDP_GRAPH, with_idp_agent=True)
    try:
        client, _ = _client(agent_id, IDP_GRAPH, monkeypatch)
        resp = client.post(_url(agent_id) + "&stream=true", json=_doc_payload())
        assert resp.status_code == 400
        assert "stream=true is not supported" in resp.json()["detail"]
    finally:
        await _cleanup(agent_id)


@pytest.mark.anyio
async def test_idp_requires_a_real_api_key(monkeypatch):
    """`_enforce_agent_api_key` fails open (auto-mints a key). IDP must not inherit that."""
    agent_id = await _seed_agent(IDP_GRAPH, with_idp_agent=True)
    try:
        client, _ = _client(agent_id, IDP_GRAPH, monkeypatch, api_key=False)
        resp = client.post(_url(agent_id), json=_doc_payload())
        assert resp.status_code == 401
        assert "API key is required" in resp.json()["detail"]
    finally:
        await _cleanup(agent_id)


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("payload", "code", "needle"),
    [
        ({"filename": "x.exe", "content_base64": PDF_B64}, 400, "is not allowed"),
        ({"filename": "x.pdf", "content_base64": "not-base64!!"}, 400, "not valid base64"),
        ({"filename": "x.pdf", "content_base64": ""}, 400, "is empty"),
        ({"filename": "", "content_base64": PDF_B64}, 400, "filename is required"),
    ],
)
async def test_idp_attachment_validation(monkeypatch, payload, code, needle):
    agent_id = await _seed_agent(IDP_GRAPH, with_idp_agent=True)
    try:
        client, _ = _client(agent_id, IDP_GRAPH, monkeypatch)
        resp = client.post(_url(agent_id), json={"document": payload})
        assert resp.status_code == code, resp.text
        assert needle in resp.json()["detail"]
    finally:
        await _cleanup(agent_id)


@pytest.mark.anyio
async def test_idp_rejects_an_oversized_attachment(monkeypatch):
    """Rejected on the ENCODED length — the payload is never materialized as bytes."""
    from agentcore.services.deps import get_settings_service

    monkeypatch.setattr(get_settings_service().settings, "idp_api_max_attachment_mb", 1, raising=False)
    agent_id = await _seed_agent(IDP_GRAPH, with_idp_agent=True)
    try:
        client, _ = _client(agent_id, IDP_GRAPH, monkeypatch)
        big = base64.b64encode(b"x" * (2 * 1024 * 1024)).decode()
        resp = client.post(_url(agent_id), json=_doc_payload(content_base64=big))
        assert resp.status_code == 413
        assert "maximum allowed size of 1MB" in resp.json()["detail"]
    finally:
        await _cleanup(agent_id)


@pytest.mark.anyio
async def test_idp_agent_without_an_idp_config_row_is_rejected(monkeypatch):
    """`/documents/upload` never auto-creates for a published env; neither may the API."""
    agent_id = await _seed_agent(IDP_GRAPH, with_idp_agent=False)
    try:
        client, _ = _client(agent_id, IDP_GRAPH, monkeypatch)
        resp = client.post(_url(agent_id), json=_doc_payload())
        assert resp.status_code == 400
        assert "no IDP configuration" in resp.json()["detail"]
        async with session_scope() as session:  # and it did NOT create one
            assert (await session.exec(select(IdpAgent).where(IdpAgent.agent_id == agent_id))).first() is None
    finally:
        await _cleanup(agent_id)


@pytest.mark.anyio
async def test_multi_doc_split_agents_are_rejected(monkeypatch):
    """`_run` would finish with status='split', zero rows, and N children still processing."""
    agent_id = await _seed_agent(IDP_GRAPH, with_idp_agent=True, multi_doc_split=True)
    try:
        client, _ = _client(agent_id, IDP_GRAPH, monkeypatch)
        resp = client.post(_url(agent_id), json=_doc_payload())
        assert resp.status_code == 400
        assert "multi-document split" in resp.json()["detail"]
    finally:
        await _cleanup(agent_id)


# ═════════════════════════ 3. the happy path ═════════════════════════
def _stub_pipeline(monkeypatch, *, fail: bool = False):
    """Replace the pipeline with a synchronous stub that writes what `_run` would write."""
    async def _fake_enqueue(session, document_id):
        async with session_scope() as s:
            doc = await s.get(IdpDocument, document_id)
            job = IdpProcessingJob(document_id=document_id, agent_id=doc.agent_id, status="queued")
            s.add(job)
            await s.commit()
            await s.refresh(job)

            if fail:
                doc.status = "failed"
                job.status = "failed"
                job.error_message = "no model configured in agent graph"
            else:
                doc.status = "pending_review"
                doc.predicted_type = "Invoice"
                doc.overall_confidence = 0.69
                job.status = "completed"
                s.add(IdpExtractedHeader(
                    document_id=document_id, job_id=job.id, field_name="invoice_number",
                    extracted_value="INV-3337", confidence_score=0.69,
                ))
            s.add(doc)
            s.add(job)
            await s.commit()
            return job.id

    async def _fake_await(job_id, timeout):
        return True     # finished within the deadline

    monkeypatch.setattr(run_api, "enqueue_document", _fake_enqueue)
    monkeypatch.setattr(run_api, "_await_job", _fake_await)


@pytest.mark.anyio
async def test_idp_happy_path_returns_the_extraction(monkeypatch):
    agent_id = await _seed_agent(IDP_GRAPH, with_idp_agent=True)
    storage = _MockStorage()
    _stub_pipeline(monkeypatch)
    try:
        client, _ = _client(agent_id, IDP_GRAPH, monkeypatch, storage=storage)
        doc_id = str(uuid4())
        resp = client.post(_url(agent_id, env=2, version="v3"), json=_doc_payload(document_id=doc_id))
        assert resp.status_code == 200, resp.text
        body = resp.json()

        assert body["document_id"] == doc_id
        assert body["environment"] == "prod" and body["version"] == "v3"
        assert body["status"] == "pending_review"
        assert body["predicted_type"] == "Invoice"
        assert body["overall_confidence"] == pytest.approx(0.69)
        assert [h["field_name"] for h in body["headers"]] == ["invoice_number"]
        assert body["headers"][0]["extracted_value"] == "INV-3337"
        assert body["error_message"] is None

        # ids echoed as headers so a caller can find the document even if it can't parse the body
        assert resp.headers["X-Idp-Document-Id"] == doc_id
        assert resp.headers["X-Idp-Job-Id"] == body["job_id"]

        # the file reached storage, and the row records its provenance + the version it ran
        assert len(storage.saved) == 1 and not storage.deleted
        async with session_scope() as session:
            doc = await session.get(IdpDocument, doc_id)
            assert doc.source == "api"
            assert doc.run_env == "prod" and doc.run_version == "v3"
            assert doc.source_metadata == {"env": "prod", "version": "v3"}
            assert doc.file_size_bytes == len(PDF_BYTES)
    finally:
        await _cleanup(agent_id)


@pytest.mark.anyio
async def test_pipeline_failure_is_a_200_with_status_failed(monkeypatch):
    """A non-2xx would make most clients discard the body — and the body holds the diagnostic."""
    agent_id = await _seed_agent(IDP_GRAPH, with_idp_agent=True)
    _stub_pipeline(monkeypatch, fail=True)
    try:
        client, _ = _client(agent_id, IDP_GRAPH, monkeypatch)
        resp = client.post(_url(agent_id), json=_doc_payload())
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["status"] == "failed"
        assert "no model configured" in body["error_message"]
        assert body["headers"] == []
    finally:
        await _cleanup(agent_id)


@pytest.mark.anyio
async def test_response_does_not_leak_orm_columns(monkeypatch):
    """End-to-end: the wire format carries only the Read model's fields."""
    agent_id = await _seed_agent(IDP_GRAPH, with_idp_agent=True)
    _stub_pipeline(monkeypatch)
    try:
        client, _ = _client(agent_id, IDP_GRAPH, monkeypatch)
        resp = client.post(_url(agent_id), json=_doc_payload())
        header = resp.json()["headers"][0]
        for leaked in ("job_id", "document_id", "extra", "created_at", "updated_at"):
            assert leaked not in header, f"{leaked} leaked out of the ORM row into the response"
    finally:
        await _cleanup(agent_id)


@pytest.mark.anyio
async def test_build_processed_doc_detail_validates_its_child_rows(monkeypatch):
    """The serializer itself must return Read models, not raw SQLModel rows.

    Pydantic v2 does not validate on assignment, so `detail.headers = <ORM rows>` silently leaves
    SQLModel objects in a `list[ExtractedHeaderRead]` field. Both current callers happen to re-validate
    on the way out (`response_model=` / `IdpRunResponse`), which is exactly what makes this invisible —
    and what would make the next caller leak `job_id`, `document_id`, `extra` and timestamps.
    """
    from agentcore.api.idp.processed_docs import (
        DetectedElementRead,
        ExtractedHeaderRead,
        ExtractedLineItemRead,
        build_processed_doc_detail,
    )

    agent_id = await _seed_agent(IDP_GRAPH, with_idp_agent=True)
    _stub_pipeline(monkeypatch)
    try:
        client, _ = _client(agent_id, IDP_GRAPH, monkeypatch)
        doc_id = str(uuid4())
        client.post(_url(agent_id), json=_doc_payload(document_id=doc_id))

        async with session_scope() as session:
            doc = await session.get(IdpDocument, doc_id)
            detail = await build_processed_doc_detail(session, doc)

        assert detail.headers, "fixture produced no headers"
        assert all(type(h) is ExtractedHeaderRead for h in detail.headers), (
            "raw ORM rows survived into `headers` — they would be serialized in full by any caller "
            "that does not declare a response_model"
        )
        assert all(type(li) is ExtractedLineItemRead for li in detail.line_items)
        assert all(type(d) is DetectedElementRead for d in detail.detected_elements)
    finally:
        await _cleanup(agent_id)


@pytest.mark.anyio
async def test_resending_the_same_document_id_does_not_reprocess(monkeypatch):
    """Idempotency: a caller retrying after a dropped connection must not process the file twice."""
    agent_id = await _seed_agent(IDP_GRAPH, with_idp_agent=True)
    storage = _MockStorage()
    _stub_pipeline(monkeypatch)
    try:
        client, _ = _client(agent_id, IDP_GRAPH, monkeypatch, storage=storage)
        doc_id = str(uuid4())
        first = client.post(_url(agent_id), json=_doc_payload(document_id=doc_id))
        second = client.post(_url(agent_id), json=_doc_payload(document_id=doc_id))
        assert first.status_code == second.status_code == 200
        assert second.json()["document_id"] == doc_id
        assert len(storage.saved) == 1, "the retry re-uploaded the file"
        async with session_scope() as session:
            jobs = (await session.exec(
                select(IdpProcessingJob).where(IdpProcessingJob.document_id == doc_id)
            )).all()
            assert len(jobs) == 1, "the retry started a second pipeline run"
    finally:
        await _cleanup(agent_id)


# ═════════════════════════ 3b. the multipart path (curl / Postman) ═════════════════════════
def _mp_url(agent_id, env=1, version="v1"):
    return f"/api/run/{agent_id}/document?env={env}&version={version}"


@pytest.mark.anyio
async def test_multipart_upload_produces_the_same_result_as_base64(monkeypatch):
    """The two entry points must not drift — they share `_process_document`."""
    agent_id = await _seed_agent(IDP_GRAPH, with_idp_agent=True)
    storage = _MockStorage()
    _stub_pipeline(monkeypatch)
    try:
        client, _ = _client(agent_id, IDP_GRAPH, monkeypatch, storage=storage)
        doc_id = str(uuid4())
        resp = client.post(
            _mp_url(agent_id, env=2, version="v3"),
            files={"file": ("invoice.pdf", PDF_BYTES, "application/pdf")},
            data={"document_id": doc_id},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["document_id"] == doc_id
        assert body["environment"] == "prod" and body["version"] == "v3"
        assert body["status"] == "pending_review"
        assert [h["field_name"] for h in body["headers"]] == ["invoice_number"]
        assert resp.headers["X-Idp-Document-Id"] == doc_id

        assert list(storage.saved.values()) == [PDF_BYTES], "the raw bytes were not stored verbatim"
        async with session_scope() as session:
            doc = await session.get(IdpDocument, doc_id)
            assert doc.source == "api"                       # same provenance as the JSON path
            assert doc.run_env == "prod" and doc.run_version == "v3"
            assert doc.original_filename == "invoice.pdf"
            assert doc.mime_type == "application/pdf"
            assert doc.file_size_bytes == len(PDF_BYTES)
    finally:
        await _cleanup(agent_id)


@pytest.mark.anyio
async def test_multipart_works_without_a_document_id(monkeypatch):
    """`document_id` is optional — the server mints one and echoes it in the header."""
    agent_id = await _seed_agent(IDP_GRAPH, with_idp_agent=True)
    _stub_pipeline(monkeypatch)
    try:
        client, _ = _client(agent_id, IDP_GRAPH, monkeypatch)
        resp = client.post(_mp_url(agent_id), files={"file": ("invoice.pdf", PDF_BYTES, "application/pdf")})
        assert resp.status_code == 200, resp.text
        assert resp.headers["X-Idp-Document-Id"] == resp.json()["document_id"]
    finally:
        await _cleanup(agent_id)


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("env", "filename", "code", "needle"),
    [
        (0, "invoice.pdf", 400, "published UAT or PROD"),
        (1, "invoice.exe", 400, "is not allowed"),
    ],
)
async def test_multipart_enforces_the_same_guards(monkeypatch, env, filename, code, needle):
    agent_id = await _seed_agent(IDP_GRAPH, with_idp_agent=True)
    try:
        client, _ = _client(agent_id, IDP_GRAPH, monkeypatch)
        resp = client.post(_mp_url(agent_id, env=env), files={"file": (filename, PDF_BYTES, "application/pdf")})
        assert resp.status_code == code, resp.text
        assert needle in resp.json()["detail"]
    finally:
        await _cleanup(agent_id)


@pytest.mark.anyio
async def test_multipart_requires_a_real_api_key(monkeypatch):
    agent_id = await _seed_agent(IDP_GRAPH, with_idp_agent=True)
    try:
        client, _ = _client(agent_id, IDP_GRAPH, monkeypatch, api_key=False)
        resp = client.post(_mp_url(agent_id), files={"file": ("invoice.pdf", PDF_BYTES, "application/pdf")})
        assert resp.status_code == 401
    finally:
        await _cleanup(agent_id)


@pytest.mark.anyio
async def test_multipart_rejects_a_chat_agent(monkeypatch):
    """The multipart route is IDP-only; a chat agent must be told where to go."""
    agent_id = await _seed_agent(CHAT_GRAPH, with_idp_agent=False)
    try:
        client, _ = _client(agent_id, CHAT_GRAPH, monkeypatch)
        resp = client.post(_mp_url(agent_id), files={"file": ("invoice.pdf", PDF_BYTES, "application/pdf")})
        assert resp.status_code == 400
        assert "not an IDP agent" in resp.json()["detail"]
    finally:
        await _cleanup(agent_id)


def test_filename_is_reduced_to_a_basename_on_any_platform():
    """`os.path.basename` only splits on the SERVER's separator — a Windows client sends backslashes.

    The behavioural asserts below pass on Windows even with the bug (`ntpath.basename` strips `\\`), so
    the meaningful guard is the last one: the implementation must not depend on the server's platform.
    Running this suite on Linux with `os.path.basename` would store `D:\\docs\\sub\\Invoice.pdf` verbatim.
    """
    import inspect

    assert run_api._clean_filename(r"D:\docs\sub\Invoice.pdf", field="f") == "Invoice.pdf"
    assert run_api._clean_filename("/var/tmp/Invoice.pdf", field="f") == "Invoice.pdf"
    assert run_api._clean_filename("  Invoice.pdf \n", field="f") == "Invoice.pdf"
    assert run_api._clean_filename(r"..\..\etc\passwd.pdf", field="f") == "passwd.pdf"
    with pytest.raises(Exception):
        run_api._clean_filename("   ", field="f")

    # Match the CALL, not the docstring — which names it deliberately, to explain the trap.
    src = inspect.getsource(run_api._clean_filename)
    assert "os.path.basename(" not in src, (
        "os.path.basename is platform-dependent: on a Linux server it leaves a Windows client's "
        "backslash path untouched, and this test would still pass on a Windows dev machine"
    )


@pytest.mark.anyio
async def test_multipart_stores_the_basename_not_the_client_path(monkeypatch):
    agent_id = await _seed_agent(IDP_GRAPH, with_idp_agent=True)
    _stub_pipeline(monkeypatch)
    try:
        client, _ = _client(agent_id, IDP_GRAPH, monkeypatch)
        doc_id = str(uuid4())
        resp = client.post(
            _mp_url(agent_id),
            files={"file": (r"D:\idp_agentic\scripts\Invoice.pdf", PDF_BYTES, "application/pdf")},
            data={"document_id": doc_id},
        )
        assert resp.status_code == 200, resp.text
        async with session_scope() as session:
            doc = await session.get(IdpDocument, doc_id)
            assert doc.original_filename == "Invoice.pdf"
            assert "\\" not in doc.file_path
    finally:
        await _cleanup(agent_id)


# ═════════════════════════ 3c. document_id is genuinely optional ═════════════════════════
def test_parse_document_id_treats_blank_as_absent():
    """Postman sends "" for a form row you leave blank. Typed as UUID, FastAPI 422s before we see it."""
    assert run_api.parse_document_id(None) is None
    assert run_api.parse_document_id("") is None
    assert run_api.parse_document_id("   ") is None
    got = uuid4()
    assert run_api.parse_document_id(str(got)) == got
    assert run_api.parse_document_id(got) is got
    with pytest.raises(Exception):     # genuine garbage still 400s, loudly
        run_api.parse_document_id("not-a-uuid")


@pytest.mark.anyio
@pytest.mark.parametrize("doc_id_field", [{}, {"document_id": ""}, {"document_id": "   "}])
async def test_multipart_accepts_a_blank_or_missing_document_id(monkeypatch, doc_id_field):
    """No document_id, or a blank one -> the server mints it. No 422, no ceremony."""
    agent_id = await _seed_agent(IDP_GRAPH, with_idp_agent=True)
    _stub_pipeline(monkeypatch)
    try:
        client, _ = _client(agent_id, IDP_GRAPH, monkeypatch)
        resp = client.post(
            _mp_url(agent_id),
            files={"file": ("invoice.pdf", PDF_BYTES, "application/pdf")},
            data=doc_id_field,
        )
        assert resp.status_code == 200, resp.text
        minted = resp.json()["document_id"]
        assert UUID(minted)                                   # a real UUID came back
        assert resp.headers["X-Idp-Document-Id"] == minted
    finally:
        await _cleanup(agent_id)


@pytest.mark.anyio
async def test_multipart_rejects_a_malformed_document_id(monkeypatch):
    agent_id = await _seed_agent(IDP_GRAPH, with_idp_agent=True)
    try:
        client, _ = _client(agent_id, IDP_GRAPH, monkeypatch)
        resp = client.post(
            _mp_url(agent_id),
            files={"file": ("invoice.pdf", PDF_BYTES, "application/pdf")},
            data={"document_id": "not-a-uuid"},
        )
        assert resp.status_code == 400
        assert "document_id must be a UUID" in resp.json()["detail"]
    finally:
        await _cleanup(agent_id)


# ═════════════════════════ 3d. every file type the canvas supports ═════════════════════════
@pytest.mark.anyio
@pytest.mark.parametrize(
    ("filename", "content_type"),
    [
        ("invoice.pdf", "application/pdf"),
        ("scan.png", "image/png"),
        ("photo.jpg", "image/jpeg"),
        ("fax.tiff", "image/tiff"),
        ("book.xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
        ("contract.docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
        ("notes.txt", "text/plain"),
    ],
)
async def test_api_accepts_every_extension_the_upload_page_accepts(monkeypatch, filename, content_type):
    """The API must not be PDF-only — it shares ALLOWED_EXTENSIONS with api/idp/documents.upload."""
    from agentcore.api.idp.documents import ALLOWED_EXTENSIONS

    agent_id = await _seed_agent(IDP_GRAPH, with_idp_agent=True)
    _stub_pipeline(monkeypatch)
    try:
        client, _ = _client(agent_id, IDP_GRAPH, monkeypatch)
        resp = client.post(_mp_url(agent_id), files={"file": (filename, PDF_BYTES, content_type)})
        assert resp.status_code == 200, resp.text
        async with session_scope() as session:
            doc = await session.get(IdpDocument, resp.json()["document_id"])
            assert f".{doc.file_type}" in ALLOWED_EXTENSIONS
            assert doc.mime_type == content_type
    finally:
        await _cleanup(agent_id)


def test_the_api_and_the_upload_page_accept_exactly_the_same_extensions():
    """One source of truth — the API can never drift into being stricter than the canvas."""
    import inspect

    from agentcore.api.idp.documents import ALLOWED_EXTENSIONS

    assert ALLOWED_EXTENSIONS >= {".pdf", ".png", ".jpg", ".jpeg", ".tiff", ".tif", ".xlsx", ".xls", ".docx", ".txt"}
    src = inspect.getsource(run_api._validate_extension)
    assert "from agentcore.api.idp.documents import ALLOWED_EXTENSIONS" in src
    assert "ALLOWED_EXTENSIONS" in src and '".pdf"' not in src, "run_api must not hard-code its own list"


# ═════════════════════════ 3e. the 300s timeout -> 202 + poll ═════════════════════════
@pytest.mark.anyio
async def test_a_slow_pipeline_returns_202_with_a_poll_url_and_is_not_cancelled(monkeypatch):
    """On timeout the request stops waiting; the PIPELINE keeps running and the result is recoverable."""
    agent_id = await _seed_agent(IDP_GRAPH, with_idp_agent=True)
    _stub_pipeline(monkeypatch)

    # Nothing finishes within the deadline.
    seen = {}

    async def _never_finishes(job_id, timeout):
        seen["timeout"] = timeout          # the setting must actually reach the wait
        return False

    monkeypatch.setattr(run_api, "_await_job", _never_finishes)
    from agentcore.services.deps import get_settings_service

    # `Settings` validates on assignment, so this must be an int (as the real setting is).
    monkeypatch.setattr(get_settings_service().settings, "idp_api_run_timeout_seconds", 1)
    try:
        client, _ = _client(agent_id, IDP_GRAPH, monkeypatch)
        doc_id = str(uuid4())
        resp = client.post(_mp_url(agent_id), files={"file": ("invoice.pdf", PDF_BYTES, "application/pdf")},
                           data={"document_id": doc_id})

        assert resp.status_code == 202, resp.text
        body = resp.json()
        assert body["status"] == "processing"
        assert body["document_id"] == doc_id
        assert body["poll_url"] == f"/api/run/{agent_id}/documents/{doc_id}"
        assert body["headers"] == [] and body["error_message"] is None
        assert resp.headers["X-Idp-Document-Id"] == doc_id
        assert seen["timeout"] == 1, "idp_api_run_timeout_seconds never reached the wait"

        # The document + job survive: the run was never cancelled, so the result is collectable.
        got = client.get(f"/api/run/{agent_id}/documents/{doc_id}")
        assert got.status_code == 200
        assert got.json()["status"] == "pending_review"      # the stub already wrote the result
    finally:
        await _cleanup(agent_id)


def test_the_wait_never_cancels_the_pipeline_on_timeout():
    """`asyncio.wait_for` would CANCEL the task on timeout. `asyncio.wait(..., timeout=)` does not."""
    import inspect

    src = inspect.getsource(run_api._await_job)
    assert "asyncio.wait([task], timeout=timeout)" in src
    # Match the CALL, not the comment that names the trap.
    assert "wait_for(" not in src, "asyncio.wait_for cancels the awaited task — it would kill the pipeline"
    statements = [line.split("#", 1)[0].strip() for line in src.splitlines()]
    assert not any(s == "await task" for s in statements)


def test_both_entry_points_share_one_code_path():
    """A guard added to one path must apply to the other. They must not be parallel implementations."""
    import inspect

    for fn in (run_api.run_idp_over_api, run_api.run_idp_over_api_multipart):
        src = inspect.getsource(fn)
        assert "_common_guards(" in src, f"{fn.__name__} does not run the shared guards"
        assert "_process_document(" in src, f"{fn.__name__} does not use the shared core"
        # Neither may persist or enqueue on its own.
        assert "IdpDocument(" not in src and "enqueue_document(" not in src


# ═════════════════════════ 4. result recovery ═════════════════════════
@pytest.mark.anyio
async def test_result_can_be_fetched_later_and_is_agent_scoped(monkeypatch):
    """The run waits with no timeout; a proxy 504 must not lose the extraction."""
    agent_id = await _seed_agent(IDP_GRAPH, with_idp_agent=True)
    other_id = await _seed_agent(IDP_GRAPH, with_idp_agent=True)
    _stub_pipeline(monkeypatch)
    try:
        client, _ = _client(agent_id, IDP_GRAPH, monkeypatch)
        doc_id = str(uuid4())
        client.post(_url(agent_id), json=_doc_payload(document_id=doc_id))

        got = client.get(f"/api/run/{agent_id}/documents/{doc_id}")
        assert got.status_code == 200, got.text
        assert got.json()["headers"][0]["extracted_value"] == "INV-3337"

        # A key for another agent must not be able to read this document.
        other_client, _ = _client(other_id, IDP_GRAPH, monkeypatch)
        assert other_client.get(f"/api/run/{other_id}/documents/{doc_id}").status_code == 404

        # No key at all -> 401.
        no_key, _ = _client(agent_id, IDP_GRAPH, monkeypatch, api_key=False)
        assert no_key.get(f"/api/run/{agent_id}/documents/{doc_id}").status_code == 401
    finally:
        await _cleanup(agent_id)
        await _cleanup(other_id)


# ═════════════════════════ 5. structural guards ═════════════════════════
def test_request_session_is_committed_before_the_wait():
    """`enqueue_document` ends with `session.refresh(job)`, which autobegins a transaction it never
    commits. Holding it across a multi-minute pipeline starves the pool that chat requests share."""
    import inspect

    src = inspect.getsource(run_api._process_document)
    enqueue = src.index("await enqueue_document(")
    commit = src.index("await session.commit()", enqueue)
    wait = src.index("await _await_job(")
    assert enqueue < commit < wait, "the request session is not committed between enqueue and the wait"


def test_results_are_read_from_a_fresh_session():
    """expire_on_commit=False: the pre-run objects never see the pipeline's writes."""
    import inspect

    src = inspect.getsource(run_api._build_response)
    assert "session_scope()" in src
