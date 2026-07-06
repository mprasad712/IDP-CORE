"""Run IDP document flows on the GENERIC ``graph_langgraph`` engine — the SAME engine, and the SAME
component loader, that run chat agents.

Every IDP node is a registered built-in component under ``components/IDP/``. The standard loader
(``instantiate_class`` → ``_get_builtin_source_code``) refreshes each node's source from disk and
builds it as a first-class vertex, so IDP nodes run identically to a simple agent's components — no
IDP-specific code injection. :func:`run_native_graph` only wires the batch document into the graph
(input/output vertices + ``document_id``) and streams per-vertex ``end_vertex`` events for native
canvas highlighting. Dispatched from ``pipeline._run`` when ``IDP_EXECUTION_ENGINE=native``.
"""

from agentcore.services.idp.graph_native.runner import run_native_graph

__all__ = ["run_native_graph"]
