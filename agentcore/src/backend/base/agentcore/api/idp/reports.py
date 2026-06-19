"""IDP date-range "Processed Docs" report endpoints (read-only).

  * ``GET /reports/processed-docs``            — paginated summary, one row per PO + timeline.
  * ``GET /reports/processed-docs/export``      — the same summary as a CSV/Excel/XML file.
  * ``GET /reports/processed-docs/export-data`` — every PO's extracted fields, flattened, as a file.

All three are GETs (need only ``view_idp``) and org-scoped via the canonical
``resolve_org_scope`` + the report query builders (which replicate ``list_processed_docs``
visibility exactly). Exports are capped at ``MAX_EXPORT_ROWS`` (→ 422) and stream from memory.
"""
import io
import warnings
from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from fastapi_pagination import Page, Params
from fastapi_pagination.ext.sqlmodel import apaginate
from pydantic import BaseModel

from agentcore.api.utils import CurrentActiveUser, DbSession
from agentcore.services.idp.output import SUPPORTED_TABULAR_FORMATS, serialize_table
from agentcore.services.idp.reports import (
    MAX_EXPORT_ROWS,
    build_header_export_query,
    build_line_export_query,
    build_report_query,
    count_query,
)
from agentcore.services.idp.scope import resolve_org_scope

router = APIRouter(prefix="/reports", tags=["IDP Reports"])


# ──────────────────────────────────────────────────────────────────────
# Schema + column orders
# ──────────────────────────────────────────────────────────────────────

class ReportRow(BaseModel):
    document_id: UUID
    original_filename: str
    agent_id: UUID
    predicted_type: str | None = None
    status: str
    overall_confidence: float | None = None  # percentage 0–100 (1 dp)
    uploaded_at: datetime
    processing_started_at: datetime | None = None
    pipeline_completed_at: datetime | None = None
    processing_time_ms: int | None = None
    reviewer: UUID | None = None
    reviewed_at: datetime | None = None
    review_final_status: str | None = None
    header_count: int = 0
    line_item_count: int = 0
    has_log: bool = False


REPORT_COLUMNS = [
    "document_id", "original_filename", "agent_id", "predicted_type", "status",
    "overall_confidence", "uploaded_at", "processing_started_at", "pipeline_completed_at",
    "processing_time_ms", "reviewer", "reviewed_at", "review_final_status",
    "header_count", "line_item_count", "has_log",
]

_COMBINED_BASE = ["document_id", "filename", "doc_status", "predicted_type", "record_type", "row_index", "field"]
COMBINED_COLUMNS_BOTH = _COMBINED_BASE + ["predicted_value", "audited_value", "is_reviewed", "confidence"]
COMBINED_COLUMNS_FINAL = _COMBINED_BASE + ["value", "confidence"]


# ──────────────────────────────────────────────────────────────────────
# Row mappers
# ──────────────────────────────────────────────────────────────────────

def _pct(c) -> float | None:
    return None if c is None else round(float(c) * 100.0, 1)


def _iso(dt) -> str | None:
    return dt.isoformat() if dt is not None else None


def _to_report_row(row) -> ReportRow:
    """Map a summary-query Row (IdpDocument + window/count columns) → ReportRow."""
    doc = row[0]
    return ReportRow(
        document_id=doc.id,
        original_filename=doc.original_filename,
        agent_id=doc.agent_id,
        predicted_type=doc.predicted_type,
        status=doc.status,
        overall_confidence=_pct(doc.overall_confidence),
        uploaded_at=doc.created_at,
        processing_started_at=doc.processing_started_at,
        pipeline_completed_at=doc.processing_completed_at,
        processing_time_ms=row.processing_time_ms,
        reviewer=row.reviewed_by,
        reviewed_at=row.review_completed_at,
        review_final_status=row.final_status,
        header_count=row.header_count,
        line_item_count=row.line_item_count,
        has_log=row.job_status is not None,
    )


def _report_export_dict(row) -> dict:
    rr = _to_report_row(row)
    return {
        "document_id": str(rr.document_id),
        "original_filename": rr.original_filename,
        "agent_id": str(rr.agent_id),
        "predicted_type": rr.predicted_type,
        "status": rr.status,
        "overall_confidence": rr.overall_confidence,
        "uploaded_at": _iso(rr.uploaded_at),
        "processing_started_at": _iso(rr.processing_started_at),
        "pipeline_completed_at": _iso(rr.pipeline_completed_at),
        "processing_time_ms": rr.processing_time_ms,
        "reviewer": str(rr.reviewer) if rr.reviewer else None,
        "reviewed_at": _iso(rr.reviewed_at),
        "review_final_status": rr.review_final_status,
        "header_count": rr.header_count,
        "line_item_count": rr.line_item_count,
        "has_log": rr.has_log,
    }


def _value_cols(extracted, reviewed, is_reviewed, conf, values: str) -> dict:
    if values == "final":
        return {"value": reviewed if is_reviewed else extracted, "confidence": _pct(conf)}
    return {
        "predicted_value": extracted,
        "audited_value": reviewed if is_reviewed else None,
        "is_reviewed": bool(is_reviewed),
        "confidence": _pct(conf),
    }


