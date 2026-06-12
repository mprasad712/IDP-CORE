import hashlib
from typing import Annotated
from uuid import UUID, uuid4
from pathlib import Path
from datetime import datetime, timezone
import mimetypes
import os
import re
from loguru import logger
from fastapi import APIRouter, Depends, HTTPException, File, Form, Response, UploadFile, status
from sqlmodel import select, or_

from pydantic import BaseModel

from agentcore.api.utils import CurrentActiveUser, DbSession
from agentcore.services.auth.permissions import normalize_role
from agentcore.services.database.models.agent.model import Agent
from agentcore.services.database.models.idp.config import IdpAgent
from agentcore.services.database.models.idp.documents import IdpDocument, IdpProcessingJob
from agentcore.services.database.models.user_organization_membership.model import UserOrganizationMembership
from agentcore.services.deps import get_storage_service, get_settings_service
from agentcore.services.idp.pipeline import PipelineError, enqueue_document
from agentcore.services.storage.service import StorageService

router = APIRouter(prefix="/documents", tags=["IDP Documents"])

ALLOWED_EXTENSIONS = {".pdf", ".png", ".jpg", ".jpeg", ".tiff", ".tif", ".xlsx", ".xls", ".docx", ".txt"}

@router.get("/health")
def health():
    return {"status": "ok", "service": "IDP Documents API"}

@router.post("/upload", status_code=status.HTTP_201_CREATED)
@router.post("/upload/", status_code=status.HTTP_201_CREATED)
async def upload_documents(
    *,
    session: DbSession,
    current_user: CurrentActiveUser,
    agent_id: Annotated[UUID, Form(description="The UUID of the base Agent or the IDP Agent config")],
    files: list[UploadFile] = File(..., description="The document files to upload"),
    storage_service: StorageService = Depends(get_storage_service),
    settings_service = Depends(get_settings_service),
):
    """Upload one or more documents for IDP processing.

    Saves the files to storage and creates IdpDocument database records
    with status set to 'queued'.
    """
    # 1. Resolve IDP Agent — auto-create if absent so the playground works without publishing
    stmt = (
        select(IdpAgent)
        .where(
            or_(
                IdpAgent.id == agent_id,
                IdpAgent.agent_id == agent_id
            )
        )
        .where(IdpAgent.deleted_at.is_(None))
    )
    idp_agent = (await session.exec(stmt)).first()
    if not idp_agent:
        base_agent = await session.get(Agent, agent_id)
        if not base_agent or base_agent.deleted_at is not None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Active IDP Agent with ID/agent_id '{agent_id}' not found."
            )
        idp_agent = IdpAgent(
            agent_id=agent_id,
            extraction_mode="dynamic_prompting",
            dynamic_prompt="",
            default_rule_action="pending_review",
            is_active=True,
            created_by=current_user.id,
            updated_by=current_user.id,
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        session.add(idp_agent)
        await session.flush()

    # 2. Get file upload settings
    try:
        max_file_size_upload = settings_service.settings.max_file_size_upload
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Upload settings are currently unavailable."
        ) from e

    max_size_bytes = max_file_size_upload * 1024 * 1024
    document_ids = []

    try:
        for file in files:
            if not file or not file.filename:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="One of the files is empty or has no filename."
                )

            # Check file extension
            ext = Path(file.filename).suffix.lower()
            if ext not in ALLOWED_EXTENSIONS:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"File extension '{ext}' is not allowed. Allowed types: {', '.join(sorted(ALLOWED_EXTENSIONS))}"
                )

            # Determine size without UploadFile.seek/tell (signatures vary across ASGI
            # backends). Prefer the Content-Length-derived file.size so an oversized file
            # is rejected BEFORE buffering it all into memory; only read early if unknown.
            size = file.size
            file_content = None
            if size is None:
                file_content = await file.read()
                size = len(file_content)

            if size > max_size_bytes:
                raise HTTPException(
                    status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                    detail=f"File '{file.filename}' exceeds the maximum allowed size of {max_file_size_upload}MB."
                )

            # Read content (if not already) and compute SHA-256 checksum
            if file_content is None:
                file_content = await file.read()
            checksum = hashlib.sha256(file_content).hexdigest()

            # Generate unique name for disk storage
            unique_id = uuid4()
            storage_filename = f"idp_{unique_id}{ext}"

            # Save the file using the storage service scoped by the IDP Agent config UUID
            await storage_service.save_file(
                agent_id=str(idp_agent.id),
                file_name=storage_filename,
                data=file_content
            )

            # Create the database record
            new_doc = IdpDocument(
                id=unique_id,
                agent_id=idp_agent.id,
                original_filename=file.filename,
                file_path=f"{idp_agent.id}/{storage_filename}",
                file_type=ext.lstrip("."),
                mime_type=file.content_type,
                file_size_bytes=size,
                checksum=checksum,
                source="upload",
                status="queued",
                uploaded_by=current_user.id,
                created_at=datetime.now(timezone.utc),
                updated_at=datetime.now(timezone.utc),
            )
            session.add(new_doc)
            document_ids.append(new_doc.id)

        await session.commit()

    except HTTPException:
        await session.rollback()
        raise
    except Exception as e:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"An error occurred while uploading documents: {str(e)}"
        )

    return {
        "document_ids": document_ids,
        "message": f"Successfully uploaded {len(document_ids)} document(s)."
    }


