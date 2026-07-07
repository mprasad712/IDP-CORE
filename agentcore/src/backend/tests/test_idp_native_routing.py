"""Native (graph_langgraph) IDP engine: shared working-set channel, router field-resolution, the
Rules / Conditions component, and the payload-strip fixes.

These guard the class of bugs where routers read the wrong place (``getattr(Message, 'confidence')``
— always ``None`` → always routed 'low') and where a node dropped the working-set from the flow
(setting ``.data`` / building a bare Message → downstream 'no document_id').
"""
from agentcore.services.idp.graph_native.payload import (
    carry, effective_payload, get_payload, get_shared_state, new_message,
    overall_confidence, resolve_field,
)


class _FakeVertex:
    """Stands in for the LangGraphVertex the engine stashes the shared snapshot on."""
    def __init__(self, snapshot):
        self._idp_shared_snapshot = snapshot


def _bind(component, snapshot):
    component._vertex = _FakeVertex(snapshot)
    return component


def _doc_msg():
    return new_message(
        text="INVOICE total 100",
        document_id="doc-1", job_id="job-1", agent_scope="scope", file_name="idp_x.pdf", file_type="pdf",
        predicted_type="invoice",
        extracted={"headers": {"total": {"value": "100", "confidence": 0.9},
                               "vendor": {"value": "ACME", "confidence": 0.7}}},
    )


# ── shared channel ─────────────────────────────────────────────────────────────
def test_shared_state_recovers_stripped_payload():
    snap = {"document_id": "doc-1", "agent_scope": "scope", "file_name": "idp_x.pdf"}
    comp = _bind(type("C", (), {})(), snap)
    assert get_shared_state(comp) == snap

    bare = new_message(text="an upstream node stripped the payload")
    assert get_payload(bare) == {}
    # The shared channel still has document_id even though the immediate input lost it.
    assert effective_payload(comp, bare).get("document_id") == "doc-1"


def test_effective_payload_local_wins_over_shared():
    comp = _bind(type("C", (), {})(), {"file_name": "old.pdf", "document_id": "doc-1"})
    local = new_message(document_id="doc-1", file_name="sliced/new.pdf")
    merged = effective_payload(comp, local)
    assert merged["file_name"] == "sliced/new.pdf"   # latest hop wins
    assert merged["document_id"] == "doc-1"


# ── field resolution ───────────────────────────────────────────────────────────
def test_resolve_field_aliases():
    m = _doc_msg()
    assert resolve_field(m, "document_type") == "invoice"   # alias -> predicted_type
    assert resolve_field(m, "total") == "100"               # extracted header value
    assert resolve_field(m, "vendor") == "ACME"


def test_overall_confidence_is_mean_of_field_confidences():
    assert abs(overall_confidence(_doc_msg()) - 0.8) < 1e-6   # mean(0.9, 0.7)


# ── routers ────────────────────────────────────────────────────────────────────
def test_confidence_router_scores_from_payload_not_attribute():
    from agentcore.components.IDP.confidence_router import IDPConfidenceRouter

    r = _bind(IDPConfidenceRouter(), {})
    r.data = _doc_msg(); r.confidence_field = "confidence"; r.threshold = 0.8
    assert abs(r._get_score() - 0.8) < 1e-6   # was always 0.0 before the fix

    low = new_message(document_id="d", extracted={"headers": {"a": {"value": "x", "confidence": 0.2}}})
    r2 = _bind(IDPConfidenceRouter(), {}); r2.data = low; r2.confidence_field = "confidence"; r2.threshold = 0.8
    assert abs(r2._get_score() - 0.2) < 1e-6


def test_multi_branch_router_resolves_document_type():
    from agentcore.components.IDP.multi_branch_router import IDPMultiBranchRouter

    mb = _bind(IDPMultiBranchRouter(), {})
    mb.document = _doc_msg(); mb.route_field = "document_type"
    mb.route_1 = "invoice"; mb.route_2 = "receipt"; mb.route_3 = ""; mb.route_4 = ""; mb.route_5 = ""
    assert mb._get_field_value() == "invoice"
    assert mb._matched_route() == 1


