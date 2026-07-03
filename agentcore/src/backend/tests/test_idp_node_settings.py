# tests/test_idp_node_settings.py
import pytest
from uuid import uuid4
from types import SimpleNamespace
from agentcore.services.idp import pipeline as pl


@pytest.fixture
def anyio_backend():
    return "asyncio"


class _NullFlow:
    steps_ok = 0
    events = []
    def step(self, *a, **k): pass


def _cfg(**over):
    base = dict(
        canvas_rules=[], rules_operator="AND", default_rule_action="auto_approve",
        confidence_threshold=0.8, confidence_router_present=False,
        approve_value="auto_approve", approval_field="rule_action",
    )
    base.update(over)
    return SimpleNamespace(**base)


@pytest.mark.anyio
async def test_confidence_router_gates_low_confidence_before_rules(monkeypatch):
    # A router IS present; overall_conf below threshold must route to review regardless of rules.
    called = {"evaluate": False}
    def _fake_eval(**kw):
        called["evaluate"] = True
        return {"action": "auto_approve", "matched_group": 1, "failed_conditions": []}
    monkeypatch.setattr(pl, "evaluate_rules", _fake_eval)
    cfg = _cfg(confidence_router_present=True, confidence_threshold=0.8,
               canvas_rules=[{"field": "total_amount", "op": "gte", "value": "0"}])
    status = await pl._route(session=None, document_id=uuid4(), job_id=uuid4(),
                             idp_agent_id=uuid4(), overall_conf=0.5, cfg=cfg, flow=_NullFlow())
    assert status == "pending_review"
    assert called["evaluate"] is False   # gate short-circuits before the rules engine


@pytest.mark.anyio
async def test_no_confidence_router_leaves_rules_path_unchanged(monkeypatch):
    monkeypatch.setattr(pl, "evaluate_rules",
        lambda **kw: {"action": "auto_approve", "matched_group": 1, "failed_conditions": []})
    # No router -> low confidence still runs rules (the OLD behavior), so a passing rule auto-approves.
    async def _fake_exec(*a, **k):
        class _R:  # session.exec(...).all() -> []
            def all(self_inner): return []
        return _R()
    sess = SimpleNamespace(exec=_fake_exec)
    cfg = _cfg(confidence_router_present=False,
               canvas_rules=[{"field": "total_amount", "op": "gte", "value": "0"}])
    status = await pl._route(session=sess, document_id=uuid4(), job_id=uuid4(),
                             idp_agent_id=uuid4(), overall_conf=0.5, cfg=cfg, flow=_NullFlow())
    assert status == "auto_approved"


@pytest.mark.anyio
async def test_confidence_router_high_confidence_falls_through_to_rules(monkeypatch):
    monkeypatch.setattr(pl, "evaluate_rules",
        lambda **kw: {"action": "auto_approve", "matched_group": 1, "failed_conditions": []})
    async def _fake_exec(*a, **k):
        class _R:
            def all(self_inner): return []
        return _R()
    sess = SimpleNamespace(exec=_fake_exec)
    cfg = _cfg(confidence_router_present=True, confidence_threshold=0.8,
               canvas_rules=[{"field": "total_amount", "op": "gte", "value": "0"}])
    status = await pl._route(session=sess, document_id=uuid4(), job_id=uuid4(),
                             idp_agent_id=uuid4(), overall_conf=0.95, cfg=cfg, flow=_NullFlow())
    assert status == "auto_approved"   # above threshold -> rules run -> pass


# ─────────────────────────── Task 2: Approval Gate approval_field ───────────────────────────

def _hdr(field_name, value):
    return SimpleNamespace(field_name=field_name, extracted_value=value, reviewed_value=None)


def _exec_returning(headers):
    # session.exec(...).all(): the header query -> headers; every other query (agent-rules on the
    # no-rules path, line_items, detected) -> []. Keyed on the queried table (not call order) because
    # the no-rules path issues the IdpAgentRule query BEFORE the headers query.
    async def _fake_exec(stmt=None, *a, **k):
        rows = headers if "idp_extracted_headers" in str(stmt) else []
        class _R:
            def all(self_inner): return rows
        return _R()
    return _fake_exec


