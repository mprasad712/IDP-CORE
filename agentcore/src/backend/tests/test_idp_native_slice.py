"""Phase 2b (native migration): the Document Upload → Processed Docs Output vertical slice.

Proves the two new IDP components (input loader + sink) resolve + build on the generic
``graph_langgraph`` engine, then runs one real document end-to-end through the engine and checks it
was persisted/finalized.
"""

from uuid import uuid4

import pytest

import test_idp_pipeline as T
from agentcore.services.idp import pipeline

# Pre-import the IDP API routers at collection time (before any test patches deps.get_storage_service).
# A streamed native run lazily imports these; if that import happens while the accessor is monkeypatched,
# the router's `Depends(get_storage_service)` default permanently binds the mock and later endpoint tests'
# dependency_overrides silently stop matching. Binding the REAL accessor up front prevents that leak.
import agentcore.api.idp.documents  # noqa: E402,F401


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture(scope="session", autouse=True)
def _component_cache():
    """IDP nodes run through the STANDARD component loader, which refreshes each node's source from the
    component cache (populated at server startup). Populate it once so these tests exercise the real
    agent build path — exactly how a simple agent's components resolve — with no IDP-specific hydration.
    """
    import asyncio

    from agentcore.interface.components import component_cache, get_and_cache_all_types_dict
    from agentcore.services.deps import get_settings_service

    if not component_cache.all_types_dict:
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(get_and_cache_all_types_dict(get_settings_service()))
        finally:
            loop.close()
    yield


def _node(node_id, display_name, template=None, outputs=None):
    return {
        "id": node_id,
        "data": {
            "type": node_id.split("-")[0],
            "id": node_id,
            "node": {"display_name": display_name, "template": template or {}, "outputs": outputs or []},
        },
    }


def _slice_graph():
    upload = _node(
        "DocumentUpload-a", "Document Upload",
        outputs=[{"name": "document", "display_name": "Document", "types": ["Message"], "selected": "Message", "method": "load"}],
    )
    out = _node(
        "ProcessedDocsOutput-z", "Processed Docs Output",
        template={"data": {"type": "other", "name": "data", "input_types": ["Data", "Message"], "list": True}},
        outputs=[{"name": "result", "display_name": "Result", "types": ["Data"], "selected": "Data", "method": "finalize"}],
    )
    edge = {
        "source": "DocumentUpload-a", "target": "ProcessedDocsOutput-z",
        "data": {"sourceHandle": {"name": "document", "id": "DocumentUpload-a", "output_types": ["Message"]},
                 "targetHandle": {"fieldName": "data", "id": "ProcessedDocsOutput-z", "inputTypes": ["Data", "Message"]}},
    }
    return {"nodes": [upload, out], "edges": [edge], "viewport": {}}


def test_raw_codeless_idp_node_resolves_via_standard_loader():
    """A RAW canvas IDP node — no embedded code, and even a STALE output method (== the output name,
    as older saved flows carry) — builds on the standard agent loader and its method is healed. This
    is the whole point of the migration: IDP nodes are first-class built-ins, no special hydration."""
    from agentcore.graph_langgraph import LangGraphAdapter
    from agentcore.interface.initialize import loading

    # Document Upload saved with NO code and a stale method="document" (the real-world failure mode).
    stale = _node(
        "DocumentUpload-670LY", "Document Upload",
        outputs=[{"name": "document", "display_name": "Document", "types": ["Message"],
                  "selected": "Message", "method": "document"}],
    )
    graph = LangGraphAdapter.from_payload({"nodes": [stale], "edges": []}, str(uuid4()), "IDP", None)
    assert graph.compiled_app is not None, "a code-less IDP node must compile on the standard loader"

    vertex = graph.get_vertex("DocumentUpload-670LY")
    comp, _ = loading.instantiate_class(vertex=vertex, user_id=None, event_manager=None)
    methods = {o.name: o.method for o in comp._outputs_map.values()}
    assert methods.get("document") == "load", f"stale output method must be healed from the class: {methods}"


