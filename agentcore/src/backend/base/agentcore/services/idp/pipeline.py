"""Per-document IDP processing pipeline (B19).

Background orchestrator that takes an uploaded document and runs it end-to-end:
load config from the agent's saved graph -> page-select -> detect digital/scanned ->
(scanned: deskew/rotate + PaddleOCR; digital: native text) -> extract via LLM ->
save extracted fields + confidence -> route to auto_approved/pending_review.

It records a readable per-document flow log (to ``IdpProcessingJob.log`` and a stored
``flow.log`` text file) and the merged page-numbered OCR text. On any fatal error the
job/document are marked ``failed`` with a message and partial results are saved.

Hybrid/config-driven: reads the proven ``services/idp/*`` functions, not the canvas node
runtime. Multimodal extraction is parked (text modes only); rules engine, multi-doc
split and classification are later tasks (hooks left below).
"""

from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime, timezone
from uuid import UUID, uuid4

import httpx
from loguru import logger
from sqlmodel import select

from agentcore.services.database.models.agent.model import Agent
from agentcore.services.database.models.idp.config import IdpAgent, IdpAgentRule
from agentcore.services.database.models.idp.documents import (
    IdpDetectedElement,
    IdpDocument,
    IdpExtractedHeader,
    IdpExtractedLineItem,
    IdpProcessingJob,
)
from agentcore.services.deps import (
    get_queue_service,
    get_settings_service,
    get_storage_service,
    session_scope,
)
from agentcore.services.idp import long_doc, text_layer
from agentcore.services.idp.agent_config import MODE_NAMED, resolve_pipeline_config
from agentcore.services.idp.extraction import (
    decide_extraction_input,
    extract_dynamic,
    extract_named_config,
    extract_vision,
    render_document_images,
    save_extraction_results,
)
from agentcore.services.idp.ocr import run_paddle_ocr
from agentcore.services.idp.pre_processing import (
    detect_and_correct_rotation,
    detect_and_correct_skew,
)
from agentcore.services.idp.restruct import build_merged_text
from agentcore.services.idp.rules_engine import _to_number, evaluate_rules
from agentcore.services.idp.idp_tracing import create_idp_trace, end_idp_trace, start_span, end_span
from agentcore.services.idp.field_defs import install_for_graph
from agentcore.services.idp.snapshot import (
    SnapshotError,
    graph_agent_view,
    graph_fingerprint,
    normalize_env,
    pin_idp_agent,
    resolve_agent_graph_with_source,
)


def _idp_engine_value(idp_agent) -> str:
    """Resolved engine string from ``IDP_EXECUTION_ENGINE`` (deployment-wide) with a per-agent
    ``idp_agent.extra["execution_engine"]`` override. Lower-cased; empty when unset."""
    import os
    env = os.getenv("IDP_EXECUTION_ENGINE", "").strip().lower()
    if env:
        return env
    engine = (getattr(idp_agent, "extra", None) or {}).get("execution_engine")
    return str(engine or "").strip().lower()


def _idp_engine_is_native(idp_agent) -> bool:
    """True when the NATIVE engine runs the IDP nodes through the *same* generic ``graph_langgraph``
    engine that simple chat agents use (hydrate canvas nodes with their ``components/IDP`` source, then
    build + stream per-vertex). ``IDP_EXECUTION_ENGINE=native``. The fixed ``_run`` is the default."""
    return _idp_engine_value(idp_agent) == "native"


def _idp_engine_label(idp_agent) -> str:
    """Which IDP execution engine will run this document, for the flow log / server log:
    ``native`` (the wired canvas run through the generic graph_langgraph engine) or ``fixed``
    (the default config-driven pipeline). Resolved from ``IDP_EXECUTION_ENGINE`` + per-agent override."""
    return "native" if _idp_engine_is_native(idp_agent) else "fixed"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class PipelineError(Exception):
    """Fatal, user-meaningful pipeline error (recorded in job.error_message)."""


# ─────────────────────────────── flow log ───────────────────────────────
class FlowLog:
    """Accumulates a readable per-document processing trail (file + job.log JSONB)."""

    def __init__(self, document_id: UUID, original_filename: str) -> None:
        self.document_id = document_id
        self.filename = original_filename
        self.events: list[dict] = []
        self.lines: list[str] = []

    def step(self, name: str, status: str, detail: str = "", **fields) -> None:
        ts = _utcnow().isoformat()
        evt = {"ts": ts, "step": name, "status": status, "detail": detail}
        evt.update(fields)
        self.events.append(evt)
        self.lines.append(f"[{ts[11:19]}] {status.upper():5} {name:14} {detail}")
        # Rich export: fold the bounded io payloads (+ timing) into the downloadable flow.log
        # text so an exported log shows the full per-step/per-node input & output — not just the
        # one-line summary. The io dicts are already bounded by the callers/sampler, so this is safe.
        io = fields.get("io")
        if isinstance(io, dict):
            for side in ("input", "output"):
                payload = io.get(side)
                if payload:
                    self.lines.append(f"        {side}:")
                    block = json.dumps(payload, indent=2, default=str, ensure_ascii=False)
                    self.lines.extend(f"          {ln}" for ln in block.splitlines())
        ms = fields.get("ms")
        if ms is not None:
            self.lines.append(f"        (took {ms} ms)")

    @property
    def steps_ok(self) -> list[str]:
        return [e["step"] for e in self.events if e["status"] in ("ok", "warn")]

    def render(self) -> str:
        head = f"Document: {self.filename}  ({self.document_id})\n" + "=" * 64
        return head + "\n" + "\n".join(self.lines) + "\n"


# ── flow-log io payload helpers (bounded, for the per-component input/output trace the UI renders) ──
_IO_TEXT_SAMPLE = 2000  # max chars of any text sample stored in the log
_IO_VALUE_SAMPLE = 400  # max chars of a single header/cell value stored in the log
_IO_MAX_ROWS = 12       # max sample line-item rows stored in the log
_IO_MAX_HEADERS = 60    # max header name->value pairs stored in the log
_IO_MAX_COLS = 40       # max columns per sampled line-item row stored in the log


def _clip(text: Any, n: int = _IO_TEXT_SAMPLE) -> str:
    """Return at most ``n`` chars of ``text`` (plus an ellipsis marker if truncated)."""
    s = "" if text is None else str(text)
    return s if len(s) <= n else s[:n] + f"…(+{len(s) - n} chars)"


def _io_value(v: Any):
    """Clip a single header/cell value for the log (None stays None; everything else bounded str)."""
    return None if v is None else _clip(v, _IO_VALUE_SAMPLE)


def _io_headers(extracted: dict) -> dict:
    """A compact {name: value} view of extracted headers, capped at _IO_MAX_HEADERS and per-value."""
    out: dict = {}
    for name, h in list((extracted.get("headers") or {}).items())[:_IO_MAX_HEADERS]:
        out[str(name)] = _io_value(h.get("value") if isinstance(h, dict) else h)
    return out


def _io_sample_rows(extracted: dict) -> list:
    """First _IO_MAX_ROWS line-item rows as flat {column: value} dicts (bounded rows/cols/values)."""
    rows = []
    for row in (extracted.get("line_items") or [])[:_IO_MAX_ROWS]:
        cols = (row.get("columns", []) if isinstance(row, dict) else [])[:_IO_MAX_COLS]
        rows.append({c.get("column_name"): _io_value(c.get("value")) for c in cols if isinstance(c, dict)})
    return rows


# ─────────────────────────────── helpers ───────────────────────────────
async def _build_llm(session, model_id: str, *, temperature: float | None = None):
    """Build a MicroserviceChatModel for a registry model UUID (validated).

    ``temperature`` is forwarded to the model service; pass 0 for tasks that should be as
    reproducible as possible (e.g. visual element detection)."""
    from agentcore.services.database.models.model_registry.model import ModelRegistry
    from agentcore.services.model_service_client import MicroserviceChatModel

    try:
        reg = await session.get(ModelRegistry, UUID(str(model_id)))
    except (ValueError, TypeError) as e:
        raise PipelineError(f"Invalid model id '{model_id}'") from e
    if reg is None:
        raise PipelineError(f"Model '{model_id}' not found in the model registry")
    if getattr(reg, "is_active", True) is False:
        raise PipelineError(f"Model '{model_id}' is inactive")

    settings = get_settings_service().settings
    return MicroserviceChatModel(
        service_url=settings.model_service_url,
        service_api_key=settings.model_service_api_key,
        registry_model_id=str(model_id),
        provider="openai",  # placeholder — the model service resolves the real provider
        model=f"idp-extract-{str(model_id)[:8]}",
        temperature=temperature,
    )


def _supports_vision(reg) -> bool:
    """Whether a registry model can accept image input for the vision extraction path.

    Checkbox-only: True ONLY when the model's ``capabilities.supports_vision`` flag is exactly True
    (set via the 'Supports vision' checkbox in the Model Catalogue). Unset / False / missing model
    all mean 'not vision-capable' -> False. Deny-list providers that silently STRINGIFY image
    blocks (they would send garbage) -> always False, even if they claim the capability. The
    model-service also raises on image content for those providers (defense in depth — Task 10).
    """
    if reg is None:
        return False
    if getattr(reg, "provider", None) in {"google_genai_vertex"}:
        return False
    caps = getattr(reg, "capabilities", None) or {}
    return caps.get("supports_vision") is True


def _vision_error_hint(msg: str) -> str:
    """Turn a raw provider/transport error string into an actionable hint for a vision failure.

    Returns "" when the error is not a recognised rate-limit / oversize condition (so the caller
    just shows the raw error). Requires the underlying error text to reach us — see the model
    client's error surfacing (invoke_via_service) which now includes the service response body.
    """
    low = (msg or "").lower()
    if any(k in low for k in ("429", "rate limit", "rate-limit", "too many requests",
                              "quota", "resource_exhausted", "resource exhausted")):
        return (" — the vision model is rate-limited or out of quota; retry later or check the "
                "model's billing/quota")
    if any(k in low for k in ("413", "too large", "request entity", "payload",
                              "context length", "maximum context", "context window",
                              "timeout", "timed out")):
        return (" — the document may be too large for vision in one request; add a Page Selector "
                "or switch Input Mode to Text (OCR)")
    return ""


async def _named_config_vision_messages(session, field_config_id):
    """Build the (system, user) VISION messages for a named Field Configuration.

    Uses the confidence-requesting vision prompt variant so per-field confidence is the model's
    genuine score (the vision path has no OCR token stream to re-score against)."""
    from agentcore.services.idp.field_defs import FieldConfigMissing, load_field_definitions
    from agentcore.services.idp.prompt_templates import build_compact_extraction_messages_vision

    try:
        headers, line_items = await load_field_definitions(session, field_config_id)
    except FieldConfigMissing as exc:
        raise PipelineError(f"named-config vision: {exc}") from exc
    return build_compact_extraction_messages_vision(headers, line_items)


