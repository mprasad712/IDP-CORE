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
