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

    _node_names = [
        ((((n.get("data") or {}).get("node")) or {}).get("display_name")) or n.get("id")
        for n in (prepared.get("nodes") or [])
    ]
    logger.info(f"[idp-native] {did}: graph nodes ({len(_node_names)}): {_node_names}")

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
        # Capture each executed node's final status (best-effort, even on error). A vertex has a
        # built custom_component only if it actually ran, so this is the list of nodes that executed.
        from agentcore.services.idp.graph_native.node_log import sample_io

        _executed = []
        for v in getattr(graph, "vertices", []) or []:
            comp = getattr(v, "custom_component", None)
            if comp is None:
                continue
            _status = str(getattr(comp, "status", "") or "").strip().replace("\n", " ")
            if len(_status) > 120:  # some nodes park large text (e.g. OCR) in .status — keep it short
                _status = _status[:117] + "…"
            entry = {
                "id": getattr(v, "id", None),
                "name": getattr(comp, "display_name", None) or getattr(v, "display_name", None) or getattr(v, "id", "node"),
                "status": _status,
            }
            # Rich, bounded per-node io sample ({input, output}) so the report/Processed Docs show what
            # went into and out of each node. Best-effort — sample_io never raises.
            _io = sample_io(v)
            if _io:
                entry["io"] = _io
            # Per-node timing if the engine populated it (build_times, seconds → ms). Not always present;
            # omit rather than invent a timer.
            _bt = getattr(v, "build_times", None)
            if _bt:
                try:
                    entry["ms"] = int(sum(_bt) / len(_bt) * 1000)
                except (TypeError, ValueError, ZeroDivisionError):
                    pass
            _executed.append(entry)
            if node_log_out is not None:
                node_log_out.append(entry)
        # One clear server-log line listing which nodes ran + their status.
        if _executed:
            logger.info(
                f"[idp-native] {did}: executed {len(_executed)} node(s): "
                + " | ".join(f"{e['name']} → {(e['status'] or 'ok')[:60]}" for e in _executed)
            )
