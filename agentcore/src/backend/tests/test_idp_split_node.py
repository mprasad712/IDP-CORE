from types import SimpleNamespace
from uuid import uuid4
import pytest

from agentcore.components.IDP.document_splitter import IDPDocumentSplitter
from agentcore.schema.message import Message


def _msg(doc_id):
    return Message(text="", additional_kwargs={"idp": {"document_id": doc_id, "file_type": "pdf",
                                                        "file_name": "b.pdf", "agent_scope": "a"}})


class _Scope:
    def __init__(self, sess): self._s = sess
    async def __aenter__(self): return self._s
    async def __aexit__(self, *a): return False


class _Sess:
    def __init__(self, registry): self._r = registry; self.committed = False
    async def get(self, model, ident): return self._r.get(ident)
    def add(self, x): pass
    async def commit(self): self.committed = True
    async def exec(self, stmt):
        class _Empty:
            def all(self): return []
        return _Empty()


@pytest.mark.anyio
async def test_splitter_forks_multi_doc_and_pretypes(monkeypatch):
    parent = SimpleNamespace(id=uuid4(), parent_document_id=None, file_type="pdf", page_count=4,
                             status="processing", extra={}, predicted_type=None, processing_completed_at=None)
    registry = {parent.id: parent}
    sess = _Sess(registry)
    monkeypatch.setattr("agentcore.components.IDP.document_splitter.session_scope", lambda: _Scope(sess))
    monkeypatch.setattr("agentcore.components.IDP.document_splitter.get_storage_service", lambda: object())

    async def fake_boundaries(*a, **k):
        return [(0, 1), (2, 3)], ["Invoice", "Medical Report"]
    monkeypatch.setattr("agentcore.components.IDP.document_splitter.detect_boundaries_hybrid", fake_boundaries)

    async def fake_inputs(self, payload, doc): return ({0: "a", 1: "b", 2: "c", 3: "d"}, [])
    monkeypatch.setattr(IDPDocumentSplitter, "_page_inputs", fake_inputs, raising=True)

    children = []
    async def fake_materialize(session, storage, parent_doc, boundaries):
        for _ in boundaries:
            c = SimpleNamespace(id=uuid4(), predicted_type=None)
            children.append(c); registry[c.id] = c
        return [c.id for c in children]
    enq = []
    async def fake_enqueue(session, cid): enq.append(cid)
    monkeypatch.setattr("agentcore.components.IDP.document_splitter.materialize_children", fake_materialize)
    monkeypatch.setattr("agentcore.services.idp.pipeline.enqueue_document", fake_enqueue)

    comp = IDPDocumentSplitter()
    comp.document = _msg(str(parent.id)); comp.llm = object()
    kind, child_ids = await comp._decide()
    assert kind == "split"
    assert len(child_ids) == 2 and len(enq) == 2
    assert parent.status == "split"
    assert [c.predicted_type for c in children] == ["Invoice", "Medical Report"]   # pre-typed from seg_types
    out = await comp.route()
    assert out is not None


@pytest.mark.anyio
async def test_splitter_passthrough_single_doc(monkeypatch):
    parent = SimpleNamespace(id=uuid4(), parent_document_id=None, file_type="pdf", page_count=2,
                             status="processing", extra={}, predicted_type=None, processing_completed_at=None)
    sess = _Sess({parent.id: parent})
    monkeypatch.setattr("agentcore.components.IDP.document_splitter.session_scope", lambda: _Scope(sess))
    monkeypatch.setattr("agentcore.components.IDP.document_splitter.get_storage_service", lambda: object())
    async def fake_boundaries(*a, **k): return [(0, 1)], None       # ONE document
    monkeypatch.setattr("agentcore.components.IDP.document_splitter.detect_boundaries_hybrid", fake_boundaries)
    async def fake_inputs(self, payload, doc): return ({0: "a", 1: "b"}, [])
    monkeypatch.setattr(IDPDocumentSplitter, "_page_inputs", fake_inputs, raising=True)

    comp = IDPDocumentSplitter(); comp.document = _msg(str(parent.id)); comp.llm = None
    kind, _ = await comp._decide()
    assert kind == "single"
    assert parent.status == "processing"                # NOT finalized
    out = await comp.route()
    assert out is not None


@pytest.mark.anyio
async def test_splitter_child_passes_through(monkeypatch):
    child = SimpleNamespace(id=uuid4(), parent_document_id=uuid4(), file_type="pdf", page_count=2,
                            status="processing", extra={}, predicted_type=None)
    sess = _Sess({child.id: child})
    monkeypatch.setattr("agentcore.components.IDP.document_splitter.session_scope", lambda: _Scope(sess))
    monkeypatch.setattr("agentcore.components.IDP.document_splitter.get_storage_service", lambda: object())
    comp = IDPDocumentSplitter(); comp.document = _msg(str(child.id)); comp.llm = None
    kind, _ = await comp._decide()
    assert kind == "single"                             # child (parent_document_id set) never re-splits


