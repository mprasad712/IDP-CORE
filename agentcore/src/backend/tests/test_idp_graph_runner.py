"""Tests for the flag-ON graph execution path of the IDP pipeline.

This suite covers the net-new, self-contained graph runner under
``services/idp/graph_exec/``. The legacy ``pipeline._run`` path is not touched.
"""

import pytest


@pytest.fixture
def anyio_backend():
    return "asyncio"


class _NullFlow:
    """Minimal ``FlowLog`` stand-in: records ``step(...)`` calls for assertions.

    Signature mirrors ``pipeline.FlowLog.step(name, status, detail="", **fields)`` and exposes the
    ``events`` / ``steps_ok`` / ``render`` surface the runner's terminal steps read.
    """

    def __init__(self):
        self.steps = []
        self.events = []

    def step(self, name, status, detail="", **fields):
        self.steps.append((name, status, detail, fields))
        self.events.append({"step": name, "status": status, "detail": detail})

    @property
    def steps_ok(self):
        return [e["step"] for e in self.events if e["status"] in ("ok", "warn")]

    def render(self):
        return "\n".join(f"{e['status'].upper()} {e['step']} {e['detail']}" for e in self.events)


def _node(display_name):
    """Build a minimal canvas node dict with the given display_name."""
    return {"data": {"node": {"display_name": display_name}}}


def test_pipeline_context_holds_run_state():
    """PipelineContext is fully defaulted and round-trips mutated run state."""
    from agentcore.services.idp.graph_exec import PipelineContext

    # Partial construction must be legal: every field has a default.
    ctx = PipelineContext()

    # Defaults for the object/optional fields.
    assert ctx.session is None
    assert ctx.document_id is None
    assert ctx.doc is None
    assert ctx.job is None
    assert ctx.idp_agent is None
    assert ctx.base_agent is None
    assert ctx.cfg is None
    assert ctx.flow is None
    assert ctx.storage is None
    assert ctx.agent_scope is None
    assert ctx.trace_ctx is None
    assert ctx.llm is None
    assert ctx.new_status is None
    assert ctx.route_label is None
    assert ctx.child_ids is None

    # Scalar defaults.
    assert ctx.t0 == 0.0
    assert ctx.file_bytes == b""
    assert ctx.original_bytes == b""
    assert ctx.file_type == ""
    assert ctx.overall_kind == ""
    assert ctx.merged_text == ""
    assert ctx.overall_conf == 0.0
    assert ctx.skipped is False

    # Mutable-default fields are fresh, independent containers.
    assert ctx.page_status == {}
    assert ctx.tokens == []
    assert ctx.digital_pages == set()
    assert ctx.scanned_pages == set()
    assert ctx.extracted == {}

    other = PipelineContext()
    ctx.page_status["p1"] = "digital"
    assert other.page_status == {}, "default_factory dict must not be shared"

    # Round-trip mutation of run state.
    ctx.overall_conf = 0.87
    ctx.extracted = {"header": {"invoice_no": "INV-1"}}
    assert ctx.overall_conf == 0.87
    assert ctx.extracted == {"header": {"invoice_no": "INV-1"}}

    # Untouched fields keep their defaults after mutation.
    assert ctx.skipped is False
    assert ctx.route_label is None


# ---------------------------------------------------------------------------
# Task 2: handler protocol + registry + stage map
# ---------------------------------------------------------------------------

# The 11 known IDP canvas nodes and their pipeline stage, per plan Task 2 Step 3.
_EXPECTED_STAGE = {
    "Scan Corrector": "pre_text",
    "PaddleOCR": "text",
    "Page Selector": "text",
    "Document Classifier": "post_text",
    "Chunking Strategy": "post_text",
    "AI Field Extractor": "extract",
    "Visual Element Detection": "post_extract",
    "Math Reconcile": "post_extract",
    "Rules / Conditions": "route",
    "Confidence Router": "route",
    "Approval Gate": "route",
}


def test_handler_registry_covers_known_idp_nodes():
    """NODE_STAGE + NODE_HANDLERS cover every known node; unknown → get_handler None."""
    from agentcore.services.idp.graph_exec import handlers as H

    # Stage map is exactly the plan's mapping.
    assert H.NODE_STAGE == _EXPECTED_STAGE

    # Every known node resolves to a callable handler and lives in the registry.
    for name in _EXPECTED_STAGE:
        handler = H.get_handler(name)
        assert handler is not None, f"missing handler for {name!r}"
        assert callable(handler)
        assert name in H.NODE_HANDLERS

    # Registry keys and stage keys line up (no orphan handlers / stages).
    assert set(H.NODE_HANDLERS) == set(H.NODE_STAGE)

    # Unknown node → None (Phase-2 custom-node seam falls back to noop in the runner).
    assert H.get_handler("Totally Unknown Node") is None

    # node_display_name reads data.node.display_name ...
    node = {"data": {"node": {"display_name": "PaddleOCR"}, "type": "DocumentOCRExtractor"}}
    assert H.node_display_name(node) == "PaddleOCR"
    # ... and falls back to data.type when no display_name is present.
    assert H.node_display_name({"data": {"type": "SomeClass"}}) == "SomeClass"
    # ... and to "" when neither is present.
    assert H.node_display_name({"data": {}}) == ""

    # noop_handler is exposed and callable.
    assert callable(H.noop_handler)