def _selected_pages(total_pages: int, cfg) -> set[int]:
    """Resolve which page numbers to process from the page-selector config.

    Always returns a NON-EMPTY set: an out-of-range request falls back to all pages
    rather than silently dropping the whole document.
    """
    total = max(1, total_pages)
    all_pages = set(range(1, total + 1))
    mode = (cfg.page_selection_mode or "all").lower()
    n = max(1, cfg.first_n_pages)
    if mode == "first_n":
        return set(range(1, min(n, total) + 1))
    if mode == "last_n":
        return set(range(max(1, total - n + 1), total + 1))
    if mode == "range":
        # A range entirely past the document -> process all (never drop everything).
        if cfg.page_range_start > total:
            return all_pages
        start = max(1, cfg.page_range_start)
        end = min(total, max(start, cfg.page_range_end))
        return set(range(start, end + 1)) or all_pages
    return all_pages


async def _maybe_preprocess(file_bytes: bytes, file_type: str, cfg, flow: "FlowLog") -> bytes:
    """Apply deskew/rotation (non-fatal) to bytes destined for OCR; returns the bytes."""
    if not (cfg.fix_skew or cfg.fix_rotation):
        return file_bytes
    try:
        if cfg.fix_skew:
            file_bytes, angle = await detect_and_correct_skew(file_bytes, file_type, cfg.skew_threshold)
            flow.step("preprocess", "ok", f"deskew angle={angle:.2f}")
        if cfg.fix_rotation:
            file_bytes, rot = await detect_and_correct_rotation(file_bytes, file_type, cfg.allowed_angles)
            flow.step("preprocess", "ok", f"rotation={rot}")
    except Exception as e:
        flow.step("preprocess", "warn", f"skipped ({e})")
    return file_bytes


def _split_storage_path(file_path: str) -> tuple[str, str]:
    """``"{agent_id}/{name}"`` -> ``(agent_id, name)`` for the storage service."""
    if "/" in file_path:
        agent_part, name = file_path.split("/", 1)
        return agent_part, name
    return "", file_path


async def _save_artifact(storage, agent_id: str, rel_path: str, data: bytes) -> None:
    try:
        await storage.save_file(agent_id=agent_id, file_name=rel_path, data=data)
    except Exception as e:  # pragma: no cover - artifacts are best-effort
        logger.warning(f"[pipeline] could not store artifact {rel_path}: {e}")


async def _maybe_webhook(cfg, document_id, job_id, extracted: dict, status: str, flow: "FlowLog") -> None:
    """POST the extraction result to a configured Webhook Output node. Non-fatal: never raises.

    No-op when no Webhook Output node is on the canvas (``cfg.webhook_url`` is None). A failing POST
    is logged as a warn step and swallowed so a bad/unreachable webhook can never break processing.
    """
    url = getattr(cfg, "webhook_url", None)
    if not url:
        return
    payload = {"status": status, "data": extracted or {}}
    if getattr(cfg, "webhook_include_metadata", False):
        payload["metadata"] = {
            "document_id": str(document_id) if document_id else None,
            "job_id": str(job_id) if job_id else None,
            "timestamp": _utcnow().isoformat(),
        }
    headers = {"Content-Type": "application/json", **(getattr(cfg, "webhook_headers", {}) or {})}
    method = (getattr(cfg, "webhook_method", "POST") or "POST").upper()
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.request(method, url, json=payload, headers=headers)
            resp.raise_for_status()
        flow.step("webhook", "ok", f"POST {url} -> {getattr(resp, 'status_code', '?')}")
    except Exception as e:  # non-fatal — a failing webhook must never break processing
        flow.step("webhook", "warn", f"webhook POST failed ({e})")


_NUMERIC_OPS = {">", "<", ">=", "<=", "==", "!="}


def _infer_field_cond_type(mapped_op: str, val) -> str:
    """Choose the condition type for a canvas FIELD rule.

    Canvas thresholds arrive as JSON-decoded values where a number is frequently a *string*
    (e.g. ``"1000"``). A rule like ``total >= 1000`` is numeric only when the operator is a
    numeric comparator AND the threshold parses as a number (rules_engine._to_number tolerates
    ints/floats/negatives/currency/thousands/percent). Otherwise it stays text — and operators
    that only make sense on text (contains/starts_with/ends_with) always stay text. Without this,
    a numeric-string threshold defaulted to ``field_value_text``, where ``>=`` is not a valid
    operator, so the rule silently never matched.
    """
    if mapped_op in _NUMERIC_OPS:
        try:
            _to_number(val)
            return "field_value_numeric"
        except (ValueError, TypeError):
            return "field_value_text"
    return "field_value_text"


async def _route(session, document_id: UUID, job_id: UUID, idp_agent_id: UUID, overall_conf: float, cfg, flow: "FlowLog", match_result: str | None = None) -> str:
    """Decide auto_approved vs pending_review.

    If the agent has rules (``idp_agent_rules``), run the real rules engine (B17) — it can
    reference per-field values, confidence, regex, field comparisons and visual elements.
    With no rules, fall back to the B19 default-action + confidence-threshold behaviour.
    A zero-confidence (no fields) extraction always routes to review, whatever the rules say.
    """
    rules = []
    if cfg.canvas_rules:
        # Construct transient/mock IdpAgentRule objects
        for idx, r in enumerate(cfg.canvas_rules):
            field_name = r.get("field")
            op = r.get("op")
            val = r.get("value")
            
            # Map operator names from canvas to rules engine expectations if needed
            op_map = {
                "gte": ">=",
                "lte": "<=",
                "gt": ">",
                "lt": "<",
                "eq": "==",
                "neq": "!=",
            }
            mapped_op = op_map.get(op, op)

            # Determine condition type
            cond_type = "field_value_text"
            if field_name == "confidence":
                cond_type = "confidence_overall"
            elif mapped_op in ("is_present", "is_missing"):
                cond_type = "field_presence"
            elif field_name in ("signature", "checkbox", "qr", "barcode"):
                cond_type = "visual_element"
            else:
                cond_type = _infer_field_cond_type(mapped_op, val)
            
            # Map rules_operator logic:
            # - If AND: all rules belong to group 1 (AND logic).
            # - If OR: each rule gets its own group (first-match wins OR logic).
            group_id = 1 if cfg.rules_operator == "AND" else (idx + 1)

            rules.append(
                IdpAgentRule(
                    id=uuid4(),
                    idp_agent_id=idp_agent_id,
                    rule_group=group_id,
                    display_order=idx,
                    condition_type=cond_type,
                    field_name=field_name,
                    operator=mapped_op,
                    value=val,
                    action=cfg.approve_value,
                )
            )
    elif getattr(cfg, "environment", "dev") == "dev":
        # `idp_agent_rules` is re-synced from the DRAFT canvas on every agent save (_sync_canvas_rules),
        # so it is only a valid source for a draft run. On a published run the snapshot is authoritative:
        # no Rules node in the frozen graph means no rules — reading the table here would apply whatever
        # rules the developer has on the canvas right now.
        rules = (
            await session.exec(
                select(IdpAgentRule)
                .where(IdpAgentRule.idp_agent_id == idp_agent_id)
                .order_by(IdpAgentRule.rule_group, IdpAgentRule.display_order)
            )
        ).all()

    # Confidence Router (when the node is on the canvas) is an OVERALL-confidence gate that runs
    # BEFORE per-field rules: below the threshold -> pending_review (the router's Low-Confidence
    # branch -> Processed Docs Output in the universal template); at/above -> fall through to the
    # rules path (the router's High-Confidence branch -> Rules). No router node -> unchanged.
    if getattr(cfg, "confidence_router_present", False) and overall_conf < cfg.confidence_threshold:
        flow.step("route", "ok",
                  f"pending_review (confidence {overall_conf:.4f} < router threshold {cfg.confidence_threshold})",
                  io={"input": {"overall_conf": round(float(overall_conf), 4),
                                "threshold": cfg.confidence_threshold, "confidence_router": True},
                      "output": {"decision": "pending_review"}})
        return "pending_review"

    # Re-query the just-saved header fields — needed by BOTH the no-rules and rules paths so the
    # Approval Gate (`approval_field`) can consult an extracted header on either path.
    headers = (
        await session.exec(select(IdpExtractedHeader).where(IdpExtractedHeader.job_id == job_id))
    ).all()

    # Approval Gate: `approval_field` names WHICH value carries the approval decision. Default
    # "rule_action" -> use the base decision unchanged. Any other value names an extracted HEADER
    # field whose value is compared against approve_value. Applied on BOTH the rules and no-rules paths.
    def _apply_gate(base_status: str) -> str:
        af = getattr(cfg, "approval_field", "rule_action") or "rule_action"
        if af == "rule_action":
            return base_status
        gate_val = next(
            (h.reviewed_value if getattr(h, "reviewed_value", None) not in (None, "") else h.extracted_value
             for h in headers if h.field_name == af),
            None,
        )
        return "auto_approved" if str(gate_val) == str(cfg.approve_value) else "pending_review"

    if not rules:
        if overall_conf <= 0.0:
            new_status = "pending_review"
        elif cfg.default_rule_action == "auto_approve" and overall_conf >= cfg.confidence_threshold:
            new_status = "auto_approved"
        else:
            new_status = "pending_review"
        new_status = _apply_gate(new_status)
        if overall_conf <= 0.0:  # zero-confidence (no fields) always routes to review, even via approval_field
            new_status = "pending_review"
        flow.step("route", "ok", f"{new_status} (no rules; threshold={cfg.confidence_threshold})",
                  io={"input": {"overall_conf": round(float(overall_conf), 4), "rules_count": 0,
                               "threshold": cfg.confidence_threshold, "default_action": cfg.default_rule_action},
                      "output": {"decision": new_status}})
        return new_status

    # Re-query the just-saved line-items + any detected visual elements for the rule conditions.
    line_items = (
        await session.exec(select(IdpExtractedLineItem).where(IdpExtractedLineItem.job_id == job_id))
    ).all()
    detected = (
        await session.exec(select(IdpDetectedElement).where(IdpDetectedElement.document_id == document_id))
    ).all()

    try:
        decision = evaluate_rules(
            overall_confidence=overall_conf,
            headers=headers,
            line_items=line_items,
            detected_elements=detected,
            rules=rules,
            default_action=cfg.default_rule_action,
            match_result=match_result,
        )
        action = decision.get("action", cfg.default_rule_action)
        matched = decision.get("matched_group")
        failed = len(decision.get("failed_conditions", []))
    except Exception as e:  # pragma: no cover - rules must never crash the pipeline
        flow.step("route", "warn", f"rules engine error ({e}); falling back to review")
        return "pending_review"

    base_status = "auto_approved" if action in (cfg.approve_value, "auto_approve") else "pending_review"
    new_status = _apply_gate(base_status)
    if overall_conf <= 0.0:
        new_status = "pending_review"
    flow.step(
        "route", "ok",
        f"{new_status} via rules (matched_group={matched}, failed={failed}, "
        f"{len(rules)} rule(s))",
        io={"input": {"overall_conf": round(float(overall_conf), 4), "rules_count": len(rules)},
            "output": {"decision": new_status, "matched_group": matched, "failed_conditions": failed}},
    )
    return new_status