def test_approval_gate_reads_rule_action_from_payload():
    from agentcore.components.IDP.approval_gate import IDPApprovalGate

    g = _bind(IDPApprovalGate(), {})
    g.data = carry(_doc_msg(), rule_action="auto_approve")
    g.approval_field = "rule_action"; g.approve_value = "auto_approve"
    assert g._is_approved() is True

    g.data = carry(_doc_msg(), rule_action="review")
    assert g._is_approved() is False


def test_condition_node_resolves_from_payload():
    from agentcore.components.IDP.condition_node import IDPConditionNode

    c = _bind(IDPConditionNode(), {})
    c.document = _doc_msg(); c.field = "document_type"; c.operator = "equals"; c.value = "invoice"
    assert c._evaluate() is True
    c.value = "receipt"
    assert c._evaluate() is False


# ── Rules / Conditions component (was missing → hard build error) ───────────────
def test_rules_conditions_component_evaluates():
    from agentcore.components.IDP.rules_conditions import IDPRulesConditions

    assert IDPRulesConditions.display_name == "Rules / Conditions"

    rc = _bind(IDPRulesConditions(), {})
    rc.data = _doc_msg(); rc.logic_operator = "AND"
    rc.conditions = '[{"field":"total","op":"gt","value":50},{"field":"document_type","op":"eq","value":"invoice"}]'
    assert rc._passes() is True

    rc.conditions = '[{"field":"total","op":"lt","value":50}]'
    assert rc._passes() is False

    rc.logic_operator = "OR"
    rc.conditions = '[{"field":"total","op":"lt","value":50},{"field":"document_type","op":"eq","value":"invoice"}]'
    assert rc._passes() is True

    # No rules configured → don't silently block the document.
    rc.conditions = "[]"
    assert rc._passes() is True


# ── payload-strip fixes: nodes must carry the working-set, never set .data ──────
def test_detector_carries_working_set():
    from agentcore.components.IDP.document_type_detector import IDPDocumentTypeDetector

    d = IDPDocumentTypeDetector(); d.document = _doc_msg(); d.digital_label = "digital"
    tagged = d._tagged("digital")
    assert get_payload(tagged).get("document_id") == "doc-1"   # not lost
    assert get_payload(tagged).get("overall_kind") == "digital"
    assert "document_type" not in (getattr(tagged, "data", {}) or {})   # label went to payload, not .data


def test_scan_corrector_actually_corrects_and_saves(monkeypatch):
    """The Scan Corrector does REAL work: it de-skews/rotates the pages, writes a corrected file to its
    own subdir, and repoints the flow at it (carrying the working-set, never .data)."""
    import asyncio
    import io

    from reportlab.pdfgen import canvas

    from agentcore.components.IDP.scan_corrector import IDPScanCorrector

    def _pdf(n=2):
        b = io.BytesIO(); c = canvas.Canvas(b)
        for i in range(n):
            c.drawString(50, 700, f"PAGE {i + 1}"); c.showPage()
        c.save(); return b.getvalue()

    class _Store:
        def __init__(self):
            self.files = {}
        async def get_file(self, agent_id, file_name):
            return self.files[(agent_id, file_name)]
        async def save_file(self, agent_id, file_name, data):
            self.files[(agent_id, file_name)] = data

    store = _Store()
    store.files[("scope", "idp_d1.pdf")] = _pdf(2)
    monkeypatch.setattr("agentcore.services.deps.get_storage_service", lambda: store, raising=False)

    async def _run():
        sc = IDPScanCorrector()
        sc.document = new_message(text="", document_id="doc-1", agent_scope="scope",
                                  file_name="idp_d1.pdf", file_type="pdf")
        sc.fix_skew = True; sc.skew_threshold = 0.5; sc.fix_rotation = True; sc.allowed_angles = "90,180,270"
        return await sc.corrected_document()

    out = asyncio.run(_run())
    p = get_payload(out)
    # A corrected file was actually written to its own subdir, and the flow points at it now.
    assert p.get("file_name", "").startswith("corrected/"), p.get("file_name")
    assert any(k[1].startswith("corrected/") for k in store.files), list(store.files)
    assert p.get("document_id") == "doc-1"                              # working-set preserved
    assert "scan_corrections" not in (getattr(out, "data", {}) or {})   # note in payload, not .data