@pytest.mark.anyio
async def test_slice_compiles_on_generic_engine():
    """_prepare (batch wiring only) → LangGraphAdapter.from_payload compiles the IDP slice."""
    from agentcore.graph_langgraph import LangGraphAdapter

    from agentcore.services.idp.graph_native.runner import _prepare

    prepared = _prepare(_slice_graph(), str(uuid4()))
    graph = LangGraphAdapter.from_payload(prepared, str(uuid4()), "IDP", None)
    assert graph.compiled_app is not None, "the IDP slice must compile on the generic engine"


@pytest.mark.anyio
async def test_slice_runs_and_persists(monkeypatch):
    """Run a real document through the generic engine via the two IDP components; it persists."""
    from sqlmodel import select

    from agentcore.services.database.models.idp.documents import IdpDocument
    from agentcore.services.deps import session_scope
    from agentcore.services.idp.graph_native.runner import run_native_graph

    # in-memory storage shared by the setup + the Document Upload component (which reads deps.get_storage_service)
    storage = T._MockStorage()
    monkeypatch.setattr(pipeline, "get_storage_service", lambda: storage)
    monkeypatch.setattr("agentcore.services.deps.get_storage_service", lambda: storage, raising=False)
    T._patch_extraction(monkeypatch)  # no real model

    async with session_scope() as s:
        agent_id, doc_id = await T._setup_document(s, graph=_slice_graph(), file_bytes=T._digital_pdf())

    await run_native_graph(document_id=str(doc_id), agent_data=_slice_graph(), agent_id=str(agent_id))

    async with session_scope() as s:
        doc = await s.get(IdpDocument, doc_id)
        assert doc.status == "pending_review", f"document should be finalized by the sink: {doc.status}"


def _slice3_graph():
    """Document Upload → AI Field Extractor → Processed Docs Output (the full extractor slice)."""
    g = _slice_graph()
    extractor = _node(
        "DocumentExtractor-e", "AI Field Extractor",
        template={"extraction_mode": {"type": "str", "name": "extraction_mode", "value": "field_configuration"},
                  "document": {"type": "other", "name": "document", "input_types": ["Message"]}},
        outputs=[{"name": "extracted_data", "display_name": "Extracted Data", "types": ["Data"], "selected": "Data", "method": "extract"}],
    )
    g["nodes"].append(extractor)
    # rewire: Upload → Extractor.document → Output.data
    g["edges"] = [
        {"source": "DocumentUpload-a", "target": "DocumentExtractor-e",
         "data": {"sourceHandle": {"name": "document", "id": "DocumentUpload-a", "output_types": ["Message"]},
                  "targetHandle": {"fieldName": "document", "id": "DocumentExtractor-e", "inputTypes": ["Message"]}}},
        {"source": "DocumentExtractor-e", "target": "ProcessedDocsOutput-z",
         "data": {"sourceHandle": {"name": "extracted_data", "id": "DocumentExtractor-e", "output_types": ["Data"]},
                  "targetHandle": {"fieldName": "data", "id": "ProcessedDocsOutput-z", "inputTypes": ["Data", "Message"]}}},
    ]
    return g


@pytest.mark.anyio
async def test_slice3_extractor_runs_and_carries_payload(monkeypatch):
    """The 3-node slice (with the AI Field Extractor as a middle vertex) runs on the generic engine;
    the extractor carries the document payload through to the sink, which persists + finalizes."""
    from agentcore.services.database.models.idp.documents import IdpDocument
    from agentcore.services.deps import session_scope
    from agentcore.services.idp.graph_native.runner import run_native_graph

    storage = T._MockStorage()
    monkeypatch.setattr(pipeline, "get_storage_service", lambda: storage)
    monkeypatch.setattr("agentcore.services.deps.get_storage_service", lambda: storage, raising=False)
    T._patch_extraction(monkeypatch)

    async with session_scope() as s:
        agent_id, doc_id = await T._setup_document(s, graph=_slice3_graph(), file_bytes=T._digital_pdf())
    await run_native_graph(document_id=str(doc_id), agent_data=_slice3_graph(), agent_id=str(agent_id))

    async with session_scope() as s:
        doc = await s.get(IdpDocument, doc_id)
        assert doc.status == "pending_review", doc.status


