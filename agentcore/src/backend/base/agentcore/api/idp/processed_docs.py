import io
import mimetypes
import os
import re
from datetime import datetime, timezone
from loguru import logger
from typing import Annotated
from uuid import UUID, uuid4
from fastapi import APIRouter, Depends, HTTPException, status, Response
from fastapi.responses import StreamingResponse
from fastapi_pagination import Params, Page
from fastapi_pagination.ext.sqlmodel import apaginate
from sqlmodel import select, or_, and_
from sqlalchemy.exc import IntegrityError
from pydantic import BaseModel, Field as PydanticField

from agentcore.api.utils import CurrentActiveUser, DbSession
from agentcore.services.database.models.idp.documents import (
    IdpDocument,
    IdpExtractedHeader,
    IdpExtractedLineItem,
    IdpProcessingJob,
)
from agentcore.services.database.models.idp.config import IdpAgent, IdpReviewSession
from agentcore.services.database.models.agent.model import Agent
from agentcore.services.database.models.user_organization_membership.model import UserOrganizationMembership
from agentcore.services.auth.permissions import normalize_role
from agentcore.services.deps import get_storage_service
from agentcore.services.idp.output import (
    SUPPORTED_FORMATS,
    build_doc_payload,
    serialize_document,
)

router = APIRouter(prefix="/processed-docs", tags=["IDP Processed Documents"])

# ──────────────────────────────────────────────────────────────────────
# Schemas
# ──────────────────────────────────────────────────────────────────────

class ProcessedDocRead(BaseModel):
    id: UUID
    agent_id: UUID
    agent_name: str | None = None
    parent_document_id: UUID | None
    original_filename: str
    file_path: str
    file_type: str
    mime_type: str | None
    file_size_bytes: int
    page_count: int | None
    checksum: str | None
    source: str
    predicted_type: str | None
    status: str
    overall_confidence: float | None
    uploaded_by: UUID | None
    processing_started_at: datetime | None
    processing_completed_at: datetime | None
    failed_at: datetime | None
    created_at: datetime
    updated_at: datetime
    review_draft: bool = False  # reviewer saved edits as a draft (not yet submitted)

    class Config:
        from_attributes = True

class ExtractedHeaderRead(BaseModel):
    id: UUID
    field_name: str
    extracted_value: str | None
    confidence_score: float | None
    reasoning_trace: str | None
    source_location: dict | None
    reviewed_value: str | None
    is_reviewed: bool

    class Config:
        from_attributes = True

class ExtractedLineItemRead(BaseModel):
    id: UUID
    row_index: int
    column_name: str
    extracted_value: str | None
    confidence_score: float | None
    reasoning_trace: str | None
    source_location: dict | None
    reviewed_value: str | None
    is_reviewed: bool

    class Config:
        from_attributes = True

class ProcessedDocDetailRead(ProcessedDocRead):
    headers: list[ExtractedHeaderRead] = []
    line_items: list[ExtractedLineItemRead] = []
    error_message: str | None = None

# Key used inside IdpDocument.extra (JSONB) to mark a saved-but-not-submitted review draft.
REVIEW_DRAFT_KEY = "review_draft"


def _clear_review_draft(doc: IdpDocument) -> None:
    """Remove the review-draft marker from ``doc.extra`` when finalizing the document.

    ``extra`` is a plain JSONB column (not MutableDict), so we reassign a fresh dict for
    SQLAlchemy change detection. Used by both POST /review and POST /approve.
    """
    if doc.extra and REVIEW_DRAFT_KEY in doc.extra:
        new_extra = dict(doc.extra)
        new_extra.pop(REVIEW_DRAFT_KEY, None)
        doc.extra = new_extra


class FieldUpdateItem(BaseModel):
    id: UUID
    value: str | None

class HumanFieldsUpdateRequest(BaseModel):
    headers: list[FieldUpdateItem] | None = None
    line_items: list[FieldUpdateItem] | None = None
    draft: bool = False  # when True, mark the doc as a saved draft (edits persisted, not finalized)
    expected_updated_at: datetime | None = None  # M4: optional optimistic-lock precondition

class DocumentReviewRequest(BaseModel):
    notes: str | None = None
    expected_updated_at: datetime | None = None  # M4: optional optimistic-lock precondition

