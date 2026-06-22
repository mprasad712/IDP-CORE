"""Read-only query builders for the IDP date-range "Processed Docs" report.

Two shapes:
  * **Summary** (``build_report_query``) — ONE row per document with its timeline
    (uploaded → processed → reviewed), status, latest-job duration, reviewer, and
    header / line-item counts.
  * **Combined all-data** (``build_header_export_query`` / ``build_line_export_query``)
    — every extracted field/cell in the range flattened to one row each.

Both replicate ``processed_docs.list_processed_docs``'s visibility EXACTLY: the
``IdpAgent.extra['has_processed_docs_output'] == 'true'`` gate plus org-scope
(``root`` sees all; everyone else only their orgs' agents). Scope is resolved by the
caller via ``services.idp.scope.resolve_org_scope`` and passed in.

No fan-out: the summary never joins a one-to-many table directly. Latest job and latest
review session come from ``row_number()`` window subqueries (one row per document); the
counts come from grouped ``func.count`` subqueries. So a document with several jobs or
review sessions still yields exactly one report row with correct counts.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import func
from sqlmodel import select

from agentcore.services.database.models.agent.model import Agent
from agentcore.services.database.models.idp.config import IdpAgent, IdpReviewSession
from agentcore.services.database.models.idp.documents import (
    IdpDocument,
    IdpExtractedHeader,
    IdpExtractedLineItem,
    IdpProcessingJob,
)

# Hard ceiling for synchronous exports — protects memory; over it the endpoint returns 422.
MAX_EXPORT_ROWS = 50_000


def _apply_doc_scope_and_filters(stmt, is_root: bool, org_ids: list, filters: dict[str, Any]):
    """Add the org-scope + processed-docs-output gate + user filters to a query over IdpDocument.

    ``stmt`` must already select from / join ``IdpDocument``. Mirrors ``list_processed_docs``.
    """
    stmt = stmt.join(IdpAgent, IdpDocument.agent_id == IdpAgent.id)
    if not is_root:
        stmt = stmt.join(Agent, IdpAgent.agent_id == Agent.id).where(Agent.org_id.in_(org_ids))
    # Only docs from agents whose canvas wired up the Processed Docs Output node (same as the list view).
    stmt = stmt.where(IdpAgent.extra["has_processed_docs_output"].astext == "true")

    if filters.get("agent_id"):
        stmt = stmt.where(IdpDocument.agent_id == filters["agent_id"])
    if filters.get("status"):
        stmt = stmt.where(IdpDocument.status == filters["status"])
    if filters.get("predicted_type"):
        stmt = stmt.where(IdpDocument.predicted_type == filters["predicted_type"])
    if filters.get("created_start"):
        stmt = stmt.where(IdpDocument.created_at >= filters["created_start"])
    if filters.get("created_end"):
        stmt = stmt.where(IdpDocument.created_at <= filters["created_end"])
    return stmt


def _latest_job_subq():
    """One row per document = its most-recent processing job (created_at desc, id desc tiebreak)."""
    rn = func.row_number().over(
        partition_by=IdpProcessingJob.document_id,
        order_by=(IdpProcessingJob.created_at.desc(), IdpProcessingJob.id.desc()),
    ).label("rn")
    inner = select(
        IdpProcessingJob.document_id.label("document_id"),
        IdpProcessingJob.processing_time_ms.label("processing_time_ms"),
        IdpProcessingJob.status.label("job_status"),
        rn,
    ).subquery()
    return select(inner).where(inner.c.rn == 1).subquery()


def _latest_review_subq():
    """One row per document = its most-recent review session (latest created == latest finalize)."""
    rn = func.row_number().over(
        partition_by=IdpReviewSession.document_id,
        order_by=(IdpReviewSession.created_at.desc(), IdpReviewSession.id.desc()),
    ).label("rn")
    inner = select(
        IdpReviewSession.document_id.label("document_id"),
        IdpReviewSession.reviewed_by.label("reviewed_by"),
        IdpReviewSession.review_completed_at.label("review_completed_at"),
        IdpReviewSession.final_status.label("final_status"),
        rn,
    ).subquery()
    return select(inner).where(inner.c.rn == 1).subquery()


# NOTE: header/line counts and the field-export queries read by ``document_id`` (all jobs),
# matching what the Processed Docs detail view (``get_processed_doc``) shows. A reprocessed
# document can thus count rows from more than one job — this is pre-existing, shared with the
# detail view, and kept consistent on purpose; the root-cause fix (cleaning old jobs' rows on
# reprocess) is a deferred pipeline-level change.
def _header_count_subq():
    return (
        select(
            IdpExtractedHeader.document_id.label("document_id"),
            func.count(IdpExtractedHeader.id).label("header_count"),
        )
        .group_by(IdpExtractedHeader.document_id)
        .subquery()
    )


def _line_count_subq():
    # Logical line-item ROWS (distinct row_index), not per-cell rows.
    return (
        select(
            IdpExtractedLineItem.document_id.label("document_id"),
            func.count(func.distinct(IdpExtractedLineItem.row_index)).label("line_item_count"),
        )
        .group_by(IdpExtractedLineItem.document_id)
        .subquery()
    )


def build_report_query(is_root: bool, org_ids: list, filters: dict[str, Any]):
    """Build the summary report SELECT: one row per document + timeline/job/review/counts."""
    lj = _latest_job_subq()
    lr = _latest_review_subq()
    hc = _header_count_subq()
    lc = _line_count_subq()

    stmt = select(
        IdpDocument,
        lj.c.processing_time_ms,
        lj.c.job_status,
        lr.c.reviewed_by,
        lr.c.review_completed_at,
        lr.c.final_status,
        func.coalesce(hc.c.header_count, 0).label("header_count"),
        func.coalesce(lc.c.line_item_count, 0).label("line_item_count"),
    ).select_from(IdpDocument)
    stmt = _apply_doc_scope_and_filters(stmt, is_root, org_ids, filters)
    stmt = stmt.outerjoin(lj, lj.c.document_id == IdpDocument.id)
    stmt = stmt.outerjoin(lr, lr.c.document_id == IdpDocument.id)
    stmt = stmt.outerjoin(hc, hc.c.document_id == IdpDocument.id)
    stmt = stmt.outerjoin(lc, lc.c.document_id == IdpDocument.id)
    return stmt.order_by(IdpDocument.created_at.desc())


def build_header_export_query(is_root: bool, org_ids: list, filters: dict[str, Any]):
    """Flat per-header-field rows for in-scope documents in range (many-to-one join, no fan-out)."""
    stmt = select(
        IdpDocument.id.label("document_id"),
        IdpDocument.original_filename.label("filename"),
        IdpDocument.status.label("doc_status"),
        IdpDocument.predicted_type.label("predicted_type"),
        IdpDocument.overall_confidence.label("overall_confidence"),
        IdpDocument.created_at.label("uploaded_at"),
        IdpDocument.processing_completed_at.label("processed_at"),
        IdpExtractedHeader.field_name.label("name"),
        IdpExtractedHeader.extracted_value.label("extracted_value"),
        IdpExtractedHeader.reviewed_value.label("reviewed_value"),
        IdpExtractedHeader.is_reviewed.label("is_reviewed"),
        IdpExtractedHeader.confidence_score.label("confidence_score"),
    ).select_from(IdpExtractedHeader).join(IdpDocument, IdpExtractedHeader.document_id == IdpDocument.id)
    stmt = _apply_doc_scope_and_filters(stmt, is_root, org_ids, filters)
    return stmt.order_by(IdpDocument.created_at.desc(), IdpDocument.id, IdpExtractedHeader.field_name.asc())


def build_line_export_query(is_root: bool, org_ids: list, filters: dict[str, Any]):
    """Flat per-line-item-cell rows for in-scope documents in range (many-to-one join, no fan-out)."""
    stmt = select(
        IdpDocument.id.label("document_id"),
        IdpDocument.original_filename.label("filename"),
        IdpDocument.status.label("doc_status"),
        IdpDocument.predicted_type.label("predicted_type"),
        IdpDocument.overall_confidence.label("overall_confidence"),
        IdpDocument.created_at.label("uploaded_at"),
        IdpDocument.processing_completed_at.label("processed_at"),
        IdpExtractedLineItem.row_index.label("row_index"),
        IdpExtractedLineItem.column_name.label("name"),
        IdpExtractedLineItem.extracted_value.label("extracted_value"),
        IdpExtractedLineItem.reviewed_value.label("reviewed_value"),
        IdpExtractedLineItem.is_reviewed.label("is_reviewed"),
        IdpExtractedLineItem.confidence_score.label("confidence_score"),
    ).select_from(IdpExtractedLineItem).join(
        IdpDocument, IdpExtractedLineItem.document_id == IdpDocument.id
    )
    stmt = _apply_doc_scope_and_filters(stmt, is_root, org_ids, filters)
    return stmt.order_by(
        IdpDocument.created_at.desc(),
        IdpDocument.id,
        IdpExtractedLineItem.row_index.asc(),
        IdpExtractedLineItem.column_name.asc(),
    )


async def count_query(session, stmt) -> int:
    """Count rows a SELECT would return (order_by stripped so it's valid inside the count subquery)."""
    count_stmt = select(func.count()).select_from(stmt.order_by(None).subquery())
    return int((await session.exec(count_stmt)).one() or 0)
