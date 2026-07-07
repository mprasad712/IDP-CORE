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


def _router_merge_graph():
    """Document Upload → Document Type Detector → (digital → Output Parser.branch_a) /
    (scanned → Scan Corrector → Output Parser.branch_b) → Output Parser → Processed Docs Output.
    The router's two branches REJOIN at the Output Parser — the merge-after-router pattern."""
    upload = _node(
        "DocumentUpload-a", "Document Upload",
        outputs=[{"name": "document", "display_name": "Document", "types": ["Message"], "selected": "Message", "method": "load"}],
    )
    detector = _node(
        "DocumentTypeDetector-d", "Document Type Detector",
        template={"document": {"type": "other", "name": "document", "input_types": ["Message"]},
                  "min_text_length": {"type": "int", "name": "min_text_length", "value": 10}},
        outputs=[
            {"name": "digital_path", "display_name": "Digital", "types": ["Message"], "selected": "Message", "method": "digital_path", "group_outputs": True},
            {"name": "scanned_path", "display_name": "Scanned", "types": ["Message"], "selected": "Message", "method": "scanned_path", "group_outputs": True},
        ],
    )
    corrector = _node(
        "ScanCorrector-s", "Scan Corrector",
        template={"document": {"type": "other", "name": "document", "input_types": ["Message"]},
                  "fix_skew": {"type": "bool", "name": "fix_skew", "value": False},
                  "fix_rotation": {"type": "bool", "name": "fix_rotation", "value": False}},
        outputs=[{"name": "corrected_document", "display_name": "Corrected", "types": ["Message"], "selected": "Message", "method": "corrected_document"}],
    )
    parser = _node(
        "OutputParser-p", "Output Parser",
        template={"branch_a": {"type": "other", "name": "branch_a", "input_types": ["Message", "Data"]},
                  "branch_b": {"type": "other", "name": "branch_b", "input_types": ["Message", "Data"]}},
        outputs=[{"name": "merged", "display_name": "Merged", "types": ["Message"], "selected": "Message", "method": "merge"}],
    )
    out = _node(
        "ProcessedDocsOutput-z", "Processed Docs Output",
        template={"data": {"type": "other", "name": "data", "input_types": ["Data", "Message"], "list": True}},
        outputs=[{"name": "result", "display_name": "Result", "types": ["Data"], "selected": "Data", "method": "finalize"}],
    )

    def _edge(src, sh, tgt, tf):
        return {"source": src, "target": tgt,
                "data": {"sourceHandle": {"name": sh, "id": src, "output_types": ["Message"]},
                         "targetHandle": {"fieldName": tf, "id": tgt, "inputTypes": ["Message", "Data"]}}}

    edges = [
        _edge("DocumentUpload-a", "document", "DocumentTypeDetector-d", "document"),
        _edge("DocumentTypeDetector-d", "digital_path", "OutputParser-p", "branch_a"),
        _edge("DocumentTypeDetector-d", "scanned_path", "ScanCorrector-s", "document"),
        _edge("ScanCorrector-s", "corrected_document", "OutputParser-p", "branch_b"),
        _edge("OutputParser-p", "merged", "ProcessedDocsOutput-z", "data"),
    ]
    return {"nodes": [upload, detector, corrector, parser, out], "edges": edges, "viewport": {}}