class LineItemRowCreate(BaseModel):
    columns: dict[str, str | None]  # column_name -> value for the new row

# ──────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────

async def _get_scope_memberships(session: DbSession, user_id: UUID) -> set[UUID]:
    rows = (
        await session.exec(
            select(UserOrganizationMembership.org_id).where(
                UserOrganizationMembership.user_id == user_id,
                UserOrganizationMembership.status.in_(["accepted", "active"]),
            )
        )
    ).all()
    return {r if isinstance(r, UUID) else r[0] for r in rows}


async def _can_access_document(session: DbSession, current_user: CurrentActiveUser, doc: IdpDocument) -> bool:
    role = normalize_role(getattr(current_user, "role", None))
    if role == "root":
        return True
    
    org_ids = await _get_scope_memberships(session, current_user.id)
    
    # Resolve the document's organization through the Agent mapping
    stmt = (
        select(Agent.org_id)
        .join(IdpAgent, IdpAgent.agent_id == Agent.id)
        .where(IdpAgent.id == doc.agent_id)
    )
    doc_org_id = (await session.exec(stmt)).first()
    
    if doc_org_id and doc_org_id in org_ids:
        return True
    return False


def _check_doc_version(doc, expected) -> None:
    """M4: optional optimistic lock. If the caller passes ``expected_updated_at`` and it no longer
    matches the doc's current ``updated_at``, another writer changed it first → 409 (refresh & retry).
    Callers that omit the field keep the legacy last-writer-wins behavior (backward compatible)."""
    if expected is not None and doc.updated_at != expected:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Document was modified by another user. Refresh and retry.",
        )


async def _latest_job_id(session, document_id):
    """M1: the most recent job id for a document (created_at desc, id tiebreak), or None.

    Detail/review read extracted rows filtered by this so a reprocessed document shows only the
    latest job's rows (no double-up). Mirrors the reports.py ``_latest_job_ids_subq`` ordering."""
    return (await session.exec(
        select(IdpProcessingJob.id)
        .where(IdpProcessingJob.document_id == document_id)
        .order_by(IdpProcessingJob.created_at.desc(), IdpProcessingJob.id.desc())
        .limit(1)
    )).first()

# ──────────────────────────────────────────────────────────────────────
# Endpoints
# ──────────────────────────────────────────────────────────────────────

@router.get("/", response_model=Page[ProcessedDocRead])
async def list_processed_docs(
    *,
    session: DbSession,
    current_user: CurrentActiveUser,
    params: Params = Depends(),
    status_filter: str | None = None,
    confidence_min: float | None = None,
    confidence_max: float | None = None,
    agent_id: UUID | None = None,
    predicted_type: str | None = None,
    created_start: datetime | None = None,
    created_end: datetime | None = None,
):
    """List and filter processed documents based on active user scopes."""
    role = normalize_role(getattr(current_user, "role", None))
    org_ids = await _get_scope_memberships(session, current_user.id)

    # Base query joining IdpDocument -> IdpAgent -> Agent to enforce scoping.
    # Only include docs from agents whose canvas has the "Processed Docs Output" node wired up
    # (stored as extra->>'has_processed_docs_output' = 'true' on the IdpAgent row).
    stmt = select(IdpDocument)
    if role != "root":
        stmt = stmt.join(IdpAgent, IdpDocument.agent_id == IdpAgent.id)
        stmt = stmt.join(Agent, IdpAgent.agent_id == Agent.id)
        stmt = stmt.where(Agent.org_id.in_(list(org_ids)))
        stmt = stmt.where(IdpAgent.extra["has_processed_docs_output"].astext == "true")
    else:
        # root sees all — but still scope to agents with the output node wired up
        stmt = stmt.join(IdpAgent, IdpDocument.agent_id == IdpAgent.id)
        stmt = stmt.where(IdpAgent.extra["has_processed_docs_output"].astext == "true")

    # Hide soft-deleted documents (deleted_at set) from all list results.
    stmt = stmt.where(IdpDocument.deleted_at.is_(None))

    # Apply query filters
    if status_filter:
        stmt = stmt.where(IdpDocument.status == status_filter)
    if confidence_min is not None:
        stmt = stmt.where(IdpDocument.overall_confidence >= confidence_min)
    if confidence_max is not None:
        stmt = stmt.where(IdpDocument.overall_confidence <= confidence_max)
    if agent_id:
        stmt = stmt.where(IdpDocument.agent_id == agent_id)
    if predicted_type:
        stmt = stmt.where(IdpDocument.predicted_type == predicted_type)
    if created_start:
        stmt = stmt.where(IdpDocument.created_at >= created_start)
    if created_end:
        stmt = stmt.where(IdpDocument.created_at <= created_end)

    stmt = stmt.order_by(IdpDocument.created_at.desc())

    import warnings
    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", category=DeprecationWarning, module=r"fastapi_pagination\.ext\.sqlalchemy")
        result = await apaginate(session, stmt, params=params)

    if result.items:
        idp_ids = [item.agent_id for item in result.items]
        name_rows = (await session.exec(
            select(IdpAgent.id, Agent.name)
            .join(Agent, IdpAgent.agent_id == Agent.id)
            .where(IdpAgent.id.in_(idp_ids))
        )).all()
        name_map = {str(r[0]): r[1] for r in name_rows}
        result = result.model_copy(update={
            "items": [
                item.model_copy(update={"agent_name": name_map.get(str(item.agent_id))})
                for item in result.items
            ]
        })

    return result


