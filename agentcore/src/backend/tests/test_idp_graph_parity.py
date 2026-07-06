"""Parity gate for the graph-driven IDP pipeline (Task 8).

Processing the SAME document with the SAME agent must give an IDENTICAL terminal state whether
the legacy in-line path runs (``IDP_GRAPH_EXECUTION`` OFF) or the graph runner runs (flag ON).
This is the milestone that proves the flag-ON graph path faithfully matches legacy.

The agent graph is the real universal template
(``frontend/src/data/templates/idp_universal_pipeline.json``) — the full node set (Scan
Corrector, PaddleOCR, Document Classifier, Output Parser, Chunking, Chunk Aggregator,
Confidence Router, Rules/Conditions, Approval Gate, AI Field Extractor, Processed Docs Output).

Determinism: the LLM/extraction is monkeypatched so BOTH paths receive the same extracted data
(``pipeline._build_llm`` stub, ``pipeline.extract_dynamic`` fixed result, classification stub).
The graph runner binds ``_build_llm`` at import, so it is patched on the runner module too. No
real model is ever hit.

Reuses the harness from ``test_idp_pipeline`` (``_setup_document`` / ``_cleanup`` /
``_digital_pdf`` / ``_patch_extraction`` / ``_MockStorage``) — cross-imported as ``T``.
"""

import copy
import json
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

import test_idp_pipeline as T  # reuse the B19 pipeline test harness (same tests/ dir)
from agentcore.services.idp import pipeline
from agentcore.services.idp.graph_exec import plan_execution, prune_confidence_branches
from agentcore.services.idp.graph_exec import runner as graph_runner

# ─────────────────────────────── fixtures ───────────────────────────────
# The autouse mock_storage fixture in test_idp_pipeline does NOT cross modules, so re-declare it
# here (plus the anyio backend selector) — same in-memory storage the harness expects.


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture(autouse=True)
def mock_storage(monkeypatch):
    s = T._MockStorage()
    monkeypatch.setattr(pipeline, "get_storage_service", lambda: s)
    return s


# ─────────────────────────────── helpers ───────────────────────────────
_TEMPLATE_PATH = (
    Path(__file__).resolve().parents[2]
    / "frontend" / "src" / "data" / "templates" / "idp_universal_pipeline.json"
)

# Confidence Router threshold in the template (drives the low/high branch + _route's conf gate).
_ROUTER_THRESHOLD = 0.8

# Node ids from the template used by the direct-pruner assertions.
_RULES_ID = "RulesConditions-bR0um"
_APPROVAL_ID = "ApprovalGate-gOCby"
_LOW_CONF_SINK_ID = "ProcessedDocsOutput-K6Jfm"  # only reachable via ConfidenceRouter low_confidence
_HIGH_CONF_SINKS = {"ProcessedDocsOutput-pNRex", "ProcessedDocsOutput-tgF9a"}


def _universal_graph(model_uuid: str) -> dict:
    """Load the universal-pipeline template and inject a resolvable model uuid into every LLM node.

    The template ships with empty ``registry_model`` values; without a model the shared pipeline
    setup raises "no model configured" before either path runs. Injecting the standard
    ``"display | model_name | uuid"`` string lets ``_resolve_model_id_from_graph`` pick it up.
    """
    template = json.loads(_TEMPLATE_PATH.read_text())
    graph = copy.deepcopy(template["data"])  # {nodes, edges, viewport}
    for node in graph.get("nodes", []):
        inner = (node.get("data") or {}).get("node") or {}
        if inner.get("display_name") == "Large Language Model":
            tmpl = inner.get("template") or {}
            if isinstance(tmpl.get("registry_model"), dict):
                tmpl["registry_model"]["value"] = f"GPT | gpt-4o | {model_uuid}"
    return graph


def _default_classify_result() -> dict:
    """A no-skip, no-auto-select classification (deterministic, keeps extraction in dynamic mode)."""
    return {"predicted_type": "Invoice", "confidence": 0.95, "candidates": None, "selected_config_id": None}