@pytest.mark.anyio
async def test_slice_streams_per_vertex_events(monkeypatch):
    """Running with an event manager emits per-vertex events — the native source for canvas
    highlighting (the frontend consumes these via /build/{job_id}/events)."""
    from agentcore.services.deps import session_scope
    from agentcore.services.idp.graph_native.runner import run_native_graph

    storage = T._MockStorage()
    monkeypatch.setattr(pipeline, "get_storage_service", lambda: storage)
    monkeypatch.setattr("agentcore.services.deps.get_storage_service", lambda: storage, raising=False)
    T._patch_extraction(monkeypatch)

    class _RecordingEM:
        def __init__(self):
            self.end_vertex_calls = 0

        def on_end_vertex(self, *args, **kwargs):
            self.end_vertex_calls += 1

        def __getattr__(self, _name):
            return lambda *a, **k: None

    em = _RecordingEM()
    async with session_scope() as s:
        agent_id, doc_id = await T._setup_document(s, graph=_slice3_graph(), file_bytes=T._digital_pdf())
    await run_native_graph(document_id=str(doc_id), agent_data=_slice3_graph(), agent_id=str(agent_id), event_manager=em)

    # Each executed vertex emits an end_vertex event → the frontend lights that node up.
    assert em.end_vertex_calls >= 2, f"expected per-vertex events, got {em.end_vertex_calls}"


@pytest.mark.anyio
async def test_process_document_native_end_to_end(monkeypatch):
    """Phase 4: the /process dispatch target ``run_native_for_document`` runs a real document through
    the generic engine, streams under the job id, persists via the sink, and finalizes the JOB row —
    the live end-to-end path (``IDP_EXECUTION_ENGINE=native``)."""
    from agentcore.services.database.models.idp.config import IdpAgent
    from agentcore.services.database.models.idp.documents import IdpDocument, IdpProcessingJob
    from agentcore.services.database.models.agent.model import Agent
    from agentcore.services.deps import session_scope
    from agentcore.services.idp.graph_native.process import run_native_for_document

    storage = T._MockStorage()
    monkeypatch.setattr(pipeline, "get_storage_service", lambda: storage)
    monkeypatch.setattr("agentcore.services.deps.get_storage_service", lambda: storage, raising=False)
    T._patch_extraction(monkeypatch)

    async with session_scope() as s:
        agent_id, doc_id = await T._setup_document(s, graph=_slice3_graph(), file_bytes=T._digital_pdf())

    async with session_scope() as s:
        base_agent = await s.get(Agent, agent_id)
        doc = await s.get(IdpDocument, doc_id)
        job = IdpProcessingJob(document_id=doc_id, agent_id=agent_id, status="running")
        s.add(job)
        await s.commit()
        await s.refresh(job)
        flow = pipeline.FlowLog(doc_id, doc.original_filename)
        await run_native_for_document(s, doc, job, base_agent, flow)
        job_id = job.id

    # Don't leak the event queue into the global queue-service singleton (pollutes later tests).
    from agentcore.services.deps import get_queue_service
    try:
        await get_queue_service().cleanup_job(str(job_id))
    except Exception:
        pass

    async with session_scope() as s:
        doc = await s.get(IdpDocument, doc_id)
        job = await s.get(IdpProcessingJob, job_id)
        assert doc.status == "pending_review", f"sink should finalize the doc: {doc.status}"
        assert job.status == "completed", f"native path should finalize the job: {job.status}"
        # engine clearly recorded in the flow log
        assert any("native" in (e.get("detail") or "").lower() for e in (job.log or [])), job.log
        # per-node log persisted (one "node:<name>" step per executed node)
        node_steps = [e for e in (job.log or []) if str(e.get("step", "")).startswith("node:")]
        assert len(node_steps) >= 2, f"expected per-node log steps: {job.log}"
