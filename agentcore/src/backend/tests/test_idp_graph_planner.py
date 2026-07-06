"""Tests for the graph planner (topo order + validation) of the flag-ON IDP path.

Covers ``services/idp/graph_exec/planner.py``: edge-driven topological ordering (with
file-order tie-break), fan-in (a node appears once, after all its sources), tolerance of
multiple terminal-output nodes, extraction-node validation, the unknown/custom-node seam
(noop handler), cycle detection, and ``_source_handle_name`` parsing for BOTH the structured
``data.sourceHandle.name`` form and the top-level ``œ``-escaped JSON string form.

These are SYNC tests — ``plan_execution`` / ``_source_handle_name`` are pure functions.
"""

import json
from pathlib import Path


# --- helpers ---------------------------------------------------------------------------------


def _n(node_id, display_name):
    """Minimal canvas node: an id + a nested display_name (the dispatch key)."""
    return {"id": node_id, "data": {"node": {"display_name": display_name}}}


def _e(source, target, handle=None):
    """Edge with source/target and an optional structured ``data.sourceHandle.name``."""
    edge = {"source": source, "target": target}
    if handle is not None:
        edge["data"] = {"sourceHandle": {"name": handle}}
    return edge


# --- ordering --------------------------------------------------------------------------------


def test_orders_by_edges_not_file_order():
    """Scan Corrector -> AI Field Extractor: Scan Corrector precedes the extractor even though
    it appears LATER in file order (proves edges drive ordering, not the raw node list)."""
    from agentcore.services.idp.graph_exec.planner import plan_execution

    graph = {
        "nodes": [_n("ext", "AI Field Extractor"), _n("scan", "Scan Corrector")],
        "edges": [_e("scan", "ext")],
    }
    plan = plan_execution(graph)

    dns = [n.display_name for n in plan.nodes]
    assert dns.index("Scan Corrector") < dns.index("AI Field Extractor")
    assert plan.errors == []  # extraction node present, no cycle


def test_fan_in_node_appears_once_after_both_sources():
    """An Output Parser fed by TWO sources appears exactly once, after both of them."""
    from agentcore.services.idp.graph_exec.planner import plan_execution

    graph = {
        "nodes": [
            _n("op", "Output Parser"),
            _n("ocr", "PaddleOCR"),
            _n("det", "Document Type Detector"),
            _n("ext", "AI Field Extractor"),
        ],
        "edges": [
            _e("ocr", "op"),
            _e("det", "op"),
            _e("op", "ext"),
        ],
    }
    plan = plan_execution(graph)

    dns = [n.display_name for n in plan.nodes]
    assert dns.count("Output Parser") == 1
    op_idx = dns.index("Output Parser")
    assert dns.index("PaddleOCR") < op_idx
    assert dns.index("Document Type Detector") < op_idx


def test_multiple_terminal_output_nodes_do_not_break_planning():
    """Three 'Processed Docs Output' nodes all appear; planning does not crash."""
    from agentcore.services.idp.graph_exec.planner import plan_execution

    graph = {
        "nodes": [
            _n("ext", "AI Field Extractor"),
            _n("o1", "Processed Docs Output"),
            _n("o2", "Processed Docs Output"),
            _n("o3", "Processed Docs Output"),
        ],
        "edges": [_e("ext", "o1"), _e("ext", "o2"), _e("ext", "o3")],
    }
    plan = plan_execution(graph)

    dns = [n.display_name for n in plan.nodes]
    assert dns.count("Processed Docs Output") == 3
    assert len(plan.nodes) == 4
    assert not any("cycle" in e for e in plan.errors)


# --- validation ------------------------------------------------------------------------------


def test_missing_extraction_node_records_error():
    """A graph with no extraction node surfaces an 'extraction' error."""
    from agentcore.services.idp.graph_exec.planner import plan_execution

    graph = {
        "nodes": [_n("scan", "Scan Corrector"), _n("out", "Processed Docs Output")],
        "edges": [_e("scan", "out")],
    }
    plan = plan_execution(graph)

    assert any("extraction" in e for e in plan.errors)


def test_extraction_node_present_no_error():
    """With an AI Field Extractor present there is no extraction error."""
    from agentcore.services.idp.graph_exec.planner import plan_execution

    graph = {"nodes": [_n("ext", "AI Field Extractor")], "edges": []}
    plan = plan_execution(graph)

    assert not any("extraction" in e for e in plan.errors)


def test_plan_execution_non_dict_graph_returns_plan_without_raising():
    """NIT 3: a non-dict graph (None / str / other) is treated as empty — plan_execution returns
    an ExecutionPlan (with the no-extraction error), never raising. Mirrors agent_config._nodes,
    which guards on ``isinstance(data, dict)``."""
    from agentcore.services.idp.graph_exec.planner import ExecutionPlan, plan_execution

    for bad in (None, "not a dict", 123, ["nodes", "edges"]):
        plan = plan_execution(bad)
        assert isinstance(plan, ExecutionPlan)
        assert plan.nodes == []
        assert any("extraction" in e for e in plan.errors)


