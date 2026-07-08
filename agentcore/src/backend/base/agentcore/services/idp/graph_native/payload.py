"""The IDP-document payload carried between components on the generic ``graph_langgraph`` engine.

Each IDP node passes a ``Message`` whose ``additional_kwargs["idp"]`` holds the accumulating working
set for the document; ``Message.text`` holds the current text (native/OCR/merged). This aligns with
what the existing ``IDPLLMExtractor`` already reads (``additional_kwargs["document_id"]`` + ``.text``)
and extends it so every node can read what it needs and add its output — pure node-to-node dataflow.

Raw file bytes are NOT stored in the payload (they'd bloat every message + the graph state). Nodes
that need bytes load them once from storage via ``agent_scope`` + ``file_name`` in the payload.

Working-set keys: ``document_id, job_id, agent_id, agent_scope, file_name, file_type, overall_kind,
page_status, tokens, route, selected_pages, extracted, overall_conf, detected, predicted_type,
decision, route_label``.
"""

from __future__ import annotations

from typing import Any

IDP_KEY = "idp"

# Decisions that mark a document as terminally handled — downstream nodes must pass
# through without triggering any model call or DB write.
TERMINAL_DECISIONS: frozenset[str] = frozenset({"skipped", "split", "dropped", "failed"})


def get_payload(src: Any) -> dict:
    """Read the IDP working set off a Message / dict (empty dict when absent)."""
    ak = getattr(src, "additional_kwargs", None)
    if isinstance(ak, dict) and isinstance(ak.get(IDP_KEY), dict):
        return dict(ak[IDP_KEY])
    if isinstance(src, dict) and isinstance(src.get(IDP_KEY), dict):
        return dict(src[IDP_KEY])
    return {}


def carry(src: Any, *, text: str | None = None, **updates: Any):
    """Return a NEW Message that carries the merged IDP payload (source payload + ``updates``), and
    optionally new ``text``. ``None`` updates are ignored so callers can pass optionals freely.

    The doc context rides in ``additional_kwargs['idp']`` (read with :func:`get_payload`) — NOT in
    ``.data``. The generic engine drops ``additional_kwargs`` when a Message's ``.data`` is populated,
    which would strip ``document_id`` mid-graph, so nodes read/write the working set via the payload."""
    from agentcore.schema.message import Message

    payload = get_payload(src)
    payload.update({k: v for k, v in updates.items() if v is not None})

    ak: dict = {}
    src_ak = getattr(src, "additional_kwargs", None)
    if isinstance(src_ak, dict):
        ak = {k: v for k, v in src_ak.items() if k != IDP_KEY}
    ak[IDP_KEY] = payload

    if text is None:
        text = getattr(src, "text", "") if not isinstance(src, str) else src
    return Message(text=text or "", additional_kwargs=ak)


def new_message(*, text: str = "", **fields: Any):
    """Seed a fresh IDP Message (used by the Document Upload input node)."""
    from agentcore.schema.message import Message

    return Message(text=text or "", additional_kwargs={IDP_KEY: {k: v for k, v in fields.items() if v is not None}})


async def load_bytes(payload: dict) -> bytes:
    """Load the document's raw bytes from storage using the payload's ``agent_scope`` + ``file_name``
    (bytes are kept by reference, not in the payload — a node that needs them loads them here)."""
    from agentcore.services.deps import get_storage_service

    return await get_storage_service().get_file(payload.get("agent_scope", ""), payload.get("file_name", ""))


# ── shared working-set (LangGraph ``idp_working_set`` channel) ─────────────────
#
# The native engine harvests every IDP node's output payload into a shared state channel and stashes a
# per-vertex snapshot on the component before it builds. These helpers read that snapshot so a node can
# see the document context globally — not only from its immediate input Message — which keeps
# ``document_id`` alive even if an upstream node forgot to forward it.
def get_shared_state(component: Any) -> dict:
    """The accumulated shared IDP working-set for the node currently building, read from the snapshot
    the engine stashes on the vertex (``vertex._idp_shared_snapshot``). Empty when not on the native
    engine (e.g. unit tests) — callers then fall back to the input Message's own payload."""
    vertex = getattr(component, "_vertex", None)
    snap = getattr(vertex, "_idp_shared_snapshot", None)
    return dict(snap) if isinstance(snap, dict) else {}


def effective_payload(component: Any, src: Any) -> dict:
    """The working-set a node should act on: the shared channel merged with the input Message's own
    payload. The local Message wins for keys it carries (it is the latest hop); the shared channel
    fills anything the immediate input didn't forward — so a node still sees ``document_id`` even if an
    upstream node dropped it from the Message."""
    local = get_payload(src)
    shared = get_shared_state(component)
    if not shared:
        return local
    return {**shared, **local}


