"""Run an IDP document through the GENERIC ``graph_langgraph`` engine — the SAME path simple agents use.

Every IDP node is a registered built-in component (``components/IDP/``), so the standard loader
(``instantiate_class`` → ``_get_builtin_source_code``) resolves each node's source from disk and builds
it as a first-class vertex — no IDP-specific code injection. This module only wires the batch document
into the graph: it marks the Document Upload node as the input vertex (seeded with the ``document_id``)
and the terminal nodes as output vertices, then builds a ``LangGraphAdapter`` and runs it, with native
per-vertex ``end_vertex`` events when an ``event_manager`` is supplied.
"""

from __future__ import annotations

import copy
from typing import Any
from uuid import UUID

from loguru import logger

_INPUT_NAMES = frozenset({"Document Upload", "Connector Input"})
_OUTPUT_NAMES = frozenset({"Processed Docs Output", "Webhook Output"})


def _prepare(agent_data: dict, document_id: str) -> dict:
    """Deep-copy the canvas graph, tag input/output vertices, and seed Document Upload with the id.

    No code hydration — the standard component loader refreshes each IDP node's source from the
    registry exactly like a simple agent's components. This is purely the batch-document wiring.
    """
    data = copy.deepcopy(agent_data) if isinstance(agent_data, dict) else {"nodes": [], "edges": []}
    for node in data.get("nodes") or []:
        inner = ((node.get("data") or {}).get("node")) or {}
        dn = inner.get("display_name")
        if dn in _INPUT_NAMES:
            inner["is_input"] = True
            tmpl = inner.setdefault("template", {})
            fld = tmpl.get("document_id")
            if isinstance(fld, dict):
                fld["value"] = document_id
            else:
                tmpl["document_id"] = {"type": "str", "name": "document_id", "value": document_id, "show": True}
        elif dn in _OUTPUT_NAMES:
            inner["is_output"] = True
    return data


async def run_native_graph(
    *,
    document_id: str | UUID,
    agent_data: dict,
    agent_id: str | UUID,
    agent_name: str | None = None,
    user_id: str | None = None,
    session_id: str | None = None,
    event_manager: Any = None,
    node_log_out: list | None = None,
) -> Any:
    """Build + run the IDP graph on the generic engine. Returns the engine's run outputs.

    If ``node_log_out`` is given, each executed node's ``{id, name, status}`` is appended to it (read
    from the built components after the run) — the caller persists this as the per-node flow log."""
    from agentcore.graph_langgraph import LangGraphAdapter

    did = str(document_id)
    prepared = _prepare(agent_data, did)

    graph = LangGraphAdapter.from_payload(
        prepared,
        str(agent_id),
        agent_name or "IDP",
        str(user_id) if user_id else None,
    )
    if graph.compiled_app is None:
        raise ValueError("IDP native graph failed to compile")

    await graph.initialize_run()
    logger.info(f"[idp-native] {did}: running on the generic graph_langgraph engine")
    try:
        return await graph.arun(
            [{"input_value": did}],
            session_id=session_id or did,
            stream=bool(event_manager),
            event_manager=event_manager,
        )
    finally:
        # Capture each node's final status for the per-node log (best-effort, even on error).
        if node_log_out is not None:
            for v in getattr(graph, "vertices", []) or []:
                comp = getattr(v, "custom_component", None)
                if comp is None:
                    continue
                node_log_out.append({
                    "id": getattr(v, "id", None),
                    "name": getattr(comp, "display_name", None) or getattr(v, "display_name", None) or getattr(v, "id", "node"),
                    "status": str(getattr(comp, "status", "") or "").strip(),
                })