@router.get("/{id}", response_model=ProcessedDocDetailRead)
async def get_processed_doc(
    *,
    session: DbSession,
    id: UUID,
    current_user: CurrentActiveUser,
):
    """Retrieve full details of an extracted document including header and line items."""
    doc = await session.get(IdpDocument, id)
    if not doc or doc.deleted_at is not None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")

    if not await _can_access_document(session, current_user, doc):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")

    # Load the most recent job first (M1: its error message AND its id scope the extracted rows
    # so a reprocessed document shows only the latest job's headers/line-items, not every job's).
    last_job = (
        await session.exec(
            select(IdpProcessingJob)
            .where(IdpProcessingJob.document_id == id)
            .order_by(IdpProcessingJob.created_at.desc(), IdpProcessingJob.id.desc())
            .limit(1)
        )
    ).first()
    latest_job_id = last_job.id if last_job else None

    # Load headers and line items for the latest job only
    stmt_headers = select(IdpExtractedHeader).where(
        IdpExtractedHeader.document_id == id,
        IdpExtractedHeader.job_id == latest_job_id,
    )
    headers = (await session.exec(stmt_headers)).all()

    stmt_lines = select(IdpExtractedLineItem).where(
        IdpExtractedLineItem.document_id == id,
        IdpExtractedLineItem.job_id == latest_job_id,
    ).order_by(
        IdpExtractedLineItem.row_index.asc(),
        IdpExtractedLineItem.column_name.asc()
    )
    line_items = (await session.exec(stmt_lines)).all()

    detail = ProcessedDocDetailRead.model_validate(doc)
    detail.headers = headers
    detail.line_items = line_items
    detail.error_message = last_job.error_message if last_job else None
    return detail


def _storage_parts(file_path: str) -> tuple[str, str]:
    """Split '{scope}/{name}' → (scope, name). Uses the scope from file_path, not doc.agent_id,
    because the file was saved under this exact scope at upload/split time."""
    if "/" in file_path:
        scope, name = file_path.split("/", 1)
        return scope, name
    return "", file_path