def test_cycle_detected_records_error_but_keeps_all_nodes():
    """A cycle is reported, and every node is still present (in file order for the remainder)."""
    from agentcore.services.idp.graph_exec.planner import plan_execution

    graph = {
        "nodes": [_n("a", "AI Field Extractor"), _n("b", "Scan Corrector")],
        "edges": [_e("a", "b"), _e("b", "a")],
    }
    plan = plan_execution(graph)

    assert any("cycle" in e for e in plan.errors)
    assert len(plan.nodes) == 2  # nodes not dropped on a cycle


# --- custom-node seam ------------------------------------------------------------------------


def test_unknown_node_gets_noop_handler():
    """An unrecognised node is planned with the noop handler (the Phase-2 custom-node seam)."""
    from agentcore.services.idp.graph_exec.planner import plan_execution
    from agentcore.services.idp.graph_exec.handlers import noop_handler

    graph = {
        "nodes": [_n("ext", "AI Field Extractor"), _n("custom", "My Custom Node")],
        "edges": [_e("ext", "custom")],
    }
    plan = plan_execution(graph)

    custom = next(n for n in plan.nodes if n.display_name == "My Custom Node")
    assert custom.handler is noop_handler


def test_known_node_gets_real_handler():
    """A known node resolves to its registered (non-noop) handler."""
    from agentcore.services.idp.graph_exec.planner import plan_execution
    from agentcore.services.idp.graph_exec.handlers import get_handler, noop_handler

    graph = {"nodes": [_n("scan", "Scan Corrector"), _n("ext", "AI Field Extractor")],
             "edges": [_e("scan", "ext")]}
    plan = plan_execution(graph)

    scan = next(n for n in plan.nodes if n.display_name == "Scan Corrector")
    assert scan.handler is get_handler("Scan Corrector")
    assert scan.handler is not noop_handler
    assert scan.stage == "pre_text"


# --- source-handle parsing -------------------------------------------------------------------


def test_source_handle_name_structured_form():
    """The structured ``data.sourceHandle.name`` form is read directly."""
    from agentcore.services.idp.graph_exec.planner import _source_handle_name

    edge = {"data": {"sourceHandle": {"name": "high_confidence"}}}
    assert _source_handle_name(edge) == "high_confidence"


def test_source_handle_name_oe_escaped_string_form():
    """The top-level ``œ``-escaped JSON string form is un-escaped and parsed."""
    from agentcore.services.idp.graph_exec.planner import _source_handle_name

    escaped = "{œdataTypeœ:œConfidenceRouterœ,œnameœ:œhigh_confidenceœ}"
    edge = {"sourceHandle": escaped}
    assert _source_handle_name(edge) == "high_confidence"


def test_source_handle_name_returns_none_on_failure():
    """No handle / unparseable string -> None (never raises)."""
    from agentcore.services.idp.graph_exec.planner import _source_handle_name

    assert _source_handle_name({}) is None
    assert _source_handle_name({"sourceHandle": "not-json-at-all"}) is None
    assert _source_handle_name({"sourceHandle": ""}) is None


# --- cross-check against the real shipped template -------------------------------------------


def _real_template_path() -> Path:
    # tests/ -> backend -> src -> frontend/src/data/templates
    return (
        Path(__file__).resolve().parents[2]
        / "frontend"
        / "src"
        / "data"
        / "templates"
        / "idp_universal_pipeline.json"
    )


def test_real_universal_pipeline_plans_without_cycle():
    """The shipped idp_universal_pipeline.json plans cleanly: no cycle, extraction present,
    the fan-in Output Parser once, and all 3 Processed Docs Output nodes."""
    import pytest

    path = _real_template_path()
    if not path.exists():
        pytest.skip(f"template not found at {path}")

    from agentcore.services.idp.graph_exec.planner import plan_execution

    doc = json.loads(path.read_text())
    graph = doc.get("data", doc)  # template wraps the graph under "data"
    plan = plan_execution(graph)

    assert not any("cycle" in e for e in plan.errors), plan.errors
    assert not any("extraction" in e for e in plan.errors), plan.errors

    dns = [n.display_name for n in plan.nodes]
    assert dns.count("Output Parser") == 1
    assert dns.count("Processed Docs Output") == 3
    # every node in the template made it into the plan
    assert len(plan.nodes) == len(graph.get("nodes", []))


# =============================================================================================
# Task 5 — two-stage router branch pruning (presence pre-extract, confidence post-extract)
# =============================================================================================

from types import SimpleNamespace  # noqa: E402


