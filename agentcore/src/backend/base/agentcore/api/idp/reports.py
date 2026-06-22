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
from sqlmodel import select

from agentcore.api.utils import CurrentActiveUser, DbSession
from agentcore.services.database.models.idp.config import IdpReviewSession
from agentcore.services.database.models.user.model import User
from agentcore.services.idp.output import (
    SUPPORTED_TABULAR_FORMATS,
    serialize_matrix,
    serialize_table,
)
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

# "Download all data" = sectioned layout: one block per file (file-info → header-fields table
# beside a line-items table), blocks stacked down a single sheet. Each block carries only that
# file's own fields, so mixed document types render correctly (no global union heading).
_GAP = 1  # blank spacer column(s) between the header table (left) and line-items table (right)


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


def _group_docs(hrows, lrows):
    """Group export rows by document → ``OrderedDict[doc_id] = {meta, headers, lines}``.

    ``headers`` is ``{field_name: rec}``; ``lines`` is ``{row_index: {column_name: rec}}``.
    Each file keeps only its OWN fields (no cross-file union), so mixed doc types stay clean.
    """
    from collections import OrderedDict

    docs: "OrderedDict[str, dict]" = OrderedDict()

    def _doc(r) -> dict:
        did = str(r.document_id)
        d = docs.get(did)
        if d is None:
            conf = _pct(getattr(r, "overall_confidence", None))
            d = {
                "meta": {
                    "file name": r.filename,
                    "type": r.predicted_type or "—",
                    "status": r.doc_status,
                    "confidence": f"{conf}%" if conf is not None else "—",
                    "uploaded": _iso(getattr(r, "uploaded_at", None)) or "—",
                    "processed": _iso(getattr(r, "processed_at", None)) or "—",
                },
                "headers": {},
                "lines": {},
            }
            docs[did] = d
        return d

    for r in hrows:
        _doc(r)["headers"][r.name] = r
    for r in lrows:
        _doc(r)["lines"].setdefault(r.row_index, {})[r.name] = r
    return docs


async def _latest_reviews(session, doc_ids: list) -> dict:
    """Map ``str(doc_id) -> {reviewer, reviewed_at, final_status}`` from the latest review session."""
    if not doc_ids:
        return {}
    rows = (
        await session.exec(
            select(
                IdpReviewSession.document_id,
                IdpReviewSession.review_completed_at,
                IdpReviewSession.final_status,
                User.username,
            )
            .join(User, IdpReviewSession.reviewed_by == User.id, isouter=True)
            .where(IdpReviewSession.document_id.in_(doc_ids))
            .order_by(IdpReviewSession.document_id, IdpReviewSession.created_at.desc())
        )
    ).all()
    out: dict = {}
    for did, completed_at, final_status, username in rows:
        key = str(did)
        if key not in out:  # first per doc = latest (created_at desc)
            out[key] = {
                "reviewer": username or "—",
                "reviewed_at": _iso(completed_at) or "—",
                "final_status": final_status or "—",
            }
    return out


def _hdr_subheader(values: str) -> list[str]:
    return ["Field", "Value"] if values == "final" else ["Field", "Predicted", "Audited", "Reviewed", "Confidence"]


def _hdr_row(field_name: str, rec, values: str) -> list:
    if values == "final":
        val = "" if rec is None else (rec.reviewed_value if rec.is_reviewed else rec.extracted_value)
        return [field_name, val]
    if rec is None:
        return [field_name, "", "", "", ""]
    return [
        field_name,
        rec.extracted_value,
        rec.reviewed_value if rec.is_reviewed else "",
        "yes" if rec.is_reviewed else "no",
        _pct(rec.confidence_score),
    ]


def _line_subheader(line_cols: list[str], values: str) -> list[str]:
    if values == "final":
        return ["Row", *line_cols]
    cols = ["Row"]
    for c in line_cols:
        cols += [c, f"{c} (audited)", f"{c} (conf)"]
    return cols


def _line_row(row_index, cells: dict, line_cols: list[str], values: str) -> list:
    out: list = [row_index]
    for c in line_cols:
        rec = cells.get(c)
        if values == "final":
            out.append("" if rec is None else (rec.reviewed_value if rec.is_reviewed else rec.extracted_value))
        elif rec is None:
            out += ["", "", ""]
        else:
            out += [rec.extracted_value, rec.reviewed_value if rec.is_reviewed else "", _pct(rec.confidence_score)]
    return out


def _section_matrix(docs, review_map: dict, values: str) -> list[list]:
    """Build the full sheet as a positional matrix: one stacked section per file."""
    matrix: list[list] = []
    hdr_sub = _hdr_subheader(values)
    left_w = len(hdr_sub)

    for did, d in docs.items():
        m = d["meta"]
        rv = review_map.get(did)
        # File-info block
        matrix.append([f"FILE  {m['file name']}"])
        matrix.append(["Type", m["type"], "", "Status", m["status"]])
        matrix.append(["Confidence", m["confidence"], "", "Uploaded", m["uploaded"]])
        matrix.append(["Processed", m["processed"], "", "Reviewed by", rv["reviewer"] if rv else "—"])
        matrix.append(["Review", rv["final_status"] if rv else "—", "", "Reviewed at", rv["reviewed_at"] if rv else "—"])
        matrix.append([])  # blank

        # Per-file fields (this file's own columns only)
        header_fields = sorted(d["headers"].keys())
        line_cols = sorted({c for cells in d["lines"].values() for c in cells}) if d["lines"] else []

        # Section labels + sub-headers, header table beside line-items table
        label = [""] * (left_w + _GAP)
        label[0] = "HEADER FIELDS"
        label.append("LINE ITEMS")
        matrix.append(label)
        matrix.append([*hdr_sub, *([""] * _GAP), *_line_subheader(line_cols, values)])

        hdr_rows = [_hdr_row(f, d["headers"][f], values) for f in header_fields]
        li_rows = [_line_row(ri, d["lines"][ri], line_cols, values) for ri in sorted(d["lines"])]
        for i in range(max(len(hdr_rows), len(li_rows))):
            left = hdr_rows[i] if i < len(hdr_rows) else [""] * left_w
            right = li_rows[i] if i < len(li_rows) else []
            matrix.append([*left, *([""] * _GAP), *right])

        matrix.append([])  # separator before the next file's section
    return matrix


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
    """Download every in-range PO's extracted data in the per-file SECTIONED layout.

    One single sheet with a self-contained block per file: a file-info header (type · status ·
    confidence · uploaded · processed · reviewed by/at) above a HEADER FIELDS table beside a
    LINE ITEMS table, files stacked. Each file carries only its own fields, so mixed document
    types render cleanly. ``values='final'`` → one value per field/cell; ``values='both'`` →
    Predicted · Audited · Reviewed · Confidence on every header field AND every line-item cell.
    """
    fmt = (format or "").lower()
    if fmt not in SUPPORTED_TABULAR_FORMATS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported format '{format}'. Supported: {', '.join(sorted(SUPPORTED_TABULAR_FORMATS))}",
        )
    if values not in ("both", "final"):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="values must be 'both' or 'final'.")

    is_root, org_ids = await resolve_org_scope(session, current_user)
    matrix: list[list] = []
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
        docs = _group_docs(hrows, lrows)
        doc_ids = list({r.document_id for r in hrows} | {r.document_id for r in lrows})
        review_map = await _latest_reviews(session, doc_ids)
        matrix = _section_matrix(docs, review_map, values)
    data, media, ext = serialize_matrix(matrix, fmt, sheet_name="All Data")
    return _attachment(data, media, f"processed_docs_data.{ext}")