def _combined_rows(hrows, lrows, values: str) -> list[dict]:
    out: list[dict] = []
    for h in hrows:
        out.append({
            "document_id": str(h.document_id), "filename": h.filename, "doc_status": h.doc_status,
            "predicted_type": h.predicted_type, "record_type": "header", "row_index": "", "field": h.name,
            **_value_cols(h.extracted_value, h.reviewed_value, h.is_reviewed, h.confidence_score, values),
        })
    for l in lrows:
        out.append({
            "document_id": str(l.document_id), "filename": l.filename, "doc_status": l.doc_status,
            "predicted_type": l.predicted_type, "record_type": "line_item", "row_index": l.row_index, "field": l.name,
            **_value_cols(l.extracted_value, l.reviewed_value, l.is_reviewed, l.confidence_score, values),
        })
    return out


def _filters(agent_id, status_filter, predicted_type, created_start, created_end) -> dict:
    return {
        "agent_id": agent_id,
        "status": status_filter,
        "predicted_type": predicted_type,
        "created_start": created_start,
        "created_end": created_end,
    }


def _attachment(data: bytes, media: str, filename: str) -> StreamingResponse:
    return StreamingResponse(
        io.BytesIO(data),
        media_type=media,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ──────────────────────────────────────────────────────────────────────
# Endpoints
# ──────────────────────────────────────────────────────────────────────

@router.get("/processed-docs", response_model=Page[ReportRow])
async def report_processed_docs(
    *,
    session: DbSession,
    current_user: CurrentActiveUser,
    params: Params = Depends(),
    agent_id: UUID | None = None,
    status_filter: str | None = None,
    predicted_type: str | None = None,
    created_start: datetime | None = None,
    created_end: datetime | None = None,
):
    """Paginated date-range report: one row per processed document with its full timeline."""
    is_root, org_ids = await resolve_org_scope(session, current_user)
    if not is_root and not org_ids:
        return Page.create([], params=params, total=0)
    stmt = build_report_query(is_root, org_ids, _filters(agent_id, status_filter, predicted_type, created_start, created_end))
    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", category=DeprecationWarning, module=r"fastapi_pagination\.ext\.sqlalchemy")
        return await apaginate(
            session, stmt, params=params, transformer=lambda rows: [_to_report_row(r) for r in rows]
        )


@router.get("/processed-docs/export")
async def export_processed_docs_report(
    *,
    session: DbSession,
    current_user: CurrentActiveUser,
    format: str = "csv",
    agent_id: UUID | None = None,
    status_filter: str | None = None,
    predicted_type: str | None = None,
    created_start: datetime | None = None,
    created_end: datetime | None = None,
):
    """Download the summary report (one row per PO) as CSV / Excel / XML."""
    fmt = (format or "").lower()
    if fmt not in SUPPORTED_TABULAR_FORMATS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported format '{format}'. Supported: {', '.join(sorted(SUPPORTED_TABULAR_FORMATS))}",
        )
    is_root, org_ids = await resolve_org_scope(session, current_user)
    rows: list[dict] = []
    if is_root or org_ids:
        stmt = build_report_query(is_root, org_ids, _filters(agent_id, status_filter, predicted_type, created_start, created_end))
        total = await count_query(session, stmt)
        if total > MAX_EXPORT_ROWS:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Report has {total} rows, over the {MAX_EXPORT_ROWS} export limit. Narrow the date range.",
            )
        result = (await session.exec(stmt.limit(MAX_EXPORT_ROWS))).all()
        rows = [_report_export_dict(r) for r in result]
    data, media, ext = serialize_table(
        REPORT_COLUMNS, rows, fmt, sheet_name="Processed Docs Report", root_tag="report", row_tag="document"
    )
    return _attachment(data, media, f"processed_docs_report.{ext}")


@router.get("/processed-docs/export-data")
async def export_processed_docs_data(
    *,
    session: DbSession,
    current_user: CurrentActiveUser,
    format: str = "csv",
    values: str = "both",
    agent_id: UUID | None = None,
    status_filter: str | None = None,
    predicted_type: str | None = None,
    created_start: datetime | None = None,
    created_end: datetime | None = None,
):
    """Download every in-range PO's extracted fields, flattened (one row per field/cell)."""
    fmt = (format or "").lower()
    if fmt not in SUPPORTED_TABULAR_FORMATS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported format '{format}'. Supported: {', '.join(sorted(SUPPORTED_TABULAR_FORMATS))}",
        )
    if values not in ("both", "final"):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="values must be 'both' or 'final'.")

    is_root, org_ids = await resolve_org_scope(session, current_user)
    rows: list[dict] = []
    if is_root or org_ids:
        filters = _filters(agent_id, status_filter, predicted_type, created_start, created_end)
        hstmt = build_header_export_query(is_root, org_ids, filters)
        lstmt = build_line_export_query(is_root, org_ids, filters)
        total = await count_query(session, hstmt) + await count_query(session, lstmt)
        if total > MAX_EXPORT_ROWS:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Export has {total} field rows, over the {MAX_EXPORT_ROWS} limit. Narrow the date range.",
            )
        hrows = (await session.exec(hstmt.limit(MAX_EXPORT_ROWS))).all()
        lrows = (await session.exec(lstmt.limit(MAX_EXPORT_ROWS))).all()
        rows = _combined_rows(hrows, lrows, values)
    columns = COMBINED_COLUMNS_FINAL if values == "final" else COMBINED_COLUMNS_BOTH
    data, media, ext = serialize_table(
        columns, rows, fmt, sheet_name="All Data", root_tag="export", row_tag="field"
    )
    return _attachment(data, media, f"processed_docs_data.{ext}")
