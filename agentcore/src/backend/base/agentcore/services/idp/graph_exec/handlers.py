"""Node handlers for the graph-driven IDP pipeline path (flag-ON).

Each handler dispatches on a canvas node's ``display_name`` and mutates the shared
:class:`~agentcore.services.idp.graph_exec.context.PipelineContext`. Handlers do NOT
reimplement OCR / classification / detection / reconcile logic — they **delegate to the
existing standalone step functions** in :mod:`agentcore.services.idp.pipeline`, so the graph
path reaches parity with the legacy ``_run`` by reusing the same code.

Two kinds of handlers live here:

* **Real handlers** — Scan Corrector — call the corresponding ``pipeline`` hook and write the
  result back to ``ctx``.
* **Marker handlers** — PaddleOCR / Page Selector / Chunking Strategy / AI Field Extractor /
  Document Classifier / Visual Element Detection / Math Reconcile / Rules / Confidence Router /
  Approval Gate — emit only a flow step. Their real effect is anchored in the runner's backbone
  stages rather than in the handler itself. Classification, Visual Element Detection and Math
  Reconcile are anchored as ``cfg``-gated steps in the runner (at their legacy ``_run`` positions),
  so they run whenever the feature is enabled by a node OR by ``idp_agent.extra`` — the node here
  is only a graph-order marker.

Unknown / custom nodes fall back to :func:`noop_handler` (the Phase-2 seam): a ``warn`` flow
step, never a crash.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Awaitable, Callable

# Reuse the existing pipeline step functions — never reimplement them here.
# NOTE: this is a top-level import on purpose. Today ``pipeline`` does not import
# ``graph_exec`` so there is no cycle, and top-level binding makes the functions
# monkeypatchable on THIS module (``handlers._maybe_preprocess`` etc.) in tests.
# If a future change makes ``pipeline`` import this package (creating a cycle),
# move these imports into the handler bodies (lazy) and monkeypatch on the
# ``pipeline`` module instead.
from agentcore.services.idp.pipeline import _maybe_preprocess

if TYPE_CHECKING:
    from agentcore.services.idp.graph_exec.context import PipelineContext

# A handler consumes the run context + the raw canvas node dict and mutates ctx in place.
Handler = Callable[["PipelineContext", dict], Awaitable[None]]


def node_display_name(node: dict) -> str:
    """Return a node's canvas ``display_name`` (falling back to ``data.type``, then "").

    Dispatch keys off ``display_name`` throughout the graph path — the template class name
    (``data.type``) does not match the backend Component class name, so it is only a fallback.
    """
    data = node.get("data", {}) or {}
    inner = data.get("node") or {}
    return inner.get("display_name") or data.get("type", "")


# display_name -> pipeline stage. Drives ordering + two-stage router pruning in the planner.
NODE_STAGE: dict[str, str] = {
    "Scan Corrector": "pre_text",
    "PaddleOCR": "text",
    "Page Selector": "text",
    "Document Classifier": "post_text",
    "Chunking Strategy": "post_text",
    "AI Field Extractor": "extract",
    "Visual Element Detection": "post_extract",
    "Math Reconcile": "post_extract",
    "Rules / Conditions": "route",
    "Confidence Router": "route",
    "Approval Gate": "route",
}


# --- real handlers: delegate to the existing pipeline hooks ---------------------------------


async def _handle_scan_corrector(ctx: "PipelineContext", node: dict) -> None:
    """Deskew / rotation-correct the working bytes before OCR (non-fatal)."""
    ctx.file_bytes = await _maybe_preprocess(ctx.file_bytes, ctx.file_type, ctx.cfg, ctx.flow)


# --- marker handlers: real effect is anchored in the runner backbone ------------------------
# Document Classifier / Visual Element Detection / Math Reconcile are MARKERS: their real work is
# anchored as cfg-gated steps in the runner (classify before extract, detect before classify, math
# after extract — legacy _run positions), so the feature runs whether it was enabled by this node
# OR by idp_agent.extra.*_enabled. The marker here only records a graph-order flow step.


def _marker_handler(display_name: str) -> Handler:
    """Build a no-work handler that only records a flow step for ``display_name``."""

    async def _handler(ctx: "PipelineContext", node: dict) -> None:
        ctx.flow.step("node", "ok", node_display_name(node))

    _handler.__name__ = f"_marker_{display_name}"
    return _handler


NODE_HANDLERS: dict[str, Handler] = {
    "Scan Corrector": _handle_scan_corrector,
    # Markers — anchored in the runner (text acquisition / detect / classify / extract / math /
    # route stages). The runner gates the anchored work on cfg.*_enabled (node OR extra).
    "Document Classifier": _marker_handler("Document Classifier"),
    "Visual Element Detection": _marker_handler("Visual Element Detection"),
    "Math Reconcile": _marker_handler("Math Reconcile"),
    "PaddleOCR": _marker_handler("PaddleOCR"),
    "Page Selector": _marker_handler("Page Selector"),
    "Chunking Strategy": _marker_handler("Chunking Strategy"),
    "AI Field Extractor": _marker_handler("AI Field Extractor"),
    "Rules / Conditions": _marker_handler("Rules / Conditions"),
    "Confidence Router": _marker_handler("Confidence Router"),
    "Approval Gate": _marker_handler("Approval Gate"),
}


def get_handler(display_name: str) -> Handler | None:
    """Return the handler for ``display_name``, or ``None`` for unknown/custom nodes."""
    return NODE_HANDLERS.get(display_name)


async def noop_handler(ctx: "PipelineContext", node: dict) -> None:
    """Fallback for unknown/custom nodes (Phase-2 seam): warn, never crash."""
    ctx.flow.step("node", "warn", f"node {node_display_name(node)!r} has no handler — skipped")