def _ctx(*, overall_conf=None, threshold=0.8, predicted_type=None, extracted=None):
    """A minimal duck-typed ``PipelineContext`` stand-in for the pruners.

    ``overall_conf`` is left OFF the object when None so presence-pruning is proven not to read
    it (an accidental reference would raise AttributeError, failing the test).
    """
    ns = SimpleNamespace(
        cfg=SimpleNamespace(confidence_threshold=threshold),
        doc=SimpleNamespace(predicted_type=predicted_type),
        extracted=extracted or {},
    )
    if overall_conf is not None:
        ns.overall_conf = overall_conf
    return ns


def _mbr(node_id, route_field="document_type", routes=None):
    """A Multi-Branch Router node whose template carries route_field + route_1..5 (real shape:
    ``data.node.template.<field>.value``), mirroring how the shipped node stores its routes."""
    routes = routes or {}
    tmpl = {"route_field": {"value": route_field}}
    for i in range(1, 6):
        tmpl[f"route_{i}"] = {"value": routes.get(i, "")}
    return {"id": node_id, "data": {"node": {"display_name": "Multi-Branch Router", "template": tmpl}}}


# --- (a) Confidence Router, high-conf: prune the low_confidence-exclusive node ----------------


def test_prune_confidence_high_conf_drops_low_branch_exclusive_node():
    from agentcore.services.idp.graph_exec.planner import (
        plan_execution,
        prune_confidence_branches,
    )

    graph = {
        "nodes": [
            _n("ext", "AI Field Extractor"),
            _n("router", "Confidence Router"),
            _n("hi", "Approval Gate"),
            _n("lo", "Processed Docs Output"),
        ],
        "edges": [
            _e("ext", "router"),
            _e("router", "hi", handle="high_confidence"),
            _e("router", "lo", handle="low_confidence"),
        ],
    }
    plan = plan_execution(graph)
    ctx = _ctx(overall_conf=0.95, threshold=0.8)  # >= threshold -> high taken, low not-taken

    pruned = prune_confidence_branches(plan, ctx, graph)

    ids = {n.id for n in pruned.nodes}
    assert "lo" not in ids       # low-branch-exclusive node pruned
    assert "hi" in ids           # taken (high) branch kept
    assert "router" in ids       # the router node itself always stays
    assert "ext" in ids


def test_prune_confidence_low_conf_drops_high_branch_exclusive_node():
    from agentcore.services.idp.graph_exec.planner import (
        plan_execution,
        prune_confidence_branches,
    )

    graph = {
        "nodes": [
            _n("ext", "AI Field Extractor"),
            _n("router", "Confidence Router"),
            _n("hi", "Approval Gate"),
            _n("lo", "Processed Docs Output"),
        ],
        "edges": [
            _e("ext", "router"),
            _e("router", "hi", handle="high_confidence"),
            _e("router", "lo", handle="low_confidence"),
        ],
    }
    plan = plan_execution(graph)
    ctx = _ctx(overall_conf=0.40, threshold=0.8)  # < threshold -> low taken, high not-taken

    pruned = prune_confidence_branches(plan, ctx, graph)

    ids = {n.id for n in pruned.nodes}
    assert "hi" not in ids       # high-branch-exclusive node pruned
    assert "lo" in ids           # taken (low) branch kept
    assert "router" in ids


# --- (b) THE critical test: a node reachable via BOTH not-taken AND a normal edge is KEPT -----


def test_prune_confidence_keeps_shared_node_reachable_via_normal_edge():
    from agentcore.services.idp.graph_exec.planner import (
        plan_execution,
        prune_confidence_branches,
    )

    graph = {
        "nodes": [
            _n("ext", "AI Field Extractor"),
            _n("router", "Confidence Router"),
            _n("hi", "Approval Gate"),
            _n("shared", "Processed Docs Output"),
            _n("normal", "Visual Element Detection"),
        ],
        "edges": [
            _e("ext", "router"),
            _e("router", "hi", handle="high_confidence"),
            _e("router", "shared", handle="low_confidence"),  # not-taken branch feeds shared
            _e("normal", "shared"),                            # ...but shared ALSO has a normal path
        ],
    }
    plan = plan_execution(graph)
    ctx = _ctx(overall_conf=0.95, threshold=0.8)  # high taken -> low is the not-taken branch

    pruned = prune_confidence_branches(plan, ctx, graph)

    ids = {n.id for n in pruned.nodes}
    assert "shared" in ids  # CRITICAL (Codex blocker): a node with ANOTHER path in is never dropped
    assert ids == {"ext", "router", "hi", "shared", "normal"}  # nothing pruned at all


# --- (c) presence-pruning must NOT touch a Confidence Router (needs post-extract conf) --------