def _match_route(cfg, doc, headers) -> str | None:
    """Multi-Branch Router: resolve route_field's value -> matched route label, else 'unmatched'.

    Returns None when no router node is present (route_field unset or route_map empty), so the
    caller leaves ``route_label`` untouched (backward-compatible). ``document_type`` /
    ``predicted_type`` read the classifier's predicted type off the document; any other route_field
    names an extracted HEADER whose (reviewed-or-extracted) value is matched. Matching is
    case-insensitive; a value with no map entry -> "unmatched".
    """
    if not getattr(cfg, "route_field", None) or not getattr(cfg, "route_map", None):
        return None
    rf = str(cfg.route_field).strip().lower()
    if rf in ("document_type", "predicted_type"):
        val = getattr(doc, "predicted_type", None)
    else:
        val = next(
            (h.reviewed_value if getattr(h, "reviewed_value", None) not in (None, "") else h.extracted_value
             for h in headers if h.field_name == cfg.route_field),
            None,
        )
    return cfg.route_map.get(str(val).strip().lower(), "unmatched") if val is not None else "unmatched"


# ─────────────────────────── differentiator hooks ───────────────────────────
# Each hook is OPTIONAL, NON-FATAL and import-guarded: it is a no-op until both (a) the
# owning service module exists (Ather's modules land via branch merge) and (b) the feature
# is enabled on the agent. A differentiator raising must never fail the document.

async def _hook_split(session, storage, doc, tokens, page_status, cfg, flow: "FlowLog") -> list[UUID] | None:
    """B32 multi-doc split. Returns child document ids to enqueue (caller early-returns), else None.

    Shared seam: Ather owns detect_document_boundaries + materialize_children; the pipeline
    (Basu) owns the enqueue + early-return wiring.
    """
    if not cfg.multi_doc_split:
        return None
    # A document that is itself a split child must never be split again (it shares the
    # parent's agent, so cfg.multi_doc_split is still True). This is the real recursion guard.
    if getattr(doc, "parent_document_id", None) is not None:
        flow.step("split", "ok", "child document — split skipped (already split from a parent)")
        return None
    try:
        from agentcore.services.idp.splitting import detect_document_boundaries, materialize_children
    except ImportError:
        flow.step("split", "warn", "multi-doc split not available yet")
        return None
    try:
        boundaries = detect_document_boundaries(tokens, page_status)
        if not boundaries or len(boundaries) < 2:
            flow.step("split", "ok", "single document (no split)")
            return None
        # Run on an ISOLATED session: materialize_children commits internally, so a failure
        # there must not poison the main pipeline session (children are committed + visible to
        # the caller's enqueue either way).
        async with session_scope() as hook_session:
            child_ids = await materialize_children(hook_session, storage, doc, boundaries)
        flow.step("split", "ok", f"split into {len(child_ids)} child document(s)")
        return child_ids or None
    except Exception as e:
        flow.step("split", "warn", f"skipped ({e})")
        return None


async def _hook_detect(session, document_id: UUID, file_bytes: bytes, file_type: str, cfg, flow: "FlowLog") -> None:
    """B30 visual element detection -> idp_detected_elements (used later by the rules engine).

    QR/barcode use pyzbar (deterministic). Signature/checkbox/stamp/logo/handwriting use a vision
    LLM built from ``cfg.detect_model_id`` (which falls back to the extraction model)."""
    if not cfg.detect_enabled:
        logger.info(f"[detect] {document_id}: skipped — no Visual Element Detection node on the canvas")
        return
    try:
        from agentcore.services.idp.visual_detection import detect_and_persist, PYZBAR_AVAILABLE
    except ImportError:
        flow.step("detect_elements", "warn", "visual detection persistence not available yet")
        return

    # Split the wanted element types: barcodes use pyzbar, everything else needs the vision model.
    wanted = cfg.detect_enabled_types  # None -> all types
    _BARCODE = {"qr", "barcode"}
    wants_barcode = wanted is None or bool(wanted & _BARCODE)
    wants_vlm = wanted is None or bool(wanted - _BARCODE)
    logger.info(
        f"[detect] {document_id}: starting — enabled_types={sorted(wanted) if wanted else 'ALL'}, "
        f"model={str(getattr(cfg, 'detect_model_id', None) or cfg.model_id)[:8]}…"
    )

    # Build the vision model only when a VLM element type is wanted and a model is resolved.
    llm_model = None
    detect_model_id = getattr(cfg, "detect_model_id", None) or cfg.model_id
    if wants_vlm and detect_model_id:
        try:
            # Use the model's default temperature — forcing 0 on this vision model produced
            # empty detections in testing, while the default reliably found elements.
            llm_model = await _build_llm(session, detect_model_id)
        except Exception as e:
            flow.step("detect_elements", "warn", f"vision model unavailable ({e}); detecting QR/barcode only")

    # Nothing can run? (no pyzbar for barcodes AND no vision model for the rest.) Say so explicitly
    # rather than logging a misleading "0 detected".
    can_barcode = wants_barcode and PYZBAR_AVAILABLE
    can_vlm = wants_vlm and llm_model is not None
    if not can_barcode and not can_vlm:
        if wants_vlm and not detect_model_id:
            msg = "no vision model — connect a Language Model to the Visual Element Detection node"
        else:
            msg = "no detection backend available — skipped"
        flow.step("detect_elements", "warn", msg)
        logger.info(f"[detect] {document_id}: {msg}")
        return

    try:
        # Isolated session: detect_and_persist commits internally; a failure must not poison main.
        diag: dict = {}
        async with session_scope() as hook_session:
            ids = await detect_and_persist(
                hook_session, document_id, file_bytes, file_type, wanted,
                llm_model=llm_model, diag=diag, clear_existing=True,
            )
        detail = f"{len(ids)} element(s) detected"
        # Surface what the vision model actually did, so a silent 0 is diagnosable in the flow log:
        # pages sent to the model, raw items it returned (before filtering), and any per-page errors.
        if llm_model is not None:
            detail += (
                f" — vision model {str(detect_model_id)[:8]}…: "
                f"{diag.get('pages', 0)} page(s) sent, {diag.get('raw_items', 0)} raw item(s)"
            )
            if diag.get("retries"):
                detail += f", {diag['retries']} retry(ies) on empty"
            errs = diag.get("errors") or []
            if errs:
                detail += f", {len(errs)} page error(s): {errs[0]}"
            # When the model returned but nothing parsed, show what it actually said.
            samples = diag.get("samples") or []
            if diag.get("raw_items", 0) == 0 and samples:
                detail += f" | model output: {samples[0]}"
        if wants_barcode and not PYZBAR_AVAILABLE:
            detail += " (QR/barcode skipped — pyzbar not installed)"
        if wants_vlm and llm_model is None:
            detail += " (signature/checkbox/stamp/logo skipped — no vision model)"
        flow.step("detect_elements", "ok", detail)
        # Also emit to the server log so detection status is visible without opening the flow log.
        logger.info(f"[detect] {document_id}: {detail}")
    except Exception as e:
        flow.step("detect_elements", "warn", f"skipped ({e})")


async def _hook_classify(session, document_id: UUID, base_agent, merged_text: str, cfg, flow: "FlowLog") -> bool:
    """B29 document classification -> idp_document_classifications; may auto-select a Named Config.

    Mutates ``cfg`` in place (field_config_id + extraction_mode) when auto-select fires.
    Returns True if the document should be skipped (type not in the user-selected doc_types list).
    """
    if not cfg.classify_enabled:
        return False
    try:
        from agentcore.services.idp.classification import classify_and_persist
    except ImportError:
        flow.step("classify", "warn", "classification not available yet")
        return False
    try:
        org_id = getattr(base_agent, "org_id", None)
        doc_types = getattr(cfg, "classify_doc_types", None) or None
        # Isolated session: classify_and_persist commits internally; a failure must not poison
        # the main pipeline session (it would otherwise crash extraction/finalize).
        async with session_scope() as hook_session:
            result = await classify_and_persist(
                hook_session, document_id, merged_text, org_id,
                auto_select=cfg.classify_auto_select,
                threshold=cfg.classify_threshold,
                doc_types=doc_types,
            )
        result = result if isinstance(result, dict) else {}

        # Sync predicted_type back to main session's doc object without full refresh
        main_doc = await session.get(IdpDocument, document_id)
        if main_doc and "predicted_type" in result:
            main_doc.predicted_type = result["predicted_type"]

        # Hard-skip gate: classification returned a skip signal (type not in selected types)
        if result.get("skip"):
            flow.step("classify", "skip",
                      f"type={result.get('predicted_type')!r} not in selected types — document skipped")
            return True

        # Multi-type routing (AI Field Extractor `config_names`): the classifier's predicted type
        # selects the matching Field Configuration; a document whose type has no matching config is
        # marked skipped. Takes precedence over the classifier's own auto-select. Empty map (no
        # config_names) -> fall through to the selected_config_id auto-select below, unchanged.
        if getattr(cfg, "config_name_map", None):
            pt = (result.get("predicted_type") or "").strip().lower()
            matched_cid = cfg.config_name_map.get(pt)
            if matched_cid is not None:
                cfg.field_config_id = matched_cid if isinstance(matched_cid, UUID) else UUID(str(matched_cid))
                cfg.extraction_mode = MODE_NAMED
                flow.step("classify", "ok", f"type={pt!r} -> matched multi-type config",
                          io={"output": {"predicted_type": result.get("predicted_type"),
                                         "selected_config_id": str(matched_cid)}})
                return False
            flow.step("classify", "skip",
                      f"type={pt!r} has no matching config in config_names — document skipped")
            return True

        sel = result.get("selected_config_id")
        _classify_io = {"output": {
            "predicted_type": result.get("predicted_type"),
            "confidence": result.get("confidence"),
            "candidates": result.get("candidates"),
            "selected_config_id": str(sel) if sel else None,
            "auto_selected": bool(sel and cfg.classify_auto_select),
        }}
        if sel and cfg.classify_auto_select:
            cfg.field_config_id = sel if isinstance(sel, UUID) else UUID(str(sel))
            cfg.extraction_mode = MODE_NAMED
            flow.step("classify", "ok",
                      f"type={result.get('predicted_type')} conf={result.get('confidence')} -> auto-selected config",
                      io=_classify_io)
        else:
            flow.step("classify", "ok",
                      f"type={result.get('predicted_type')} conf={result.get('confidence')}",
                      io=_classify_io)
    except Exception as e:
        flow.step("classify", "warn", f"skipped ({e})")
    return False


