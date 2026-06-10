from datetime import datetime, timezone
from typing import Annotated
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, status, Response
from fastapi_pagination import Params, Page
from fastapi_pagination.ext.sqlmodel import apaginate
from sqlmodel import select, or_, and_
from pydantic import BaseModel, Field as PydanticField

from agentcore.api.utils import CurrentActiveUser, DbSession
from agentcore.services.database.models.idp.documents import (
    IdpDocument,
    IdpExtractedHeader,
    IdpExtractedLineItem,
)
from agentcore.services.database.models.idp.config import IdpAgent, IdpReviewSession
from agentcore.services.database.models.agent.model import Agent
from agentcore.services.database.models.user_organization_membership.model import UserOrganizationMembership
from agentcore.services.auth.permissions import normalize_role

router = APIRouter(prefix="/processed-docs", tags=["IDP Processed Documents"])

# ──────────────────────────────────────────────────────────────────────
# Schemas
# ──────────────────────────────────────────────────────────────────────

class ProcessedDocRead(BaseModel):
    id: UUID
    agent_id: UUID
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

class FieldUpdateItem(BaseModel):
    id: UUID
    value: str | None

class HumanFieldsUpdateRequest(BaseModel):
    headers: list[FieldUpdateItem] | None = None
    line_items: list[FieldUpdateItem] | None = None

class DocumentReviewRequest(BaseModel):
    notes: str | None = None

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

    # Base query joining IdpDocument -> IdpAgent -> Agent to enforce scoping
    stmt = select(IdpDocument)
    if role != "root":
        stmt = stmt.join(IdpAgent, IdpDocument.agent_id == IdpAgent.id)
        stmt = stmt.join(Agent, IdpAgent.agent_id == Agent.id)
        stmt = stmt.where(Agent.org_id.in_(list(org_ids)))

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
        return await apaginate(session, stmt, params=params)


@router.get("/{id}", response_model=ProcessedDocDetailRead)
async def get_processed_doc(
    *,
    session: DbSession,
    id: UUID,
    current_user: CurrentActiveUser,
):
    """Retrieve full details of an extracted document including header and line items."""
    doc = await session.get(IdpDocument, id)
    if not doc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")

    if not await _can_access_document(session, current_user, doc):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")

    # Load headers and line items
    stmt_headers = select(IdpExtractedHeader).where(IdpExtractedHeader.document_id == id)
    headers = (await session.exec(stmt_headers)).all()

    stmt_lines = select(IdpExtractedLineItem).where(IdpExtractedLineItem.document_id == id).order_by(
        IdpExtractedLineItem.row_index.asc(),
        IdpExtractedLineItem.column_name.asc()
    )
    line_items = (await session.exec(stmt_lines)).all()

    detail = ProcessedDocDetailRead.model_validate(doc)
    detail.headers = headers
    detail.line_items = line_items
    return detail


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
    if not doc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")

    if not await _can_access_document(session, current_user, doc):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not authorized to edit this document.")

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

    doc.updated_at = datetime.now(timezone.utc)
    session.add(doc)
    await session.commit()

    return await get_processed_doc(session=session, id=id, current_user=current_user)


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
    if not doc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")

    if not await _can_access_document(session, current_user, doc):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not authorized to review this document.")

    # Determine if any human corrections were made
    stmt_headers = select(IdpExtractedHeader).where(IdpExtractedHeader.document_id == id)
    headers = (await session.exec(stmt_headers)).all()
    stmt_lines = select(IdpExtractedLineItem).where(IdpExtractedLineItem.document_id == id)
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
    if not doc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")

    if not await _can_access_document(session, current_user, doc):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not authorized to approve this document.")

    # Mark as approved by transitioning status
    # If the document is currently in review, we mark it reviewed
    doc.status = "reviewed"
    doc.updated_at = datetime.now(timezone.utc)
    session.add(doc)
    await session.commit()
    return {"status": "success", "document_status": doc.status}