def test_scan_corrector_preserves_upstream_ocr_text(monkeypatch):
    """Regression: when Scan Corrector is wired AFTER an OCR node (PaddleOCR), the source Message
    already carries real OCR text. Scan Corrector must PRESERVE it — re-deriving text from the
    rasterized corrected image returns EMPTY, which would WIPE the OCR text and break every downstream
    text node (Chunking → extractor → 0 chunks → 0 fields)."""
    import asyncio
    import io

    from PIL import Image

    from agentcore.components.IDP.scan_corrector import IDPScanCorrector

    def _png():
        b = io.BytesIO(); Image.new("RGB", (240, 140), "white").save(b, format="PNG"); return b.getvalue()

    class _Store:
        def __init__(self):
            self.files = {}
        async def get_file(self, agent_id, file_name):
            return self.files[(agent_id, file_name)]
        async def save_file(self, agent_id, file_name, data):
            self.files[(agent_id, file_name)] = data

    store = _Store()
    store.files[("scope", "idp_img.png")] = _png()
    monkeypatch.setattr("agentcore.services.deps.get_storage_service", lambda: store, raising=False)

    ocr_text = "ACME CORP INVOICE #INV-1 TOTAL 600.00"  # what PaddleOCR produced upstream

    async def _run():
        sc = IDPScanCorrector()
        sc.document = new_message(text=ocr_text, document_id="doc-img", agent_scope="scope",
                                  file_name="idp_img.png", file_type="png")
        sc.fix_skew = True; sc.skew_threshold = 0.5; sc.fix_rotation = True; sc.allowed_angles = "90,180,270"
        return await sc.corrected_document()

    out = asyncio.run(_run())
    # The upstream OCR text survived — NOT wiped by re-deriving empty text from the rasterized file.
    assert out.text == ocr_text, f"OCR text was wiped by Scan Corrector: {out.text!r}"
    assert get_payload(out).get("document_id") == "doc-img"     # working-set still intact


def test_long_doc_merge_dedups_overlapping_rows():
    """Codex #2: overlapping chunks re-extract boundary rows; the merge must NOT double-count them."""
    from agentcore.services.idp.long_doc import merge_chunk_extractions

    def _row(item, amt):
        return {"columns": [{"column_name": "item", "value": item},
                            {"column_name": "amount", "value": amt}]}

    chunk1 = {"headers": {}, "line_items": [_row("Widget", "100"), _row("Gadget", "200")]}
    chunk2 = {"headers": {}, "line_items": [_row("Gadget", "200"), _row("Gizmo", "300")]}  # Gadget overlaps
    merged = merge_chunk_extractions([chunk1, chunk2])
    items = merged["line_items"]
    assert len(items) == 3, f"overlapping row double-counted: {items}"   # Widget, Gadget, Gizmo — once each


def test_opencv_orientation_distinguishes_90_from_270():
    """Codex #5: the OpenCV fallback must tell 90° from 270°, not always return 90."""
    import numpy as np

    from agentcore.services.idp.doc_orientation import _predict_opencv

    # Upright page: dark horizontal "text" bands in the TOP half, blank bottom → top-heavy.
    up = np.full((120, 120, 3), 255, np.uint8)
    for r in range(8, 56, 6):
        up[r:r + 2, 10:110] = 0
    # A page physically rotated 90° CW from upright (top points RIGHT) → detector should say 90.
    assert _predict_opencv(np.rot90(up, -1)) == 90
    # A page rotated 90° CCW (top points LEFT, i.e. 270° CW) → detector should say 270.
    assert _predict_opencv(np.rot90(up, 1)) == 270