def _full_flow_graph():
    """Approximates the user's canvas: Upload → Detector → (digital / scanned→Scan Corrector) →
    Output Parser → Document Classifier → AI Field Extractor → Confidence Router → (high/low) →
    Processed Docs Output. Two merge-after-router joins (Output Parser + Confidence Router→sink)."""
    def n(nid, dn, tmpl=None, outs=None):
        return _node(nid, dn, template=tmpl, outputs=outs)

    def o(name, method, grouped=False):
        d = {"name": name, "display_name": name, "types": ["Message"], "selected": "Message", "method": method}
        if grouped:
            d["group_outputs"] = True
        return d

    def e(src, sh, tgt, tf):
        return {"source": src, "target": tgt,
                "data": {"sourceHandle": {"name": sh, "id": src, "output_types": ["Message"]},
                         "targetHandle": {"fieldName": tf, "id": tgt, "inputTypes": ["Message", "Data"]}}}

    hi = {"type": "other", "name": "document", "input_types": ["Message"]}
    nodes = [
        n("DocumentUpload-a", "Document Upload", outs=[o("document", "load")]),
        n("DocumentTypeDetector-d", "Document Type Detector",
          {"document": hi, "min_text_length": {"type": "int", "name": "min_text_length", "value": 10}},
          [o("digital_path", "digital_path", True), o("scanned_path", "scanned_path", True)]),
        n("ScanCorrector-s", "Scan Corrector",
          {"document": hi, "fix_skew": {"type": "bool", "name": "fix_skew", "value": False},
           "fix_rotation": {"type": "bool", "name": "fix_rotation", "value": False}},
          [o("corrected_document", "corrected_document")]),
        n("OutputParser-p", "Output Parser",
          {"branch_a": {"type": "other", "name": "branch_a", "input_types": ["Message", "Data"]},
           "branch_b": {"type": "other", "name": "branch_b", "input_types": ["Message", "Data"]}},
          [o("merged", "merge")]),
        n("DocumentClassifier-c", "Document Classifier",
          {"document": hi, "document_types": {"type": "other", "name": "document_types", "value": []}},
          [o("classified_document", "classify")]),
        n("DocumentExtractor-e", "AI Field Extractor",
          {"extraction_mode": {"type": "str", "name": "extraction_mode", "value": "field_configuration"}, "document": hi},
          [o("extracted_data", "extract")]),
        n("ConfidenceRouter-r", "Confidence Router",
          {"data": {"type": "other", "name": "data", "input_types": ["Message", "Data"]},
           "threshold": {"type": "float", "name": "threshold", "value": 0.8}},
          [o("high_confidence", "high_confidence", True), o("low_confidence", "low_confidence", True)]),
        n("ProcessedDocsOutput-z", "Processed Docs Output",
          {"data": {"type": "other", "name": "data", "input_types": ["Data", "Message"], "list": True}},
          [o("result", "finalize")]),
    ]
    edges = [
        e("DocumentUpload-a", "document", "DocumentTypeDetector-d", "document"),
        e("DocumentTypeDetector-d", "digital_path", "OutputParser-p", "branch_a"),
        e("DocumentTypeDetector-d", "scanned_path", "ScanCorrector-s", "document"),
        e("ScanCorrector-s", "corrected_document", "OutputParser-p", "branch_b"),
        e("OutputParser-p", "merged", "DocumentClassifier-c", "document"),
        e("DocumentClassifier-c", "classified_document", "DocumentExtractor-e", "document"),
        e("DocumentExtractor-e", "extracted_data", "ConfidenceRouter-r", "data"),
        e("ConfidenceRouter-r", "high_confidence", "ProcessedDocsOutput-z", "data"),
        e("ConfidenceRouter-r", "low_confidence", "ProcessedDocsOutput-z", "data"),
    ]
    return {"nodes": nodes, "edges": edges, "viewport": {}}


@pytest.mark.anyio
async def test_full_flow_topology_reaches_the_sink(monkeypatch):
    """The user's full topology (router→merge→classifier→extractor→confidence-router→sink) must
    structurally execute end-to-end — every node on the active (digital) path runs and the sink
    finalizes. Two merge-after-router joins exercise the fix."""
    from agentcore.services.database.models.idp.documents import IdpDocument
    from agentcore.services.deps import session_scope
    from agentcore.services.idp.graph_native.runner import run_native_graph

    storage = T._MockStorage()
    monkeypatch.setattr(pipeline, "get_storage_service", lambda: storage)
    monkeypatch.setattr("agentcore.services.deps.get_storage_service", lambda: storage, raising=False)
    T._patch_extraction(monkeypatch)

    async with session_scope() as s:
        agent_id, doc_id = await T._setup_document(s, graph=_full_flow_graph(), file_bytes=T._digital_pdf())

    node_log: list = []
    await run_native_graph(document_id=str(doc_id), agent_data=_full_flow_graph(),
                           agent_id=str(agent_id), node_log_out=node_log)
    ran = {e["name"] for e in node_log}

    for required in ("Document Type Detector", "Output Parser", "Document Classifier",
                     "AI Field Extractor", "Confidence Router", "Processed Docs Output"):
        assert required in ran, f"{required} did not run — flow broke. ran={ran}"
    assert "Scan Corrector" not in ran, f"scanned branch should be inactive for a digital doc: {ran}"

    async with session_scope() as s:
        doc = await s.get(IdpDocument, doc_id)
        assert doc.status == "pending_review", f"sink must finalize: {doc.status}"


