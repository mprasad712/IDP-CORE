"""Resolve the agent graph an IDP document must run against — the PUBLISHED deployment snapshot.

Chat agents resolve a published version via ``api/endpoints._resolve_agent_data_for_env``. That helper
cannot be reused from the IDP background pipeline: it opens its OWN ``session_scope()`` and raises
``HTTPException`` (an API concern), and importing API code into a service is a layering violation. This
is its service-layer twin — it takes the caller's session and raises :class:`SnapshotError`.
``tests/test_idp_published_snapshot.py`` asserts the two stay in parity.

Semantics mirror the chat helper EXACTLY:
  * ``dev``  -> ``Agent.data`` (the draft canvas); ``version`` is ignored.
  * ``uat``  -> ``agent_deployment_uat.agent_snapshot``
  * ``prod`` -> ``agent_deployment_prod.agent_snapshot``
  * explicit ``version`` ("v2") -> that exact PUBLISHED row. ``is_active`` is deliberately NOT filtered,
    so a superseded-but-still-published version remains runnable (shadow deployments).
  * ``version is None`` -> the latest ``is_active`` PUBLISHED row.

It NEVER falls back to the draft when a published version is missing — that silent fallback is precisely
the bug this module exists to remove (publish v2, edit the canvas, and the "published" run executed the
unpublished edits).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy import desc
from sqlmodel import select

DEV, UAT, PROD = "dev", "uat", "prod"

# Accept the same encoding the /api/run endpoint uses (?env=1 -> uat), plus the plain names.
_ENV_ALIASES = {"0": DEV, "1": UAT, "2": PROD, DEV: DEV, UAT: UAT, PROD: PROD}
_VERSION_RE = re.compile(r"^v?(\d+)$", re.IGNORECASE)

#: Key under which publish freezes the IdpAgent settings into the graph snapshot.
IDP_CONFIG_KEY = "_idp_agent_config"


class SnapshotError(Exception):
    """The requested (env, version) has no runnable graph. Callers convert this to a PipelineError."""


def normalize_env(env: Any) -> str:
    """``'uat'`` / ``'UAT'`` / ``'1'`` / ``1`` -> ``'uat'``.

    Anything unknown or blank -> ``'dev'`` (the draft), which preserves the behavior of every caller that
    doesn't pass an env: the Playground, connector-ingested documents, and pre-existing rows.
    """
    return _ENV_ALIASES.get(str(env or "").strip().lower(), DEV)


def _version_number(version: str) -> int:
    """``'v2'`` -> ``2``.

    The chat helper does ``int(version.lstrip("v"))``, which raises an uncaught ``ValueError`` (surfacing
    as HTTP 500) on ``""`` / ``"latest"`` / ``"1.0"``, and mis-parses ``"vv3"``. We validate instead.
    """
    match = _VERSION_RE.match(str(version or "").strip())
    if not match:
        raise SnapshotError(f"Invalid version {version!r}. Expected a form like 'v2' or '2'.")
    return int(match.group(1))


def pinned_idp_config(graph: dict | None) -> dict:
    """The ``IdpAgent`` settings frozen into the snapshot at publish time.

    Empty for the draft and for snapshots published before this key existed — callers then fall back to
    the live ``IdpAgent`` row (which is synced from the *draft* graph, hence the need to pin).
    """
    config = (graph or {}).get(IDP_CONFIG_KEY)
    return dict(config) if isinstance(config, dict) else {}


class _Overlay:
    """A non-ORM read-through view: ``overrides`` win, everything else comes from the real row.

    Deliberately NOT a ``SimpleNamespace``. A namespace only has the attributes you remembered to copy,
    so the first consumer to read an un-copied column dies with ``AttributeError`` at run time — which is
    exactly what happened when ``resolve_pipeline_config`` read ``idp_agent.multi_doc_split`` off a shim
    built from an enumerated field list. Reading through means a new column can never go missing.

    Writes land on the overlay, never on the wrapped row: the pipeline commits several times mid-run, and
    the whole point of these shims is that a published run must not persist anything back over the
    developer's draft (``Agent.data``) or the live ``IdpAgent`` settings.
    """

    __slots__ = ("_row", "_overrides")

    def __init__(self, row, overrides: dict):
        object.__setattr__(self, "_row", row)
        object.__setattr__(self, "_overrides", dict(overrides))

    def __getattr__(self, name: str):
        # Only called when `name` isn't a slot — i.e. for everything the caller wants off the row.
        overrides = object.__getattribute__(self, "_overrides")
        if name in overrides:
            return overrides[name]
        return getattr(object.__getattribute__(self, "_row"), name)

    def __setattr__(self, name: str, value) -> None:
        object.__getattribute__(self, "_overrides")[name] = value

    def __repr__(self) -> str:
        row = object.__getattribute__(self, "_row")
        return f"<Overlay {type(row).__name__} overrides={sorted(object.__getattribute__(self, '_overrides'))}>"


def graph_agent_view(base_agent, graph: dict):
    """A non-ORM stand-in for the ``Agent`` row whose ``.data`` is the graph THIS run must execute.

    HARD RULE: never assign ``base_agent.data = graph``. ``base_agent`` is a session-attached ORM row and
    the pipeline commits several times mid-run, so the assignment would persist the published snapshot
    OVER the developer's draft canvas. Every other attribute reads through to the real row.
    """
    return _Overlay(base_agent, {"data": graph})


def pin_idp_agent(idp_agent, graph: dict):
    """A read-only view of ``IdpAgent`` whose SETTINGS come from the published snapshot.

    ``IdpAgent`` rows are re-synced from the DRAFT graph on every agent save (``sync_idp_agent_from_graph``),
    so a published run would otherwise execute the frozen graph with *today's* draft settings — execution
    engine, ocr_language, long-doc limits, confidence threshold, dedup strategy. Everything the snapshot
    did NOT pin (identity, ``multi_doc_split``, ``is_active``, …) reads through to the real row.

    Returns the ORM row untouched when nothing is pinned — i.e. for ``dev``, and for snapshots published
    before the config was frozen (backwards compatible).
    """
    pinned = pinned_idp_config(graph)
    if not pinned:
        return idp_agent

    # field_config_id round-trips through JSON as a string; coerce it back, falling back on garbage.
    field_config_id = idp_agent.field_config_id
    if "field_config_id" in pinned:
        raw = pinned["field_config_id"]
        if raw is None:
            field_config_id = None
        else:
            try:
                field_config_id = UUID(str(raw))
            except (TypeError, ValueError):
                field_config_id = idp_agent.field_config_id

    return _Overlay(idp_agent, {
        # The snapshot's `extra` wins key-by-key; anything it didn't pin falls back to the live row.
        "extra": {**(getattr(idp_agent, "extra", None) or {}), **(pinned.get("extra") or {})},
        "extraction_mode": pinned.get("extraction_mode", idp_agent.extraction_mode),
        "dynamic_prompt": pinned.get("dynamic_prompt", idp_agent.dynamic_prompt),
        "field_config_id": field_config_id,
        "default_rule_action": pinned.get("default_rule_action", idp_agent.default_rule_action),
    })


def graph_fingerprint(graph: dict | None) -> str:
    """A short, stable digest of a graph's ``nodes`` + ``edges``.

    Only the graph proper — the publish-time keys (``_input_type``, ``_idp_agent_config``) are excluded, so
    comparing a snapshot's fingerprint against the draft's answers the question that actually matters:
    *is the graph being executed the same one that's on the canvas right now?*
    """
    import hashlib
    import json

    payload = {
        "nodes": (graph or {}).get("nodes") or [],
        "edges": (graph or {}).get("edges") or [],
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:12]


@dataclass(frozen=True)
class GraphSource:
    """Where a run's graph was actually READ FROM. Logged so a published run is auditable."""

    env: str
    #: The real table the row came from: ``agent`` (draft) | ``agent_deployment_uat`` | ``agent_deployment_prod``
    table: str
    #: Primary key of the deployment row. ``None`` only for the draft.
    deployment_id: UUID | None
    version_number: int | None
    is_active: bool | None
    fingerprint: str
    node_count: int

    def describe(self) -> str:
        if self.deployment_id is None:
            return f"table={self.table} (DRAFT canvas) sha={self.fingerprint} nodes={self.node_count}"
        return (
            f"table={self.table} row={self.deployment_id} version=v{self.version_number} "
            f"is_active={self.is_active} sha={self.fingerprint} nodes={self.node_count}"
        )