@pytest.mark.anyio
async def test_splitter_fails_parent_when_no_child_enqueues(monkeypatch):
    """Safety net: if every child enqueue raises, parent must be set to 'failed', not 'split'."""
    parent = SimpleNamespace(id=uuid4(), parent_document_id=None, file_type="pdf", page_count=4,
                             status="processing", extra={}, predicted_type=None,
                             processing_completed_at=None, error_message=None, failed_at=None)
    registry = {parent.id: parent}
    sess = _Sess(registry)
    monkeypatch.setattr("agentcore.components.IDP.document_splitter.session_scope", lambda: _Scope(sess))
    monkeypatch.setattr("agentcore.components.IDP.document_splitter.get_storage_service", lambda: object())

    async def fake_boundaries(*a, **k):
        return [(0, 1), (2, 3)], ["Invoice", "Receipt"]
    monkeypatch.setattr("agentcore.components.IDP.document_splitter.detect_boundaries_hybrid", fake_boundaries)

    async def fake_inputs(self, payload, doc): return ({0: "a", 1: "b", 2: "c", 3: "d"}, [])
    monkeypatch.setattr(IDPDocumentSplitter, "_page_inputs", fake_inputs, raising=True)

    children = []
    async def fake_materialize(session, storage, parent_doc, boundaries):
        for _ in boundaries:
            c = SimpleNamespace(id=uuid4(), predicted_type=None)
            children.append(c); registry[c.id] = c
        return [c.id for c in children]
    monkeypatch.setattr("agentcore.components.IDP.document_splitter.materialize_children", fake_materialize)

    async def fake_enqueue(session, cid): raise RuntimeError("queue down")
    monkeypatch.setattr("agentcore.services.idp.pipeline.enqueue_document", fake_enqueue)

    comp = IDPDocumentSplitter()
    comp.document = _msg(str(parent.id)); comp.llm = object()
    kind, child_ids = await comp._decide()

    # Decision is still ("split", child_ids) — terminal, not passthrough
    assert kind == "split"
    assert len(child_ids) == 2

    # Parent must be failed, not "split" (the safety net)
    assert parent.status == "failed"
    assert parent.error_message is not None and "no child" in parent.error_message
    assert parent.failed_at is not None


@pytest.mark.anyio
async def test_splitter_reuses_existing_children_on_retry(monkeypatch):
    """On retry: if children already exist in DB, reuse them; never call materialize_children again."""
    parent = SimpleNamespace(id=uuid4(), parent_document_id=None, file_type="pdf", page_count=4,
                             status="processing", extra={}, predicted_type=None,
                             processing_completed_at=None, error_message=None, failed_at=None)
    child1 = SimpleNamespace(id=uuid4(), predicted_type="Invoice")
    child2 = SimpleNamespace(id=uuid4(), predicted_type="Receipt")

    class _SessWithExec:
        def __init__(self, registry):
            self._r = registry
            self.committed = False
            self._exec_result = [child1, child2]

        async def get(self, model, ident):
            return self._r.get(ident)

        def add(self, x):
            pass

        async def commit(self):
            self.committed = True

        async def exec(self, stmt):
            class _Result:
                def __init__(self, rows): self._rows = rows
                def all(self): return self._rows
            return _Result(self._exec_result)

    registry = {parent.id: parent}
    sess = _SessWithExec(registry)
    monkeypatch.setattr("agentcore.components.IDP.document_splitter.session_scope", lambda: _Scope(sess))
    monkeypatch.setattr("agentcore.components.IDP.document_splitter.get_storage_service", lambda: object())

    async def fake_boundaries(*a, **k):
        return [(0, 1), (2, 3)], ["Invoice", "Receipt"]
    monkeypatch.setattr("agentcore.components.IDP.document_splitter.detect_boundaries_hybrid", fake_boundaries)

    async def fake_inputs(self, payload, doc): return ({0: "a", 1: "b", 2: "c", 3: "d"}, [])
    monkeypatch.setattr(IDPDocumentSplitter, "_page_inputs", fake_inputs, raising=True)

    async def _should_not_be_called(*a, **k):
        raise AssertionError("materialize_children was called on retry — duplicate children would be created")
    monkeypatch.setattr("agentcore.components.IDP.document_splitter.materialize_children", _should_not_be_called)

    enq = []
    async def fake_enqueue(session, cid): enq.append(cid)
    monkeypatch.setattr("agentcore.services.idp.pipeline.enqueue_document", fake_enqueue)

    comp = IDPDocumentSplitter()
    comp.document = _msg(str(parent.id)); comp.llm = object()
    kind, child_ids = await comp._decide()

    assert kind == "split"
    assert set(child_ids) == {child1.id, child2.id}
    assert set(enq) == {child1.id, child2.id}  # existing children enqueued


# ─────────────────────────────────────────────────────────────────────────────
# Task-3: single-output node (route()) + decision carry
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_splitter_single_output_passthrough(monkeypatch):
    """route() on a single-doc decision must return a carry whose payload has NO 'decision' key."""
    from agentcore.services.idp.graph_native.payload import get_payload

    # Monkeypatch _decide so no DB / storage is needed
    async def fake_decide(self): return ("single", None)
    monkeypatch.setattr(IDPDocumentSplitter, "_decide", fake_decide, raising=True)

    comp = IDPDocumentSplitter()
    comp.document = _msg(str(__import__("uuid").uuid4()))

    out = await comp.route()
    assert out is not None
    payload = get_payload(out)
    assert "decision" not in payload, f"Expected no 'decision' key for single-doc path, got {payload!r}"


@pytest.mark.anyio
async def test_splitter_split_carries_decision(monkeypatch):
    """route() on a multi-doc decision must return a carry with decision='split' in the payload."""
    from agentcore.services.idp.graph_native.payload import get_payload
    from uuid import uuid4

    child_ids = [uuid4(), uuid4()]

    async def fake_decide(self): return ("split", child_ids)
    monkeypatch.setattr(IDPDocumentSplitter, "_decide", fake_decide, raising=True)

    comp = IDPDocumentSplitter()
    comp.document = _msg(str(uuid4()))

    out = await comp.route()
    assert out is not None
    payload = get_payload(out)
    assert payload.get("decision") == "split", (
        f"Expected decision='split' in payload for multi-doc path, got {payload!r}"
    )