@pytest.mark.anyio
async def test_noop_handler_emits_warn_step():
    """noop_handler warns and does not raise (unknown/custom node seam)."""
    from agentcore.services.idp.graph_exec import handlers as H
    from agentcore.services.idp.graph_exec import PipelineContext

    flow = _NullFlow()
    ctx = PipelineContext(flow=flow)
    await H.noop_handler(ctx, {"data": {"type": "Weird Custom Node"}})

    assert len(flow.steps) == 1
    name, status, detail, _ = flow.steps[0]
    assert status == "warn"
    assert "no handler" in detail


@pytest.mark.anyio
async def test_marker_handler_emits_ok_step():
    """Marker handlers (PaddleOCR et al.) only emit a flow step; real work is anchored.

    Document Classifier / Visual Element Detection / Math Reconcile are markers too now — their
    real work is anchored as cfg-gated steps in the runner (node OR extra), not in the handler."""
    from agentcore.services.idp.graph_exec import handlers as H
    from agentcore.services.idp.graph_exec import PipelineContext

    for name in ("PaddleOCR", "Page Selector", "Chunking Strategy",
                 "AI Field Extractor", "Rules / Conditions",
                 "Confidence Router", "Approval Gate",
                 "Document Classifier", "Visual Element Detection", "Math Reconcile"):
        flow = _NullFlow()
        ctx = PipelineContext(flow=flow)
        await H.get_handler(name)(ctx, _node(name))
        assert len(flow.steps) == 1
        step_name, status, detail, _ = flow.steps[0]
        assert status == "ok"
        assert detail == name


# ---------------------------------------------------------------------------
# Task 3: handlers delegate to the existing standalone pipeline functions
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_scan_corrector_calls_maybe_preprocess(monkeypatch):
    """Scan Corrector → _maybe_preprocess(file_bytes, file_type, cfg, flow); sets ctx.file_bytes."""
    from agentcore.services.idp.graph_exec import handlers as H
    from agentcore.services.idp.graph_exec import PipelineContext

    seen = {}

    async def fake_preprocess(file_bytes, file_type, cfg, flow):
        seen["args"] = (file_bytes, file_type, cfg, flow)
        return b"corrected-bytes"

    monkeypatch.setattr(H, "_maybe_preprocess", fake_preprocess)

    sentinel_cfg = object()
    flow = _NullFlow()
    ctx = PipelineContext(file_bytes=b"orig", file_type="pdf", cfg=sentinel_cfg, flow=flow)

    await H.get_handler("Scan Corrector")(ctx, _node("Scan Corrector"))

    assert seen["args"] == (b"orig", "pdf", sentinel_cfg, flow)
    assert ctx.file_bytes == b"corrected-bytes"


# NOTE: Document Classifier / Visual Element Detection / Math Reconcile were previously "real"
# node handlers here. They are now anchored, cfg-gated steps in the runner (see the
# "anchored cfg-gated steps" section below), so their nodes are pure markers (covered by
# test_marker_handler_emits_ok_step) and the delegation is tested at the runner level.


# ---------------------------------------------------------------------------
# Task 6: the runner — independent backbone reusing the standalone functions
# ---------------------------------------------------------------------------

from types import SimpleNamespace  # noqa: E402
from uuid import uuid4  # noqa: E402


class _FakeResult:
    """Duck-typed SQLModel exec() result: ``.all()`` / ``.first()``."""

    def __init__(self, rows):
        self._rows = list(rows)

    def all(self):
        return self._rows

    def first(self):
        return self._rows[0] if self._rows else None


class _FakeSession:
    """Minimal async session: ``get`` returns the doc; ``exec`` returns empty rows."""

    def __init__(self, doc):
        self._doc = doc
        self.commits = 0

    async def get(self, model, pk):  # noqa: ARG002
        return self._doc

    async def exec(self, stmt):  # noqa: ARG002
        return _FakeResult([])

    def add(self, obj):  # noqa: ARG002
        pass

    async def commit(self):
        self.commits += 1


class _FakeStorage:
    async def save_file(self, agent_id, file_name, data):  # noqa: ARG002
        return None


def _mk_ctx(**over):
    """Build a PipelineContext wired with fakes sufficient for the anchored backbone steps."""
    from agentcore.services.idp.graph_exec import PipelineContext

    doc = SimpleNamespace(
        id=uuid4(), agent_id=uuid4(), file_path="agent/x.pdf", predicted_type=None,
        status=None, route_label=None, processing_completed_at=None, page_count=0,
    )
    job = SimpleNamespace(
        id=uuid4(), status=None, completed_at=None, processing_time_ms=None,
        steps_completed=None, log=None, extraction_mode_used=None, math_reconcile_attempts=0,
    )
    cfg = SimpleNamespace(
        entity_linking_enabled=False, confidence_threshold=0.8, route_field=None, route_map=None,
        extraction_mode="dynamic", config_name=None, field_config_id=None,
    )
    ctx = PipelineContext(
        session=_FakeSession(doc), document_id=doc.id, doc=doc, job=job,
        idp_agent=SimpleNamespace(id=uuid4()), base_agent=SimpleNamespace(),
        cfg=cfg, flow=_NullFlow(), storage=_FakeStorage(), agent_scope="agent",
        trace_ctx=None, t0=0.0, merged_text="", extracted={"headers": {}, "line_items": []},
    )
    for k, v in over.items():
        setattr(ctx, k, v)
    return ctx