@pytest.mark.anyio
async def test_approval_field_named_header_with_rules(monkeypatch):
    monkeypatch.setattr(pl, "evaluate_rules",
        lambda **kw: {"action": "pending_review", "matched_group": None, "failed_conditions": []})
    sess = SimpleNamespace(exec=_exec_returning([_hdr("status", "APPROVED")]))
    cfg = _cfg(confidence_router_present=False, approval_field="status", approve_value="APPROVED",
               canvas_rules=[{"field": "total_amount", "op": "gte", "value": "0"}])
    status = await pl._route(session=sess, document_id=uuid4(), job_id=uuid4(),
                             idp_agent_id=uuid4(), overall_conf=0.95, cfg=cfg, flow=_NullFlow())
    assert status == "auto_approved"   # rules said pending, approval_field 'status' says APPROVED


@pytest.mark.anyio
async def test_approval_field_named_header_NO_rules(monkeypatch):
    # No rules at all -> approval_field must STILL be honored (the Codex-flagged bug).
    sess = SimpleNamespace(exec=_exec_returning([_hdr("status", "APPROVED")]))
    cfg = _cfg(confidence_router_present=False, approval_field="status", approve_value="APPROVED",
               canvas_rules=[], default_rule_action="pending_review")
    status = await pl._route(session=sess, document_id=uuid4(), job_id=uuid4(),
                             idp_agent_id=uuid4(), overall_conf=0.95, cfg=cfg, flow=_NullFlow())
    assert status == "auto_approved"   # default action is pending, but approval_field says APPROVED


# ─────────────────────────── Task 3: Scan Corrector skew_threshold ───────────────────────────

import numpy as np  # noqa: E402
from agentcore.services.database.models.idp.config import IdpAgent  # noqa: E402
from agentcore.services.idp.agent_config import resolve_pipeline_config  # noqa: E402
from agentcore.services.idp.pre_processing import deskew_image  # noqa: E402


def _tilted_text_image(angle_deg: float):
    import cv2
    img = np.full((200, 400, 3), 255, np.uint8)
    cv2.putText(img, "INVOICE 12345", (20, 100), cv2.FONT_HERSHEY_SIMPLEX, 1.2, (0, 0, 0), 3)
    M = cv2.getRotationMatrix2D((200, 100), angle_deg, 1.0)
    return cv2.warpAffine(img, M, (400, 200), borderValue=(255, 255, 255))


def test_deskew_skips_below_threshold():
    img = _tilted_text_image(0.3)
    _, applied = deskew_image(img, skew_threshold=0.5)
    assert applied == 0.0   # 0.3 < 0.5 -> no correction


def test_deskew_applies_above_threshold():
    img = _tilted_text_image(5.0)
    _, applied = deskew_image(img, skew_threshold=0.5)
    assert abs(applied) >= 0.5   # a real tilt above threshold is corrected


def _agent_with_nodes(nodes):
    class MockBaseAgent:
        def __init__(self):
            self.id = uuid4()
            self.data = {"nodes": nodes}
    base = MockBaseAgent()
    idp = IdpAgent(
        agent_id=base.id,
        extraction_mode="dynamic_prompting",
        default_rule_action="pending_review",
        extra={},
    )
    return base, idp


@pytest.mark.anyio
async def test_skew_threshold_resolver_defaults():
    # No Scan Corrector node -> historical effective floor (0.1) is preserved (backward-compat).
    base, idp = _agent_with_nodes([])
    cfg = await resolve_pipeline_config(None, idp, base)
    assert cfg.skew_threshold == 0.1

    # Scan Corrector node present with an unset threshold -> the node's default (0.5).
    scan = {"data": {"node": {"display_name": "Scan Corrector", "template": {}}}}
    base2, idp2 = _agent_with_nodes([scan])
    cfg2 = await resolve_pipeline_config(None, idp2, base2)
    assert cfg2.skew_threshold == 0.5


# ─────────────────────────── Task 4: Scan Corrector allowed_angles ───────────────────────────

