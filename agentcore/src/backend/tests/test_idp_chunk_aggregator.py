from uuid import uuid4
from agentcore.schema.message import Message
from agentcore.schema.data import Data
from agentcore.services.idp.graph_native.payload import get_payload
from agentcore.components.IDP.chunk_aggregator import IDPChunkAggregator


def _chunk(doc_id, headers, line_items=None):
    return Message(text="", additional_kwargs={"idp": {"document_id": doc_id, "extracted": {"headers": headers, "line_items": line_items or []}}})


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
    assert headers["invoice_number"]["value"] == "INV-1"
    assert headers["total"]["value"] == "100"


def test_aggregator_merges_line_items():
    doc_id = str(uuid4())
    comp = IDPChunkAggregator()
    comp.chunks_data = [
        _chunk(doc_id, {}, line_items=[{"desc": "Widget", "amount": "10"}]),
        _chunk(doc_id, {}, line_items=[{"desc": "Gadget", "amount": "20"}]),
    ]
    comp.dedup_strategy = "keep_highest_confidence"
    out = comp.aggregated_data()
    extracted = (get_payload(out).get("extracted") or {})
    line_items = extracted.get("line_items", [])
    assert len(line_items) == 2
    descs = {row["desc"] for row in line_items}
    assert "Widget" in descs and "Gadget" in descs


def test_aggregator_backcompat_data_dict():
    comp = IDPChunkAggregator()
    comp.chunks_data = [
        Data(data={"headers": {"vendor": {"value": "ACME", "confidence": 0.8}}, "line_items": []}),
    ]
    comp.dedup_strategy = "keep_highest_confidence"
    out = comp.aggregated_data()
    # Back-compat path: chunk.data dict with "headers" key is used directly
    extracted = (get_payload(out).get("extracted") or {})
    if not extracted:
        # Fallback: the rep itself may be the Data, check out.data
        extracted = getattr(out, "data", {})
    headers = (extracted.get("headers") or {}) if isinstance(extracted, dict) else {}
    assert "vendor" in headers
    assert headers["vendor"]["value"] == "ACME"


def test_aggregator_merge_all_last_wins():
    doc_id = str(uuid4())
    comp = IDPChunkAggregator()
    comp.chunks_data = [
        _chunk(doc_id, {"total": {"value": "100", "confidence": 0.9}}),
        _chunk(doc_id, {"total": {"value": "200", "confidence": 0.7}}),
    ]
    comp.dedup_strategy = "merge_all"
    out = comp.aggregated_data()
    extracted = (get_payload(out).get("extracted") or {})
    headers = extracted.get("headers", {})
    assert headers["total"]["value"] == "200"


def test_aggregator_keep_first():
    doc_id = str(uuid4())
    comp = IDPChunkAggregator()
    comp.chunks_data = [
        _chunk(doc_id, {"total": {"value": "100", "confidence": 0.9}}),
        _chunk(doc_id, {"total": {"value": "200", "confidence": 0.7}}),
    ]
    comp.dedup_strategy = "keep_first"
    out = comp.aggregated_data()
    extracted = (get_payload(out).get("extracted") or {})
    headers = extracted.get("headers", {})
    assert headers["total"]["value"] == "100"


def test_aggregator_empty_chunks_returns_data():
    comp = IDPChunkAggregator()
    comp.chunks_data = []
    out = comp.aggregated_data()
    assert isinstance(out, Data)
    # Production returns Data(data={}, text="") — Data stores text in .data,
    # so we assert no IDP envelope keys are present rather than an exact-empty check.
    assert out.data.get("document_id") is None
    assert out.data.get("extracted") is None