def test_digital_confidence_can_reach_autoapprove():
    """Codex #9: a high-confidence digital extraction (no OCR tokens) must be able to clear an
    auto-approve threshold — the old 0.75 cap made auto-approve unreachable for digital docs."""
    from agentcore.services.idp.extraction import _ocr_evidence

    _loc, conf = _ocr_evidence("600.00", [], 0.97, vision_mode=False)
    assert conf >= 0.8, f"digital confidence still capped too low for auto-approve: {conf}"


def test_chunking_passes_through_when_no_text():
    """No text to chunk (a scanned / image doc with no OCR text) → the Chunking Strategy emits ONE
    passthrough chunk carrying the document handle, so the extractor can read it via vision (not an
    empty list that extracts nothing)."""
    from agentcore.components.IDP.chunking_strategy import IDPChunkingStrategy

    cs = IDPChunkingStrategy()
    cs.document = new_message(text="", document_id="d-img", agent_scope="scope",
                              file_name="x.png", file_type="png")
    cs.chunk_size_tokens = 4096
    cs.overlap_tokens = 256
    cs.chunking_method = "fixed_token"
    cs.model_name = "gpt-4o"
    out = cs.chunks()
    assert len(out) == 1, f"expected 1 passthrough chunk, got {len(out)}"
    assert get_payload(out[0]).get("document_id") == "d-img"   # document handle preserved


def test_extractor_falls_back_to_vision_when_chunks_have_no_text(monkeypatch):
    """A scanned/image doc yields a passthrough chunk with empty text; the AI Field Extractor must then
    read the page IMAGES via vision instead of extracting nothing (paddleocr-not-installed case)."""
    import asyncio
    from uuid import uuid4

    from agentcore.components.IDP.llm_extractor import IDPLLMExtractor

    class _Cfg:
        def __init__(self):
            self.id = uuid4()

    class _Res:
        def first(self):
            return _Cfg()

    class _Sess:
        async def exec(self, s):
            return _Res()
        async def get(self, *a, **k):
            return None

    class _Scope:
        async def __aenter__(self):
            return _Sess()
        async def __aexit__(self, *a):
            return False

    monkeypatch.setattr("agentcore.services.deps.session_scope", lambda: _Scope(), raising=False)

    called = {}

    async def _fake_vision(self, src, cfg_id, session=None):
        called["vision"] = True
        return {"headers": {"total": {"value": "600", "confidence": 0.95}}, "line_items": []}

    monkeypatch.setattr(IDPLLMExtractor, "_extract_via_vision", _fake_vision, raising=True)

    ex = IDPLLMExtractor()
    ex.extraction_mode = "field_configuration"
    ex.config_name = "Invoice"
    ex.config_names = []
    ex.llm = object()  # truthy — a model is connected
    chunk = new_message(text="", document_id="d-img", agent_scope="scope", file_name="x.png", file_type="png")

    out = asyncio.run(ex._extract_chunks([chunk]))
    assert called.get("vision") is True, "vision fallback was not used for a no-text chunk"
    total = get_payload(out).get("extracted", {}).get("headers", {}).get("total", {})
    assert total.get("value") == "600", f"vision extraction not merged into output: {get_payload(out)}"