from agentcore.services.idp.agent_config import _parse_angles  # noqa: E402
from agentcore.services.idp import pre_processing as pp  # noqa: E402


def test_parse_angles_accepts_str_and_list():
    assert _parse_angles("90,180") == [90, 180]
    assert _parse_angles([180]) == [180]            # list value (was silently dropped -> default)
    assert _parse_angles((90, 270)) == [90, 270]    # tuple
    assert _parse_angles("") == [90, 180, 270]       # empty -> default
    assert _parse_angles(None) == [90, 180, 270]


def test_rotation_respects_allowed_angles(monkeypatch):
    import cv2
    monkeypatch.setattr(pp, "detect_rotation_angle", lambda img: 90)  # model says 90
    img = np.full((100, 60, 3), 255, np.uint8)                        # portrait
    _, encoded = cv2.imencode(".png", img)
    out_bytes, applied = pp._sync_correct_rotation(encoded.tobytes(), "png", allowed_angles=[180])
    assert applied == 0    # 90 not in {180} -> no rotation


def test_rotation_applies_allowed_angle(monkeypatch):
    import cv2
    monkeypatch.setattr(pp, "detect_rotation_angle", lambda img: 90)
    img = np.full((100, 60, 3), 255, np.uint8)
    _, encoded = cv2.imencode(".png", img)
    _, applied = pp._sync_correct_rotation(encoded.tobytes(), "png", allowed_angles=[90, 180, 270])
    assert applied == 90


# ─────────────────────── Task 5: AI Field Extractor config_names (multi-type routing) ───────────────────────

from agentcore.services.idp.agent_config import _build_config_name_map  # noqa: E402


@pytest.mark.anyio
async def test_config_name_map_builds_from_names(monkeypatch):
    inv, po = uuid4(), uuid4()

    async def _fake_lookup(session, org_id, name):
        return {"invoice": inv, "purchase order": po}.get(name.strip().lower())

    monkeypatch.setattr("agentcore.services.idp.agent_config._lookup_config_id_by_name", _fake_lookup)
    m = await _build_config_name_map(session=None, org_id=None, names=["Invoice", "Purchase Order", "Ghost"])
    assert m == {"invoice": inv, "purchase order": po}   # unknown name dropped


class _FakeScope:
    """Async context manager mirroring session_scope() -> yields a fake session."""
    def __init__(self, sess):
        self._sess = sess

    async def __aenter__(self):
        return self._sess

    async def __aexit__(self, *a):
        return False


class _FakeSess:
    """Minimal session supporting session.get(...) used by _hook_classify's main-session sync."""
    def __init__(self):
        self._doc = SimpleNamespace(predicted_type=None)

    async def get(self, model, ident):
        return self._doc


@pytest.mark.anyio
async def test_config_names_routes_matched_and_skips_unmatched(monkeypatch):
    inv = uuid4()

    async def _fake_classify(*a, **k):
        return {"predicted_type": _fake_classify.pt, "confidence": 0.9}
    _fake_classify.pt = "invoice"
    monkeypatch.setattr("agentcore.services.idp.classification.classify_and_persist", _fake_classify)
    # _hook_classify opens an isolated session via session_scope() — yield a fake.
    monkeypatch.setattr(pl, "session_scope", lambda: _FakeScope(_FakeSess()))

    # matched: classifier predicts "invoice", which is in the map -> field_config_id set, not skipped.
    cfg = _cfg(classify_enabled=True, classify_auto_select=True, classify_threshold=0.7,
               classify_doc_types=[], config_name_map={"invoice": inv},
               field_config_id=None, extraction_mode="named_config")
    skip = await pl._hook_classify(session=_FakeSess(), document_id=uuid4(),
                                   base_agent=SimpleNamespace(org_id=None),
                                   merged_text="INVOICE", cfg=cfg, flow=_NullFlow())
    assert skip is False and cfg.field_config_id == inv

    # unmatched: classifier predicts "memo", not in the map -> document skipped.
    _fake_classify.pt = "memo"
    cfg2 = _cfg(classify_enabled=True, classify_auto_select=True, classify_threshold=0.7,
                classify_doc_types=[], config_name_map={"invoice": inv},
                field_config_id=None, extraction_mode="named_config")
    skip2 = await pl._hook_classify(session=_FakeSess(), document_id=uuid4(),
                                    base_agent=SimpleNamespace(org_id=None),
                                    merged_text="MEMO", cfg=cfg2, flow=_NullFlow())
    assert skip2 is True


