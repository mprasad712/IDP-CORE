from uuid import uuid4
from agentcore.schema.message import Message
from agentcore.services.idp.graph_native.payload import get_payload
from agentcore.components.IDP.chunk_aggregator import IDPChunkAggregator


def _chunk(doc_id, headers):
    return Message(text="", additional_kwargs={"idp": {"document_id": doc_id, "extracted": {"headers": headers, "line_items": []}}})


def test_aggregator_preserves_document_id_and_extracted():
    doc_id = str(uuid4())
    comp = IDPChunkAggregator()
    comp.chunks_data = [
        _chunk(doc_id, {"invoice_number": {"value": "INV-1", "confidence": 0.9}}),
        _chunk(doc_id, {"total": {"value": "100", "confidence": 0.8}}),
    ]
    comp.dedup_strategy = "keep_highest_confidence"
    out = comp.aggregated_data()
    payload = get_payload(out)
    assert payload.get("document_id") == doc_id            # envelope survives
    extracted = payload.get("extracted") or {}
    headers = extracted.get("headers") or {}
    assert "invoice_number" in headers and "total" in headers  # fields merged, not dropped