async def _hook_math_reconcile(extracted, llm, job, cfg, flow: "FlowLog", *, page_images=None, ocr_text=None):
    """B31 math reconcile: fix arithmetic BEFORE save. Returns the (possibly corrected) extraction.

    ``page_images`` (vision route) / ``ocr_text`` (text route) are forwarded so a correction can
    re-read the document instead of only rebalancing numbers."""
    if not cfg.math_reconcile_enabled or not isinstance(extracted, dict):
        return extracted
    try:
        from agentcore.services.idp.math_reconcile import reconcile_math
    except ImportError:
        flow.step("math_reconcile", "warn", "math reconcile not available yet")
        return extracted
    try:
        res = await reconcile_math(
            extracted, llm,
            max_attempts=cfg.math_reconcile_max_attempts,
            tolerance=getattr(cfg, "math_reconcile_tolerance", 0.01),
            ocr_text=ocr_text,
            page_images=page_images,
        )
        res = res if isinstance(res, dict) else {}
        job.math_reconcile_attempts = int(res.get("attempts", 0) or 0)
        flow.step("math_reconcile", "ok",
                  f"balanced={res.get('balanced')} attempts={res.get('attempts')}")
        return res.get("extracted") or extracted
    except Exception as e:
        flow.step("math_reconcile", "warn", f"skipped ({e})")
        return extracted


async def _hook_sap_matching(
    session,
    document_id: UUID,
    idp_agent_id: UUID,
    extracted: dict,
    flow: "FlowLog",
) -> str | None:
    """Run SAP two-way or three-way match if configured for this agent. Returns overall_status or None."""
    from agentcore.services.idp.matching_service import (
        MatchConfig,
        MatchingService,
        load_agent_match_config,
        persist_match_result,
    )
    from agentcore.services.idp.sap_client import SapS4HanaClient
    from agentcore.api.connector_catalogue import _decrypt_provider_config, SAP_PROVIDERS
    from agentcore.services.database.models.connector_catalogue.model import ConnectorCatalogue

    agent_cfg = await load_agent_match_config(session, idp_agent_id)
    if not agent_cfg or not agent_cfg.match_enabled:
        return None

    if not agent_cfg.sap_connector_id:
        flow.step("sap_match", "warn", "skipped (no SAP connector configured)")
        return None

    connector = await session.get(ConnectorCatalogue, agent_cfg.sap_connector_id)
    if not connector or connector.provider not in SAP_PROVIDERS:
        flow.step("sap_match", "warn", "skipped (SAP connector not found)")
        return None

    sap_config = _decrypt_provider_config(connector.provider, connector.provider_config or {})
    sap_client = SapS4HanaClient(sap_config)

    match_config = MatchConfig(
        match_type=agent_cfg.match_type,
        amount_tolerance_pct=agent_cfg.amount_tolerance_pct,
        quantity_tolerance_pct=agent_cfg.quantity_tolerance_pct,
        unit_price_tolerance_pct=agent_cfg.unit_price_tolerance_pct,
        vendor_name_fuzzy_threshold=agent_cfg.vendor_name_fuzzy_threshold,
        sap_connector_id=str(agent_cfg.sap_connector_id),
    )

    headers_dict: dict = extracted.get("headers", {}) if isinstance(extracted.get("headers"), dict) else {}
    line_items_list: list = extracted.get("line_items", []) if isinstance(extracted.get("line_items"), list) else []

    po_number = headers_dict.get("po_number", "")
    if not po_number:
        flow.step("sap_match", "warn", "skipped (no po_number extracted)")
        return None

    service = MatchingService(match_config, sap_client)
    result = await service.run(headers_dict, line_items_list)

    # Persist results
    sap_po_snapshot = sap_client._last_po_snapshot if hasattr(sap_client, "_last_po_snapshot") else None
    sap_gr_snapshot = sap_client._last_gr_snapshot if hasattr(sap_client, "_last_gr_snapshot") else None
    await persist_match_result(
        session=session,
        document_id=document_id,
        result=result,
        config=match_config,
        sap_po_snapshot=sap_po_snapshot,
        sap_gr_snapshot=sap_gr_snapshot,
    )

    flow.step(
        "sap_match", "ok" if result.overall_status != "error" else "warn",
        f"{agent_cfg.match_type}: {result.overall_status} (score={result.match_score:.1f}%, "
        f"po={po_number}, {len(result.discrepancies)} field(s) compared)",
    )
    return result.overall_status


async def _extract_text(session, cfg, llm, text: str) -> dict:
    """Run the configured text-mode extractor on one block of text (used per-chunk too)."""
    if cfg.extraction_mode == MODE_NAMED:
        if not cfg.field_config_id:
            raise PipelineError("named-config extraction selected but no field configuration resolved")
        return await extract_named_config(
            session=session, ocr_text=text, field_config_id=cfg.field_config_id, llm_model=llm
        )
    prompt = cfg.prompt or "Extract all key fields from this document. Return structured JSON."
    return await extract_dynamic(ocr_text=text, prompt=prompt, llm_model=llm)


# ─────────────────────────────── orchestrator ───────────────────────────────
async def process_document(document_id: UUID, job_id: UUID | None = None) -> None:
    """Background entrypoint: process one document end-to-end. Never raises."""
    try:
        async with session_scope() as session:
            await _run(session, document_id, job_id)
    except Exception:
        # Last-resort guard: this runs as a detached background task, so nothing
        # may escape. Errors inside the flow are handled by _run -> _fail; this only
        # catches setup failures (session/job creation) before the main try-block.
        logger.exception(f"[pipeline] unhandled error in process_document {document_id}")