# ─────────────────────────── Task 6: Webhook Output ───────────────────────────


@pytest.mark.anyio
async def test_maybe_webhook_posts_payload(monkeypatch):
    sent = {}

    class _Resp:
        status_code = 200
        def raise_for_status(self):
            pass

    class _Client:
        def __init__(self, *a, **k):
            pass
        async def __aenter__(self):
            return self
        async def __aexit__(self, *a):
            return False
        async def request(self, method, url, json=None, headers=None):
            sent.update(method=method, url=url, json=json, headers=headers)
            return _Resp()

    monkeypatch.setattr(pl.httpx, "AsyncClient", _Client)
    cfg = _cfg(webhook_url="https://example.com/ingest", webhook_method="POST",
               webhook_headers={"X-Api-Key": "k"}, webhook_include_metadata=True)
    did, jid = uuid4(), uuid4()
    await pl._maybe_webhook(cfg, did, jid, {"headers": {"total": "9"}}, "auto_approved", _NullFlow())
    assert sent["url"] == "https://example.com/ingest" and sent["method"] == "POST"
    assert sent["json"]["status"] == "auto_approved"
    assert sent["json"]["data"] == {"headers": {"total": "9"}}
    assert sent["json"]["metadata"]["document_id"] == str(did)   # include_metadata=True
    assert sent["headers"]["X-Api-Key"] == "k"


@pytest.mark.anyio
async def test_maybe_webhook_no_url_is_noop(monkeypatch):
    called = {"n": 0}

    class _Client:
        def __init__(self, *a, **k):
            called["n"] += 1

    monkeypatch.setattr(pl.httpx, "AsyncClient", _Client)
    await pl._maybe_webhook(_cfg(webhook_url=None), None, None, {}, "auto_approved", _NullFlow())
    assert called["n"] == 0


@pytest.mark.anyio
async def test_maybe_webhook_swallows_errors(monkeypatch):
    class _Client:
        def __init__(self, *a, **k):
            pass
        async def __aenter__(self):
            raise RuntimeError("dns fail")
        async def __aexit__(self, *a):
            return False

    monkeypatch.setattr(pl.httpx, "AsyncClient", _Client)
    # must NOT raise
    await pl._maybe_webhook(_cfg(webhook_url="https://x"), None, None, {}, "failed", _NullFlow())


@pytest.mark.anyio
async def test_finalize_invokes_webhook_call_site(monkeypatch):
    """Call-site proof (scoped): the finalize path calls _maybe_webhook exactly once with the final
    status. Drives the REAL pipeline end-to-end via the DB harness in tests/test_idp_pipeline.py
    (a digital PDF -> native text, no OCR), with a Webhook Output node on the graph and a recorder
    swapped in for _maybe_webhook — proving the wiring at the actual call site, not just the helper.
    """
    import test_idp_pipeline as H
    from agentcore.services.deps import session_scope

    # The harness's autouse in-memory-storage fixture isn't active in THIS module -> set it up here.
    storage = H._MockStorage()
    monkeypatch.setattr(pl, "get_storage_service", lambda: storage)
    H._patch_extraction(monkeypatch)  # fake LLM -> a high-confidence header

    recorded = []

    async def _recorder(cfg, document_id, job_id, extracted, status, flow):
        recorded.append({"status": status, "document_id": document_id, "extracted": extracted})

    monkeypatch.setattr(pl, "_maybe_webhook", _recorder)

    webhook_node = H._node("Webhook Output",
                           {"url": "https://example.com/ingest", "method": "POST",
                            "headers": "", "include_metadata": False})
    graph = H._graph(str(uuid4()), extra_nodes=[webhook_node])
    async with session_scope() as session:
        agent_id, doc_id = await H._setup_document(
            session, graph=graph, file_bytes=H._digital_pdf(), default_rule_action="auto_approve"
        )
    try:
        await pl.process_document(doc_id)
        assert len(recorded) == 1
        assert recorded[0]["document_id"] == doc_id
        assert recorded[0]["status"] in ("auto_approved", "pending_review")
    finally:
        await H._cleanup(agent_id, doc_id)