def _validation_chain_graph():
    """Extractor → Math Reconcile → Confidence Router → Rules/Conditions → Approval Gate → sink.
    Each router's BOTH outputs feed the next node, so every node runs whichever branch fires — a stress
    test of three routers in series (Confidence, Rules, Approval) plus Math Reconcile."""
    def o(name, method, grouped=False):
        d = {"name": name, "display_name": name, "types": ["Message"], "selected": "Message", "method": method}
        if grouped:
            d["group_outputs"] = True
        return d

    def e(src, sh, tgt, tf):
        return {"source": src, "target": tgt,
                "data": {"sourceHandle": {"name": sh, "id": src, "output_types": ["Message"]},
                         "targetHandle": {"fieldName": tf, "id": tgt, "inputTypes": ["Message", "Data"]}}}

    hi = {"type": "other", "name": "data", "input_types": ["Message", "Data"]}
    nodes = [
        _node("DocumentUpload-a", "Document Upload", outputs=[o("document", "load")]),
        _node("DocumentExtractor-e", "AI Field Extractor",
              template={"extraction_mode": {"type": "str", "name": "extraction_mode", "value": "field_configuration"},
                        "document": {"type": "other", "name": "document", "input_types": ["Message"]}},
              outputs=[o("extracted_data", "extract")]),
        _node("MathReconcile-m", "Math Reconcile", template={"data": hi},
              outputs=[o("validated_data", "reconcile")]),
        _node("ConfidenceRouter-c", "Confidence Router",
              template={"data": hi, "threshold": {"type": "float", "name": "threshold", "value": 0.8}},
              outputs=[o("high_confidence", "high_confidence", True), o("low_confidence", "low_confidence", True)]),
        _node("RulesConditions-r", "Rules / Conditions",
              template={"data": hi, "logic_operator": {"type": "str", "name": "logic_operator", "value": "AND"},
                        "conditions": {"type": "str", "name": "conditions", "value": "[]"}},
              outputs=[o("passed", "passed", True), o("failed", "failed", True)]),
        _node("ApprovalGate-g", "Approval Gate",
              template={"data": hi, "approval_field": {"type": "str", "name": "approval_field", "value": "rule_action"},
                        "approve_value": {"type": "str", "name": "approve_value", "value": "auto_approve"}},
              outputs=[o("auto_approved", "auto_approved", True), o("pending_review", "pending_review", True)]),
        _node("ProcessedDocsOutput-z", "Processed Docs Output",
              template={"data": {"type": "other", "name": "data", "input_types": ["Data", "Message"], "list": True}},
              outputs=[o("result", "finalize")]),
    ]
    edges = [
        e("DocumentUpload-a", "document", "DocumentExtractor-e", "document"),
        e("DocumentExtractor-e", "extracted_data", "MathReconcile-m", "data"),
        e("MathReconcile-m", "validated_data", "ConfidenceRouter-c", "data"),
        e("ConfidenceRouter-c", "high_confidence", "RulesConditions-r", "data"),
        e("ConfidenceRouter-c", "low_confidence", "RulesConditions-r", "data"),
        e("RulesConditions-r", "passed", "ApprovalGate-g", "data"),
        e("RulesConditions-r", "failed", "ApprovalGate-g", "data"),
        e("ApprovalGate-g", "auto_approved", "ProcessedDocsOutput-z", "data"),
        e("ApprovalGate-g", "pending_review", "ProcessedDocsOutput-z", "data"),
    ]
    return {"nodes": nodes, "edges": edges, "viewport": {}}