async def _run(session, document_id: UUID, job_id: UUID | None) -> None:
    t0 = time.monotonic()
    trace_ctx = None
    doc = await session.get(IdpDocument, document_id)
    if doc is None:
        logger.warning(f"[pipeline] document {document_id} not found; nothing to process")
        return

    flow = FlowLog(document_id, doc.original_filename)
    storage = get_storage_service()
    # Scope reads AND sibling artifacts (corrected scans, OCR dumps, flow logs) by the directory the
    # document was actually written into — never by reconstructing it. `file_path` is the only record of
    # that, and it is what lets documents stored under the old `idp_agents.id` scheme keep resolving after
    # the directory naming changed. See services/idp/storage_scope.py.
    agent_scope, file_name = _split_storage_path(doc.file_path)

    # ensure a job row (created by the endpoint normally; create one for direct calls)
    job = await session.get(IdpProcessingJob, job_id) if job_id else None
    if job is None:
        job = IdpProcessingJob(id=job_id or uuid4(), document_id=document_id, agent_id=doc.agent_id, status="queued")
        session.add(job)
        await session.commit()

    try:
        # 0. idempotency — refuse if another job for this doc is already running
        running = (
            await session.exec(
                select(IdpProcessingJob).where(
                    IdpProcessingJob.document_id == document_id,
                    IdpProcessingJob.status == "running",
                    IdpProcessingJob.id != job.id,
                )
            )
        ).first()
        if running is not None:
            flow.step("load", "error", "another job already running for this document")
            await _fail(session, doc, job, flow, "another job already running for this document", touch_doc=False)
            return

        # mark running
        doc.status = "processing"
        doc.processing_started_at = _utcnow()
        job.status = "running"
        job.started_at = _utcnow()
        session.add(doc)
        session.add(job)
        await session.commit()
        # Email provenance for connector-ingested docs (which email this attachment came from) — so
        # the per-document flow log shows the sender / subject / attachment, not just the filename.
        _sm = doc.source_metadata if isinstance(doc.source_metadata, dict) else {}
        _email_io: dict = {}
        _prov = ""
        if doc.source == "mail_connector":
            _filters = _sm.get("filters_applied") if isinstance(_sm.get("filters_applied"), dict) else {}
            _email_io = {
                # WHERE the mail was read from (populated by the connector ingest; omitted if absent)
                "connector_name": _sm.get("connector_name"),
                "mailbox": _sm.get("mailbox_email") or _sm.get("account_email"),
                "folder": _sm.get("mail_folder") or _sm.get("folder"),
                "filters_applied": _filters or None,
                # WHICH email/attachment was picked
                "email_from": _sm.get("from"),
                "email_subject": _sm.get("subject"),
                "email_to": _sm.get("to"),
                "email_cc": _sm.get("cc"),
                "received": _sm.get("received"),
                "attachment_name": _sm.get("attachment_name") or doc.original_filename,
            }
            _email_io = {k: v for k, v in _email_io.items() if v is not None}
            _mbx, _fld = _email_io.get("mailbox"), _email_io.get("folder")
            _where = (
                f" via {_sm.get('connector_name') or 'connector'}"
                f"{f' mailbox {_mbx}' if _mbx else ''}{f' folder {_fld}' if _fld else ''}"
            )
            _filt = ", ".join(f"{k}={str(v)[:80]}" for k, v in _filters.items()) if _filters else ""
            _prov = (
                f"{_where} — from email sent by {_sm.get('from') or '?'}"
                f" (subject: '{_sm.get('subject') or ''}', attachment: '{_email_io['attachment_name']}')"
                f"{f'; filters: {_filt}' if _filt else ''}"
            )
        flow.step(
            "load", "ok",
            f"input received: '{doc.original_filename}' ({doc.file_size_bytes} bytes, .{doc.file_type}, "
            f"source={doc.source}){_prov} — document marked processing",
            document_id=str(document_id),
            io={"input": {
                "filename": doc.original_filename,
                "size_bytes": doc.file_size_bytes,
                "file_type": doc.file_type,
                "source": doc.source,
                **_email_io,
            }},
        )

        # resolve agents + config
        idp_agent = await session.get(IdpAgent, doc.agent_id)
        if idp_agent is None:
            raise PipelineError(f"IDP agent {doc.agent_id} not found")
        base_agent = await session.get(Agent, idp_agent.agent_id)
        if base_agent is None:
            raise PipelineError(f"Agent {idp_agent.agent_id} not found")

        # WHICH graph runs: the PUBLISHED snapshot for this document's (run_env, run_version), or the
        # draft canvas when unset (Playground / connectors / pre-existing rows). Chat agents resolve a
        # published version the same way — see services/idp/snapshot.py for the parity contract.
        #
        # HARD RULE: never assign onto `base_agent.data`. `base_agent` is a session-attached ORM row and
        # `_run` commits several times below, so reassigning would persist the published snapshot OVER
        # the developer's draft canvas. Every consumer reads the graph via getattr(), so pass a shim.
        run_env = normalize_env(doc.run_env)
        run_version = doc.run_version
        try:
            graph, graph_source = await resolve_agent_graph_with_source(
                session, base_agent.id, run_env, run_version
            )
        except SnapshotError as exc:
            # Never fall back to the draft — a missing published version must fail loudly.
            raise PipelineError(str(exc)) from exc

        # PROVE which row of which table this graph came from. "running UAT v5" on its own proves nothing
        # — it is just `doc.run_env` echoed back. `graph_source` is built from the ORM row that was
        # actually fetched (its table name + primary key), and the fingerprints compare the graph being
        # EXECUTED against the draft canvas as it stands right now.
        draft_fingerprint = graph_fingerprint(getattr(base_agent, "data", None))
        _same_as_draft = draft_fingerprint == graph_source.fingerprint
        logger.info(f"[pipeline] {document_id}: graph source -> {graph_source.describe()}")
        logger.info(
            f"[pipeline] {document_id}: executing sha={graph_source.fingerprint} | "
            f"draft agent.data sha={draft_fingerprint} -> "
            + (
                "IDENTICAL (draft happens to match this version; the source is still the deployment row above)"
                if _same_as_draft
                else "DIFFERENT (the draft canvas is NOT what is executing)"
            )
        )

        graph_agent = graph_agent_view(base_agent, graph)
        # IdpAgent settings are draft-synced; pin them from the snapshot on a published run.
        idp_view = pin_idp_agent(idp_agent, graph)

        # Field Configurations are mutable in place, and the graph only stores their NAME. Install this
        # run's frozen definitions (empty for a draft graph) BEFORE anything resolves a config or loads
        # its fields — both engines, and the isolated classify session, read it from the task context.
        # Called unconditionally, so a previous run's set can never leak into this one.
        install_for_graph(graph)

        cfg = await resolve_pipeline_config(session, idp_view, graph_agent)
        cfg.environment = run_env

        # Always recorded — for a draft run too, so the log never leaves the question open.
        _src_io = {
            "requested": {"env": run_env, "version": run_version or "(latest active)"},
            "read_from": {
                "table": graph_source.table,
                "deployment_id": str(graph_source.deployment_id) if graph_source.deployment_id else None,
                "version_number": graph_source.version_number,
                "is_active": graph_source.is_active,
            },
            "executing_graph": {"sha256_12": graph_source.fingerprint, "nodes": graph_source.node_count},
            "draft_canvas": {"sha256_12": draft_fingerprint, "matches_executing_graph": _same_as_draft},
        }
        if graph_source.deployment_id is not None:
            flow.step(
                "version", "ok",
                f"running PUBLISHED {run_env.upper()} v{graph_source.version_number} — graph read from "
                f"{graph_source.table} row {graph_source.deployment_id} "
                f"(sha={graph_source.fingerprint}, {graph_source.node_count} nodes); "
                f"draft canvas sha={draft_fingerprint} "
                + ("(identical content)" if _same_as_draft else "(DIFFERENT — draft not executed)"),
                io={"output": _src_io},
            )
        else:
            flow.step(
                "version", "ok",
                f"running the DRAFT canvas — graph read from agent.data "
                f"(sha={graph_source.fingerprint}, {graph_source.node_count} nodes)",
                io={"output": _src_io},
            )

        if not cfg.model_id:
            raise PipelineError("no model configured in agent graph")

        # Resolve user name if document was uploaded by a user
        user_name = None
        if doc.uploaded_by:
            try:
                from agentcore.services.database.models.user.model import User
                user_obj = await session.get(User, doc.uploaded_by)
                if user_obj:
                    user_name = user_obj.email or user_obj.username or str(doc.uploaded_by)
            except Exception:
                pass

        try:
            trace_ctx = await create_idp_trace(
                session=session,
                document_id=document_id,
                job_id=job.id,
                agent_id=base_agent.id,
                agent_name=base_agent.name,
                user_id=doc.uploaded_by,
                user_name=user_name,
                original_filename=doc.original_filename,
                file_type=doc.file_type,
                environment=getattr(cfg, "environment", "production"),
                org_id=base_agent.org_id,
                dept_id=base_agent.dept_id,
            )
        except Exception as tr_err:
            logger.debug(f"Failed to start IDP Langfuse trace: {tr_err}")

        # Log the resolved agent configuration so the trail shows EXACTLY what this agent is set
        # to do for this document (which steps/features run, the model, the extraction mode).
        _features = [
            name for name, on in (
                ("skew", cfg.fix_skew), ("rotation", cfg.fix_rotation), ("classify", cfg.classify_enabled),
                ("detect", cfg.detect_enabled), ("long-doc", cfg.long_doc_enabled),
                ("entity-link", cfg.entity_linking_enabled), ("math-reconcile", cfg.math_reconcile_enabled),
                ("multi-doc-split", cfg.multi_doc_split),
            ) if on
        ]
        flow.step(
            "config", "ok",
            f"agent config: extraction={cfg.extraction_mode}"
            + (f" (config={cfg.config_name})" if cfg.config_name else "")
            + f", model={str(cfg.model_id)[:8]}…, ocr_lang={cfg.ocr_lang}, "
            f"page_select={cfg.page_selection_mode}, features=[{', '.join(_features) or 'none'}]",
            io={"output": {
                "extraction_mode": cfg.extraction_mode,
                "config_name": cfg.config_name,
                "field_config_id": str(cfg.field_config_id) if cfg.field_config_id else None,
                "model_id": str(cfg.model_id) if cfg.model_id else None,
                "ocr_lang": cfg.ocr_lang,
                "page_selection": cfg.page_selection_mode,
                "features_enabled": _features,
                "classify": {"enabled": cfg.classify_enabled, "auto_select": cfg.classify_auto_select,
                             "threshold": cfg.classify_threshold} if cfg.classify_enabled else None,
                "long_doc": {"max_tokens": cfg.long_doc_max_tokens, "overlap": cfg.long_doc_overlap_tokens},
                "rules": {"operator": cfg.rules_operator, "count": len(cfg.canvas_rules),
                          "conditions": cfg.canvas_rules} if cfg.canvas_rules else None,
                "default_rule_action": cfg.default_rule_action,
                "confidence_threshold": cfg.confidence_threshold,
            }},
        )

        logger.info(f"[pipeline] {document_id}: fetching file from storage ({agent_scope}/{file_name})")
        file_bytes = await storage.get_file(agent_scope, file_name)
        file_type = (doc.file_type or "").lower().lstrip(".")
        logger.info(f"[pipeline] {document_id}: file fetched ({len(file_bytes)} bytes, type={file_type})")

        # 1. extension gate
        if cfg.allowed_extensions and file_type not in cfg.allowed_extensions and cfg.skip_unmatched:
            raise PipelineError(f"file type '{file_type}' not allowed by this agent")

        # Record which execution engine runs this document (IDP_EXECUTION_ENGINE / per-agent), so the
        # flow log clearly shows graph vs fixed.
        _engine = _idp_engine_label(idp_view)
        flow.step("engine", "ok", f"IDP execution engine: {_engine}")
        logger.info(f"[pipeline] {document_id}: IDP execution engine = {_engine}")

        if _idp_engine_is_native(idp_view):
            # NATIVE engine: run the wired canvas nodes through the SAME generic graph_langgraph
            # engine simple agents use (hydrate each node with its components/IDP source, build the
            # LangGraphAdapter, stream per-vertex events under job.id for native canvas highlighting).
            # LAZY import to avoid any import cycle.
            from agentcore.services.idp.graph_native.process import run_native_for_document
            # graph_agent (NOT base_agent) — its .data is the resolved snapshot for this run.
            await run_native_for_document(session, doc, job, graph_agent, flow, trace_ctx)
            return

        # 2. detect digital/scanned (per page)
        logger.info(f"[pipeline] {document_id}: classifying document (digital/scanned)")
        original_bytes = file_bytes
        if trace_ctx:
            start_span(trace_ctx, "detect_page_kind", inputs={"file_type": file_type, "size_bytes": len(original_bytes)})
        overall_kind, page_status = text_layer.classify_document(
            original_bytes, file_type, min_text_length=cfg.min_text_length
        )
        if trace_ctx:
            end_span(trace_ctx, "detect_page_kind", outputs={"overall_kind": overall_kind, "pages": len(page_status)})

        flow.step("detect", "ok", f"{overall_kind} ({len(page_status)} page(s))", page_status=page_status)
        logger.info(f"[pipeline] {document_id}: detected as {overall_kind} ({len(page_status)} pages)")

        digital_pages = {int(p) for p, k in page_status.items() if k == text_layer.DIGITAL}
        scanned_pages = {int(p) for p, k in page_status.items() if k == text_layer.SCANNED}

        # Decide the extraction ROUTE now — BEFORE any OCR — from the agent's Input Mode + the
        # detected kind + the model's vision capability. 'vision' skips OCR entirely (the model
        # reads page images directly); 'text'/'text_vision' run the existing OCR/native-text path.
        # decide_extraction_input may raise PipelineError (vision requested on a text-only model);
        # that propagates to the outer except -> _fail with a clear message.
        from agentcore.services.database.models.model_registry.model import ModelRegistry
        _route_reg = None
        if cfg.model_id:
            try:
                _route_reg = await session.get(ModelRegistry, UUID(str(cfg.model_id)))
            except (ValueError, TypeError):
                _route_reg = None
        route = decide_extraction_input(cfg.input_mode, overall_kind, _supports_vision(_route_reg), cfg.has_ocr_node)
        if cfg.input_mode == "text_vision" and route == "vision":
            flow.step("input", "warn", "text+vision requested but the document has no native/OCR text — using vision only")
        logger.info(f"[pipeline] {document_id}: extraction route={route} (input_mode={cfg.input_mode}, kind={overall_kind})")

        # 3+4. Read text by page kind. Native text MUST come from the ORIGINAL bytes
        # (preprocessing rasterizes a PDF and would destroy a digital page's text layer);
        # OCR runs only when scanned pages exist and never replaces native digital text.
        tokens: list[dict] = []
        if route == "vision":
            # Vision route: skip OCR/native-text entirely — the vision model reads the page images
            # directly. This is what lets a scanned PDF be extracted with NO OCR (Intel-Mac path).
            flow.step("ocr", "ok", "vision route — OCR skipped (model reads page images directly)")
        elif overall_kind == text_layer.DIGITAL:
            if trace_ctx:
                start_span(trace_ctx, "extract_native_text", inputs={"file_type": file_type})
            _, tokens = text_layer.extract_native_text(original_bytes, file_type)
            if trace_ctx:
                end_span(trace_ctx, "extract_native_text", outputs={"token_count": len(tokens)})

            _chars = sum(len(str(t.get("text", ""))) for t in tokens)
            flow.step("native", "ok", f"digital — native text layer used (no OCR): {len(tokens)} token(s), {_chars} chars",
                      io={"output": {"char_count": _chars, "token_count": len(tokens),
                                     "text_sample": _clip(" ".join(str(t.get("text", "")) for t in tokens))}})
        elif overall_kind == text_layer.SCANNED:
            if trace_ctx:
                start_span(trace_ctx, "preprocessing", inputs={"file_type": file_type})
            ocr_bytes = await _maybe_preprocess(original_bytes, file_type, cfg, flow)
            if trace_ctx:
                end_span(trace_ctx, "preprocessing", outputs={"bytes_length": len(ocr_bytes)})
            if ocr_bytes is not original_bytes:
                await _save_artifact(storage, agent_scope, file_name, ocr_bytes)
                logger.info(f"[pipeline] {document_id}: corrected file saved back to storage for preview")

            if trace_ctx:
                start_span(trace_ctx, "ocr", inputs={"ocr_lang": cfg.ocr_lang})
            tokens = await run_paddle_ocr(ocr_bytes, file_type, cfg.ocr_lang)
            if trace_ctx:
                end_span(trace_ctx, "ocr", outputs={"token_count": len(tokens)})

            _chars = sum(len(str(t.get("text", ""))) for t in tokens)
            flow.step("ocr", "ok", f"PaddleOCR ({cfg.ocr_lang}) output: {len(tokens)} token(s), {_chars} chars",
                      io={"output": {"char_count": _chars, "token_count": len(tokens),
                                     "text_sample": _clip(" ".join(str(t.get("text", "")) for t in tokens))}})
        else:  # mixed: native text for digital pages, OCR for scanned pages
            if trace_ctx:
                start_span(trace_ctx, "extract_native_text", inputs={"file_type": file_type})
            _, native = text_layer.extract_native_text(original_bytes, file_type)
            if trace_ctx:
                end_span(trace_ctx, "extract_native_text", outputs={"token_count": len(native)})

            tokens += [t for t in native if int(t.get("page_number", 1) or 1) in digital_pages]

            if trace_ctx:
                start_span(trace_ctx, "preprocessing", inputs={"file_type": file_type})
            ocr_bytes = await _maybe_preprocess(original_bytes, file_type, cfg, flow)
            if trace_ctx:
                end_span(trace_ctx, "preprocessing", outputs={"bytes_length": len(ocr_bytes)})
            if ocr_bytes is not original_bytes:
                await _save_artifact(storage, agent_scope, file_name, ocr_bytes)
                logger.info(f"[pipeline] {document_id}: corrected file saved back to storage for preview")

            if trace_ctx:
                start_span(trace_ctx, "ocr", inputs={"ocr_lang": cfg.ocr_lang})
            ocr = await run_paddle_ocr(ocr_bytes, file_type, cfg.ocr_lang)
            if trace_ctx:
                end_span(trace_ctx, "ocr", outputs={"token_count": len(ocr)})

            tokens += [t for t in ocr if int(t.get("page_number", 1) or 1) in scanned_pages]
            flow.step("ocr", "ok", f"mixed: native {len(digital_pages)} digital + OCR {len(scanned_pages)} scanned page(s)",
                      io={"output": {"token_count": len(tokens),
                                     "text_sample": _clip(" ".join(str(t.get("text", "")) for t in tokens))}})

        # HOOK: B32 multi-doc split — operate on the WHOLE document (before page selection).
        # If the file holds several documents, materialise a child doc+job each and stop here;
        # the children are normal standalone docs that process independently.
        child_ids = await _hook_split(session, storage, doc, tokens, page_status, cfg, flow)
        if child_ids:
            enqueued_ok = 0
            for cid in child_ids:
                try:
                    await enqueue_document(session, cid)
                    enqueued_ok += 1
                except Exception as e:  # one bad child must not abort the rest
                    flow.step("split", "warn", f"could not enqueue child {cid}: {e}")
            # Don't mark the parent 'split' (a successful terminal state) if NOTHING was
            # enqueued — that would silently drop the document. Fail it so it's visible/retryable.
            if enqueued_ok == 0:
                raise PipelineError(
                    f"multi-doc split produced {len(child_ids)} child document(s) but none could be enqueued"
                )
            if enqueued_ok < len(child_ids):
                flow.step("split", "warn", f"only {enqueued_ok}/{len(child_ids)} child document(s) enqueued")
            doc = await session.get(IdpDocument, document_id)
            doc.status = "split"
            doc.processing_completed_at = _utcnow()
            job.status = "completed"
            job.completed_at = _utcnow()
            job.steps_completed = flow.steps_ok
            job.log = flow.events
            session.add(doc)
            session.add(job)
            await session.commit()
            await _save_artifact(
                storage, agent_scope, f"flow_logs/{document_id}/flow.log", flow.render().encode("utf-8")
            )
            # Webhook Output: a split parent has no extraction of its own -> notify with an empty
            # payload and status "skipped" (each child notifies from its own finalize). Non-fatal.
            await _maybe_webhook(cfg, document_id, job.id, {}, "skipped", flow)
            if trace_ctx:
                await end_idp_trace(trace_ctx, outputs={"status": "split", "child_ids": [str(cid) for cid in child_ids]})
            return

        # HOOK: B30 visual element detection — uses the original bytes (rasterised internally).
        if trace_ctx:
            start_span(trace_ctx, "visual_element_detection", inputs={"detect_enabled": cfg.detect_enabled})
        await _hook_detect(session, document_id, original_bytes, file_type, cfg, flow)
        if trace_ctx:
            end_span(trace_ctx, "visual_element_detection", outputs={"detect_enabled": cfg.detect_enabled})

        # Authoritative page count = max(classified pages, highest token page) so multi-sheet
        # office files (one classification entry, many token "pages") are NOT truncated.
        token_max = max([int(t.get("page_number", 1) or 1) for t in tokens], default=1)
        total_pages = max(len(page_status), token_max)
        doc.page_count = total_pages

        # page selection (never empty; uses the authoritative page count)
        selected = _selected_pages(total_pages, cfg)
        tokens = [t for t in tokens if int(t.get("page_number", 1) or 1) in selected]
        dropped = total_pages - len(selected)
        status = "warn" if dropped > 0 else "ok"
        detail = f"selected {sorted(selected)} of {total_pages}"
        if dropped > 0:
            detail += f" ({dropped} page(s) skipped by Page Selector)"
        flow.step("pages", status, detail)

        # merged, page-numbered, layout-reconstructed text (citation-friendly)
        if trace_ctx:
            start_span(trace_ctx, "text_merge", inputs={"token_count": len(tokens)})
        # NOTE: this unconditional merge of digital + OCR tokens IS the "Output Parser" node's
        # behavior; the node has no settings, so no per-node resolution is needed (see plan Task 7).
        merged_text = build_merged_text(tokens)
        if trace_ctx:
            end_span(trace_ctx, "text_merge", outputs={"char_count": len(merged_text)})

        await _save_artifact(
            storage, agent_scope, f"ocr_output/{document_id}/ocr_text.txt", merged_text.encode("utf-8")
        )
        if merged_text.strip():
            flow.step(
                "text", "ok",
                f"merged page-numbered text built: {len(merged_text)} chars across {len(selected)} page(s) "
                f"→ saved to ocr_output/{document_id}/ocr_text.txt",
                io={"output": {
                    "chars": len(merged_text),
                    "pages": len(selected),
                    "path": f"ocr_output/{document_id}/ocr_text.txt",
                    "text_sample": _clip(merged_text),
                }},
            )
        else:
            flow.step("text", "warn", "no text extracted from the document")

        logger.info(f"[pipeline] {document_id}: starting classification (classify_enabled={cfg.classify_enabled})")
        # HOOK: B29 classification — may set cfg.predicted_type + auto-select a Named Config
        # (mutates cfg.field_config_id / cfg.extraction_mode) before extraction runs.
        # Returns True when doc_types filter is active and document type is not in selected list.
        if trace_ctx:
            start_span(trace_ctx, "classification", inputs={"classify_enabled": cfg.classify_enabled})
        classify_skip = await _hook_classify(session, document_id, graph_agent, merged_text, cfg, flow)
        
        classify_usage = None
        classify_model = None
        # Extract classification token usage if available in the classification result or in the session context
        # (We can pass it if we intercept the result; since _hook_classify returns bool, we don't have the result dict.
        # But wait! We can update _hook_classify or just let the span record the classification step. Let's keep it simple.)
        
        if trace_ctx:
            end_span(
                trace_ctx, "classification",
                outputs={
                    "classify_skip": classify_skip,
                    "predicted_type": doc.predicted_type if doc else None,
                    "extraction_mode": cfg.extraction_mode,
                    "field_config_id": str(cfg.field_config_id) if cfg.field_config_id else None
                }
            )

        if classify_skip:
            doc.status = "skipped"
            doc.processing_completed_at = _utcnow()
            job.status = "completed"
            job.completed_at = _utcnow()
            job.steps_completed = flow.steps_ok
            job.log = flow.events
            session.add(doc)
            session.add(job)
            await session.commit()
            # Webhook Output: a doc skipped by classification still notifies (empty extraction). Non-fatal.
            await _maybe_webhook(cfg, document_id, job.id, {}, "skipped", flow)
            if trace_ctx:
                await end_idp_trace(trace_ctx, outputs={"status": "skipped"})
            return

        logger.info(f"[pipeline] {document_id}: starting extraction (mode={cfg.extraction_mode}, route={route})")
        # 5. extract — vision route sends page images to the model; text routes run OCR/native
        # text (+ long-doc chunking). The legacy `multimodal_requested` flag (from the extraction_mode
        # string) only downgrades to text; the real vision path is driven by Input Mode (route) above.
        if cfg.multimodal_requested and route in ("text", "text_vision"):
            flow.step("extract", "warn", "legacy multimodal flag → using text extraction (set Input Mode = Vision for the vision path)")
        llm = await _build_llm(session, cfg.model_id)
        vision_used = route in ("vision", "text_vision")
        _rmid = getattr(llm, "registry_model_id", None)
        page_images = None  # set on the vision path below; kept None for text routes

        # HOOK: B35 long-document handling — long docs are split into section/page chunks,
        # extracted per chunk and merged; short docs take the single-pass path unchanged.
        use_long_doc = route in ("text", "text_vision") and bool(cfg.long_doc_enabled) and long_doc.should_chunk(
            tokens, max_pages=cfg.long_doc_max_pages, max_tokens=cfg.long_doc_max_tokens
        )
        try:
            if route in ("vision", "text_vision"):
                # VISION path: render the selected pages to images and let the model read them
                # directly. No page cap (product decision) — but log the payload size and turn a
                # 413 / timeout / context-window error into a readable message (not a raw 413).
                # CPU-bound rasterization — off the event loop, or every HTTP request stalls.
                page_images = await asyncio.to_thread(render_document_images, original_bytes, file_type, selected)
                if not page_images:
                    raise PipelineError("vision mode: could not render any page images from the document")
                _payload_kb = sum(len(b) for b, _ in page_images) // 1024
                logger.info(
                    f"[pipeline] {document_id}: vision extraction — {len(page_images)} page image(s), ~{_payload_kb} KB payload"
                )
                if trace_ctx:
                    start_span(
                        trace_ctx, "extraction",
                        inputs={"page_images": len(page_images), "payload_kb": _payload_kb, "vision": True},
                        observation_type="generation",
                    )
                try:
                    if cfg.extraction_mode == MODE_NAMED and cfg.field_config_id:
                        fc_msgs = await _named_config_vision_messages(session, cfg.field_config_id)
                        extracted = await extract_vision(
                            page_images, llm_model=llm, field_config_messages=fc_msgs,
                            ocr_text=(merged_text if route == "text_vision" else None),
                        )
                    else:
                        v_prompt = cfg.prompt or "Extract all key fields from this document. Return structured JSON."
                        extracted = await extract_vision(
                            page_images, llm_model=llm, prompt=v_prompt,
                            ocr_text=(merged_text if route == "text_vision" else None),
                        )
                except PipelineError:
                    raise
                except Exception as ve:
                    # No page cap (product decision) — but map rate-limit / oversize / timeout to a
                    # readable, actionable message instead of a raw 500/stack.
                    raise PipelineError(f"vision extraction failed{_vision_error_hint(str(ve))}: {ve}") from ve
                usage = None
                model = None
                if isinstance(extracted, dict) and "_usage" in extracted:
                    usage = extracted.pop("_usage")
                    if usage:
                        model = usage.get("model")
                if trace_ctx:
                    end_span(trace_ctx, "extraction", outputs=extracted, usage=usage, model=model)
                flow.step("extract", "ok",
                          f"vision extraction — {len(page_images)} page image(s) (~{_payload_kb} KB) read by the model")
            elif use_long_doc:
                sections = long_doc.build_sections(tokens)
                try:
                    await long_doc.persist_sections(session, document_id, sections)
                except Exception as e:  # pragma: no cover - section persistence is best-effort
                    flow.step("long_doc", "warn", f"could not persist sections ({e})")
                chunk_texts = long_doc.chunks_for_extraction(
                    tokens,
                    sections,
                    max_tokens=cfg.long_doc_max_tokens,
                    overlap_tokens=cfg.long_doc_overlap_tokens,
                )
                flow.step("long_doc", "ok", f"{len(sections)} section(s) -> {len(chunk_texts)} chunk(s)")
                
                if trace_ctx:
                    start_span(trace_ctx, "extraction", inputs={"chunk_count": len(chunk_texts), "long_doc": True}, observation_type="generation", registry_model_id=_rmid)

                results: list[dict] = []
                last_err: str | None = None
                aggregated_usage = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
                models_used = set()

                for i, ct in enumerate(chunk_texts):
                    if trace_ctx:
                        start_span(trace_ctx, f"extraction_chunk_{i+1}", inputs={"chunk_index": i}, observation_type="generation", registry_model_id=_rmid)
                    try:
                        res = await _extract_text(session, cfg, llm, ct)
                        
                        usage = None
                        model = None
                        if isinstance(res, dict) and "_usage" in res:
                            usage = res.pop("_usage")
                            if usage:
                                model = usage.get("model")
                                aggregated_usage["input_tokens"] += usage.get("input_tokens", 0)
                                aggregated_usage["output_tokens"] += usage.get("output_tokens", 0)
                                aggregated_usage["total_tokens"] += usage.get("total_tokens", 0)
                                if model:
                                    models_used.add(model)
                                    
                        if trace_ctx:
                            end_span(trace_ctx, f"extraction_chunk_{i+1}", outputs=res, usage=usage, model=model, registry_model_id=_rmid)

                        if isinstance(res, dict) and res.get("error"):
                            last_err = str(res.get("error"))
                            flow.step("long_doc", "warn", f"chunk {i + 1}/{len(chunk_texts)} error: {last_err}")
                        results.append(res)
                    except PipelineError:
                        if trace_ctx:
                            end_span(trace_ctx, f"extraction_chunk_{i+1}", error="PipelineError", registry_model_id=_rmid)
                        raise
                    except Exception as e:
                        if trace_ctx:
                            end_span(trace_ctx, f"extraction_chunk_{i+1}", error=str(e), registry_model_id=_rmid)
                        last_err = str(e)
                        flow.step("long_doc", "warn", f"chunk {i + 1}/{len(chunk_texts)} failed ({e})")
                if chunk_texts and not results:
                    raise PipelineError(f"extraction failed: all {len(chunk_texts)} chunk(s) failed ({last_err})")
                extracted = (
                    long_doc.merge_chunk_extractions(results, strategy=cfg.dedup_strategy)
                    if results
                    else {"headers": {}, "line_items": []}
                )
                if trace_ctx:
                    primary_model = list(models_used)[0] if models_used else None
                    end_span(
                        trace_ctx, "extraction",
                        outputs=extracted,
                        usage=aggregated_usage if aggregated_usage["total_tokens"] > 0 else None,
                        model=primary_model,
                        registry_model_id=_rmid
                    )
            else:
                if trace_ctx:
                    start_span(trace_ctx, "extraction", inputs={"text_length": len(merged_text), "long_doc": False}, observation_type="generation", registry_model_id=_rmid)

                extracted = await _extract_text(session, cfg, llm, merged_text)
                
                usage = None
                model = None
                if isinstance(extracted, dict) and "_usage" in extracted:
                    usage = extracted.pop("_usage")
                    if usage:
                        model = usage.get("model")

                if trace_ctx:
                    end_span(trace_ctx, "extraction", outputs=extracted, usage=usage, model=model, registry_model_id=_rmid)
        except PipelineError:
            if trace_ctx:
                end_span(trace_ctx, "extraction", error="PipelineError", registry_model_id=_rmid)
            raise
        except Exception as e:
            if trace_ctx:
                end_span(trace_ctx, "extraction", error=str(e), registry_model_id=_rmid)
            raise PipelineError(f"extraction failed: {e}") from e

        # Unify failure semantics across extraction modes: extract_dynamic swallows
        # LLM/transport errors into an {"error": ...} dict instead of raising. If the
        # extractor reports an error AND produced no fields, treat it as a fatal failure
        # (same terminal state as a named-config error) rather than a clean pending_review.
        if isinstance(extracted, dict) and extracted.get("error"):
            n_fields = len(extracted.get("headers", {})) + len(extracted.get("line_items", []))
            if n_fields == 0:
                raise PipelineError(f"extraction failed: {extracted.get('error')}")
            flow.step("extract", "warn", f"partial extraction: {extracted.get('error')}")

        # HOOK: B31 math reconcile — re-prompt the LLM to fix arithmetic BEFORE saving.
        if trace_ctx:
            start_span(trace_ctx, "math_reconcile", inputs={"attempts_max": cfg.math_reconcile_max_attempts}, observation_type="generation", registry_model_id=_rmid)
        extracted = await _hook_math_reconcile(
            extracted, llm, job, cfg, flow,
            page_images=(page_images if vision_used else None),
            ocr_text=(merged_text if (merged_text and merged_text.strip()) else None),
        )
        
        reconcile_usage = None
        reconcile_model = None
        if isinstance(extracted, dict) and "_usage" in extracted:
            reconcile_usage = extracted.pop("_usage")
            if reconcile_usage:
                reconcile_model = reconcile_usage.get("model")
                
        if trace_ctx:
            end_span(
                trace_ctx, "math_reconcile",
                outputs=extracted,
                usage=reconcile_usage,
                model=reconcile_model,
                registry_model_id=_rmid
            )

        # 6. save. NOTE: save_extraction_results commits internally, so extracted rows
        # persist even if a later step fails — intentional partial-result persistence
        # (the failure path also saves partials; the HITL review flow handles partials).
        _headers = extracted.get("headers", {}) if isinstance(extracted, dict) else {}
        n_head = len(_headers)
        n_line = len(extracted.get("line_items", [])) if isinstance(extracted, dict) else 0
        
        if trace_ctx:
            start_span(trace_ctx, "save_results", inputs={"n_headers": n_head, "n_line_items": n_line})
        overall_conf = await save_extraction_results(
            session=session,
            document_id=document_id,
            job_id=job.id,
            extraction_result=extracted if isinstance(extracted, dict) else {},
            ocr_tokens=tokens,   # grounding only — `vision_used` no longer affects the score
        )
        if trace_ctx:
            end_span(trace_ctx, "save_results", outputs={"overall_confidence": overall_conf})
            
        _names = list(_headers.keys())
        _preview = ", ".join(_names[:12]) + ("…" if len(_names) > 12 else "")
        job.extraction_mode_used = cfg.extraction_mode
        _ex = extracted if isinstance(extracted, dict) else {}
        flow.step(
            "extract", "ok",
            f"LLM ({cfg.extraction_mode}) returned {n_head} header field(s) [{_preview}] "
            f"+ {n_line} line-item row(s); overall confidence={overall_conf:.2f} → saved to "
            f"idp_extracted_headers/idp_extracted_line_items",
            io={
                "input": {"mode": cfg.extraction_mode, "config_name": cfg.config_name,
                          "field_config_id": str(cfg.field_config_id) if cfg.field_config_id else None,
                          "chars": len(merged_text), "text_sample": _clip(merged_text)},
                "output": {
                    "headers": _io_headers(_ex),
                    "line_item_rows": n_line,
                    "sample_rows": _io_sample_rows(_ex),
                    "overall_confidence": round(float(overall_conf), 4),
                },
            },
        )

        # HOOK: B36 cross-page entity linking — record entities seen on multiple pages.
        if cfg.entity_linking_enabled:
            try:
                from agentcore.services.idp.entity_linking import link_entities
                if trace_ctx:
                    start_span(trace_ctx, "entity_linking")
                n_links = await link_entities(
                    session, document_id, extracted if isinstance(extracted, dict) else {}, tokens
                )
                if trace_ctx:
                    end_span(trace_ctx, "entity_linking", outputs={"links_count": n_links})
                if n_links:
                    flow.step("entity_link", "ok", f"{n_links} cross-page entity link(s)")
            except Exception as e:  # pragma: no cover - linking must never fail the doc
                if trace_ctx:
                    end_span(trace_ctx, "entity_linking", error=str(e))
                flow.step("entity_link", "warn", f"skipped ({e})")

        # HOOK: SAP two-way / three-way matching — compare extracted invoice against SAP PO/GR.
        sap_match_result: str | None = None
        try:
            sap_match_result = await _hook_sap_matching(
                session=session,
                document_id=document_id,
                idp_agent_id=idp_agent.id,
                extracted=extracted if isinstance(extracted, dict) else {},
                flow=flow,
            )
        except Exception as _e:  # matching must never abort the pipeline
            flow.step("sap_match", "warn", f"skipped ({_e})")

        # 7. route — the rules engine (B17) decides auto_approve vs pending_review.
        if trace_ctx:
            start_span(trace_ctx, "rules_evaluation", inputs={"overall_confidence": overall_conf})
        new_status = await _route(session, document_id, job.id, idp_agent.id, overall_conf, cfg, flow, match_result=sap_match_result)
        if trace_ctx:
            end_span(trace_ctx, "rules_evaluation", outputs={"new_status": new_status})

        # 8. finalize
        doc = await session.get(IdpDocument, document_id)  # refresh (save committed)
        doc.status = new_status
        # Multi-Branch Router: label the document by which route its route_field matched (or
        # "unmatched"). No router node -> _match_route returns None -> route_label left untouched
        # (backward-compatible). Headers are keyed by job.id (their save key), same as _route.
        _route_headers = (
            await session.exec(select(IdpExtractedHeader).where(IdpExtractedHeader.job_id == job.id))
        ).all()
        route_label = _match_route(cfg, doc, headers=_route_headers)
        if route_label is not None:
            doc.route_label = route_label
            flow.step("route_branch", "ok", f"multi-branch route: {route_label}")
        doc.processing_completed_at = _utcnow()
        ms = int((time.monotonic() - t0) * 1000)
        flow.step("finalize", "ok", f"completed in {ms} ms")
        job.status = "completed"
        job.completed_at = _utcnow()
        job.processing_time_ms = ms
        job.steps_completed = flow.steps_ok
        job.log = flow.events
        session.add(doc)
        session.add(job)
        await session.commit()
        await _save_artifact(
            storage, agent_scope, f"flow_logs/{document_id}/flow.log", flow.render().encode("utf-8")
        )
        # Webhook Output: notify the configured endpoint with the finalized result. Non-fatal and a
        # no-op when no Webhook Output node is present (cfg.webhook_url is None).
        await _maybe_webhook(cfg, document_id, job.id, extracted, new_status, flow)
        if trace_ctx:
            await end_idp_trace(trace_ctx, outputs={"status": new_status}, processing_time_ms=ms)

    except Exception as e:
        if trace_ctx:
            try:
                await end_idp_trace(trace_ctx, error=e)
            except Exception as tr_err:
                logger.debug(f"Error ending IDP trace: {tr_err}")
        message = str(e)
        if not isinstance(e, PipelineError):
            logger.exception(f"[pipeline] unexpected error processing {document_id}")
        flow.step("fatal", "error", message)
        await _fail(session, doc, job, flow, message)