# ─────────────────────────── Task 7: Output Parser merge (regression) ───────────────────────────
# The Output Parser node has NO settings; its intended effect (joining the digital + OCR token
# branches into one page-numbered text) is performed unconditionally by build_merged_text in the
# pipeline. This locks that behavior so it can never silently regress (no code change — sync test).

def test_output_parser_merges_pages_in_order():
    from agentcore.services.idp.restruct import build_merged_text
    # Mixed-page tokens fed OUT of page order (page 2 fragments before page 1) — mimics the
    # digital-branch + OCR-branch tokens arriving interleaved. The merge must group by page and
    # emit pages in ascending order regardless of input order.
    tokens = [
        {"page_number": 2, "text": "SECONDPAGEOCRTOKEN"},
        {"page_number": 1, "text": "FIRSTPAGEDIGITALTOKEN"},
        {"page_number": 1, "text": "morefirstpage"},
        {"page_number": 2, "text": "moresecondpage"},
    ]
    merged = build_merged_text(tokens)
    assert "FIRSTPAGEDIGITALTOKEN" in merged   # page 1 content present
    assert "SECONDPAGEOCRTOKEN" in merged      # page 2 content present
    # both page-1 tokens precede both page-2 tokens (page order preserved)
    assert merged.index("FIRSTPAGEDIGITALTOKEN") < merged.index("SECONDPAGEOCRTOKEN")
    assert merged.index("morefirstpage") < merged.index("SECONDPAGEOCRTOKEN")
    assert "PAGE 1" in merged and "PAGE 2" in merged


# ─────────────────────────── Task 8: Multi-Branch Router (route-labeler) ───────────────────────────

def test_multibranch_route_matcher():
    from agentcore.services.idp.pipeline import _match_route
    cfg = _cfg(route_field="document_type", route_map={"invoice": "route_1", "receipt": "route_2"})
    doc = SimpleNamespace(predicted_type="Invoice")
    assert _match_route(cfg, doc, headers=[]) == "route_1"          # case-insensitive match
    doc2 = SimpleNamespace(predicted_type="Memo")
    assert _match_route(cfg, doc2, headers=[]) == "unmatched"       # router present, nothing matched
    assert _match_route(_cfg(route_field=None, route_map={}), doc, headers=[]) is None  # no router -> None
    # route_field naming a HEADER value (not the doc type) resolves off the extracted headers.
    cfg_hdr = _cfg(route_field="status", route_map={"paid": "route_3"})
    hdrs = [_hdr("status", "PAID")]
    assert _match_route(cfg_hdr, SimpleNamespace(predicted_type=None), headers=hdrs) == "route_3"


# ── Codex-review regression tests (2 blockers fixed) ───────────────────────────

@pytest.mark.anyio
async def test_zero_confidence_forces_review_even_with_approval_field():
    # No-rules path: approval_field header matches approve_value, BUT overall_conf=0 (no fields
    # extracted) must still route to review — the zero-confidence override is re-applied AFTER the gate.
    sess = SimpleNamespace(exec=_exec_returning([_hdr("status", "APPROVED")]))
    cfg = _cfg(confidence_router_present=False, approval_field="status", approve_value="APPROVED",
               canvas_rules=[], default_rule_action="auto_approve")
    status = await pl._route(session=sess, document_id=uuid4(), job_id=uuid4(),
                             idp_agent_id=uuid4(), overall_conf=0.0, cfg=cfg, flow=_NullFlow())
    assert status == "pending_review"


def test_match_route_handles_non_string_route_field():
    # A non-string route_field must NOT crash finalize (it runs after extraction is saved).
    cfg = _cfg(route_field=123, route_map={"123": "route_1"})
    assert pl._match_route(cfg, SimpleNamespace(predicted_type=None), headers=[]) == "unmatched"