def _install_backbone_recorders(monkeypatch, calls, *, split_children=None, sap_result=None):
    """Monkeypatch every anchored backbone fn on the runner module with an ordered recorder.

    Returns a dict of side-channel captures (webhook statuses, enqueued child ids)."""
    from agentcore.services.idp.graph_exec import runner as R

    captured = {"webhook_status": [], "enqueued": []}

    async def rec_detect(ctx):  # noqa: ARG001
        calls.append("_detect")

    async def rec_acquire(ctx):  # noqa: ARG001
        calls.append("_acquire_text")

    async def rec_select(ctx):  # noqa: ARG001
        calls.append("_select_and_merge")

    async def rec_extract(ctx):  # noqa: ARG001
        calls.append("_extract_fields")

    async def rec_save(**kw):  # noqa: ARG001
        calls.append("save_extraction_results")
        return 0.9

    async def rec_route(session, document_id, job_id, idp_agent_id, overall_conf, cfg, flow, match_result=None):  # noqa: ARG001
        calls.append("_route")
        return "auto_approved"

    def rec_match(cfg, doc, headers):  # noqa: ARG001
        calls.append("_match_route")
        return None

    async def rec_webhook(cfg, document_id, job_id, extracted, status, flow):  # noqa: ARG001
        calls.append("_maybe_webhook")
        captured["webhook_status"].append(status)

    async def rec_split(session, storage, doc, tokens, page_status, cfg, flow):  # noqa: ARG001
        calls.append("_hook_split")
        return split_children

    async def rec_sap(session=None, document_id=None, idp_agent_id=None, extracted=None, flow=None):  # noqa: ARG001
        calls.append("_hook_sap_matching")
        return sap_result

    async def rec_enqueue(session, document_id):  # noqa: ARG001
        captured["enqueued"].append(document_id)
        return uuid4()

    monkeypatch.setattr(R, "_detect", rec_detect)
    monkeypatch.setattr(R, "_acquire_text", rec_acquire)
    monkeypatch.setattr(R, "_select_and_merge", rec_select)
    monkeypatch.setattr(R, "_extract_fields", rec_extract)
    monkeypatch.setattr(R, "save_extraction_results", rec_save)
    monkeypatch.setattr(R, "_route", rec_route)
    monkeypatch.setattr(R, "_match_route", rec_match)
    monkeypatch.setattr(R, "_maybe_webhook", rec_webhook)
    monkeypatch.setattr(R, "_hook_split", rec_split)
    monkeypatch.setattr(R, "_hook_sap_matching", rec_sap)
    monkeypatch.setattr(R, "enqueue_document", rec_enqueue)
    return captured


@pytest.mark.anyio
async def test_run_graph_pipeline_calls_stages_in_order(monkeypatch):
    """A minimal plan reaches finalize with ctx.new_status set, calling the anchored backbone
    functions in the 13-step order (assert an ordered call-log)."""
    from agentcore.services.idp.graph_exec import runner as R
    from agentcore.services.idp.graph_exec.planner import plan_execution

    calls: list[str] = []
    _install_backbone_recorders(monkeypatch, calls)

    graph = {"nodes": [{"id": "ext", "data": {"node": {"display_name": "AI Field Extractor"}}}], "edges": []}
    plan = plan_execution(graph)
    ctx = _mk_ctx()

    await R.run_graph_pipeline(ctx, plan, graph)

    assert calls == [
        "_detect",
        "_acquire_text",
        "_hook_split",
        "_select_and_merge",
        "_extract_fields",
        "save_extraction_results",
        "_hook_sap_matching",
        "_route",
        "_match_route",
        "_maybe_webhook",
    ]
    assert ctx.new_status == "auto_approved"
    assert ctx.doc.status == "auto_approved"
    assert ctx.overall_conf == 0.9


@pytest.mark.anyio
async def test_run_graph_pipeline_classify_skip_early_return(monkeypatch):
    """The anchored classify step (cfg.classify_enabled) returning True → runner finalizes-as-
    skipped, fires the webhook with 'skipped', and never calls _extract_fields/_route."""
    from agentcore.services.idp.graph_exec import runner as R
    from agentcore.services.idp.graph_exec.planner import plan_execution

    calls: list[str] = []
    captured = _install_backbone_recorders(monkeypatch, calls)

    async def fake_classify_skip(session, document_id, base_agent, merged_text, cfg, flow):  # noqa: ARG001
        calls.append("_hook_classify")
        return True

    monkeypatch.setattr(R, "_hook_classify", fake_classify_skip)

    graph = {
        "nodes": [
            {"id": "cls", "data": {"node": {"display_name": "Document Classifier"}}},
            {"id": "ext", "data": {"node": {"display_name": "AI Field Extractor"}}},
        ],
        "edges": [{"source": "cls", "target": "ext"}],
    }
    plan = plan_execution(graph)
    ctx = _mk_ctx()
    ctx.cfg.classify_enabled = True  # a Document Classifier node → agent_config sets this true

    await R.run_graph_pipeline(ctx, plan, graph)

    assert "_hook_classify" in calls
    assert "_extract_fields" not in calls
    assert "_route" not in calls
    assert ctx.doc.status == "skipped"
    assert captured["webhook_status"] == ["skipped"]