@pytest.mark.anyio
async def test_cancel_document_stops_the_run(monkeypatch):
    """The Stop button path: cancel_document cancels the running background task and marks the job
    'cancelled' + the document terminal."""
    import asyncio

    from agentcore.services.database.models.idp.documents import IdpDocument, IdpProcessingJob
    from agentcore.services.deps import get_queue_service, session_scope
    from agentcore.services.idp.pipeline import cancel_document

    storage = T._MockStorage()
    monkeypatch.setattr(pipeline, "get_storage_service", lambda: storage)

    async with session_scope() as s:
        agent_id, doc_id = await T._setup_document(s, graph=_slice_graph(), file_bytes=T._digital_pdf())
        doc = await s.get(IdpDocument, doc_id)
        doc.status = "processing"
        s.add(doc)
        job = IdpProcessingJob(document_id=doc_id, agent_id=agent_id, status="running")
        s.add(job)
        await s.commit()
        await s.refresh(job)
        job_id = job.id

    # Start a long-running fake job task in the queue service.
    queue = get_queue_service()
    if not queue.is_started():
        queue.start()
    job_key = str(job_id)
    started = asyncio.Event()

    async def _slow():
        started.set()
        await asyncio.sleep(60)

    queue.create_queue(job_key)
    queue.start_job(job_key, _slow())
    await asyncio.wait_for(started.wait(), timeout=5)
    _, _, task, _ = queue.get_queue_data(job_key)
    assert task is not None and not task.done(), "fake job task should be running"

    # Cancel.
    async with session_scope() as s:
        cancelled = await cancel_document(s, doc_id)
    assert cancelled is True

    # Let the cancellation propagate, then confirm the background task actually stopped.
    try:
        await asyncio.wait_for(asyncio.gather(task, return_exceptions=True), timeout=3)
    except Exception:
        pass
    assert task.cancelled() or task.done(), "the running task must be cancelled"
    # … and the job + doc are terminal.
    async with session_scope() as s:
        job = await s.get(IdpProcessingJob, job_id)
        doc = await s.get(IdpDocument, doc_id)
        assert job.status == "failed" and "Cancelled" in (job.error_message or ""), (job.status, job.error_message)
        assert doc.status == "failed" and (doc.extra or {}).get("cancelled") is True, (doc.status, doc.extra)


@pytest.mark.anyio
async def test_validation_chain_runs_every_node(monkeypatch):
    """Math Reconcile + Confidence Router + Rules/Conditions + Approval Gate in series must all execute
    without error and reach the sink (three routers back-to-back)."""
    from agentcore.services.database.models.idp.documents import IdpDocument
    from agentcore.services.deps import session_scope
    from agentcore.services.idp.graph_native.runner import run_native_graph

    storage = T._MockStorage()
    monkeypatch.setattr(pipeline, "get_storage_service", lambda: storage)
    monkeypatch.setattr("agentcore.services.deps.get_storage_service", lambda: storage, raising=False)
    T._patch_extraction(monkeypatch)

    async with session_scope() as s:
        agent_id, doc_id = await T._setup_document(s, graph=_validation_chain_graph(), file_bytes=T._digital_pdf())

    node_log: list = []
    await run_native_graph(document_id=str(doc_id), agent_data=_validation_chain_graph(),
                           agent_id=str(agent_id), node_log_out=node_log)
    ran = {e["name"] for e in node_log}

    for required in ("AI Field Extractor", "Math Reconcile", "Confidence Router",
                     "Rules / Conditions", "Approval Gate", "Processed Docs Output"):
        assert required in ran, f"{required} did NOT run — chain broke. ran={ran}"

    async with session_scope() as s:
        doc = await s.get(IdpDocument, doc_id)
        assert doc.status in ("pending_review", "auto_approved"), f"sink must finalize: {doc.status}"