@router.get("/{id}/file")
async def get_processed_doc_file(
    *,
    session: DbSession,
    id: UUID,
    current_user: CurrentActiveUser,
):
    """Stream the raw document file (PDF, image, etc.) for inline preview.

    Falls back to the parent document's file when a split child document's own
    page-range PDF is not found in storage (child page files may not be persisted
    on all deployment configurations).
    """
    doc = await session.get(IdpDocument, id)
    if not doc or doc.deleted_at is not None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")

    if not await _can_access_document(session, current_user, doc):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")

    storage = get_storage_service()

    async def _fetch(d: IdpDocument) -> bytes | None:
        scope, fname = _storage_parts(d.file_path)
        # Use scope from file_path (set at upload time) — more reliable than str(d.agent_id)
        # which could drift if the agent config was ever remapped.
        agent_scope = scope or str(d.agent_id)
        try:
            return await storage.get_file(agent_scope, fname)
        except Exception as exc:
            logger.warning(
                f"[processed-docs/file] doc={d.id} scope={agent_scope!r} file={fname!r} → {exc}"
            )
            return None

    file_bytes = await _fetch(doc)
    serving_doc = doc

    # If the document's own file is missing and it's a split child, try the parent.
    if file_bytes is None and doc.parent_document_id is not None:
        parent = await session.get(IdpDocument, doc.parent_document_id)
        if parent:
            file_bytes = await _fetch(parent)
            if file_bytes is not None:
                serving_doc = parent

    if file_bytes is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File not found in storage")

    mime_type = (
        serving_doc.mime_type
        or mimetypes.guess_type(serving_doc.original_filename)[0]
        or "application/octet-stream"
    )
    return StreamingResponse(
        io.BytesIO(file_bytes),
        media_type=mime_type,
        headers={
            "Content-Disposition": f'inline; filename="{serving_doc.original_filename}"',
            "Cache-Control": "no-store",
        },
    )


@router.get("/{id}/export")
async def export_processed_doc(
    *,
    session: DbSession,
    id: UUID,
    current_user: CurrentActiveUser,
    format: str = "csv",
    values: str = "both",
):
    """Download one processed document's extracted data in the chosen format.

    Mirrors the review/detail view (WYSIWYG): the very same header + line-item rows shown on
    the Processed Docs screen, serialized to CSV / Excel / XML / JSON / TXT. ``values=both``
    (default) keeps the full audit trail — predicted + audited + reviewed-flag columns;
    ``values=final`` collapses to a single value per field (audited if reviewed, else predicted).
    Read-only; requires only ``view_idp`` (this router's read permission).
    """
    fmt = (format or "").lower()
    if fmt not in SUPPORTED_FORMATS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported format '{format}'. Supported: {', '.join(sorted(SUPPORTED_FORMATS))}",
        )
    if values not in ("both", "final"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="values must be 'both' or 'final'.",
        )

    doc = await session.get(IdpDocument, id)
    if not doc or doc.deleted_at is not None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")
    if not await _can_access_document(session, current_user, doc):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")

    # Same row source as get_processed_doc (the detail view): both read ONLY the latest job's rows
    # (M1) so the exported single-doc file matches exactly what's on screen, and a reprocessed doc
    # doesn't carry stale rows from older jobs.
    latest_job_id = await _latest_job_id(session, id)
    headers = (
        await session.exec(
            select(IdpExtractedHeader)
            .where(IdpExtractedHeader.document_id == id, IdpExtractedHeader.job_id == latest_job_id)
            .order_by(IdpExtractedHeader.field_name.asc())
        )
    ).all()
    line_items = (
        await session.exec(
            select(IdpExtractedLineItem)
            .where(IdpExtractedLineItem.document_id == id, IdpExtractedLineItem.job_id == latest_job_id)
            .order_by(IdpExtractedLineItem.row_index.asc(), IdpExtractedLineItem.column_name.asc())
        )
    ).all()
    # Latest review session (created_at desc == latest finalize). Usually 0 or 1 per doc.
    review_session = (
        await session.exec(
            select(IdpReviewSession)
            .where(IdpReviewSession.document_id == id)
            .order_by(IdpReviewSession.created_at.desc(), IdpReviewSession.id.desc())
            .limit(1)
        )
    ).first()

    payload = build_doc_payload(doc, headers, line_items, review_session, values=values)
    data, media_type, ext = serialize_document(payload, fmt)

    base = os.path.splitext(os.path.basename(doc.original_filename or "document"))[0]
    safe_name = re.sub(r'[\r\n";]', "_", f"{base}_export.{ext}")
    return StreamingResponse(
        io.BytesIO(data),
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{safe_name}"'},
    )