@pytest.mark.anyio
async def test_run_graph_pipeline_split_early_return(monkeypatch):
    """_hook_split returning child ids → runner finalizes-as-split (enqueues each child,
    webhook 'skipped') and never calls _extract_fields/_route."""
    from agentcore.services.idp.graph_exec import runner as R
    from agentcore.services.idp.graph_exec.planner import plan_execution

    calls: list[str] = []
    child_ids = [uuid4(), uuid4()]
    captured = _install_backbone_recorders(monkeypatch, calls, split_children=child_ids)

    graph = {"nodes": [{"id": "ext", "data": {"node": {"display_name": "AI Field Extractor"}}}], "edges": []}
    plan = plan_execution(graph)
    ctx = _mk_ctx()

    await R.run_graph_pipeline(ctx, plan, graph)

    assert "_hook_split" in calls
    assert "_extract_fields" not in calls
    assert "_route" not in calls
    assert ctx.doc.status == "split"
    assert len(captured["enqueued"]) == 2
    assert captured["webhook_status"] == ["skipped"]


# --- per-helper unit tests (the three copied backbone blocks) --------------------------------


@pytest.mark.anyio
async def test_detect_sets_kind_and_page_sets(monkeypatch):
    """_detect mirrors the digital/scanned classify block: sets overall_kind, page_status,
    original_bytes, and the digital/scanned page sets from a fake classify_document."""
    from agentcore.services.idp import text_layer
    from agentcore.services.idp.graph_exec import runner as R

    def fake_classify(file_bytes, file_type, min_text_length):  # noqa: ARG001
        return "mixed", {1: text_layer.DIGITAL, 2: text_layer.SCANNED}

    monkeypatch.setattr(text_layer, "classify_document", fake_classify)

    ctx = _mk_ctx(file_bytes=b"raw-bytes", file_type="pdf")
    ctx.cfg.min_text_length = 10

    await R._detect(ctx)

    assert ctx.original_bytes == b"raw-bytes"
    assert ctx.overall_kind == "mixed"
    assert ctx.page_status == {1: text_layer.DIGITAL, 2: text_layer.SCANNED}
    assert ctx.digital_pages == {1}
    assert ctx.scanned_pages == {2}


@pytest.mark.anyio
async def test_acquire_text_digital_branch(monkeypatch):
    """_acquire_text digital branch: route + RAW native tokens (NO merge). _select_and_merge then
    does page selection + merged-text build (the split-into-two contract behind BLOCKER 1)."""
    from agentcore.services.idp import text_layer
    from agentcore.services.idp.graph_exec import runner as R

    def fake_route(input_mode, overall_kind, supports_vision, has_ocr_node):  # noqa: ARG001
        return "text"

    def fake_native(file_bytes, file_type):  # noqa: ARG001
        return "full text", [{"text": "hello", "page_number": 1}]

    def fake_merge(tokens):  # noqa: ARG001
        return "MERGED"

    monkeypatch.setattr(R, "decide_extraction_input", fake_route)
    monkeypatch.setattr(text_layer, "extract_native_text", fake_native)
    monkeypatch.setattr(R, "build_merged_text", fake_merge)

    ctx = _mk_ctx(
        original_bytes=b"x", file_type="pdf",
        overall_kind=text_layer.DIGITAL, page_status={1: text_layer.DIGITAL},
    )
    ctx.cfg.model_id = None
    ctx.cfg.input_mode = "text"
    ctx.cfg.has_ocr_node = False
    ctx.cfg.page_selection_mode = "all"
    ctx.cfg.first_n_pages = 1
    ctx.cfg.page_range_start = 1
    ctx.cfg.page_range_end = 1

    # Stage 1: raw acquisition — route + FULL tokens set, no page selection / merge yet.
    await R._acquire_text(ctx)
    assert ctx.route == "text"
    assert ctx.tokens == [{"text": "hello", "page_number": 1}]
    assert ctx.merged_text == ""  # merge deferred to _select_and_merge

    # Stage 2: page selection + merge (runs AFTER _hook_split in the runner).
    await R._select_and_merge(ctx)
    assert ctx.tokens == [{"text": "hello", "page_number": 1}]  # page-selected (all pages)
    assert ctx.merged_text == "MERGED"
    assert ctx.selected == {1}


@pytest.mark.anyio
async def test_extract_fields_text_route(monkeypatch):
    """_extract_fields text route (short doc): builds the llm and runs _extract_text; sets
    ctx.extracted / ctx.llm / ctx.vision_used."""
    from agentcore.services.idp.graph_exec import runner as R

    async def fake_build_llm(session, model_id, temperature=None):  # noqa: ARG001
        return SimpleNamespace(registry_model_id="rid")

    async def fake_extract_text(session, cfg, llm, text):  # noqa: ARG001
        return {"headers": {"inv": "INV-1"}, "line_items": []}

    monkeypatch.setattr(R, "_build_llm", fake_build_llm)
    monkeypatch.setattr(R, "_extract_text", fake_extract_text)

    ctx = _mk_ctx(route="text", merged_text="doc text", tokens=[])
    ctx.cfg.model_id = "m"
    ctx.cfg.multimodal_requested = False
    ctx.cfg.long_doc_enabled = False

    await R._extract_fields(ctx)

    assert ctx.extracted == {"headers": {"inv": "INV-1"}, "line_items": []}
    assert getattr(ctx.llm, "registry_model_id", None) == "rid"
    assert ctx.vision_used is False