def test_math_reconcile_unwraps_and_keeps_extraction(monkeypatch):
    """Math Reconcile must set the payload's `extracted` to the INNER extraction, not reconcile_math's
    wrapper ({extracted, attempts, balanced, report}). And when the fix can't run (e.g. the re-prompt
    hits a quota error) it must KEEP the original extraction — never drop it. That wrapper leak was the
    '0 fields saved' bug whenever Math Reconcile was in the flow."""
    import asyncio

    from agentcore.components.IDP.math_reconcile import IDPMathReconcile

    original = {
        "headers": {"invoice_value": {"value": "540.00", "confidence": 0.75}},
        "line_items": [{"columns": [{"column_name": "amount", "value": "100"}]}],
    }

    async def _fake_reconcile(extracted, llm, **kw):
        # Mimic the service: it returns a WRAPPER; here the re-prompt failed so it's the un-fixed data.
        return {"extracted": extracted, "attempts": 1, "balanced": False, "report": ["failed"], "_usage": None}

    monkeypatch.setattr(
        "agentcore.services.idp.math_reconcile.reconcile_math", _fake_reconcile, raising=False
    )

    mr = IDPMathReconcile()
    mr.data = new_message(text="INVOICE", document_id="d", extracted=original)
    mr.llm = object()
    mr.tolerance = 0.01
    mr.max_retries = 2
    out = asyncio.run(mr.reconcile())
    ex = get_payload(out).get("extracted", {})
    # The sink reads extracted["headers"] — it must be the INNER extraction, not the wrapper.
    assert ex.get("headers", {}).get("invoice_value", {}).get("value") == "540.00", f"lost extraction: {ex}"
    assert "attempts" not in ex and "balanced" not in ex, f"wrapper leaked into extracted: {ex}"


def test_overall_confidence_ignores_null_fields():
    """The Confidence Router's overall confidence must count only POPULATED fields. Averaging in null
    fields (value=None, confidence 0.0) drags the mean below the real score and mis-routes a good
    extraction to Low — even though the displayed confidence (which excludes nulls) is well above."""
    from agentcore.services.idp.graph_native.payload import overall_confidence

    extracted = {
        "headers": {
            "invoice_number": {"value": "90015218", "confidence": 0.75},
            "supplier_name": {"value": "IDES Holding AG", "confidence": 0.75},
            "irn": {"value": None, "confidence": 0.0},        # absent field — must NOT count
            "hsn_code": {"value": None, "confidence": 0.0},
        },
        "line_items": [{"columns": [
            {"column_name": "amount", "value": "120.00", "confidence": 0.75},
            {"column_name": "cgst_value", "value": None, "confidence": 0.0},
        ]}],
    }
    conf = overall_confidence(new_message(text="", document_id="d", extracted=extracted))
    assert conf is not None and conf >= 0.7, f"null fields dragged confidence down to {conf}"


def test_prepare_drops_orphan_terminal_sink():
    """A Processed Docs Output with NO incoming edge is an orphan sink — the engine would otherwise run
    it as an entry point with no data. _prepare must drop it (and keep a properly-wired one)."""
    from agentcore.services.idp.graph_native.runner import _prepare

    agent_data = {
        "nodes": [
            {"id": "DocumentUpload-1", "data": {"node": {"display_name": "Document Upload", "template": {}}}},
            {"id": "Extractor-1", "data": {"node": {"display_name": "AI Field Extractor"}}},
            {"id": "Sink-wired", "data": {"node": {"display_name": "Processed Docs Output"}}},
            {"id": "Sink-orphan", "data": {"node": {"display_name": "Processed Docs Output"}}},
        ],
        "edges": [
            {"source": "DocumentUpload-1", "target": "Extractor-1"},
            {"source": "Extractor-1", "target": "Sink-wired"},
        ],
    }
    out = _prepare(agent_data, "doc-1")
    ids = {n["id"] for n in out["nodes"]}
    assert "Sink-orphan" not in ids, "orphan sink (no incoming edge) was not dropped"
    assert {"DocumentUpload-1", "Extractor-1", "Sink-wired"} <= ids, "dropped a wired node"


def test_output_parser_prefers_the_branch_with_text():
    """A router stops one branch but it still forwards a no-text message; the Output Parser must pick the
    branch that actually carries text (OCR/native), not blindly branch A."""
    from agentcore.components.IDP.output_parser import IDPOutputParser

    # Scanned case: branch A = stopped digital path (no text), branch B = active OCR path (text).
    op = IDPOutputParser()
    op.branch_a = new_message(text="", document_id="d")
    op.branch_b = new_message(text="INVOICE TOTAL 600.00", document_id="d")
    assert op.merge().text == "INVOICE TOTAL 600.00", "picked the no-text branch"

    # Digital case: branch A = active native text, branch B = stopped (no text) → pick A.
    op2 = IDPOutputParser()
    op2.branch_a = new_message(text="NATIVE PDF TEXT", document_id="d")
    op2.branch_b = new_message(text="", document_id="d")
    assert op2.merge().text == "NATIVE PDF TEXT"