@router.patch("/{id}/fields", response_model=ProcessedDocDetailRead)
async def update_extracted_fields(
    *,
    session: DbSession,
    id: UUID,
    payload: HumanFieldsUpdateRequest,
    current_user: CurrentActiveUser,
):
    """Apply human modifications to extracted headers or line items (HITL edits)."""
    doc = await session.get(IdpDocument, id)
    if not doc or doc.deleted_at is not None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")

    if not await _can_access_document(session, current_user, doc):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not authorized to edit this document.")
    _check_doc_version(doc, payload.expected_updated_at)  # M4

    # Update headers
    if payload.headers:
        for item in payload.headers:
            header = await session.get(IdpExtractedHeader, item.id)
            if not header or header.document_id != id:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Extracted header ID {item.id} is invalid or doesn't belong to this document."
                )
            header.reviewed_value = item.value
            header.is_reviewed = True
            session.add(header)

    # Update line items
    if payload.line_items:
        for item in payload.line_items:
            line = await session.get(IdpExtractedLineItem, item.id)
            if not line or line.document_id != id:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Extracted line item ID {item.id} is invalid or doesn't belong to this document."
                )
            line.reviewed_value = item.value
            line.is_reviewed = True
            session.add(line)

    # "Save as Draft": persist a marker so the UI can flag the doc as a saved draft.
    # JSONB is a plain column (no MutableDict) → reassign the whole dict for change detection.
    # Only mark a doc that is genuinely in review — a finalized doc (reviewed/auto_approved)
    # must not acquire a draft marker that no submit/approve path would later clear.
    # A non-draft PATCH (draft=False, the default) leaves the marker untouched → prior behavior.
    if payload.draft and doc.status == "pending_review":
        doc.extra = {**(doc.extra or {}), REVIEW_DRAFT_KEY: True}

    doc.updated_at = datetime.now(timezone.utc)
    session.add(doc)
    await session.commit()

    return await get_processed_doc(session=session, id=id, current_user=current_user)


@router.post("/{id}/line-items", status_code=status.HTTP_201_CREATED)
async def add_line_item_row(
    *, session: DbSession, id: UUID, payload: LineItemRowCreate, current_user: CurrentActiveUser,
):
    """Add a new line-item ROW (one cell per column) to the latest job of a document."""
    doc = await session.get(IdpDocument, id)
    if not doc or doc.deleted_at is not None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")
    if not await _can_access_document(session, current_user, doc):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not authorized to edit this document.")
    job_id = await _latest_job_id(session, id)
    if not job_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No processing job for this document.")
    if not payload.columns:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No columns provided for the new row.")

    async def _attempt() -> int:
        """Compute max(row_index)+1 and insert the row's cells + bump the doc. One call = one txn."""
        max_row = (await session.exec(
            select(IdpExtractedLineItem.row_index)
            .where(IdpExtractedLineItem.document_id == id, IdpExtractedLineItem.job_id == job_id)
            .order_by(IdpExtractedLineItem.row_index.desc()).limit(1)
        )).first()
        new_row = (max_row + 1) if max_row is not None else 0
        for col_name, val in payload.columns.items():
            session.add(IdpExtractedLineItem(
                id=uuid4(), document_id=id, job_id=job_id, row_index=new_row,
                column_name=col_name, extracted_value=(str(val) if val is not None else None),
                reviewed_value=(str(val) if val is not None else None), confidence_score=None, is_reviewed=True,
            ))
        doc.updated_at = datetime.now(timezone.utc); session.add(doc)
        await session.commit()
        return new_row

    # Two concurrent adds can both read the same max(row_index) and collide on the
    # UniqueConstraint(job_id, row_index, column_name), raising IntegrityError. Roll back,
    # recompute the next index once, and retry; if it still collides, return a clean 409.
    try:
        new_row = await _attempt()
    except IntegrityError:
        await session.rollback()
        try:
            new_row = await _attempt()
        except IntegrityError:
            await session.rollback()
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Could not add row due to a concurrent edit. Please retry.",
            )
    return {"row_index": new_row}