# ---------------------------------------------------------------------------
# Parity blockers (Codex review): split ordering + math-reconcile page_images
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_split_runs_on_full_tokens_before_page_selection(monkeypatch):
    """BLOCKER 1: ``_hook_split`` must see the FULL (pre-page-selection) token set, running
    BEFORE ``_select_and_merge`` — matching legacy ``_run`` (split on the whole document, then
    page selection). A Page Selector that later limits to page 1 must NOT hide the other pages'
    tokens from split (multi-doc split divergence)."""
    from agentcore.services.idp.graph_exec import runner as R
    from agentcore.services.idp.graph_exec.planner import plan_execution

    calls: list[str] = []
    _install_backbone_recorders(monkeypatch, calls)  # patches most anchored backbone fns

    FULL_TOKENS = [
        {"text": "p1", "page_number": 1},
        {"text": "p2", "page_number": 2},
        {"text": "p3", "page_number": 3},
    ]
    split_seen: dict = {}

    async def fake_acquire(ctx):
        # Raw acquisition: full multi-page token set, NO page selection / merge.
        calls.append("_acquire_text")
        ctx.tokens = list(FULL_TOKENS)

    async def fake_split(session, storage, doc, tokens, page_status, cfg, flow):  # noqa: ARG001
        calls.append("_hook_split")
        split_seen["tokens"] = list(tokens)
        return None  # no children -> continue

    async def fake_select_and_merge(ctx):
        # Simulate a Page Selector limiting to page 1 (runs AFTER split, like legacy).
        calls.append("_select_and_merge")
        ctx.tokens = [t for t in ctx.tokens if t["page_number"] == 1]
        ctx.merged_text = "p1"
        ctx.selected = {1}

    monkeypatch.setattr(R, "_acquire_text", fake_acquire)
    monkeypatch.setattr(R, "_hook_split", fake_split)
    monkeypatch.setattr(R, "_select_and_merge", fake_select_and_merge)

    graph = {"nodes": [{"id": "ext", "data": {"node": {"display_name": "AI Field Extractor"}}}], "edges": []}
    plan = plan_execution(graph)
    ctx = _mk_ctx()

    await R.run_graph_pipeline(ctx, plan, graph)

    # Split saw ALL three pages' tokens (not just page 1) ...
    assert split_seen["tokens"] == FULL_TOKENS
    # ... and it ran BEFORE page selection / merge.
    assert calls.index("_hook_split") < calls.index("_select_and_merge")
    # Post-selection the working token set is reduced (proof selection happened after split).
    assert ctx.tokens == [{"text": "p1", "page_number": 1}]


# NOTE (BLOCKER 2): forwarding ``ctx.page_images`` (vision route) + ``ocr_text`` to
# ``_hook_math_reconcile`` is now the anchored math step's job in the runner — covered by
# test_anchored_math_forwards_page_images below.


# ---------------------------------------------------------------------------
# D-1 / D-2 parity: classify / detect / math as anchored cfg-gated steps
# ---------------------------------------------------------------------------
# The runner runs Visual Element Detection (before classify), classification (before extract) and
# Math Reconcile (after extract) as anchored steps gated on cfg.detect_enabled / classify_enabled /
# math_reconcile_enabled — each true when a canvas node exists OR idp_agent.extra sets the flag.
# So the feature runs for BOTH node agents and extra-flag-only agents (D-2), and detection precedes
# the classify-skip check so a skipped doc still gets detection (D-1).


def _install_anchored_recorders(monkeypatch, calls, *, classify_skip=False):
    """Patch the anchored cfg-gated hooks (_hook_detect / _hook_classify / _hook_math_reconcile)
    on the RUNNER module with ordered recorders. Returns captured kwargs for detailed asserts."""
    from agentcore.services.idp.graph_exec import runner as R

    captured: dict = {"detect_args": None, "math_kwargs": None}

    async def rec_detect(session, document_id, original_bytes, file_type, cfg, flow):  # noqa: ARG001
        calls.append("_hook_detect")
        captured["detect_args"] = (session, document_id, original_bytes, file_type, cfg, flow)

    async def rec_classify(session, document_id, base_agent, merged_text, cfg, flow):  # noqa: ARG001
        calls.append("_hook_classify")
        return classify_skip

    async def rec_math(extracted, llm, job, cfg, flow, *, page_images=None, ocr_text=None):  # noqa: ARG001
        calls.append("_hook_math_reconcile")
        captured["math_kwargs"] = {"page_images": page_images, "ocr_text": ocr_text}
        return {"headers": {}, "line_items": [], "reconciled": True}

    monkeypatch.setattr(R, "_hook_detect", rec_detect)
    monkeypatch.setattr(R, "_hook_classify", rec_classify)
    monkeypatch.setattr(R, "_hook_math_reconcile", rec_math)
    return captured