def test_classifier_tags_predicted_type_into_payload():
    """The Document Classifier writes predicted_type into the IDP payload (routers resolve it) AND the
    classification block (extractor multi-config routing) — the native-engine contract."""
    from agentcore.components.IDP.document_classifier import IDPDocumentClassifier

    out = IDPDocumentClassifier()._tag_message(
        new_message(text="INVOICE #1", document_id="d"), "invoice", 0.95, "has invoice number")
    assert get_payload(out).get("predicted_type") == "invoice"
    assert out.additional_kwargs.get("classification", {}).get("type") == "invoice"


def test_classifier_survives_structured_output_none():
    """_invoke_llm must never return None — a model's structured-output call can yield None, and
    classify() would then crash the whole run on result.predicted_type. It falls through to plain JSON
    parsing, or 'unknown'."""
    import asyncio

    from agentcore.components.IDP.document_classifier import IDPDocumentClassifier

    class _StructNone:
        async def ainvoke(self, m):
            return None

    class _LLM:
        def with_structured_output(self, s):
            return _StructNone()
        async def ainvoke(self, m):
            class _R:
                content = '{"predicted_type":"invoice","confidence":0.9}'
            return _R()

    r = asyncio.run(IDPDocumentClassifier()._invoke_llm(_LLM(), []))
    assert r is not None and r.predicted_type == "invoice"   # fell through to plain JSON, no crash


def test_extractor_extracts_each_chunk_and_merges(monkeypatch):
    """Long-doc: a LIST of chunks (from the Chunking Strategy) makes the AI Field Extractor extract each
    chunk with the Field Config and MERGE the per-chunk JSONs — so the Chunk Aggregator node isn't
    needed and the user tunes chunk size on the Chunking node."""
    import asyncio
    from uuid import uuid4

    from agentcore.components.IDP.llm_extractor import IDPLLMExtractor

    class _Cfg:
        def __init__(self):
            self.id = uuid4()

    class _Res:
        def first(self):
            return _Cfg()

    class _Sess:
        async def exec(self, s):
            return _Res()
        async def get(self, *a, **k):
            return None

    class _Scope:
        async def __aenter__(self):
            return _Sess()
        async def __aexit__(self, *a):
            return False

    monkeypatch.setattr("agentcore.services.deps.session_scope", lambda: _Scope(), raising=False)

    async def _fake(session, ocr_text, field_config_id, llm_model):
        if "100" in ocr_text:
            return {"headers": {"total": {"value": "100", "confidence": 0.9}}, "line_items": []}
        if "ACME" in ocr_text:
            return {"headers": {"vendor": {"value": "ACME", "confidence": 0.8}}, "line_items": []}
        return {"headers": {}, "line_items": []}

    monkeypatch.setattr("agentcore.services.idp.extraction.extract_named_config", _fake, raising=False)

    ex = IDPLLMExtractor()
    ex.config_name = "Invoice"; ex.config_names = []; ex.extraction_mode = "field_configuration"
    ex.llm = object(); ex.input_mode = "text"
    ex.document = [new_message(text="chunk one total 100", document_id="d"),
                   new_message(text="chunk two vendor ACME", document_id="d")]

    out = asyncio.run(ex.extract())
    p = get_payload(out)
    assert set(p.get("extracted", {}).get("headers", {})) == {"total", "vendor"}   # both chunks merged
    assert p.get("document_id") == "d"


def test_chunk_aggregator_merges_per_chunk_data():
    """The Chunk Aggregator combines per-chunk EXTRACTION Data (keep-highest-confidence). NOTE: it must
    be fed extracted Data — wiring Chunking → Aggregator directly (raw Message chunks) does not work."""
    from agentcore.schema.data import Data

    from agentcore.components.IDP.chunk_aggregator import IDPChunkAggregator

    a = IDPChunkAggregator()
    a.chunks_data = [
        Data(data={"vendor": "ACME", "vendor_confidence": 0.8}),
        Data(data={"vendor": "ACME Inc", "vendor_confidence": 0.95, "total": "100"}),
    ]
    a.dedup_strategy = "keep_highest_confidence"
    agg = a.aggregated_data()
    assert agg.data.get("vendor") == "ACME Inc"   # higher confidence wins
    assert agg.data.get("total") == "100"