def _apply_failure(doc, job, flow: FlowLog, message: str, *, touch_doc: bool) -> None:
    if touch_doc and doc is not None:
        doc.status = "failed"
        doc.failed_at = _utcnow()
    if job is not None:
        job.status = "failed"
        job.error_message = message[:2000]
        job.completed_at = _utcnow()
        job.steps_completed = flow.steps_ok
        job.log = flow.events


async def _fail(session, doc, job, flow: FlowLog, message: str, *, touch_doc: bool = True) -> None:
    """Record a failed run (best-effort, with its own rollback guard)."""
    # NOTE: the failure-path Webhook Output notification is a DEFERRED follow-up (Codex blocker #4):
    # _fail has no `cfg` and a failure can occur before cfg is resolved, so no _maybe_webhook here.
    doc_id = doc.id if doc is not None else None
    job_id = job.id if job is not None else None
    try:
        _apply_failure(doc, job, flow, message, touch_doc=touch_doc)
        if doc is not None:
            session.add(doc)
        if job is not None:
            session.add(job)
        await session.commit()
    except Exception:
        logger.exception("[pipeline] failed to persist failure state; retrying after rollback")
        await session.rollback()
        try:
            # re-fetch by PK and reapply the COMPLETE failure state (don't drop fields)
            doc2 = await session.get(IdpDocument, doc_id) if doc_id else None
            job2 = await session.get(IdpProcessingJob, job_id) if job_id else None
            _apply_failure(doc2, job2, flow, message, touch_doc=touch_doc)
            if doc2 is not None:
                session.add(doc2)
            if job2 is not None:
                session.add(job2)
            await session.commit()
        except Exception:
            logger.exception("[pipeline] could not record failure state")
    # best-effort flow.log artifact
    try:
        if doc is not None:
            await get_storage_service().save_file(
                # The document's own directory, not a reconstruction — see storage_scope.scope_of.
                agent_id=_split_storage_path(doc.file_path)[0],
                file_name=f"flow_logs/{doc.id}/flow.log",
                data=flow.render().encode("utf-8"),
            )
    except Exception:  # pragma: no cover
        pass


