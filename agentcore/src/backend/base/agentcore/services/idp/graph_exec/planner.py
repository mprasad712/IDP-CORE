"""Plan a canvas graph into an ordered, validated execution plan (flag-ON IDP path).

``plan_execution`` turns the agent's saved React-Flow graph (``{"nodes": [...], "edges": [...]}``)
into an :class:`ExecutionPlan`: a topologically ordered list of :class:`PlannedNode` (each carrying
its ``display_name``, pipeline ``stage``, and resolved ``handler``) plus a list of validation
``errors``. Ordering follows the edges (Kahn's algorithm); ties are broken by ORIGINAL file order
so the plan is stable and reproducible. Fan-in nodes appear exactly once (after all their sources);
multiple terminal-output nodes and unknown/custom nodes never break planning — the latter fall
back to :func:`~agentcore.services.idp.graph_exec.handlers.noop_handler` (the Phase-2 seam).

Dispatch keys off ``display_name`` throughout (the template ``data.type`` class name does not match
the backend Component class), mirroring the rest of the graph path.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from agentcore.services.idp.graph_exec.handlers import (
    NODE_STAGE,
    get_handler,
    node_display_name,
    noop_handler,
)

# Extraction is REQUIRED: an IDP agent that never extracts is malformed. Today the only extraction
# node is the "AI Field Extractor" (``components/IDP/llm_extractor.py``); the dynamic / named-config
# / multimodal modes are FIELDS on that single node (``agent_config._N_EXTRACTOR``), not separate
# node types — so this set has one member. Extend it here if a distinct extractor node is ever added.
_EXTRACTION_NODE_NAMES: frozenset[str] = frozenset({"AI Field Extractor"})

# Fallback stage for nodes not in NODE_STAGE (unknown/custom + non-pipeline nodes like the
# Document Upload input, Output Parser, Chunk Aggregator, Processed Docs Output sinks). They are
# ordered and carried through, but do no anchored backbone work.
_DEFAULT_STAGE = "post_text"


@dataclass
class PlannedNode:
    """One canvas node resolved for execution: its id, display_name, stage and handler callable."""

    id: str
    display_name: str
    stage: str
    handler: Any  # Handler (async callable) — get_handler(display_name) or noop_handler


@dataclass
class ExecutionPlan:
    """The ordered nodes to run plus any validation errors gathered while planning."""

    nodes: list[PlannedNode] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def _source_handle_name(edge: dict) -> str | None:
    """Return an edge's source-handle ``name`` (which output pin it leaves from), or None.

    Two on-disk forms are tolerated:

    * **structured** — ``edge["data"]["sourceHandle"]["name"]`` (the parsed form React-Flow keeps
      alongside the string); read directly when present.
    * **``œ``-escaped string** — the top-level ``edge["sourceHandle"]`` is JSON with every ``"``
      replaced by ``œ`` (U+0153). Un-escape (``œ`` -> ``"``) then ``json.loads`` and read ``name``.

    Needed by branch pruning (Task 5) to identify a router's taken / not-taken output pins.
    Returns None on anything unparseable — never raises.
    """
    data = edge.get("data") or {}
    if isinstance(data, dict):
        sh = data.get("sourceHandle")
        if isinstance(sh, dict) and sh.get("name"):
            return sh["name"]

    raw = edge.get("sourceHandle")
    if isinstance(raw, str) and raw:
        try:
            parsed = json.loads(raw.replace("œ", '"'))
        except (ValueError, TypeError):
            return None
        if isinstance(parsed, dict):
            name = parsed.get("name")
            if name:
                return name
    return None


def _topo_order(nodes: list[dict], edges: list[dict]) -> tuple[list[dict], list[str]]:
    """Kahn topological sort of ``nodes`` by ``edges`` (source -> target on ``node["id"]``).

    Ties among ready (in-degree-0) nodes are broken by ORIGINAL file order, so the result is stable.
    Only edges whose BOTH endpoints are known node ids contribute to ordering (dangling edges are
    ignored). On a cycle, the un-orderable remainder is appended in file order and a
    ``"cycle detected in graph"`` error is returned — nodes are never dropped.

    Returns ``(ordered_nodes, errors)``.
    """
    ordered_ids = [n.get("id") for n in nodes]
    id_to_node = {n.get("id"): n for n in nodes}
    id_set = set(ordered_ids)

    indeg: dict = {nid: 0 for nid in ordered_ids}
    adj: dict = {nid: [] for nid in ordered_ids}
    for e in edges:
        if not isinstance(e, dict):
            continue
        src, tgt = e.get("source"), e.get("target")
        if src in id_set and tgt in id_set:
            adj[src].append(tgt)
            indeg[tgt] += 1

    result_ids: list = []
    remaining = list(ordered_ids)  # preserves file order for the tie-break + cycle remainder
    errors: list[str] = []
    while remaining:
        picked = next((nid for nid in remaining if indeg[nid] == 0), None)
        if picked is None:
            # Every remaining node has an unmet dependency -> a cycle. Keep them (file order).
            errors.append("cycle detected in graph")
            result_ids.extend(remaining)
            break
        result_ids.append(picked)
        remaining.remove(picked)
        for tgt in adj[picked]:
            indeg[tgt] -= 1

    return [id_to_node[i] for i in result_ids], errors


def plan_execution(graph: dict) -> ExecutionPlan:
    """Build an :class:`ExecutionPlan` from a canvas graph (``{"nodes", "edges"}``).

    Topologically orders the nodes (edge order, file-order tie-break), resolves each node's
    ``display_name`` -> ``stage`` (:data:`~agentcore.services.idp.graph_exec.handlers.NODE_STAGE`,
    defaulting to ``post_text``) and ``handler`` (``get_handler`` or ``noop_handler`` for
    unknown/custom nodes), and validates that an extraction node is present. Errors (missing
    extraction, cycle) are collected on the plan rather than raised.

    A non-dict ``graph`` (None / str / …) is treated as empty (mirrors ``agent_config._nodes``,
    which guards on ``isinstance(data, dict)``) so a malformed agent yields the no-extraction
    error instead of crashing.
    """
    if not isinstance(graph, dict):
        graph = {}
    nodes = graph.get("nodes", []) or []
    edges = graph.get("edges", []) or []

    ordered, errors = _topo_order(nodes, edges)

    plan = ExecutionPlan()
    plan.errors.extend(errors)

    display_names: list[str] = []
    for node in ordered:
        dn = node_display_name(node)
        stage = NODE_STAGE.get(dn, _DEFAULT_STAGE)
        handler = get_handler(dn) or noop_handler
        plan.nodes.append(
            PlannedNode(id=node.get("id"), display_name=dn, stage=stage, handler=handler)
        )
        display_names.append(dn)

    if not any(dn in _EXTRACTION_NODE_NAMES for dn in display_names):
        plan.errors.append("no extraction node in graph")

    return plan


# ─────────────────────────── Task 5: two-stage branch pruning ───────────────────────────
# A canvas router has ONE input and several NAMED outputs (source handles). At run time exactly
# one output pin is "taken"; the others are dead. We drop the PlannedNodes that live ONLY on a
# not-taken branch — but NEVER a node that is also reachable another way (via a taken branch or a
# plain edge). Pruning is two-stage because the deciding value appears at different times:
#
#   * PRE-extract  (:func:`prune_presence_branches`) — Multi-Branch Router (on the classifier's
#     ``doc.predicted_type``) and Condition (on a header value) — inputs known before extraction.
#   * POST-extract (:func:`prune_confidence_branches`) — Confidence Router, which needs
#     ``ctx.overall_conf`` (only known after extract + save).
#
# Both share :func:`_reachable_from` (forward reachability) and :func:`_prune_router_branches`.


def _node_field(node: dict | None, name: str, default: Any = None) -> Any:
    """Read a canvas node's template field value (``data.node.template.<name>.value``).

    Mirrors ``agent_config._field``: an unset / empty (``None``/``""``) value yields ``default``.
    Kept local so the planner does not depend on a private helper in another module.
    """
    if not node:
        return default
    tmpl = ((node.get("data") or {}).get("node") or {}).get("template") or {}
    fdef = tmpl.get(name)
    if isinstance(fdef, dict) and fdef.get("value") not in (None, ""):
        return fdef.get("value")
    return default


def _reachable_from(node_ids, edges: list[dict]) -> set[str]:
    """Forward reachability: every node reachable by following ``edges`` (source -> target) from
    the seed ``node_ids`` (the seeds themselves are included). Dangling / non-dict edges ignored.
    """
    adj: dict[str, list[str]] = {}
    for e in edges:
        if not isinstance(e, dict):
            continue
        src, tgt = e.get("source"), e.get("target")
        if src is None or tgt is None:
            continue
        adj.setdefault(src, []).append(tgt)

    seen: set[str] = {nid for nid in node_ids if nid is not None}
    stack = list(seen)
    while stack:
        nid = stack.pop()
        for tgt in adj.get(nid, []):
            if tgt not in seen:
                seen.add(tgt)
                stack.append(tgt)
    return seen


def _not_taken_only_ids(router_id: str, taken_handle: str, edges: list[dict]) -> set[str]:
    """Node ids reachable EXCLUSIVELY via ``router_id``'s not-taken output handle(s).

    ``taken_handle`` is the one output pin that fires; every other pin off this router is dead.
    We compute the nodes reachable from the dead pins' targets, then subtract everything the rest
    of the graph can still reach WITHOUT the router's dead edges — so a node with any other path
    in (a taken branch or a plain edge) is retained (Codex shared-node blocker). The router node
    itself is never in the result (reachability starts from the dead pins' *targets*).
    """
    router_edges = [
        e for e in edges if isinstance(e, dict) and e.get("source") == router_id
    ]
    if not router_edges:
        return set()

    # Dead (not-taken) edges = router edges whose handle isn't the taken one. Their targets seed
    # the not-taken branch.
    not_taken_edges = [e for e in router_edges if _source_handle_name(e) != taken_handle]
    not_taken_roots = {e.get("target") for e in not_taken_edges if e.get("target") is not None}
    if not not_taken_roots:
        return set()

    not_taken_reachable = _reachable_from(not_taken_roots, edges)

    # The rest of the graph = all edges EXCEPT the router's dead pins. If a not-taken node is still
    # reachable here, it has another way in and must stay.
    dead_edge_ids = {id(e) for e in not_taken_edges}
    other_edges = [e for e in edges if id(e) not in dead_edge_ids]
    # Seed from every "outside" edge target: taken-pin targets (source == router, kept in
    # other_edges) and any edge whose source is NOT inside the not-taken branch.
    external_roots = {
        e.get("target")
        for e in other_edges
        if isinstance(e, dict)
        and e.get("target") is not None
        and e.get("source") not in not_taken_reachable
    }
    elsewhere_reachable = _reachable_from(external_roots, other_edges)

    return not_taken_reachable - elsewhere_reachable


def _prune_router_branches(plan: ExecutionPlan, graph: dict, decide) -> ExecutionPlan:
    """Drop plan nodes on the not-taken branch of every router ``decide`` resolves.

    ``decide(display_name, raw_node) -> taken_handle | None``: return the taken output-handle name
    for a router this stage owns, or ``None`` to leave that node's branches intact (unknown node,
    or the deciding value isn't available yet — the safe default). Returns a NEW plan (errors
    preserved); the input plan is not mutated. A non-dict ``graph`` is treated as empty.
    """
    if not isinstance(graph, dict):
        graph = {}
    edges = graph.get("edges", []) or []
    id_to_raw = {n.get("id"): n for n in (graph.get("nodes", []) or []) if isinstance(n, dict)}

    remove_ids: set[str] = set()
    for pnode in plan.nodes:
        taken = decide(pnode.display_name, id_to_raw.get(pnode.id))
        if taken is None:
            continue
        remove_ids |= _not_taken_only_ids(pnode.id, taken, edges)

    if not remove_ids:
        return plan
    kept = [n for n in plan.nodes if n.id not in remove_ids]
    return ExecutionPlan(nodes=kept, errors=list(plan.errors))


def prune_confidence_branches(plan: ExecutionPlan, ctx, graph: dict) -> ExecutionPlan:
    """POST-extract pruning of **Confidence Router** dead branches.

    Taken handle = ``high_confidence`` when ``ctx.overall_conf >= ctx.cfg.confidence_threshold``,
    else ``low_confidence``. Call AFTER extract + save (``overall_conf`` is only known then).
    """

    def decide(display_name: str, raw_node: dict | None) -> str | None:
        if display_name != "Confidence Router":
            return None
        threshold = getattr(getattr(ctx, "cfg", None), "confidence_threshold", 0.0) or 0.0
        return "high_confidence" if ctx.overall_conf >= threshold else "low_confidence"

    return _prune_router_branches(plan, graph, decide)


def prune_presence_branches(plan: ExecutionPlan, ctx, graph: dict) -> ExecutionPlan:
    """PRE-extract pruning of **Multi-Branch Router** + **Condition** dead branches.

    Runs BEFORE extraction, so it must NOT read ``ctx.overall_conf`` (only presence/type inputs).
    Multi-Branch Router routes on ``ctx.doc.predicted_type`` (when ``route_field`` is
    document_type / predicted_type); Condition on a pre-extract field value. When the deciding
    value isn't available yet, that router is left intact (safe default — keep all branches).
    """

    def decide(display_name: str, raw_node: dict | None) -> str | None:
        if display_name == "Multi-Branch Router":
            return _multi_branch_taken_handle(raw_node, ctx)
        if display_name == "Condition":
            return _condition_taken_handle(raw_node, ctx)
        return None

    return _prune_router_branches(plan, graph, decide)


def _multi_branch_taken_handle(node: dict | None, ctx) -> str | None:
    """Taken output handle of a Multi-Branch Router, or ``None`` to skip pruning.

    Value-matching follows ``pipeline._match_route``: only ``document_type`` / ``predicted_type``
    route fields are resolvable pre-extract (read off ``ctx.doc.predicted_type``); matching is
    case-insensitive (``strip().lower()``). Returns ``route_{N}_out`` for the first matching
    ``route_N`` value, ``"unmatched"`` when a value is present but maps to nothing, or ``None``
    (don't prune) when the field is header-based or the value isn't available yet.
    """
    if node is None:
        return None
    route_field = _node_field(node, "route_field")
    if route_field is None:
        return None
    if str(route_field).strip().lower() not in ("document_type", "predicted_type"):
        return None  # a header route_field is not resolvable pre-extract -> keep all branches

    doc = getattr(ctx, "doc", None)
    val = getattr(doc, "predicted_type", None) if doc is not None else None
    if val in (None, ""):
        return None  # deciding value not available yet -> safe default

    val_norm = str(val).strip().lower()
    for i in range(1, 6):
        rv = _node_field(node, f"route_{i}")
        if rv and str(rv).strip().lower() == val_norm:
            return f"route_{i}_out"
    return "unmatched"


def _condition_taken_handle(node: dict | None, ctx) -> str | None:
    """Taken output handle (``true_path`` / ``false_path``) of a Condition, or ``None`` to skip.

    Evaluated pre-extract, so only fields resolvable now decide: ``document_type`` /
    ``predicted_type`` (off ``ctx.doc``) or a header already present on ``ctx.extracted``. When the
    field value isn't available the branches are left intact (safe default). Never reads
    ``ctx.overall_conf``.
    """
    if node is None:
        return None
    lhs = _condition_lhs(_node_field(node, "field"), ctx)
    if lhs is None:
        return None  # deciding value not available pre-extract -> keep both branches
    op = _node_field(node, "operator", "equals")
    rhs = _node_field(node, "value", "")
    return "true_path" if _eval_condition(str(lhs), op, rhs) else "false_path"


def _condition_lhs(field: Any, ctx) -> Any:
    """Resolve a Condition's left-hand value from pre-extract context, or ``None`` if unavailable."""
    fname = str(field).strip() if field else ""
    if fname.lower() in ("document_type", "predicted_type"):
        doc = getattr(ctx, "doc", None)
        val = getattr(doc, "predicted_type", None) if doc is not None else None
        return val if val not in (None, "") else None
    # A header may already be on ctx.extracted (defensive: several shapes, never raise).
    extracted = getattr(ctx, "extracted", None)
    if isinstance(extracted, dict) and fname:
        val = extracted.get(fname)
        if val not in (None, ""):
            return val
    return None


def _eval_condition(lhs: str, operator: Any, rhs: Any) -> bool:
    """Evaluate a Condition operator (mirrors ``components.IDP.condition_node._evaluate``)."""
    rhs = str(rhs).strip() if rhs is not None else ""
    op = operator or "equals"
    if op == "is_empty":
        return not lhs.strip()
    if op == "is_not_empty":
        return bool(lhs.strip())
    if op == "equals":
        return lhs == rhs
    if op == "not_equals":
        return lhs != rhs
    if op == "contains":
        return rhs in lhs
    try:
        lhs_n, rhs_n = float(lhs), float(rhs)
        if op == "greater_than":
            return lhs_n > rhs_n
        if op == "less_than":
            return lhs_n < rhs_n
        if op == "greater_than_or_equal":
            return lhs_n >= rhs_n
        if op == "less_than_or_equal":
            return lhs_n <= rhs_n
    except ValueError:
        pass
    return False
