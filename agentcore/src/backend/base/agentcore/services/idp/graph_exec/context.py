"""Shared execution state for the graph-driven IDP pipeline path.

``PipelineContext`` is the bag of state that flows between node handlers + the anchored
backbone steps in the flag-ON graph runner (``IDP_GRAPH_EXECUTION``). It mirrors the local
variables of the legacy ``services.idp.pipeline._run`` so the graph path can reach parity by
reusing the same standalone step functions.

Object fields (doc/job/cfg/flow/session/…) are typed ``Any`` on purpose — importing the real
models/pipeline here would create an import cycle, and the runner/handlers only pass them
through to the existing functions.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from uuid import UUID


@dataclass
class PipelineContext:
    # --- identity / infra ---
    session: Any = None
    document_id: UUID | None = None
    doc: Any = None
    job: Any = None
    idp_agent: Any = None
    base_agent: Any = None
    cfg: Any = None
    flow: Any = None
    storage: Any = None
    agent_scope: str | None = None
    trace_ctx: Any = None
    t0: float = 0.0

    # --- document bytes / type ---
    file_bytes: bytes = b""
    original_bytes: bytes = b""
    file_type: str = ""

    # --- text acquisition ---
    page_status: dict = field(default_factory=dict)
    overall_kind: str = ""
    tokens: list = field(default_factory=list)
    merged_text: str = ""
    digital_pages: set = field(default_factory=set)
    scanned_pages: set = field(default_factory=set)

    # --- model / extraction ---
    llm: Any = None
    extracted: dict = field(default_factory=dict)
    overall_conf: float = 0.0

    # --- routing / terminal ---
    new_status: str | None = None
    route_label: str | None = None
    child_ids: list | None = None
    skipped: bool = False