def test_scan_corrector_output_flows_to_the_next_node(monkeypatch):
    """The corrected file the Scan Corrector writes is what the NEXT node reads — same contract as the
    Page Selector's sliced file. Proven end-to-end: the Scan Corrector's output payload points at the
    corrected file, and a downstream load (exactly what the extractor's vision path does:
    effective_payload -> load_bytes) returns the CORRECTED bytes, never the uploaded original."""
    import asyncio
    import io

    from reportlab.pdfgen import canvas

    from agentcore.components.IDP.llm_extractor import IDPLLMExtractor
    from agentcore.components.IDP.scan_corrector import IDPScanCorrector
    from agentcore.services.idp.graph_native.payload import effective_payload, load_bytes

    def _pdf(tag, n=2):
        b = io.BytesIO(); c = canvas.Canvas(b)
        for i in range(n):
            c.drawString(50, 700, f"{tag} PAGE {i + 1}"); c.showPage()
        c.save(); return b.getvalue()

    class _Store:
        def __init__(self):
            self.files = {}
            self.reads = []
        async def get_file(self, agent_id, file_name):
            self.reads.append(file_name)
            return self.files[(agent_id, file_name)]
        async def save_file(self, agent_id, file_name, data):
            self.files[(agent_id, file_name)] = data

    store = _Store()
    original = _pdf("ORIGINAL")
    store.files[("scope", "idp_d1.pdf")] = original
    monkeypatch.setattr("agentcore.services.deps.get_storage_service", lambda: store, raising=False)

    async def _run():
        # 1) Scan Corrector runs → writes a corrected file and repoints the flow at it.
        sc = IDPScanCorrector()
        sc.document = new_message(text="", document_id="doc-1", agent_scope="scope",
                                  file_name="idp_d1.pdf", file_type="pdf")
        sc.fix_skew = True; sc.skew_threshold = 0.5; sc.fix_rotation = False; sc.allowed_angles = "90,180,270"
        corrected_out = await sc.corrected_document()

        # 2) The AI Field Extractor receives that output as its `document` and loads bytes the way its
        #    vision path does: effective_payload(self, src) -> load_bytes(payload).
        extractor = IDPLLMExtractor()
        extractor.document = corrected_out
        payload = effective_payload(extractor, extractor.document)
        loaded = await load_bytes(payload)
        return corrected_out, payload, loaded

    corrected_out, payload, loaded = asyncio.run(_run())
    corrected_name = get_payload(corrected_out)["file_name"]

    assert corrected_name.startswith("corrected/"), corrected_name          # flow points at the corrected file
    assert payload["file_name"] == corrected_name                            # extractor sees the corrected file
    assert loaded == store.files[("scope", corrected_name)]                  # it loads the corrected bytes …
    assert loaded != original                                                # … NOT the uploaded original
    assert store.reads[-1] == corrected_name                                 # last storage read was the corrected file


def test_chunking_carries_working_set_into_every_chunk():
    from agentcore.components.IDP.chunking_strategy import IDPChunkingStrategy

    ck = IDPChunkingStrategy()
    ck.document = new_message(text="para one.\n\npara two.\n\npara three.", document_id="doc-1", agent_scope="scope")
    ck.chunking_method = "fixed_token"; ck.chunk_size_tokens = 5; ck.overlap_tokens = 1; ck.model_name = "gpt-4o"
    chunks = ck.chunks()
    assert len(chunks) >= 1
    assert all(get_payload(c).get("document_id") == "doc-1" for c in chunks)
    assert [get_payload(c).get("chunk_index") for c in chunks] == list(range(len(chunks)))