async def resolve_agent_graph(
    session, agent_id: UUID, env: Any, version: str | None = None
) -> tuple[dict, UUID | None]:
    """Return ``(graph, deployment_id)`` for the requested environment + version.

    ``deployment_id`` is ``None`` for ``dev``; otherwise it identifies the exact deployment row the graph
    came from. Raises :class:`SnapshotError` when no matching PUBLISHED deployment exists.
    """
    graph, source = await resolve_agent_graph_with_source(session, agent_id, env, version)
    return graph, source.deployment_id


async def resolve_agent_graph_with_source(
    session, agent_id: UUID, env: Any, version: str | None = None
) -> tuple[dict, GraphSource]:
    """As :func:`resolve_agent_graph`, but also reports WHICH ROW of WHICH TABLE the graph came from.

    The pipeline logs this. "running UAT v5" alone proves nothing — it is just the value of
    ``doc.run_env``. The deployment row's primary key, read back off the ORM object that was actually
    fetched, is what proves the graph came out of ``agent_deployment_uat`` and not ``agent.data``.
    """
    from agentcore.services.database.models.agent.model import Agent
    from agentcore.services.database.models.agent_deployment_prod.model import (
        AgentDeploymentProd,
        DeploymentPRODStatusEnum,
    )
    from agentcore.services.database.models.agent_deployment_uat.model import (
        AgentDeploymentUAT,
        DeploymentUATStatusEnum,
    )

    resolved = normalize_env(env)

    if resolved == DEV:
        agent = await session.get(Agent, agent_id)
        if agent is None or not agent.data:
            raise SnapshotError(f"Agent {agent_id} not found or has no graph.")
        return agent.data, GraphSource(
            env=DEV, table="agent", deployment_id=None, version_number=None, is_active=None,
            fingerprint=graph_fingerprint(agent.data), node_count=len(agent.data.get("nodes") or []),
        )

    if resolved == UAT:
        model, published = AgentDeploymentUAT, DeploymentUATStatusEnum.PUBLISHED
    else:
        model, published = AgentDeploymentProd, DeploymentPRODStatusEnum.PUBLISHED

    stmt = select(model).where(model.agent_id == agent_id, model.status == published)
    if version is not None:
        # Exact version: no is_active filter — a superseded but published version stays runnable.
        stmt = stmt.where(model.version_number == _version_number(version))
    else:
        stmt = stmt.where(model.is_active == True).order_by(desc(model.version_number))  # noqa: E712

    record = (await session.exec(stmt)).first()
    if record is None:
        what = f"version '{version}'" if version is not None else "an active deployment"
        raise SnapshotError(
            f"Agent {agent_id} has no PUBLISHED {resolved.upper()} {what}. "
            f"Publish the agent to {resolved.upper()} before processing documents against it."
        )
    if not record.agent_snapshot:
        raise SnapshotError(
            f"PUBLISHED {resolved.upper()} deployment {record.id} has an empty agent_snapshot."
        )
    # `record` is an ORM row fetched from `model.__tablename__` — its primary key is the audit trail.
    return record.agent_snapshot, GraphSource(
        env=resolved,
        table=model.__tablename__,
        deployment_id=record.id,
        version_number=record.version_number,
        is_active=record.is_active,
        fingerprint=graph_fingerprint(record.agent_snapshot),
        node_count=len(record.agent_snapshot.get("nodes") or []),
    )