class ProcessBulkRequest(BaseModel):
    document_ids: list[UUID]


_PROCESSABLE_STATUSES = {"queued", "extracted", "pending_review", "auto_approved", "reviewed", "failed"}


async def _enqueue_one(session, document_id: UUID):
    """Enqueue a single document; returns (job_id, None) or (None, error_detail)."""
    doc = (await session.exec(select(IdpDocument).where(IdpDocument.id == document_id))).first()
    if doc is None:
        return None, "document not found"
    if doc.status == "processing":
        return None, "document is already processing"
    running = (
        await session.exec(
            select(IdpProcessingJob).where(
                IdpProcessingJob.document_id == document_id,
                IdpProcessingJob.status.in_(("queued", "running")),
            )
        )
    ).first()
    if running is not None:
        return None, "a job for this document is already queued or running"
    try:
        job_id = await enqueue_document(session, document_id)
        return job_id, None
    except PipelineError as e:
        return None, str(e)


@router.post("/{document_id}/process", status_code=status.HTTP_202_ACCEPTED)
async def process_document_endpoint(
    *, session: DbSession, current_user: CurrentActiveUser, document_id: UUID
):
    """Enqueue a single uploaded document for background IDP processing."""
    doc = (await session.exec(select(IdpDocument).where(IdpDocument.id == document_id))).first()
    if doc is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found.")
    job_id, error = await _enqueue_one(session, document_id)
    if error:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=error)
    return {"job_id": job_id, "document_id": document_id, "status": "queued"}


@router.post("/process", status_code=status.HTTP_202_ACCEPTED)
async def process_documents_bulk(
    *, session: DbSession, current_user: CurrentActiveUser, body: ProcessBulkRequest
):
    """Enqueue multiple documents for background IDP processing."""
    jobs = []
    skipped = []
    for document_id in body.document_ids:
        job_id, error = await _enqueue_one(session, document_id)
        if error:
            skipped.append({"document_id": str(document_id), "reason": error})
        else:
            jobs.append({"document_id": str(document_id), "job_id": str(job_id)})
    return {"jobs": jobs, "skipped": skipped}