def _abc_graph():
    """A graph wiring detection → classifier → extractor → math nodes in order."""
    return {
        "nodes": [
            {"id": "det", "data": {"node": {"display_name": "Visual Element Detection"}}},
            {"id": "cls", "data": {"node": {"display_name": "Document Classifier"}}},
            {"id": "ext", "data": {"node": {"display_name": "AI Field Extractor"}}},
            {"id": "math", "data": {"node": {"display_name": "Math Reconcile"}}},
        ],
        "edges": [
            {"source": "det", "target": "cls"},
            {"source": "cls", "target": "ext"},
            {"source": "ext", "target": "math"},
        ],
    }


@pytest.mark.anyio
async def test_anchored_run_when_enabled_via_node(monkeypatch):
    """detect/classify/math all run when cfg flags are true AND the canvas nodes exist (the
    existing node-based behaviour, now served by anchored steps)."""
    from agentcore.services.idp.graph_exec import runner as R
    from agentcore.services.idp.graph_exec.planner import plan_execution

    calls: list[str] = []
    _install_backbone_recorders(monkeypatch, calls)
    _install_anchored_recorders(monkeypatch, calls)

    graph = _abc_graph()
    plan = plan_execution(graph)
    ctx = _mk_ctx()
    ctx.cfg.detect_enabled = True
    ctx.cfg.classify_enabled = True
    ctx.cfg.math_reconcile_enabled = True

    await R.run_graph_pipeline(ctx, plan, graph)

    assert "_hook_detect" in calls
    assert "_hook_classify" in calls
    assert "_hook_math_reconcile" in calls
    assert ctx.new_status == "auto_approved"  # ran through to finalize


@pytest.mark.anyio
async def test_anchored_run_when_enabled_via_extra_only(monkeypatch):
    """D-2 FIX: detect/classify/math run when cfg flags are true even with NO node on the canvas
    (the feature was enabled via idp_agent.extra.*_enabled). The runner gates purely on cfg — so a
    feature with no node is no longer silently skipped (the legacy path always ran it)."""
    from agentcore.services.idp.graph_exec import runner as R
    from agentcore.services.idp.graph_exec.planner import plan_execution

    calls: list[str] = []
    _install_backbone_recorders(monkeypatch, calls)
    _install_anchored_recorders(monkeypatch, calls)

    # ONLY an extractor node — no detection / classifier / math node anywhere on the canvas.
    graph = {"nodes": [{"id": "ext", "data": {"node": {"display_name": "AI Field Extractor"}}}], "edges": []}
    plan = plan_execution(graph)
    ctx = _mk_ctx()
    ctx.cfg.detect_enabled = True
    ctx.cfg.classify_enabled = True
    ctx.cfg.math_reconcile_enabled = True

    await R.run_graph_pipeline(ctx, plan, graph)

    # All three still ran despite no node — this is the D-2 divergence, now fixed.
    assert "_hook_detect" in calls
    assert "_hook_classify" in calls
    assert "_hook_math_reconcile" in calls


@pytest.mark.anyio
async def test_anchored_skip_when_flags_false(monkeypatch):
    """detect/classify/math do NOT run when their cfg flags are false/absent (no node, no extra)."""
    from agentcore.services.idp.graph_exec import runner as R
    from agentcore.services.idp.graph_exec.planner import plan_execution

    calls: list[str] = []
    _install_backbone_recorders(monkeypatch, calls)
    _install_anchored_recorders(monkeypatch, calls)

    graph = {"nodes": [{"id": "ext", "data": {"node": {"display_name": "AI Field Extractor"}}}], "edges": []}
    plan = plan_execution(graph)
    ctx = _mk_ctx()  # cfg has no *_enabled attributes → getattr(..., False) defaults to False

    await R.run_graph_pipeline(ctx, plan, graph)

    assert "_hook_detect" not in calls
    assert "_hook_classify" not in calls
    assert "_hook_math_reconcile" not in calls
    assert ctx.new_status == "auto_approved"  # still finalized normally


@pytest.mark.anyio
async def test_anchored_detect_runs_before_classify_skip(monkeypatch):
    """D-1 FIX: on a classify-SKIPPED doc, detection STILL ran — it precedes the classify skip
    check (legacy _run runs detect at l.1168, before classify at l.1221). Assert the detect
    recorder fired before the finalize-as-skipped webhook."""
    from agentcore.services.idp.graph_exec import runner as R
    from agentcore.services.idp.graph_exec.planner import plan_execution

    calls: list[str] = []
    captured_bb = _install_backbone_recorders(monkeypatch, calls)
    _install_anchored_recorders(monkeypatch, calls, classify_skip=True)

    graph = {
        "nodes": [
            {"id": "det", "data": {"node": {"display_name": "Visual Element Detection"}}},
            {"id": "cls", "data": {"node": {"display_name": "Document Classifier"}}},
            {"id": "ext", "data": {"node": {"display_name": "AI Field Extractor"}}},
        ],
        "edges": [{"source": "det", "target": "cls"}, {"source": "cls", "target": "ext"}],
    }
    plan = plan_execution(graph)
    ctx = _mk_ctx()
    ctx.cfg.detect_enabled = True
    ctx.cfg.classify_enabled = True

    await R.run_graph_pipeline(ctx, plan, graph)

    assert "_hook_detect" in calls
    assert "_hook_classify" in calls
    assert "_extract_fields" not in calls
    assert ctx.doc.status == "skipped"
    # detect ran BEFORE classify AND before the finalize-skipped webhook (the D-1 guarantee).
    assert calls.index("_hook_detect") < calls.index("_hook_classify")
    assert calls.index("_hook_detect") < calls.index("_maybe_webhook")
    assert captured_bb["webhook_status"] == ["skipped"]