@pytest.mark.anyio
async def test_merge_after_router_runs_the_active_branch(monkeypatch):
    """A router's branches rejoin at a merge node (Output Parser) that continues to the sink. Stopping
    the not-taken branch must NOT deactivate the shared merge + sink. Regression for the flow where a
    digital doc routed 'digital' but the AI Extractor / Output Parser / Processed Docs never ran because
    the 'scanned' stop() cascaded through the shared downstream."""
    from agentcore.services.database.models.idp.documents import IdpDocument
    from agentcore.services.deps import session_scope
    from agentcore.services.idp.graph_native.runner import run_native_graph

    storage = T._MockStorage()
    monkeypatch.setattr(pipeline, "get_storage_service", lambda: storage)
    monkeypatch.setattr("agentcore.services.deps.get_storage_service", lambda: storage, raising=False)
    T._patch_extraction(monkeypatch)

    async with session_scope() as s:
        agent_id, doc_id = await T._setup_document(s, graph=_router_merge_graph(), file_bytes=T._digital_pdf())

    node_log: list = []
    await run_native_graph(document_id=str(doc_id), agent_data=_router_merge_graph(),
                           agent_id=str(agent_id), node_log_out=node_log)
    ran = {e["name"] for e in node_log}

    # The digital doc routes 'digital' → Scan Corrector (scanned branch) must NOT run …
    assert "Scan Corrector" not in ran, f"scanned branch should be inactive: {ran}"
    # … but the merge node and the sink DO run (they'd be deactivated before the fix).
    assert "Output Parser" in ran, f"merge-after-router must run on the active branch: {ran}"
    assert "Processed Docs Output" in ran, f"sink after the merge must run: {ran}"

    async with session_scope() as s:
        doc = await s.get(IdpDocument, doc_id)
        assert doc.status == "pending_review", f"the sink must finalize the doc: {doc.status}"


@pytest.mark.anyio
async def test_shared_idp_channel_accumulates_through_the_engine(monkeypatch):
    """The idp_working_set state is maintained THROUGH the LangGraph engine, not around it.

    WRITE path: every node's output is harvested into the shared channel via the returned state update
    (reduced by _merge_dicts) — a spy on the real harvest shows Document Upload contributes document_id
    and the AI Field Extractor contributes `extracted`, i.e. the channel accumulates across nodes as the
    engine runs. READ path: the sink finalizes the document off that accumulated channel.
    """
    from agentcore.graph_langgraph import nodes as lgnodes
    from agentcore.services.database.models.idp.documents import IdpDocument
    from agentcore.services.deps import session_scope
    from agentcore.services.idp.graph_native.runner import run_native_graph

    storage = T._MockStorage()
    monkeypatch.setattr(pipeline, "get_storage_service", lambda: storage)
    monkeypatch.setattr("agentcore.services.deps.get_storage_service", lambda: storage, raising=False)
    T._patch_extraction(monkeypatch)

    # Spy on the REAL harvest (module-global, looked up per call inside node_function) to observe what
    # each executed node contributes to the shared channel as the engine runs it.
    harvested: list[dict] = []
    _real_delta = lgnodes._extract_idp_delta

    def _spy(result):
        d = _real_delta(result)
        if d:
            harvested.append(d)
        return d

    monkeypatch.setattr(lgnodes, "_extract_idp_delta", _spy)

    async with session_scope() as s:
        agent_id, doc_id = await T._setup_document(s, graph=_slice3_graph(), file_bytes=T._digital_pdf())
    await run_native_graph(document_id=str(doc_id), agent_data=_slice3_graph(), agent_id=str(agent_id))

    # WRITE: the channel received contributions from MULTIPLE nodes across the run (not just one).
    assert any("document_id" in d for d in harvested), f"Document Upload must harvest document_id: {harvested}"
    assert any("extracted" in d for d in harvested), f"the extractor must harvest `extracted`: {harvested}"

    # READ: the sink finalized the document off the accumulated channel.
    async with session_scope() as s:
        doc = await s.get(IdpDocument, doc_id)
        assert doc.status == "pending_review", f"sink finalizes via the maintained state: {doc.status}"