@router.get("/{document_id}/file")
async def get_document_file(
    *,
    session: DbSession,
    current_user: CurrentActiveUser,
    document_id: UUID,
    storage_service: StorageService = Depends(get_storage_service),
):
    """Stream the original uploaded document bytes (for the Processed Docs viewer)."""
    # Scope: root sees all; everyone else only documents whose agent belongs to one of their orgs.
    role = normalize_role(getattr(current_user, "role", None))
    stmt = select(IdpDocument).where(IdpDocument.id == document_id)
    if role != "root":
        rows = (
            await session.exec(
                select(UserOrganizationMembership.org_id).where(
                    UserOrganizationMembership.user_id == current_user.id,
                    UserOrganizationMembership.status.in_(["accepted", "active"]),
                )
            )
        ).all()
        org_ids = [r if not isinstance(r, tuple) else r[0] for r in rows]
        stmt = (
            stmt.join(IdpAgent, IdpDocument.agent_id == IdpAgent.id)
            .join(Agent, IdpAgent.agent_id == Agent.id)
            .where(Agent.org_id.in_(org_ids))
        )
    doc = (await session.exec(stmt)).first()
    if doc is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found.")

    # file_path is stored as "{agent_id}/{storage_filename}"; scope reads by the real agent_id.
    file_name = doc.file_path.split("/", 1)[1] if "/" in doc.file_path else doc.file_path
    try:
        data = await storage_service.get_file(agent_id=str(doc.agent_id), file_name=file_name)
    except FileNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Stored file not found.") from e
    except Exception as e:
        logger.exception(f"[idp] failed to read stored document {document_id}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Could not read the stored document.",
        ) from e

    media_type = (
        doc.mime_type
        or mimetypes.guess_type(doc.original_filename or file_name)[0]
        or "application/octet-stream"
    )
    # Sanitize the filename before putting it in a response header (strip path + CR/LF/quote/semicolon).
    safe_name = re.sub(r'[\r\n";]', "_", os.path.basename(doc.original_filename or file_name or "document"))
    return Response(
        content=data,
        media_type=media_type,
        headers={"Content-Disposition": f'inline; filename="{safe_name}"'},
    )


@router.get("/{document_id}/log")
async def get_document_log(
    *,
    session: DbSession,
    current_user: CurrentActiveUser,
    document_id: UUID,
):
    """Return the per-document processing FLOW LOG (the step-by-step trail) for the viewer.

    This is the readable cycle — input received → config → OCR/native → classify → extract
    (what the LLM returned) → route → finalize — recorded on the latest processing job.
    """
    # Scope: root sees all; everyone else only documents whose agent belongs to one of their orgs.
    role = normalize_role(getattr(current_user, "role", None))
    stmt = select(IdpDocument).where(IdpDocument.id == document_id)
    if role != "root":
        rows = (
            await session.exec(
                select(UserOrganizationMembership.org_id).where(
                    UserOrganizationMembership.user_id == current_user.id,
                    UserOrganizationMembership.status.in_(["accepted", "active"]),
                )
            )
        ).all()
        org_ids = [r if not isinstance(r, tuple) else r[0] for r in rows]
        stmt = (
            stmt.join(IdpAgent, IdpDocument.agent_id == IdpAgent.id)
            .join(Agent, IdpAgent.agent_id == Agent.id)
            .where(Agent.org_id.in_(org_ids))
        )
    doc = (await session.exec(stmt)).first()
    if doc is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found.")

    job = (
        await session.exec(
            select(IdpProcessingJob)
            .where(IdpProcessingJob.document_id == document_id)
            .order_by(IdpProcessingJob.created_at.desc())
        )
    ).first()
    if job is None:
        return {
            "document_id": str(document_id),
            "job_id": None,
            "status": doc.status,
            "steps_completed": [],
            "error_message": None,
            "processing_time_ms": None,
            "log": [],
        }
    return {
        "document_id": str(document_id),
        "job_id": str(job.id),
        "status": job.status,
        "steps_completed": job.steps_completed or [],
        "error_message": job.error_message,
        "processing_time_ms": job.processing_time_ms,
        "log": job.log or [],
    }