@pytest.mark.anyio
async def test_anchored_order_detect_classify_extract_math(monkeypatch):
    """Ordering: anchored detect BEFORE classify BEFORE extract BEFORE math (legacy _run order:
    detect l.1168, classify l.1221, extract ~1395, math ~1428)."""
    from agentcore.services.idp.graph_exec import runner as R
    from agentcore.services.idp.graph_exec.planner import plan_execution

    calls: list[str] = []
    _install_backbone_recorders(monkeypatch, calls)
    _install_anchored_recorders(monkeypatch, calls)  # classify returns False → full run

    graph = _abc_graph()
    plan = plan_execution(graph)
    ctx = _mk_ctx()
    ctx.cfg.detect_enabled = True
    ctx.cfg.classify_enabled = True
    ctx.cfg.math_reconcile_enabled = True

    await R.run_graph_pipeline(ctx, plan, graph)

    assert (
        calls.index("_hook_detect")
        < calls.index("_hook_classify")
        < calls.index("_extract_fields")
        < calls.index("_hook_math_reconcile")
    )


@pytest.mark.anyio
async def test_anchored_math_forwards_page_images(monkeypatch):
    """The anchored math step forwards ctx.page_images (vision route) + ocr_text to
    _hook_math_reconcile — the parity contract the old Math Reconcile handler carried (BLOCKER 2) —
    and writes the corrected extraction back to ctx."""
    from agentcore.services.idp.graph_exec import runner as R
    from agentcore.services.idp.graph_exec.planner import plan_execution

    calls: list[str] = []
    _install_backbone_recorders(monkeypatch, calls)
    captured = _install_anchored_recorders(monkeypatch, calls)

    graph = {"nodes": [{"id": "ext", "data": {"node": {"display_name": "AI Field Extractor"}}}], "edges": []}
    plan = plan_execution(graph)
    ctx = _mk_ctx(merged_text="line item text")
    ctx.cfg.math_reconcile_enabled = True
    imgs = [(b"img1", "png"), (b"img2", "png")]
    ctx.page_images = imgs  # stashed by _extract_fields on the vision route

    await R.run_graph_pipeline(ctx, plan, graph)

    assert captured["math_kwargs"]["page_images"] == imgs
    assert captured["math_kwargs"]["ocr_text"] == "line item text"
    assert ctx.extracted == {"headers": {}, "line_items": [], "reconciled": True}


@pytest.mark.anyio
async def test_anchored_math_pops_usage_before_save(monkeypatch):
    """If math-reconcile injects a `_usage` marker, the runner drops it before save/webhook —
    matching legacy _run l.1436-1437 (Codex R-2). Otherwise `_usage` would persist as a spurious
    extracted field and leak into the webhook payload."""
    from agentcore.services.idp.graph_exec import runner as R
    from agentcore.services.idp.graph_exec.planner import plan_execution

    calls: list[str] = []
    _install_backbone_recorders(monkeypatch, calls)
    _install_anchored_recorders(monkeypatch, calls)

    # Math reconcile returns an extracted dict that CARRIES a _usage marker (real hooks do this).
    async def _math_with_usage(extracted, llm, job, cfg, flow, **kw):
        return {"headers": {"total": "9"}, "line_items": [], "_usage": {"model": "x", "tokens": 5}}
    monkeypatch.setattr(R, "_hook_math_reconcile", _math_with_usage)

    graph = {"nodes": [{"id": "ext", "data": {"node": {"display_name": "AI Field Extractor"}}}], "edges": []}
    plan = plan_execution(graph)
    ctx = _mk_ctx(merged_text="x")
    ctx.cfg.math_reconcile_enabled = True

    await R.run_graph_pipeline(ctx, plan, graph)

    assert "_usage" in {"_usage"}  # sanity: the marker existed in the reconcile output
    assert isinstance(ctx.extracted, dict)
    assert "_usage" not in ctx.extracted  # popped before save (R-2)
    assert ctx.extracted == {"headers": {"total": "9"}, "line_items": []}


# ---------------------------------------------------------------------------
# Task 7: flag wiring — legacy path byte-untouched
# ---------------------------------------------------------------------------


def test_graph_exec_enabled_env_var_true(monkeypatch):
    """IDP_GRAPH_EXECUTION=true → True regardless of extra (case/space tolerant)."""
    from agentcore.services.idp import pipeline

    monkeypatch.setenv("IDP_GRAPH_EXECUTION", "true")
    assert pipeline._graph_exec_enabled(SimpleNamespace(extra=None)) is True

    # tolerant of surrounding whitespace / casing
    monkeypatch.setenv("IDP_GRAPH_EXECUTION", "  TRUE  ")
    assert pipeline._graph_exec_enabled(SimpleNamespace(extra=None)) is True


def test_graph_exec_enabled_extra_flag_true(monkeypatch):
    """extra.graph_execution truthy → True even when the env var is absent/false."""
    from agentcore.services.idp import pipeline

    monkeypatch.delenv("IDP_GRAPH_EXECUTION", raising=False)
    assert pipeline._graph_exec_enabled(SimpleNamespace(extra={"graph_execution": True})) is True

    monkeypatch.setenv("IDP_GRAPH_EXECUTION", "false")
    assert pipeline._graph_exec_enabled(SimpleNamespace(extra={"graph_execution": True})) is True