def _patch_all(monkeypatch, *, result=None, raises=False, classify_result=None):
    """Deterministic extraction for BOTH paths + a classification stub (universal template has a
    Document Classifier, so classification always fires — stub it so no real model is hit).

    ``T._patch_extraction`` patches ``pipeline._build_llm`` + ``pipeline.extract_dynamic``. The
    graph runner imported ``_build_llm`` at module load, so its binding must be patched too.
    """
    T._patch_extraction(monkeypatch, result=result, raises=raises)
    monkeypatch.setattr(graph_runner, "_build_llm", pipeline._build_llm)  # runner's own binding

    _cr = classify_result if classify_result is not None else _default_classify_result()

    async def _stub_classify(session, document_id, merged_text, org_id, *, auto_select, threshold, doc_types=None):
        return _cr

    monkeypatch.setattr("agentcore.services.idp.classification.classify_and_persist", _stub_classify)


async def _terminal_state(doc_id) -> dict:
    """The comparable terminal state of a processed document (path-agnostic)."""
    from agentcore.services.database.models.idp.documents import (
        IdpDocument,
        IdpExtractedHeader,
        IdpExtractedLineItem,
        IdpProcessingJob,
    )
    from agentcore.services.deps import session_scope
    from sqlmodel import select

    async with session_scope() as session:
        doc = await session.get(IdpDocument, doc_id)
        job = (
            await session.exec(select(IdpProcessingJob).where(IdpProcessingJob.document_id == doc_id))
        ).first()
        n_head = len(
            (await session.exec(select(IdpExtractedHeader).where(IdpExtractedHeader.job_id == job.id))).all()
        )
        n_line = len(
            (await session.exec(select(IdpExtractedLineItem).where(IdpExtractedLineItem.job_id == job.id))).all()
        )
        return {
            "status": doc.status,
            "overall_confidence": round(float(doc.overall_confidence or 0.0), 4),
            "n_headers": n_head,
            "n_line_items": n_line,
            "route_label": doc.route_label,
            "job_status": job.status,
            "log": list(job.log or []),
        }


def _comparable(state: dict) -> dict:
    return {k: state[k] for k in ("status", "overall_confidence", "n_headers", "n_line_items", "route_label")}


async def _process_offthen_on(monkeypatch, graph, *, file_bytes):
    """Process one doc with the flag OFF (legacy) and another identical doc with the flag ON
    (graph). Returns ``(off_state, on_state, off_id, on_id)``. Agents are byte-identical; the
    ONLY difference between the two runs is the ``IDP_GRAPH_EXECUTION`` env flag.
    """
    from agentcore.services.deps import session_scope

    # flag OFF → legacy path
    monkeypatch.delenv("IDP_GRAPH_EXECUTION", raising=False)
    async with session_scope() as session:
        off_agent, off_doc = await T._setup_document(session, graph=graph, file_bytes=file_bytes)
    await pipeline.process_document(off_doc)
    off_state = await _terminal_state(off_doc)

    # flag ON → graph runner path (identical agent/graph/file)
    monkeypatch.setenv("IDP_GRAPH_EXECUTION", "true")
    async with session_scope() as session:
        on_agent, on_doc = await T._setup_document(session, graph=copy.deepcopy(graph), file_bytes=file_bytes)
    await pipeline.process_document(on_doc)
    on_state = await _terminal_state(on_doc)

    return off_state, on_state, (off_agent, off_doc), (on_agent, on_doc)


