"""Resolve the IDP processing config from a saved agent graph (with DB fallbacks).

The orchestrator is hybrid/config-driven: it reads the agent's saved React-Flow graph
(``Agent.data = {nodes, edges}``) to learn which steps to run, the chosen model, and the
extraction settings, falling back to the ``idp_agents`` columns where the graph is absent.
This mirrors the legacy ``process_backend_bulk.py`` (parse graph JSON -> run a fixed pipeline).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from uuid import UUID

from loguru import logger
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from agentcore.services.database.models.idp.config import IdpAgent, IdpFieldConfiguration

# Node display names on the canvas (components/IDP/*, components/models/registry_model.py)
_N_EXTRACTOR = "AI Field Extractor"
_N_MODEL = "Large Language Model"
_N_SCAN = "Scan Corrector"
_N_PAGE = "Page Selector"
_N_DETECTOR = "Document Type Detector"
# Differentiator nodes (frontend-only nodes; their presence on the canvas drives the toggles)
_N_CLASSIFIER = "Document Classifier"
_N_DETECTION = "Visual Element Detection"
_N_MATH = "Math Reconcile"
# Visual Element Detection node toggles -> the element types the backend detector ACTUALLY
# emits (signature, checkbox, qr, barcode). Stamps/logos/handwriting are not yet detected by
# the backend, so those node toggles are intentionally NOT mapped — mapping them would put a
# never-emitted type into the filter and silently drop everything. If a user selects only the
# unsupported toggles (or none), the set is empty -> None -> detect all supported types.
_DETECT_FIELD_TO_TYPES = {
    "detect_signatures": ("signature",),
    "detect_checkboxes": ("checkbox",),
    "detect_qr": ("qr", "barcode"),
}

# Service-level extraction modes (multimodal is parked -> downgraded to a text mode)
MODE_DYNAMIC = "dynamic_prompt"
MODE_NAMED = "named_config"


@dataclass
class ResolvedPipelineConfig:
    model_id: str | None
    extraction_mode: str  # MODE_DYNAMIC | MODE_NAMED
    prompt: str | None
    config_name: str | None
    field_config_id: UUID | None
    multimodal_requested: bool
    # page select
    page_selection_mode: str
    first_n_pages: int
    page_range_start: int
    page_range_end: int
    # detector
    allowed_extensions: set[str] = field(default_factory=set)
    skip_unmatched: bool = True
    min_text_length: int = 50
    # scan corrector
    fix_skew: bool = True
    skew_threshold: float = 0.5
    fix_rotation: bool = True
    allowed_angles: list[int] = field(default_factory=lambda: [90, 180, 270])
    # routing / behaviour
    default_rule_action: str = "pending_review"
    confidence_threshold: float = 0.8
    multi_doc_split: bool = False
    ocr_lang: str = "en"
    # differentiators. The three node-features turn ON when their canvas node is present
    # (Document Classifier / Visual Element Detection / Math Reconcile), with idp_agent.extra
    # as a fallback/override. The two automatic backend features default ON but only do work
    # when the document warrants it (long docs / cross-page repeats).
    classify_enabled: bool = False
    classify_auto_select: bool = True
    classify_threshold: float = 0.7
    detect_enabled: bool = False
    detect_enabled_types: set[str] | None = None
    math_reconcile_enabled: bool = False
    math_reconcile_max_attempts: int = 2
    math_reconcile_tolerance: float = 0.01
    long_doc_enabled: bool = True
    long_doc_max_pages: int = 8
    long_doc_max_tokens: int = 12000
    long_doc_overlap_tokens: int = 256
    entity_linking_enabled: bool = True
    dedup_strategy: str = "keep_highest_confidence"
    rules_operator: str = "AND"
    canvas_rules: list[dict] = field(default_factory=list)
    approval_field: str = "rule_action"
    approve_value: str = "auto_approve"


# ───────────────────────── graph helpers ─────────────────────────
def _nodes(data: dict | None) -> list[dict]:
    return (data or {}).get("nodes", []) if isinstance(data, dict) else []


def _find_node(data: dict | None, display_name: str) -> dict | None:
    for node in _nodes(data):
        if (node.get("data", {}).get("node", {}) or {}).get("display_name") == display_name:
            return node
    return None


def _field(node: dict | None, name: str, default=None):
    if not node:
        return default
    tmpl = node.get("data", {}).get("node", {}).get("template", {}) or {}
    fdef = tmpl.get(name)
    if isinstance(fdef, dict) and fdef.get("value") not in (None, ""):
        return fdef.get("value")
    return default


def _resolve_model_id_from_graph(data: dict | None) -> str | None:
    """Read the model registry UUID from the 'Large Language Model' node.

    The ``registry_model`` value is formatted ``"display | model_name | uuid"``.
    """
    node = _find_node(data, _N_MODEL)
    raw = _field(node, "registry_model")
    if not raw or not isinstance(raw, str):
        return None
    parts = [p.strip() for p in raw.split("|")]
    if len(parts) >= 3 and parts[2]:
        return parts[2]
    return None


def _normalize_mode(graph_mode: str | None, agent_mode: str | None) -> tuple[str, bool]:
    """Return (service_mode, multimodal_requested). Multimodal is parked -> downgraded."""
    m = (graph_mode or agent_mode or "").strip().lower()
    multimodal = "multimodal" in m
    if m in ("named_config", "field_configuration", "multimodal_config"):
        return MODE_NAMED, multimodal
    # dynamic_prompt, dynamic_prompting, multimodal_prompt, or unknown -> dynamic
    return MODE_DYNAMIC, multimodal


def _to_int(v, default: int) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def _to_float(v, default: float) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _as_bool(v, default: bool) -> bool:
    """Coerce a config value to bool. Strings are interpreted ('false'/'0'/'no' -> False)
    rather than using truthiness (which would make the string 'false' True)."""
    if v is None:
        return default
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return v != 0
    if isinstance(v, str):
        s = v.strip().lower()
        if s in ("true", "1", "yes", "y", "on"):
            return True
        if s in ("false", "0", "no", "n", "off", ""):
            return False
    return default


def _parse_angles(v) -> list[int]:
    if isinstance(v, str):
        out = []
        for part in v.split(","):
            part = part.strip()
            if part.isdigit():
                out.append(int(part))
        return out or [90, 180, 270]
    return [90, 180, 270]


def _parse_ext(v) -> set[str]:
    if isinstance(v, str):
        return {e.strip().lower().lstrip(".") for e in v.split(",") if e.strip()}
    return set()


async def resolve_pipeline_config(
    session: AsyncSession, idp_agent: IdpAgent, base_agent
) -> ResolvedPipelineConfig:
    """Build the resolved config from the agent graph, with idp_agents fallbacks."""
    data = getattr(base_agent, "data", None)

    extractor = _find_node(data, _N_EXTRACTOR)
    scan = _find_node(data, _N_SCAN)
    page = _find_node(data, _N_PAGE)
    detector = _find_node(data, _N_DETECTOR)

    # extraction mode / prompt / config
    graph_mode = _field(extractor, "extraction_mode")
    mode, multimodal_requested = _normalize_mode(graph_mode, idp_agent.extraction_mode)
    prompt = _field(extractor, "prompt") or idp_agent.dynamic_prompt
    config_name = _field(extractor, "config_name")

    # resolve named-config -> field_config_id (graph name first, else idp_agent column).
    # Deterministic when several configs share a name: prefer the agent's org (unique by the
    # (org_id, name) constraint), else the NEWEST active config of that name — never an arbitrary
    # .first() over unordered rows.
    field_config_id: UUID | None = idp_agent.field_config_id
    if mode == MODE_NAMED and config_name:
        try:
            base_q = select(IdpFieldConfiguration).where(
                IdpFieldConfiguration.name == config_name,
                IdpFieldConfiguration.deleted_at.is_(None),
                IdpFieldConfiguration.is_active.is_(True),
            )
            agent_org_id = getattr(base_agent, "org_id", None)
            row = None
            if agent_org_id is not None:
                row = (
                    await session.exec(
                        base_q.where(IdpFieldConfiguration.org_id == agent_org_id)
                        .order_by(IdpFieldConfiguration.created_at.desc())
                    )
                ).first()
            if row is None:  # no org-scoped match -> ONLY global configs (org_id NULL), never
                # another tenant's private config. Prefer a real global config over a catalogue
                # template (is_template False sorts first), then newest. Deterministic + tenant-safe.
                row = (
                    await session.exec(
                        base_q.where(IdpFieldConfiguration.org_id.is_(None)).order_by(
                            IdpFieldConfiguration.is_template.asc(),
                            IdpFieldConfiguration.created_at.desc(),
                        )
                    )
                ).first()
            if row:
                field_config_id = row.id
        except Exception as e:  # pragma: no cover - defensive
            logger.warning(f"[agent_config] config_name lookup failed: {e}")

    # model
    model_id = _resolve_model_id_from_graph(data)

    # routing / misc from idp_agent + extra
    extra = idp_agent.extra or {}
    default_rule_action = (idp_agent.default_rule_action or "pending_review").strip().lower()
    ocr_lang = str(extra.get("ocr_language") or _field(extractor, "language") or "en")

    # ── Resolve visual canvas configuration nodes if present on the graph ──
    chunking_node = _find_node(data, "Chunking Strategy")
    aggregator_node = _find_node(data, "Chunk Aggregator")
    router_node = _find_node(data, "Confidence Router")
    rules_node = _find_node(data, "Rules / Conditions")
    gate_node = _find_node(data, "Approval Gate")

    # 1. Chunking settings
    long_doc_enabled = chunking_node is not None or _as_bool(extra.get("long_doc_enabled"), True)
    long_doc_max_tokens = 12000
    if chunking_node:
        long_doc_max_tokens = _to_int(_field(chunking_node, "chunk_size_tokens"), 4096)
    else:
        long_doc_max_tokens = _to_int(extra.get("long_doc_max_tokens"), 12000)
    
    long_doc_overlap_tokens = _to_int(
        _field(chunking_node, "overlap_tokens") if chunking_node else extra.get("long_doc_overlap_tokens"), 256
    )

    # 2. Aggregator settings
    dedup_strategy = str(
        _field(aggregator_node, "dedup_strategy") if aggregator_node else extra.get("dedup_strategy") or "keep_highest_confidence"
    )

    # 3. Router settings
    if router_node:
        confidence_threshold = _to_float(_field(router_node, "threshold"), 0.8)
    else:
        confidence_threshold = _to_float(extra.get("confidence_threshold"), 0.8)

    # 4. Rules / Conditions settings
    rules_operator = "AND"
    canvas_rules = []
    if rules_node:
        rules_operator = str(_field(rules_node, "logic_operator") or "AND")
        raw_conds = _field(rules_node, "conditions")
        if isinstance(raw_conds, str) and raw_conds.strip():
            try:
                parsed_conds = json.loads(raw_conds)
                if isinstance(parsed_conds, list):
                    canvas_rules = parsed_conds
            except Exception as e:
                logger.warning(f"[agent_config] failed to parse rules conditions JSON: {e}")
        elif isinstance(raw_conds, list):
            canvas_rules = raw_conds

    # 5. Gate settings
    approval_field = str(
        _field(gate_node, "approval_field") if gate_node else extra.get("approval_field") or "rule_action"
    )
    approve_value = str(
        _field(gate_node, "approve_value") if gate_node else extra.get("approve_value") or "auto_approve"
    )

    # ── Differentiator toggles: a canvas node's PRESENCE enables the feature; idp_agent.extra
    # is a fallback/override (so API/test config and on-the-fly toggling still work). ──
    classifier_node = _find_node(data, _N_CLASSIFIER)
    detection_node = _find_node(data, _N_DETECTION)
    math_node = _find_node(data, _N_MATH)

    classify_enabled = classifier_node is not None or _as_bool(extra.get("classify_enabled"), False)
    classify_threshold = _to_float(
        _field(classifier_node, "confidence_threshold") if classifier_node else extra.get("classify_threshold"), 0.7
    )

    detect_enabled = detection_node is not None or _as_bool(extra.get("detect_enabled"), False)
    if detection_node is not None:
        # the node's per-element toggles select which element types to detect (none/unsupported -> all)
        selected: set[str] = set()
        for fname, etypes in _DETECT_FIELD_TO_TYPES.items():
            if _as_bool(_field(detection_node, fname), False):
                selected.update(etypes)
        detect_enabled_types = selected or None
    else:
        det_types = extra.get("detect_enabled_types")
        detect_enabled_types = (
            {str(x).strip().lower() for x in det_types if str(x).strip()}
            if isinstance(det_types, list) and det_types
            else None
        )

    math_reconcile_enabled = math_node is not None or _as_bool(extra.get("math_reconcile_enabled"), False)
    math_reconcile_max_attempts = _to_int(
        _field(math_node, "max_retries") if math_node else extra.get("math_reconcile_max_attempts"), 2
    )
    math_reconcile_tolerance = _to_float(
        _field(math_node, "tolerance") if math_node else extra.get("math_reconcile_tolerance"), 0.01
    )

    return ResolvedPipelineConfig(
        model_id=model_id,
        extraction_mode=mode,
        prompt=prompt,
        config_name=config_name,
        field_config_id=field_config_id,
        multimodal_requested=multimodal_requested,
        page_selection_mode=str(_field(page, "selection_mode", "all")),
        first_n_pages=_to_int(_field(page, "first_n_pages"), 3),
        page_range_start=_to_int(_field(page, "page_range_start"), 1),
        page_range_end=_to_int(_field(page, "page_range_end"), 5),
        allowed_extensions=_parse_ext(_field(detector, "allowed_extensions")),
        skip_unmatched=bool(_field(detector, "skip_unmatched", True)),
        min_text_length=_to_int(_field(detector, "min_text_length"), 50),
        fix_skew=bool(_field(scan, "fix_skew", True)),
        skew_threshold=_to_float(_field(scan, "skew_threshold"), 0.5),
        fix_rotation=bool(_field(scan, "fix_rotation", True)),
        allowed_angles=_parse_angles(_field(scan, "allowed_angles")),
        default_rule_action=default_rule_action,
        confidence_threshold=confidence_threshold,
        multi_doc_split=bool(idp_agent.multi_doc_split),
        ocr_lang=ocr_lang,
        classify_enabled=classify_enabled,
        classify_auto_select=_as_bool(extra.get("classify_auto_select"), True),
        classify_threshold=classify_threshold,
        detect_enabled=detect_enabled,
        detect_enabled_types=detect_enabled_types,
        math_reconcile_enabled=math_reconcile_enabled,
        math_reconcile_max_attempts=math_reconcile_max_attempts,
        math_reconcile_tolerance=math_reconcile_tolerance,
        long_doc_enabled=long_doc_enabled,
        long_doc_max_pages=_to_int(extra.get("long_doc_max_pages"), 8),
        long_doc_max_tokens=long_doc_max_tokens,
        long_doc_overlap_tokens=long_doc_overlap_tokens,
        entity_linking_enabled=_as_bool(extra.get("entity_linking_enabled"), True),
        dedup_strategy=dedup_strategy,
        rules_operator=rules_operator,
        canvas_rules=canvas_rules,
        approval_field=approval_field,
        approve_value=approve_value,
    )


# ───────────────────────── IdpAgent-on-save sync ─────────────────────────
def agent_contains_idp_nodes(data) -> bool:
    """True if the agent's saved graph contains IDP nodes (the AI Field Extractor or the IDP
    output node) — i.e. the builder configured it for document processing."""
    return _find_node(data, _N_EXTRACTOR) is not None or _find_node(data, "Processed Docs Output") is not None


def _idp_extraction_mode(graph_mode: str | None) -> str:
    """Map the canvas extraction_mode value to the IdpAgent.extraction_mode enum
    ('dynamic_prompting' | 'named_config' | 'multimodal')."""
    m = (graph_mode or "").strip().lower()
    if m in ("field_configuration", "named_config", "multimodal_config"):
        return "named_config"
    if m == "multimodal_prompt":
        return "multimodal"
    return "dynamic_prompting"


async def sync_idp_agent_from_graph(session: AsyncSession, base_agent, user_id) -> None:
    """Create or sync the IdpAgent row for an agent whose graph contains IDP nodes, so a
    builder-built agent becomes processable without a manual DB insert.

    No-op for non-IDP agents. Idempotent (upsert by agent_id). Best-effort — the caller MUST
    guard this so a sync failure never breaks the agent save.
    """
    data = getattr(base_agent, "data", None)
    if not agent_contains_idp_nodes(data):
        return

    extractor = _find_node(data, _N_EXTRACTOR)
    mode = _idp_extraction_mode(_field(extractor, "extraction_mode"))

    # Query WITHOUT the deleted_at filter: agent_id is UNIQUE across ALL rows (incl. soft-deleted),
    # so a deleted_at-only filter could miss a soft-deleted row and then INSERT a duplicate
    # (IntegrityError). If a soft-deleted row exists, reactivate it instead.
    existing = (await session.exec(select(IdpAgent).where(IdpAgent.agent_id == base_agent.id))).first()
    if existing is None:
        session.add(
            IdpAgent(
                agent_id=base_agent.id,
                extraction_mode=mode,
                default_rule_action="pending_review",
                is_active=True,
                created_by=user_id,
                updated_by=user_id,
            )
        )
    else:
        existing.extraction_mode = mode
        existing.is_active = True
        existing.deleted_at = None  # reactivate if it had been soft-deleted (agent still has IDP nodes)
        existing.updated_by = user_id
        existing.updated_at = datetime.now(timezone.utc)
        session.add(existing)
    await session.commit()
