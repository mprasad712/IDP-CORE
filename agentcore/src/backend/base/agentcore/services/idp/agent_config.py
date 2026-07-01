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

from agentcore.services.database.models.idp.config import IdpAgent, IdpAgentRule, IdpFieldConfiguration

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
_N_CONFIDENCE_ROUTER = "Confidence Router"
_N_CHUNKING = "Chunking Strategy"
_N_RULES = "Rules / Conditions"
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
    # NOTE: classify_model_id is defaulted below (in the defaults block) so it can't precede
    # the remaining non-default fields — see ``classify_model_id: str | None = None``.
    prompt: str | None
    config_name: str | None
    field_config_id: UUID | None
    multimodal_requested: bool
    # page select
    page_selection_mode: str
    first_n_pages: int
    page_range_start: int
    page_range_end: int
    # Classifier may target its own model (SLM/local); else shares the extraction model_id.
    classify_model_id: str | None = None
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
    classify_doc_types: list[str] = field(default_factory=list)
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
    nodes = (data or {}).get("nodes", []) if isinstance(data, dict) else []
    return nodes if isinstance(nodes, list) else []  # tolerate {"nodes": None}


def _edges(data: dict | None) -> list[dict]:
    edges = (data or {}).get("edges", []) if isinstance(data, dict) else []
    return edges if isinstance(edges, list) else []


def _find_node(data: dict | None, display_name: str) -> dict | None:
    for node in _nodes(data):
        if (node.get("data", {}).get("node", {}) or {}).get("display_name") == display_name:
            return node
    return None


def _find_nodes(data: dict | None, display_name: str) -> list[dict]:
    """All nodes with the given display_name (a graph may hold several, e.g. one LLM node per consumer)."""
    return [
        node for node in _nodes(data)
        if (node.get("data", {}).get("node", {}) or {}).get("display_name") == display_name
    ]


def _field(node: dict | None, name: str, default=None):
    if not node:
        return default
    tmpl = node.get("data", {}).get("node", {}).get("template", {}) or {}
    fdef = tmpl.get(name)
    if isinstance(fdef, dict) and fdef.get("value") not in (None, ""):
        return fdef.get("value")
    return default


def _llm_node_model_uuid(node: dict | None) -> str | None:
    """Parse a validated model registry UUID from an LLM node's ``registry_model`` field.

    Format ``"display | model_name | uuid"``. Returns the canonical UUID string, or None if the
    field is empty / malformed / the third part is not a real UUID (so the caller keeps scanning)."""
    raw = _field(node, "registry_model")
    if not raw or not isinstance(raw, str):
        return None
    parts = [p.strip() for p in raw.split("|")]
    if len(parts) < 3 or not parts[2]:
        return None
    try:
        return str(UUID(parts[2]))
    except (ValueError, TypeError):
        return None


def _direct_model_uuid(node: dict | None) -> str | None:
    """UUID chosen directly on an IDP node via the IDPModelDropdown (template key ``model_id``).

    The dropdown stores the app-standard ``"display | model_name | uuid"`` string, so the UUID is
    the LAST ``|``-segment. A bare UUID (direct entry) is also accepted. Returns the canonical UUID
    string, or None if empty / not a valid UUID (so the caller keeps resolving)."""
    raw = _field(node, "model_id")
    if not raw or not isinstance(raw, str):
        return None
    raw = raw.strip()
    try:
        return str(UUID(raw))  # bare UUID
    except (ValueError, TypeError):
        pass
    candidate = raw.rsplit("|", 1)[-1].strip()
    try:
        return str(UUID(candidate))
    except (ValueError, TypeError):
        return None