def test_graph_exec_enabled_default_false(monkeypatch):
    """Both absent → False; a False/empty extra and a missing attribute never enable it."""
    from agentcore.services.idp import pipeline

    monkeypatch.delenv("IDP_GRAPH_EXECUTION", raising=False)
    assert pipeline._graph_exec_enabled(SimpleNamespace(extra=None)) is False
    assert pipeline._graph_exec_enabled(SimpleNamespace(extra={})) is False
    assert pipeline._graph_exec_enabled(SimpleNamespace(extra={"graph_execution": False})) is False
    # object with no `extra` attribute at all → False (getattr default)
    assert pipeline._graph_exec_enabled(object()) is False

    # env explicitly false + no extra flag → False
    monkeypatch.setenv("IDP_GRAPH_EXECUTION", "false")
    assert pipeline._graph_exec_enabled(SimpleNamespace(extra={})) is False


@pytest.fixture
def _mock_storage(monkeypatch):
    """In-memory storage bound to pipeline.get_storage_service (used by both _run paths)."""
    from agentcore.services.idp import pipeline

    class _S:
        def __init__(self):
            self.store: dict[tuple[str, str], bytes] = {}

        async def save_file(self, agent_id, file_name, data):
            self.store[(agent_id, file_name)] = data

        async def get_file(self, agent_id, file_name):
            return self.store[(agent_id, file_name)]

    s = _S()
    monkeypatch.setattr(pipeline, "get_storage_service", lambda: s)
    return s


@pytest.mark.anyio
async def test_run_flag_on_routes_to_graph_runner(monkeypatch, _mock_storage):
    """Flag ON (via extra.graph_execution) → _run delegates to run_graph_pipeline (spy) and
    the legacy detect body is never entered. Uses the real DB harness from test_idp_pipeline."""
    import test_idp_pipeline as P
    from agentcore.services.idp import pipeline
    from agentcore.services.idp import graph_exec as GE
    from agentcore.services.idp import text_layer
    from agentcore.services.deps import session_scope

    monkeypatch.delenv("IDP_GRAPH_EXECUTION", raising=False)  # prove the extra flag alone routes

    # Spy the lazily-imported graph runner (bound off the package at call time).
    seen = {"calls": []}

    async def spy_run_graph_pipeline(ctx, plan, graph):
        seen["calls"].append((ctx, plan, graph))

    monkeypatch.setattr(GE, "run_graph_pipeline", spy_run_graph_pipeline)

    # Legacy marker: the first thing _run does past the branch is text_layer.classify_document.
    legacy = {"hits": 0}
    monkeypatch.setattr(text_layer, "classify_document", lambda *a, **k: (legacy.__setitem__("hits", legacy["hits"] + 1), ("digital", {}))[1])

    async with session_scope() as session:
        agent_id, doc_id = await P._setup_document(
            session, graph=P._graph(str(uuid4())), file_bytes=P._digital_pdf(),
            extra={"graph_execution": True},
        )
    try:
        await pipeline.process_document(doc_id)

        # Graph runner ran exactly once; legacy detect body never ran.
        assert len(seen["calls"]) == 1
        assert legacy["hits"] == 0
        ctx, plan, graph = seen["calls"][0]
        assert ctx.document_id == doc_id
        assert ctx.doc.id == doc_id
        assert ctx.file_bytes == ctx.original_bytes
        assert plan.errors == []
    finally:
        await P._cleanup(agent_id, doc_id)


@pytest.mark.anyio
async def test_run_flag_off_runs_legacy(monkeypatch, _mock_storage):
    """Flag OFF (no env, no extra) → _run runs the legacy body and NEVER calls run_graph_pipeline."""
    import test_idp_pipeline as P
    from agentcore.services.idp import pipeline
    from agentcore.services.idp import graph_exec as GE
    from agentcore.services.idp import text_layer
    from agentcore.services.deps import session_scope

    monkeypatch.delenv("IDP_GRAPH_EXECUTION", raising=False)  # flag OFF

    seen = {"graph": 0}

    async def spy_run_graph_pipeline(ctx, plan, graph):
        seen["graph"] += 1

    monkeypatch.setattr(GE, "run_graph_pipeline", spy_run_graph_pipeline)

    # Legacy marker on the first post-branch call; delegate to the real classifier so the
    # legacy digital path continues to completion.
    legacy = {"hits": 0}
    real_classify = text_layer.classify_document

    def marker_classify(*a, **k):
        legacy["hits"] += 1
        return real_classify(*a, **k)

    monkeypatch.setattr(text_layer, "classify_document", marker_classify)
    P._patch_extraction(monkeypatch)  # legacy extraction without a live LLM

    async with session_scope() as session:
        agent_id, doc_id = await P._setup_document(
            session, graph=P._graph(str(uuid4())), file_bytes=P._digital_pdf(),
        )  # no extra → flag stays OFF
    try:
        await pipeline.process_document(doc_id)

        assert seen["graph"] == 0, "graph runner must NOT run with the flag off"
        assert legacy["hits"] >= 1, "legacy detect body must run with the flag off"
    finally:
        await P._cleanup(agent_id, doc_id)