# ─────────────────────────────── tests ───────────────────────────────
@pytest.mark.anyio
async def test_graph_runner_matches_legacy_on_universal_template(monkeypatch):
    """PARITY GATE: legacy (flag OFF) and graph (flag ON) reach an IDENTICAL terminal state for the
    same document + universal-template agent (status, overall_confidence, header/line counts,
    route_label)."""
    _patch_all(monkeypatch)
    graph = _universal_graph(str(uuid4()))
    file_bytes = T._digital_pdf()

    off_state, on_state, off_ids, on_ids = await _process_offthen_on(monkeypatch, graph, file_bytes=file_bytes)
    try:
        # Both paths completed cleanly.
        assert off_state["job_status"] == "completed", off_state
        assert on_state["job_status"] == "completed", on_state

        # The two runs actually took DIFFERENT execution paths: only the graph path emits "node"
        # marker steps; legacy never does. Guards against the flag failing to take effect.
        off_steps = [e["step"] for e in off_state["log"]]
        on_steps = [e["step"] for e in on_state["log"]]
        assert "node" not in off_steps, "legacy path unexpectedly emitted graph 'node' steps"
        assert "node" in on_steps, "graph path did not emit any 'node' marker steps"

        # THE PARITY ASSERTION.
        assert _comparable(off_state) == _comparable(on_state), (
            f"parity divergence:\n  legacy(off)={_comparable(off_state)}\n  graph(on) ={_comparable(on_state)}"
        )
    finally:
        await T._cleanup(*off_ids)
        await T._cleanup(*on_ids)


@pytest.mark.anyio
async def test_graph_flow_log_lists_nodes_in_graph_order(monkeypatch):
    """The flag-ON flow log lists the expected pipeline steps in graph order (load→config→detect→
    text→extract→route→finalize) and contains no error/traceback."""
    _patch_all(monkeypatch)
    graph = _universal_graph(str(uuid4()))

    from agentcore.services.deps import session_scope

    monkeypatch.setenv("IDP_GRAPH_EXECUTION", "true")
    async with session_scope() as session:
        agent_id, doc_id = await T._setup_document(session, graph=graph, file_bytes=T._digital_pdf())
    try:
        await pipeline.process_document(doc_id)
        state = await _terminal_state(doc_id)
        assert state["job_status"] == "completed", state

        steps = [e["step"] for e in state["log"]]
        # every expected anchor step is present …
        expected = ["load", "config", "detect", "text", "extract", "route", "finalize"]
        for s in expected:
            assert s in steps, f"missing pipeline step {s!r}; got {steps}"
        # … and appears in graph order (first occurrence of each is strictly increasing).
        first_idx = [steps.index(s) for s in expected]
        assert first_idx == sorted(first_idx), f"steps out of graph order: {list(zip(expected, first_idx))}"

        # the graph markers ran (proof the canvas nodes were walked, not just the backbone).
        assert "node" in steps

        # no error / traceback anywhere in the log.
        assert all(e.get("status") != "error" for e in state["log"]), state["log"]
    finally:
        await T._cleanup(agent_id, doc_id)


