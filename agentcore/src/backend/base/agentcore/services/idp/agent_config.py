"""Resolve the IDP processing config from a saved agent graph (with DB fallbacks).

The orchestrator is hybrid/config-driven: it reads the agent's saved React-Flow graph
(``Agent.data = {nodes, edges}``) to learn which steps to run, the chosen model, and the
extraction settings, falling back to the ``idp_agents`` columns where the graph is absent.
This mirrors the legacy ``process_backend_bulk.py`` (parse graph JSON -> run a fixed pipeline).
"""

from __future__ import annotations

from dataclasses import dataclass, field
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
    # differentiators. The three node-features default OFF (only run when explicitly
    # enabled via idp_agent.extra, since their canvas nodes are not built yet); the two
    # automatic backend features default ON but only do work when the document warrants
    # it (long docs / cross-page repeats), so short docs are unaffected.
    classify_enabled: bool = False
    classify_auto_select: bool = True
    classify_threshold: float = 0.7
    detect_enabled: bool = False
    detect_enabled_types: set[str] | None = None
    math_reconcile_enabled: bool = False
    math_reconcile_max_attempts: int = 2
    long_doc_enabled: bool = True
    long_doc_max_pages: int = 8
    long_doc_max_tokens: int = 12000
    entity_linking_enabled: bool = True


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

    # resolve named-config -> field_config_id (graph name first, else idp_agent column)
    field_config_id: UUID | None = idp_agent.field_config_id
    if mode == MODE_NAMED and config_name:
        try:
            row = (
                await session.exec(
                    select(IdpFieldConfiguration).where(
                        IdpFieldConfiguration.name == config_name,
                        IdpFieldConfiguration.deleted_at.is_(None),
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
    confidence_threshold = _to_float(extra.get("confidence_threshold"), 0.8)
    ocr_lang = str(extra.get("ocr_language") or _field(extractor, "language") or "en")

    # differentiator toggles (config-driven via idp_agent.extra until canvas nodes exist)
    det_types = extra.get("detect_enabled_types")
    detect_enabled_types = (
        {str(x).strip().lower() for x in det_types if str(x).strip()}
        if isinstance(det_types, list) and det_types
        else None
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
        classify_enabled=bool(extra.get("classify_enabled", False)),
        classify_auto_select=bool(extra.get("classify_auto_select", True)),
        classify_threshold=_to_float(extra.get("classify_threshold"), 0.7),
        detect_enabled=bool(extra.get("detect_enabled", False)),
        detect_enabled_types=detect_enabled_types,
        math_reconcile_enabled=bool(extra.get("math_reconcile_enabled", False)),
        math_reconcile_max_attempts=_to_int(extra.get("math_reconcile_max_attempts"), 2),
        long_doc_enabled=bool(extra.get("long_doc_enabled", True)),
        long_doc_max_pages=_to_int(extra.get("long_doc_max_pages"), 8),
        long_doc_max_tokens=_to_int(extra.get("long_doc_max_tokens"), 12000),
        entity_linking_enabled=bool(extra.get("entity_linking_enabled", True)),
    )
