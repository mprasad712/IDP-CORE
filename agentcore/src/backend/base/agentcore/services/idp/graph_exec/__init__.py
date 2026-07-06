"""Graph-driven IDP pipeline execution (flag-ON path).

Phase 1: a lightweight interpreter over the existing ``services.idp`` step functions that runs
the agent's canvas nodes in graph order + prunes not-taken router branches, behind the
``IDP_GRAPH_EXECUTION`` flag. The legacy ``pipeline._run`` remains the default until parity holds.
"""

from agentcore.services.idp.graph_exec.context import PipelineContext
from agentcore.services.idp.graph_exec.planner import (
    plan_execution,
    prune_confidence_branches,
    prune_presence_branches,
)
from agentcore.services.idp.graph_exec.runner import run_graph_pipeline

__all__ = [
    "PipelineContext",
    "plan_execution",
    "prune_confidence_branches",
    "prune_presence_branches",
    "run_graph_pipeline",
]