def _resolve_model_id_from_graph(data: dict | None) -> str | None:
    """Resolve the model registry UUID for the extraction run.

    A graph (e.g. the universal pipeline) may have SEVERAL 'Large Language Model' nodes — one
    feeding the classifier, one the extractor — possibly with different models, and the user may
    have filled only some. Resolve in priority order:
      1. the LLM node EDGE-CONNECTED to the AI Field Extractor (the model that actually drives
         extraction) — correct even when nodes carry different models;
      2. else the first LLM node that carries a valid model uuid (handles the common single-model
         case where only one of the duplicate nodes was filled).
    """
    # 0. Model chosen directly on the extractor node via the IDPModelDropdown (highest priority;
    #    empty -> fall through to the connected "Large Language Model" node logic below).
    direct = _direct_model_uuid(_find_node(data, _N_EXTRACTOR))
    if direct:
        return direct

    llm_nodes = _find_nodes(data, _N_MODEL)
    if not llm_nodes:
        return None

    # 1. Prefer the LLM wired into the extractor node.
    extractor = _find_node(data, _N_EXTRACTOR)
    extractor_id = extractor.get("id") if extractor else None
    if extractor_id:
        llm_by_id = {n.get("id"): n for n in llm_nodes}
        for edge in _edges(data):
            if not isinstance(edge, dict):
                continue
            if edge.get("target") == extractor_id and edge.get("source") in llm_by_id:
                mid = _llm_node_model_uuid(llm_by_id[edge.get("source")])
                if mid:
                    return mid

    # 2. Fallback: first LLM node with a valid model.
    for node in llm_nodes:
        mid = _llm_node_model_uuid(node)
        if mid:
            return mid
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

    # If the extractor node exposes NO explicit extraction_mode but a Field Configuration IS
    # selected on it, treat the run as named-config. Some node templates (e.g. the universal
    # pipeline) have a config_name field but no extraction_mode dropdown — without this, a chosen
    # config would be silently ignored and the run would fall back to a generic dynamic prompt.
    if mode == MODE_DYNAMIC and not graph_mode and config_name:
        mode = MODE_NAMED

    # resolve named-config -> field_config_id (graph name first, else idp_agent column).
    # Deterministic when several configs share a name: prefer the agent's org (unique by the
    # (org_id, name) constraint), else the NEWEST active config of that name — never an arbitrary
    # .first() over unordered rows.
    field_config_id: UUID | None = idp_agent.field_config_id
    if mode == MODE_NAMED and config_name:
        # The graph's config_name takes precedence over idp_agent.field_config_id: resolve it
        # fresh, and on a MISS leave field_config_id = None (so the pipeline fails clearly with
        # "no field configuration resolved") rather than silently using a stale/wrong config.
        field_config_id = None
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
            else:
                logger.warning(f"[agent_config] named config '{config_name}' not found / not active")
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
    # A Document Classifier node may target its own model (SLM/local); else share extraction's.
    classify_model_id = _direct_model_uuid(classifier_node) or model_id
    detection_node = _find_node(data, _N_DETECTION)
    math_node = _find_node(data, _N_MATH)
    confidence_router_node = _find_node(data, _N_CONFIDENCE_ROUTER)

    classify_enabled = classifier_node is not None or _as_bool(extra.get("classify_enabled"), False)
    # classify_threshold: min confidence for classification acceptance (DocumentClassifier node)
    classify_threshold = _to_float(
        _field(classifier_node, "confidence_threshold") if classifier_node else extra.get("classify_threshold"), 0.7
    )
    # classify_doc_types: if non-empty, classifier filters against only these types; non-matches are skipped
    raw_doc_types = _field(classifier_node, "document_types") if classifier_node else None
    classify_doc_types: list[str] = (
        [t for t in raw_doc_types if isinstance(t, str) and t.strip()]
        if isinstance(raw_doc_types, list) else []
    )

    # ConfidenceRouter threshold overrides the extra/default confidence_threshold
    if confidence_router_node:
        confidence_threshold = _to_float(_field(confidence_router_node, "threshold"), confidence_threshold)

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
    # NOTE: long_doc_max_tokens + long_doc_overlap_tokens are already resolved from the Chunking
    # Strategy node in the "Chunking settings" block above; no recomputation needed here.

    return ResolvedPipelineConfig(
        model_id=model_id,
        classify_model_id=classify_model_id,
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
        classify_doc_types=classify_doc_types,
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


async def _sync_canvas_rules(session: AsyncSession, data: dict | None, idp_agent_id: UUID) -> None:
    """Replace all IdpAgentRule rows with the conditions from the 'Rules / Conditions' canvas node.

    The canvas is treated as the authoritative source of rules. If the node is absent, all
    existing rules are still deleted (clean slate). Errors are caught so a rules-sync failure
    never breaks the agent save.
    """
    try:
        existing_rules = (
            await session.exec(select(IdpAgentRule).where(IdpAgentRule.idp_agent_id == idp_agent_id))
        ).all()
        for r in existing_rules:
            await session.delete(r)

        rules_node = _find_node(data, _N_RULES)
        if rules_node:
            combinator = str(_field(rules_node, "logic_operator") or "AND").upper()
            if combinator not in ("AND", "OR"):
                combinator = "AND"
            raw_conditions = _field(rules_node, "conditions") or []
            if not isinstance(raw_conditions, list):
                raw_conditions = []

            for i, cond in enumerate(raw_conditions):
                if not isinstance(cond, dict):
                    continue
                canvas_field = str(cond.get("field", "")).strip()
                op = str(cond.get("op", "eq")).strip()
                val = str(cond.get("value", "")).strip()

                if canvas_field in ("confidence", "overall_confidence"):
                    ctype = "confidence_overall"
                    db_field = None
                else:
                    ctype = "field_value_numeric"
                    db_field = canvas_field

                session.add(
                    IdpAgentRule(
                        idp_agent_id=idp_agent_id,
                        rule_group=1,
                        condition_type=ctype,
                        field_name=db_field,
                        operator=op,
                        value=val,
                        combinator=combinator,
                        action="auto_approve",
                        display_order=i,
                    )
                )

        await session.commit()
    except Exception as e:
        logger.warning(f"[agent_config] canvas rules sync failed (non-fatal): {e}")
        try:
            await session.rollback()
        except Exception:
            pass


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
    has_processed_docs_output = _find_node(data, "Processed Docs Output") is not None
    agent_extra = {"has_processed_docs_output": has_processed_docs_output}

    # Query WITHOUT the deleted_at filter: agent_id is UNIQUE across ALL rows (incl. soft-deleted),
    # so a deleted_at-only filter could miss a soft-deleted row and then INSERT a duplicate
    # (IntegrityError). If a soft-deleted row exists, reactivate it instead.
    existing = (await session.exec(select(IdpAgent).where(IdpAgent.agent_id == base_agent.id))).first()
    if existing is None:
        new_agent = IdpAgent(
            agent_id=base_agent.id,
            extraction_mode=mode,
            default_rule_action="pending_review",
            is_active=True,
            extra=agent_extra,
            created_by=user_id,
            updated_by=user_id,
        )
        session.add(new_agent)
        await session.commit()
        await session.refresh(new_agent)
        idp_agent = new_agent
    else:
        existing.extraction_mode = mode
        existing.is_active = True
        existing.deleted_at = None  # reactivate if it had been soft-deleted (agent still has IDP nodes)
        existing.updated_by = user_id
        existing.updated_at = datetime.now(timezone.utc)
        existing.extra = {**(existing.extra or {}), **agent_extra}
        session.add(existing)
        await session.commit()
        idp_agent = existing

    # Sync canvas Rules / Conditions node → idp_agent_rules table
    await _sync_canvas_rules(session, data, idp_agent.id)