@router.delete("/{id}/line-items/{row_index}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_line_item_row(
    *, session: DbSession, id: UUID, row_index: int, current_user: CurrentActiveUser,
):
    """Delete an entire line-item ROW (all its cells) from the latest job of a document."""
    doc = await session.get(IdpDocument, id)
    if not doc or doc.deleted_at is not None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")
    if not await _can_access_document(session, current_user, doc):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not authorized to edit this document.")
    job_id = await _latest_job_id(session, id)
    rows = (await session.exec(
        select(IdpExtractedLineItem).where(
            IdpExtractedLineItem.document_id == id,
            IdpExtractedLineItem.job_id == job_id,
            IdpExtractedLineItem.row_index == row_index,
        )
    )).all()
    for r in rows:
        await session.delete(r)
    doc.updated_at = datetime.now(timezone.utc); session.add(doc)
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{id}/review", status_code=status.HTTP_200_OK)
async def review_processed_doc(
    *,
    session: DbSession,
    id: UUID,
    payload: DocumentReviewRequest,
    current_user: CurrentActiveUser,
):
    """Complete document review, updating its status to 'reviewed' and logging the HITL session."""
    doc = await session.get(IdpDocument, id)
    if not doc or doc.deleted_at is not None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")

    if not await _can_access_document(session, current_user, doc):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not authorized to review this document.")
    _check_doc_version(doc, payload.expected_updated_at)  # M4

    # Determine if any human corrections were made (M1: latest job only, consistent with the detail view)
    latest_job_id = await _latest_job_id(session, id)
    stmt_headers = select(IdpExtractedHeader).where(
        IdpExtractedHeader.document_id == id, IdpExtractedHeader.job_id == latest_job_id
    )
    headers = (await session.exec(stmt_headers)).all()
    stmt_lines = select(IdpExtractedLineItem).where(
        IdpExtractedLineItem.document_id == id, IdpExtractedLineItem.job_id == latest_job_id
    )
    line_items = (await session.exec(stmt_lines)).all()

    corrections_made = False
    for h in headers:
        if h.reviewed_value is not None and h.reviewed_value != h.extracted_value:
            corrections_made = True
            break
    if not corrections_made:
        for li in line_items:
            if li.reviewed_value is not None and li.reviewed_value != li.extracted_value:
                corrections_made = True
                break

    final_status = "corrections_made" if corrections_made else "approved"

    # Create review session record
    review_session = IdpReviewSession(
        document_id=id,
        reviewed_by=current_user.id,
        review_started_at=doc.processing_completed_at or doc.updated_at or datetime.now(timezone.utc),
        review_completed_at=datetime.now(timezone.utc),
        final_status=final_status,
        notes=payload.notes,
    )
    session.add(review_session)

    # Submit clears any "draft" marker left by a prior Save-as-Draft.
    _clear_review_draft(doc)

    # Transition status
    doc.status = "reviewed"
    doc.updated_at = datetime.now(timezone.utc)
    session.add(doc)

    await session.commit()
    return {"status": "success", "document_status": doc.status, "review_session_status": final_status}


@router.post("/{id}/approve", status_code=status.HTTP_200_OK)
async def approve_processed_doc(
    *,
    session: DbSession,
    id: UUID,
    current_user: CurrentActiveUser,
):
    """Directly approve a processed document, transitioning its status to 'reviewed' (or 'auto_approved' if root)."""
    doc = await session.get(IdpDocument, id)
    if not doc or doc.deleted_at is not None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")

    if not await _can_access_document(session, current_user, doc):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not authorized to approve this document.")

    # M3: record the approval as a HITL review session (reviewer + timestamps), mirroring POST /review,
    # so a direct approve has the same audit trail as a reviewed submission.
    review_session = IdpReviewSession(
        document_id=id,
        reviewed_by=current_user.id,
        review_started_at=doc.processing_completed_at or doc.updated_at or datetime.now(timezone.utc),
        review_completed_at=datetime.now(timezone.utc),
        final_status="approved",
        notes=None,
    )
    session.add(review_session)

    # Mark as approved by transitioning status
    # Approving also finalizes the doc, so clear any leftover draft marker (same as POST /review).
    _clear_review_draft(doc)
    doc.status = "reviewed"
    doc.updated_at = datetime.now(timezone.utc)
    session.add(doc)
    await session.commit()
    return {"status": "success", "document_status": doc.status}


@router.delete("/{id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_processed_doc(
    *, session: DbSession, id: UUID, current_user: CurrentActiveUser,
):
    """Soft-delete a processed document: mark deleted_at (hidden from lists) but keep the row/file."""
    doc = await session.get(IdpDocument, id)
    if not doc or doc.deleted_at is not None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")
    if not await _can_access_document(session, current_user, doc):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")
    doc.deleted_at = datetime.now(timezone.utc)
    doc.updated_at = datetime.now(timezone.utc)
    session.add(doc)
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