# ── field resolution for routing / condition / gate nodes ──────────────────────
#
# A router names a "field" (e.g. document_type, confidence, rule_action) but the value lives in the
# IDP working-set under a canonical key. These aliases map the user-facing name to the payload keys to
# try, in order, so a router reads the right place instead of ``getattr(Message, field)`` (always None).
_FIELD_ALIASES: dict[str, tuple[str, ...]] = {
    "document_type": ("overall_kind", "predicted_type", "document_type", "type"),
    "type": ("predicted_type", "overall_kind", "type"),
    "predicted_type": ("predicted_type", "overall_kind"),
    "overall_kind": ("overall_kind",),
    "confidence": ("overall_conf", "confidence"),
    "overall_confidence": ("overall_conf", "confidence"),
    "rule_action": ("rule_action", "decision"),
    "decision": ("decision", "rule_action"),
    "route_label": ("route_label",),
}


def _classification_of(src: Any) -> dict:
    """The Document Classifier's block (``idp['classification']`` = {type, confidence, reasoning}).
    Reads it from the IDP working-set payload (where the classifier now carries it); falls back to the
    legacy top-level ``additional_kwargs['classification']`` for older messages."""
    cl = get_payload(src).get("classification")
    if not isinstance(cl, dict):
        ak = getattr(src, "additional_kwargs", None)
        cl = ak.get("classification") if isinstance(ak, dict) else None
    return cl if isinstance(cl, dict) else {}


def resolve_field(src: Any, field: str, component: Any = None) -> Any:
    """Resolve a routing/condition field from an IDP ``Message``/``Data``/dict — checking, in order:
    the IDP payload (with key aliases), the classification block, the extracted header values, then a
    plain ``Data.data`` / dict / attribute. Returns ``None`` when unresolved.

    Pass ``component=self`` so the shared working-set channel is merged in — then the field resolves
    even if the immediate input Message didn't carry it. Centralizes 'where does each field live' so
    routers stop reading the wrong place (the class of bug where a Confidence Router read
    ``getattr(Message, 'confidence')`` — always ``None`` — and therefore always routed 'low')."""
    field = (field or "").strip()
    if not field:
        return getattr(src, "text", "") if not isinstance(src, (dict, str)) else src

    payload = effective_payload(component, src) if component is not None else get_payload(src)
    classification = _classification_of(src)

    # 1. Payload key(s), honoring aliases.
    for key in _FIELD_ALIASES.get(field, (field,)):
        if payload.get(key) not in (None, ""):
            return payload[key]

    # 2. Classification block (Document Classifier).
    if classification:
        if field in ("document_type", "type", "predicted_type") and classification.get("type") not in (None, ""):
            return classification["type"]
        if field in ("confidence", "overall_confidence") and classification.get("confidence") is not None:
            return classification["confidence"]
        if classification.get(field) not in (None, ""):
            return classification[field]

    # 3. Extracted header value: extracted = {headers: {name: {value, confidence}}}.
    extracted = payload.get("extracted")
    if isinstance(extracted, dict):
        headers = extracted.get("headers")
        if isinstance(headers, dict) and field in headers:
            h = headers[field]
            return h.get("value") if isinstance(h, dict) else h

    # 4. Non-IDP fallbacks: Data.data / dict / attribute.
    try:
        from agentcore.schema.data import Data

        if isinstance(src, Data) and isinstance(src.data, dict) and field in src.data:
            return src.data[field]
    except Exception:  # noqa: BLE001
        pass
    if isinstance(src, dict) and field in src:
        return src[field]
    return getattr(src, field, None)


def overall_confidence(src: Any, component: Any = None) -> float | None:
    """Overall extraction confidence for a Confidence Router: a payload ``overall_conf`` if present,
    else the mean of the per-field confidences in the payload's ``extracted`` block (same basis as
    ``save_extraction_results``). ``None`` when no confidence signal is available. Pass ``component``
    to also consult the shared working-set channel."""
    payload = effective_payload(component, src) if component is not None else get_payload(src)
    if payload.get("overall_conf") is not None:
        try:
            return float(payload["overall_conf"])
        except (TypeError, ValueError):
            pass

    extracted = payload.get("extracted")
    confs: list[float] = []
    if isinstance(extracted, dict):
        headers = extracted.get("headers")
        if isinstance(headers, dict):
            for h in headers.values():
                # Count ONLY populated fields. A field that's absent from the document comes back with a
                # null value at confidence 0.0; averaging those in drags the mean far below the real
                # score of what WAS extracted (and below what save_extraction_results / the UI display),
                # which mis-routes a good extraction to Low. (Codex-style confidence mismatch.)
                if not isinstance(h, dict) or h.get("value") in (None, ""):
                    continue
                c = h.get("confidence")
                if c is not None:
                    try:
                        confs.append(float(c))
                    except (TypeError, ValueError):
                        pass
        for row in extracted.get("line_items", []) or []:
            for col in (row.get("columns", []) if isinstance(row, dict) else []):
                if not isinstance(col, dict) or col.get("value") in (None, ""):
                    continue
                c = col.get("confidence")
                if c is not None:
                    try:
                        confs.append(float(c))
                    except (TypeError, ValueError):
                        pass
    if confs:
        return sum(confs) / len(confs)
    return None