# ─────────────────────────────── enqueue ───────────────────────────────
async def enqueue_document(session, document_id: UUID) -> UUID:
    """Create a queued job and start the background pipeline. Returns the job id.

    Raises PipelineError if a job for this document is already queued/running.
    """
    # Lock the document row so two concurrent enqueues for the SAME document serialize
    # (the second waits, then sees the first's queued/running job and is rejected).
    doc = (
        await session.exec(
            select(IdpDocument).where(IdpDocument.id == document_id).with_for_update()
        )
    ).first()
    if doc is None:
        raise PipelineError(f"document {document_id} not found")

    existing = (
        await session.exec(
            select(IdpProcessingJob).where(
                IdpProcessingJob.document_id == document_id,
                IdpProcessingJob.status.in_(("queued", "running")),
            )
        )
    ).first()
    if existing is not None:
        raise PipelineError("a processing job for this document is already queued or running")

    job = IdpProcessingJob(document_id=document_id, agent_id=doc.agent_id, status="queued")
    session.add(job)
    await session.commit()
    await session.refresh(job)

    queue = get_queue_service()
    job_key = str(job.id)
    coro = process_document(document_id, job.id)
    try:
        queue.create_queue(job_key)
        queue.start_job(job_key, coro)
    except Exception as e:
        coro.close()  # the task was never scheduled — close it so it isn't "never awaited"
        # The job row was already committed; if it can't actually be started, mark it failed
        # so it doesn't sit 'queued' forever with no running task (a phantom job).
        logger.exception(f"[pipeline] could not start job {job_key}; marking it failed")
        try:
            job.status = "failed"
            job.error_message = f"could not start background job: {e}"[:2000]
            job.completed_at = _utcnow()
            session.add(job)
            await session.commit()
        except Exception:  # pragma: no cover - best-effort
            await session.rollback()
        raise PipelineError(f"could not start background job for document {document_id}") from e

    # Reap the queue entry once the job finishes. JobQueueService only auto-cleans
    # cancelled/errored tasks; process_document never raises, so without this every
    # processed document would leak a queue entry forever. The monitor runs as a
    # separate task so cleanup_job (which cancels) is never called on the live job task.
    try:
        _, _, task, _ = queue.get_queue_data(job_key)
        if task is not None:
            _spawn_monitor(_reap_when_done(queue, job_key, task))
    except Exception:  # pragma: no cover - reaping is best-effort
        logger.warning(f"[pipeline] could not schedule queue reap for {job_key}")
    return job.id


