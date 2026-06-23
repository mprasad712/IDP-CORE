"""IDP dashboard metric query builders — read-only, organization-scoped.

Pure aggregation helpers consumed by the ``/api/dashboard/sections/idp-*`` handlers.
Every query is READ-ONLY and scoped to the caller's organizations (``root`` sees all),
mirroring the scoping used by the IDP processed-docs and document endpoints. No writes,
no migrations — this module only reads existing ``idp_*`` tables.

The handlers keep all timezone/bucketing logic; these helpers return raw counts,
averages, or timestamp lists so the caller can bucket them with the dashboard's
existing ``_normalize_day`` utility.
"""
from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import func
from sqlmodel import select

from agentcore.services.database.models.agent.model import Agent
from agentcore.services.database.models.idp.config import (
    IdpAgent,
    IdpFieldConfiguration,
    IdpReviewSession,
)
from agentcore.services.database.models.idp.documents import (
    IdpDocument,
    IdpProcessingJob,
)
from agentcore.services.idp.scope import resolve_org_scope

# Status groupings (mirror the idp_documents status CHECK constraint).
# NOTE: 'split' is intentionally EXCLUDED — a split parent itself is never extracted; its
# child documents carry their own terminal statuses and are counted instead. Counting the
# parent too would double-count documents in "processed"/throughput and skew success rates.
TERMINAL_STATUSES = ("extracted", "pending_review", "auto_approved", "reviewed", "failed", "skipped")
SUCCESS_STATUSES = ("auto_approved", "reviewed")
FAILED_STATUSES = ("failed", "skipped")  # "Failed / Skipped" submission KPI (user-facing grouping)
# For success/error RATES: only a hard pipeline error counts as a failure. A doc routed to
# pending_review processed successfully (it just needs a human), and 'skipped' means the human
# skipped review — neither is a processing failure. So rates use FAILURE_STATUSES, not FAILED_STATUSES.
FAILURE_STATUSES = ("failed",)


async def idp_scope(session, current_user) -> tuple[bool, list[UUID]]:
    """Return ``(is_root, org_ids)`` — canonical IDP org-scope (see ``services.idp.scope``)."""
    return await resolve_org_scope(session, current_user)


def _apply_doc_scope(stmt, is_root: bool, org_ids: list[UUID]):
    """Constrain a SELECT over IdpDocument to the caller's orgs (no-op for root)."""
    if is_root:
        return stmt
    return (
        stmt.join(IdpAgent, IdpDocument.agent_id == IdpAgent.id)
        .join(Agent, IdpAgent.agent_id == Agent.id)
        .where(Agent.org_id.in_(org_ids))
    )


async def count_docs(
    session,
    is_root: bool,
    org_ids: list[UUID],
    *,
    statuses: tuple[str, ...] | None = None,
    start_dt: datetime | None = None,
    end_dt: datetime | None = None,
    uploaded_by: UUID | None = None,
) -> int:
    """Count documents in scope, optionally filtered by status set and a created_at window."""
    stmt = _apply_doc_scope(select(func.count(IdpDocument.id)), is_root, org_ids)
    if statuses is not None:
        stmt = stmt.where(IdpDocument.status.in_(list(statuses)))
    if start_dt is not None:
        stmt = stmt.where(IdpDocument.created_at >= start_dt)
    if end_dt is not None:
        stmt = stmt.where(IdpDocument.created_at < end_dt)
    if uploaded_by is not None:
        stmt = stmt.where(IdpDocument.uploaded_by == uploaded_by)
    return int((await session.exec(stmt)).one() or 0)


async def doc_timestamps(
    session,
    is_root: bool,
    org_ids: list[UUID],
    *,
    start_dt: datetime,
    end_dt: datetime,
    statuses: tuple[str, ...] | None = None,
    uploaded_by: UUID | None = None,
) -> list[datetime]:
    """Return created_at timestamps for in-scope docs in [start, end) — for daily series bucketing."""
    stmt = _apply_doc_scope(select(IdpDocument.created_at), is_root, org_ids).where(
        IdpDocument.created_at >= start_dt, IdpDocument.created_at < end_dt
    )
    if statuses is not None:
        stmt = stmt.where(IdpDocument.status.in_(list(statuses)))
    if uploaded_by is not None:
        stmt = stmt.where(IdpDocument.uploaded_by == uploaded_by)
    rows = (await session.exec(stmt)).all()
    return [r if isinstance(r, datetime) else r[0] for r in rows]