@pytest.mark.anyio
async def test_graph_low_confidence_routes_to_pending_review(monkeypatch):
    """Edge (a): a low-confidence doc lands in pending_review under BOTH paths, and the Confidence
    Router's LOW branch is the one taken (its high-branch nodes are pruned post-extract)."""
    # overall confidence 0.3 < router threshold 0.8
    _patch_all(monkeypatch, result={"headers": {"f": {"value": "X", "confidence": 0.3}}, "line_items": []})
    graph = _universal_graph(str(uuid4()))

    off_state, on_state, off_ids, on_ids = await _process_offthen_on(
        monkeypatch, graph, file_bytes=T._digital_pdf()
    )
    try:
        assert on_state["status"] == "pending_review", on_state
        assert off_state["status"] == "pending_review", off_state  # legacy agrees
        assert _comparable(off_state) == _comparable(on_state)

        # Direct proof that the LOW branch is taken: with overall_conf below threshold, the
        # high_confidence-exclusive nodes (Rules/Conditions, Approval Gate, its auto/pending sinks)
        # are pruned, while the low_confidence sink survives.
        plan = plan_execution(graph)
        low_ctx = SimpleNamespace(overall_conf=0.3, cfg=SimpleNamespace(confidence_threshold=_ROUTER_THRESHOLD))
        low_ids = {n.id for n in prune_confidence_branches(plan, low_ctx, graph).nodes}
        assert _RULES_ID not in low_ids and _APPROVAL_ID not in low_ids, "high branch not pruned on low conf"
        assert _HIGH_CONF_SINKS.isdisjoint(low_ids), "high-branch sinks not pruned on low conf"
        assert _LOW_CONF_SINK_ID in low_ids, "low-confidence sink was wrongly pruned"

        # Inverse: high confidence keeps the high branch and prunes only the low sink.
        high_ctx = SimpleNamespace(overall_conf=0.95, cfg=SimpleNamespace(confidence_threshold=_ROUTER_THRESHOLD))
        high_ids = {n.id for n in prune_confidence_branches(plan, high_ctx, graph).nodes}
        assert _RULES_ID in high_ids and _APPROVAL_ID in high_ids, "high branch wrongly pruned on high conf"
        assert _LOW_CONF_SINK_ID not in high_ids, "low sink not pruned on high conf"
    finally:
        await T._cleanup(*off_ids)
        await T._cleanup(*on_ids)


@pytest.mark.anyio
async def test_graph_unknown_custom_node_processes_without_crashing(monkeypatch):
    """Edge (b): an unknown/custom node on the canvas runs via the noop handler (a warn flow step),
    the document still processes to a terminal state, and it matches the legacy result."""
    _patch_all(monkeypatch)
    graph = _universal_graph(str(uuid4()))
    # Append a node with a display_name no handler knows about (Phase-2 seam), no edges needed.
    graph["nodes"].append(
        {
            "id": "CustomWidget-zzz99",
            "type": "genericNode",
            "data": {
                "node": {
                    "display_name": "My Custom Widget",
                    "template": {"_type": "Component", "note": {"value": "hi"}},
                },
                "type": "CustomWidget",
                "id": "CustomWidget-zzz99",
            },
        }
    )

    off_state, on_state, off_ids, on_ids = await _process_offthen_on(
        monkeypatch, graph, file_bytes=T._digital_pdf()
    )
    try:
        # graph path survived the unknown node …
        assert on_state["job_status"] == "completed", on_state
        assert on_state["status"] in ("auto_approved", "pending_review"), on_state
        # … via the noop handler warn (Phase-2 seam) — never a crash.
        assert any(
            e["step"] == "node" and e.get("status") == "warn" and "no handler" in e.get("detail", "")
            for e in on_state["log"]
        ), [e for e in on_state["log"] if e["step"] == "node"]
        # legacy ignores the unknown node entirely → same terminal state.
        assert _comparable(off_state) == _comparable(on_state)
    finally:
        await T._cleanup(*off_ids)
        await T._cleanup(*on_ids)


@pytest.mark.anyio
async def test_graph_classify_skip_ends_skipped(monkeypatch):
    """Edge (c): when classification returns a skip verdict, the graph path ends 'skipped' — same
    as legacy — with no extraction."""
    _patch_all(
        monkeypatch,
        classify_result={"predicted_type": "Unknown", "confidence": 0.4, "skip": True},
    )
    graph = _universal_graph(str(uuid4()))

    off_state, on_state, off_ids, on_ids = await _process_offthen_on(
        monkeypatch, graph, file_bytes=T._digital_pdf()
    )
    try:
        assert on_state["status"] == "skipped", on_state
        assert off_state["status"] == "skipped", off_state  # legacy agrees
        assert on_state["job_status"] == "completed" and off_state["job_status"] == "completed"
        # skip short-circuits before extraction → no header/line rows on either path.
        assert on_state["n_headers"] == 0 and on_state["n_line_items"] == 0, on_state
        assert _comparable(off_state) == _comparable(on_state)
    finally:
        await T._cleanup(*off_ids)
        await T._cleanup(*on_ids)