async def cancel_document(session, document_id: UUID) -> bool:
    """Cancel a queued/running IDP processing job for the document (the Stop button).

    Cancels the background task via the queue service and marks the job 'cancelled' + the document
    terminal. Returns True if an active job was cancelled, False if there was none.
    """
    job = (
        await session.exec(
            select(IdpProcessingJob)
            .where(
                IdpProcessingJob.document_id == document_id,
                IdpProcessingJob.status.in_(("queued", "running")),
            )
            .order_by(IdpProcessingJob.created_at.desc())
        )
    ).first()
    if job is None:
        return False

    job_key = str(job.id)
    # Mark the job + doc terminal FIRST, so the run's safety-net finalization (which forces
    # 'pending_review' for a non-terminal doc) can't override the cancellation as the task unwinds.
    # NOTE: neither status CHECK constraint allows 'cancelled', so use 'failed' + a clear message.
    job.status = "failed"
    job.error_message = "Cancelled by user."
    job.completed_at = _utcnow()
    session.add(job)
    doc = await session.get(IdpDocument, document_id)
    if doc is not None and doc.status in ("queued", "processing"):
        # 'cancelled' isn't in the IdpDocument.status CHECK constraint → use 'failed', and flag the
        # real reason in `extra` (IdpDocument has no error_message field) so the UI can distinguish a
        # user cancellation from a genuine failure.
        doc.status = "failed"
        doc.failed_at = _utcnow()
        doc.processing_completed_at = _utcnow()
        doc.extra = {**(doc.extra or {}), "cancelled": True, "cancel_reason": "Cancelled by user."}
        session.add(doc)
    await session.commit()

    # Cancel the running background task directly. NOTE: do NOT use queue.cleanup_job() here — it calls
    # task.exception() on the just-cancelled task, which re-raises CancelledError (a BaseException, so it
    # would escape). We cancel the task and let the existing reap monitor clean up the queue entry.
    try:
        _q, _em, task, _ts = get_queue_service().get_queue_data(job_key)
        if task is not None and not task.done():
            task.cancel()
    except Exception as e:  # noqa: BLE001 — the queue entry may already be gone (job just finished)
        logger.debug(f"[pipeline] cancel: no active task for job {job_key}: {e}")
    logger.info(f"[pipeline] {document_id}: processing cancelled by user (job {job_key})")
    return True


# Hold strong references to detached monitor tasks so they are not GC'd mid-run.
_MONITOR_TASKS: set = set()


def _spawn_monitor(coro) -> None:
    task = asyncio.create_task(coro)
    _MONITOR_TASKS.add(task)
    task.add_done_callback(_MONITOR_TASKS.discard)


async def _reap_when_done(queue, job_key: str, job_task) -> None:
    try:
        await asyncio.wait([job_task])
    except Exception:  # pragma: no cover
        pass
    try:
        await queue.cleanup_job(job_key)  # job_task is done now -> no self-cancel
    except Exception:  # pragma: no cover
        logger.warning(f"[pipeline] queue cleanup failed for {job_key}")