async def avg_processing_seconds(
    session,
    is_root: bool,
    org_ids: list[UUID],
    *,
    start_dt: datetime | None = None,
    end_dt: datetime | None = None,
) -> float:
    """Average processing time (seconds) over the LATEST completed job per document, in scope.

    Averages one row per document — the most recent ``status == 'completed'`` job (with a
    non-null time). This excludes failed attempts AND avoids double-counting a re-processed
    document that accumulated multiple completed job rows.
    """
    # Inner query: one row per document = its latest completed, timed job.
    latest = (
        select(IdpProcessingJob.processing_time_ms.label("ms"))
        .where(
            IdpProcessingJob.processing_time_ms.is_not(None),
            IdpProcessingJob.status == "completed",
        )
    )
    if not is_root:
        latest = (
            latest.join(IdpDocument, IdpProcessingJob.document_id == IdpDocument.id)
            .join(IdpAgent, IdpDocument.agent_id == IdpAgent.id)
            .join(Agent, IdpAgent.agent_id == Agent.id)
            .where(Agent.org_id.in_(org_ids))
        )
    if start_dt is not None:
        latest = latest.where(IdpProcessingJob.created_at >= start_dt)
    if end_dt is not None:
        latest = latest.where(IdpProcessingJob.created_at < end_dt)
    # DISTINCT ON (document_id) + ORDER BY created_at DESC → the latest timed job per document.
    latest = latest.distinct(IdpProcessingJob.document_id).order_by(
        IdpProcessingJob.document_id, IdpProcessingJob.created_at.desc()
    ).subquery()

    avg_ms = (await session.exec(select(func.avg(latest.c.ms)))).one()
    return round(float(avg_ms) / 1000.0, 2) if avg_ms is not None else 0.0


async def avg_confidence_pct(
    session,
    is_root: bool,
    org_ids: list[UUID],
    *,
    start_dt: datetime | None = None,
    end_dt: datetime | None = None,
) -> float:
    """Average overall_confidence (as a 0-100 %) over in-scope docs that have a score."""
    stmt = _apply_doc_scope(
        select(func.avg(IdpDocument.overall_confidence)), is_root, org_ids
    ).where(IdpDocument.overall_confidence.is_not(None))
    if start_dt is not None:
        stmt = stmt.where(IdpDocument.created_at >= start_dt)
    if end_dt is not None:
        stmt = stmt.where(IdpDocument.created_at < end_dt)
    avg_conf = (await session.exec(stmt)).one()
    return round(float(avg_conf) * 100.0, 1) if avg_conf is not None else 0.0


async def count_active_field_configs(session, is_root: bool, org_ids: list[UUID]) -> int:
    """Count the caller's own active, non-deleted field configurations.

    Org-scoped (root sees all). Global catalogue templates (``org_id IS NULL``) are NOT
    counted — this KPI reflects an org's working configs, and a user with no orgs gets 0
    (consistent with every other scoped metric here).
    """
    stmt = select(func.count(IdpFieldConfiguration.id)).where(
        IdpFieldConfiguration.is_active.is_(True),
        IdpFieldConfiguration.deleted_at.is_(None),
    )
    if not is_root:
        stmt = stmt.where(IdpFieldConfiguration.org_id.in_(org_ids))
    return int((await session.exec(stmt)).one() or 0)


async def review_session_timestamps(
    session,
    is_root: bool,
    org_ids: list[UUID],
    *,
    start_dt: datetime,
    end_dt: datetime,
    reviewed_by: UUID | None = None,
) -> list[datetime]:
    """Return review_completed_at timestamps for in-scope review sessions in [start, end)."""
    stmt = (
        select(IdpReviewSession.review_completed_at)
        .where(
            IdpReviewSession.review_completed_at.is_not(None),
            IdpReviewSession.review_completed_at >= start_dt,
            IdpReviewSession.review_completed_at < end_dt,
        )
    )
    if not is_root:
        stmt = (
            stmt.join(IdpDocument, IdpReviewSession.document_id == IdpDocument.id)
            .join(IdpAgent, IdpDocument.agent_id == IdpAgent.id)
            .join(Agent, IdpAgent.agent_id == Agent.id)
            .where(Agent.org_id.in_(org_ids))
        )
    if reviewed_by is not None:
        stmt = stmt.where(IdpReviewSession.reviewed_by == reviewed_by)
    rows = (await session.exec(stmt)).all()
    return [r if isinstance(r, datetime) else r[0] for r in rows]


async def count_review_sessions(
    session,
    is_root: bool,
    org_ids: list[UUID],
    *,
    start_dt: datetime | None = None,
    end_dt: datetime | None = None,
    final_status: str | None = None,
) -> int:
    """Count review sessions in scope, optionally by completion window and final_status."""
    stmt = select(func.count(IdpReviewSession.id))
    if not is_root:
        stmt = (
            stmt.join(IdpDocument, IdpReviewSession.document_id == IdpDocument.id)
            .join(IdpAgent, IdpDocument.agent_id == IdpAgent.id)
            .join(Agent, IdpAgent.agent_id == Agent.id)
            .where(Agent.org_id.in_(org_ids))
        )
    if start_dt is not None:
        stmt = stmt.where(IdpReviewSession.review_completed_at >= start_dt)
    if end_dt is not None:
        stmt = stmt.where(IdpReviewSession.review_completed_at < end_dt)
    if final_status is not None:
        stmt = stmt.where(IdpReviewSession.final_status == final_status)
    return int((await session.exec(stmt)).one() or 0)
