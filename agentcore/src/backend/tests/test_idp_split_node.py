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
    monkeypatch.setattr("agentcore.components.IDP.document_splitter.enqueue_document", fake_enqueue)

    comp = IDPDocumentSplitter()
    comp.document = _msg(str(parent.id)); comp.llm = object()
    kind, child_ids = await comp._decide()
    assert kind == "split"
    assert len(child_ids) == 2 and len(enq) == 2
    assert parent.status == "split"
    assert [c.predicted_type for c in children] == ["Invoice", "Medical Report"]   # pre-typed from seg_types
    out = await comp.split()
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
    out = await comp.single_document()
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