def test_presence_prune_leaves_confidence_router_graph_unchanged():
    from agentcore.services.idp.graph_exec.planner import (
        plan_execution,
        prune_presence_branches,
    )

    graph = {
        "nodes": [
            _n("ext", "AI Field Extractor"),
            _n("router", "Confidence Router"),
            _n("hi", "Approval Gate"),
            _n("lo", "Processed Docs Output"),
        ],
        "edges": [
            _e("ext", "router"),
            _e("router", "hi", handle="high_confidence"),
            _e("router", "lo", handle="low_confidence"),
        ],
    }
    plan = plan_execution(graph)
    before = {n.id for n in plan.nodes}
    # ctx has NO overall_conf attribute: if presence-pruning reads it, this raises -> test fails.
    ctx = _ctx(overall_conf=None, predicted_type="invoice")

    pruned = prune_presence_branches(plan, ctx, graph)

    assert {n.id for n in pruned.nodes} == before  # confidence router branches untouched pre-extract


# --- (d) Multi-Branch Router: keep only the matched route_N branch's exclusive nodes ----------


def _multi_branch_graph():
    return {
        "nodes": [
            _n("ext", "AI Field Extractor"),
            _mbr("mbr", route_field="document_type", routes={1: "invoice", 2: "receipt", 3: "contract"}),
            _n("r1", "Processed Docs Output"),
            _n("r2", "Approval Gate"),
            _n("r3", "Visual Element Detection"),
            _n("un", "Math Reconcile"),
        ],
        "edges": [
            _e("ext", "mbr"),
            _e("mbr", "r1", handle="route_1_out"),
            _e("mbr", "r2", handle="route_2_out"),
            _e("mbr", "r3", handle="route_3_out"),
            _e("mbr", "un", handle="unmatched"),
        ],
    }


def test_presence_prune_multi_branch_keeps_only_matched_route():
    from agentcore.services.idp.graph_exec.planner import (
        plan_execution,
        prune_presence_branches,
    )

    graph = _multi_branch_graph()
    plan = plan_execution(graph)
    ctx = _ctx(predicted_type="receipt")  # matches route_2

    pruned = prune_presence_branches(plan, ctx, graph)

    ids = {n.id for n in pruned.nodes}
    assert "r2" in ids            # matched route_2 branch kept
    assert "r1" not in ids        # other routes' exclusive nodes pruned
    assert "r3" not in ids
    assert "un" not in ids        # unmatched pruned because a route matched
    assert "mbr" in ids and "ext" in ids


def test_presence_prune_multi_branch_unmatched_kept_when_nothing_matches():
    from agentcore.services.idp.graph_exec.planner import (
        plan_execution,
        prune_presence_branches,
    )

    graph = _multi_branch_graph()
    plan = plan_execution(graph)
    ctx = _ctx(predicted_type="passport")  # matches no route -> unmatched is the taken branch

    pruned = prune_presence_branches(plan, ctx, graph)

    ids = {n.id for n in pruned.nodes}
    assert "un" in ids                       # unmatched branch kept
    assert not ({"r1", "r2", "r3"} & ids)    # every mapped route pruned


def test_presence_prune_multi_branch_no_predicted_type_keeps_all():
    """Safe default: the deciding value isn't available yet -> don't prune any branch."""
    from agentcore.services.idp.graph_exec.planner import (
        plan_execution,
        prune_presence_branches,
    )

    graph = _multi_branch_graph()
    plan = plan_execution(graph)
    ctx = _ctx(predicted_type=None)  # value unavailable pre-extract

    pruned = prune_presence_branches(plan, ctx, graph)

    ids = {n.id for n in pruned.nodes}
    assert {"r1", "r2", "r3", "un"}.issubset(ids)  # nothing pruned


def test_presence_prune_multi_branch_header_route_field_not_pruned_preextract():
    """A route_field naming a HEADER (not document_type) isn't resolvable pre-extract -> no prune."""
    from agentcore.services.idp.graph_exec.planner import (
        plan_execution,
        prune_presence_branches,
    )

    graph = {
        "nodes": [
            _n("ext", "AI Field Extractor"),
            _mbr("mbr", route_field="vendor_name", routes={1: "acme", 2: "globex"}),
            _n("r1", "Approval Gate"),
            _n("r2", "Processed Docs Output"),
        ],
        "edges": [
            _e("ext", "mbr"),
            _e("mbr", "r1", handle="route_1_out"),
            _e("mbr", "r2", handle="route_2_out"),
        ],
    }
    plan = plan_execution(graph)
    ctx = _ctx(predicted_type="acme")  # predicted_type is irrelevant; route_field is a header

    pruned = prune_presence_branches(plan, ctx, graph)

    ids = {n.id for n in pruned.nodes}
    assert {"r1", "r2"}.issubset(ids)  # header value unavailable pre-extract -> nothing pruned
