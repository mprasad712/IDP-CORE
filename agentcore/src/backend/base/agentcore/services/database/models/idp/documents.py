"""IDP (Intelligent Document Processing) document, processing-job and extraction tables.

These are new ``idp_*`` tables added alongside the existing schema; no existing table is
modified. Any schema change must be applied through an Alembic migration.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, SQLModel


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ───────────────────────────── idp_documents ─────────────────────────────
class IdpDocument(SQLModel, table=True):  # type: ignore[call-arg]
    __tablename__ = "idp_documents"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    agent_id: UUID = Field(
        sa_column=Column(Uuid, ForeignKey("idp_agents.id", ondelete="CASCADE"), nullable=False, index=True)
    )
    parent_document_id: UUID | None = Field(
        default=None,
        sa_column=Column(Uuid, ForeignKey("idp_documents.id", ondelete="SET NULL"), nullable=True, index=True),
    )
    original_filename: str = Field(sa_column=Column(String(500), nullable=False))
    file_path: str = Field(sa_column=Column(Text, nullable=False))
    file_type: str = Field(sa_column=Column(String(20), nullable=False))
    mime_type: str | None = Field(default=None, sa_column=Column(String(120), nullable=True))
    file_size_bytes: int = Field(sa_column=Column(BigInteger, nullable=False))
    page_count: int | None = Field(default=None, sa_column=Column(Integer, nullable=True))
    checksum: str | None = Field(default=None, sa_column=Column(String(128), nullable=True))
    source: str = Field(sa_column=Column(String(30), nullable=False))  # upload|mail_connector|sharepoint|other
    connector_id: UUID | None = Field(
        default=None,
        sa_column=Column(Uuid, ForeignKey("connector_catalogue.id", ondelete="SET NULL"), nullable=True, index=True),
    )
    source_metadata: dict | None = Field(default=None, sa_column=Column(JSONB, nullable=True))
    predicted_type: str | None = Field(default=None, sa_column=Column(String(100), nullable=True))
    status: str = Field(
        default="queued",
        sa_column=Column(String(30), nullable=False, default="queued", index=True),
    )  # queued|processing|extracted|pending_review|auto_approved|reviewed|failed
    overall_confidence: float | None = Field(default=None, sa_column=Column(Numeric(5, 4), nullable=True))
    uploaded_by: UUID | None = Field(
        default=None, sa_column=Column(Uuid, ForeignKey("user.id", ondelete="SET NULL"), nullable=True, index=True)
    )
    extra: dict | None = Field(default=None, sa_column=Column(JSONB, nullable=True))  # escape hatch
    processing_started_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True), nullable=True))
    processing_completed_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True), nullable=True))
    failed_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True), nullable=True))
    created_at: datetime = Field(default_factory=_utcnow, sa_column=Column(DateTime(timezone=True), nullable=False, index=True))
    updated_at: datetime = Field(default_factory=_utcnow, sa_column=Column(DateTime(timezone=True), nullable=False))

    __table_args__ = (
        CheckConstraint("source IN ('upload','mail_connector','sharepoint','onedrive','other')", name="ck_idp_documents_source"),
        CheckConstraint(
            "status IN ('queued','processing','extracted','pending_review','auto_approved','reviewed','failed','split','skipped')",
            name="ck_idp_documents_status",
        ),
        CheckConstraint("overall_confidence IS NULL OR (overall_confidence >= 0 AND overall_confidence <= 1)", name="ck_idp_documents_conf"),
        Index("ix_idp_documents_agent_status_created", "agent_id", "status", "created_at"),
        # G3: declared here too (not just in the migration) so alembic's model-vs-DB drift check
        # sees the partial-unique dedup index and stays clean on boot.
        Index(
            "uq_idp_documents_agent_dedup_key",
            "agent_id",
            text("(source_metadata->>'dedup_key')"),
            unique=True,
            postgresql_where=text("source_metadata ? 'dedup_key' AND source_metadata->>'dedup_key' <> ''"),
        ),
    )

    @property
    def review_draft(self) -> bool:
        """True when a reviewer saved HITL edits as a draft (persisted but not yet submitted).

        Read-only convenience derived from the ``extra`` JSONB escape hatch — no column,
        no migration. The flag is set by ``PATCH /fields?draft=true`` and cleared on
        ``POST /review``; the document itself stays in ``pending_review`` while drafting.
        """
        return bool((self.extra or {}).get("review_draft"))


# ──────────────────────────── idp_processing_jobs ────────────────────────────
class IdpProcessingJob(SQLModel, table=True):  # type: ignore[call-arg]
    __tablename__ = "idp_processing_jobs"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    document_id: UUID = Field(
        sa_column=Column(Uuid, ForeignKey("idp_documents.id", ondelete="CASCADE"), nullable=False, index=True)
    )
    agent_id: UUID = Field(
        sa_column=Column(Uuid, ForeignKey("idp_agents.id", ondelete="CASCADE"), nullable=False, index=True)
    )
    status: str = Field(default="queued", sa_column=Column(String(20), nullable=False, default="queued", index=True))
    steps_completed: list | None = Field(default=None, sa_column=Column(JSONB, nullable=True))
    log: list | None = Field(default=None, sa_column=Column(JSONB, nullable=True))  # ordered per-step flow-log events
    extraction_mode_used: str | None = Field(default=None, sa_column=Column(String(30), nullable=True))
    math_reconcile_attempts: int = Field(default=0, sa_column=Column(Integer, nullable=False, default=0))
    error_message: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    retry_count: int = Field(default=0, sa_column=Column(Integer, nullable=False, default=0))
    started_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True), nullable=True))
    completed_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True), nullable=True))
    processing_time_ms: int | None = Field(default=None, sa_column=Column(BigInteger, nullable=True))
    extra: dict | None = Field(default=None, sa_column=Column(JSONB, nullable=True))
    created_at: datetime = Field(default_factory=_utcnow, sa_column=Column(DateTime(timezone=True), nullable=False))
    updated_at: datetime = Field(default_factory=_utcnow, sa_column=Column(DateTime(timezone=True), nullable=False))

    __table_args__ = (
        CheckConstraint("status IN ('queued','running','completed','failed')", name="ck_idp_jobs_status"),
        Index("ix_idp_jobs_document_created", "document_id", "created_at"),
        Index("ix_idp_jobs_agent_status_created", "agent_id", "status", "created_at"),
    )


# ─────────────────────────── idp_extracted_headers ───────────────────────────
class IdpExtractedHeader(SQLModel, table=True):  # type: ignore[call-arg]
    __tablename__ = "idp_extracted_headers"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    document_id: UUID = Field(
        sa_column=Column(Uuid, ForeignKey("idp_documents.id", ondelete="CASCADE"), nullable=False, index=True)
    )
    job_id: UUID = Field(
        sa_column=Column(Uuid, ForeignKey("idp_processing_jobs.id", ondelete="CASCADE"), nullable=False, index=True)
    )
    field_name: str = Field(sa_column=Column(String(100), nullable=False))
    extracted_value: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    confidence_score: float | None = Field(default=None, sa_column=Column(Numeric(5, 4), nullable=True))
    reasoning_trace: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    source_location: dict | None = Field(default=None, sa_column=Column(JSONB, nullable=True))
    reviewed_value: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    is_reviewed: bool = Field(default=False, sa_column=Column(Boolean, nullable=False, default=False))
    extra: dict | None = Field(default=None, sa_column=Column(JSONB, nullable=True))
    created_at: datetime = Field(default_factory=_utcnow, sa_column=Column(DateTime(timezone=True), nullable=False))
    updated_at: datetime = Field(default_factory=_utcnow, sa_column=Column(DateTime(timezone=True), nullable=False))

    __table_args__ = (
        UniqueConstraint("job_id", "field_name", name="uq_idp_ext_header_job_field"),
        CheckConstraint("confidence_score IS NULL OR (confidence_score >= 0 AND confidence_score <= 1)", name="ck_idp_ext_header_conf"),
        Index("ix_idp_ext_header_doc_field", "document_id", "field_name"),
    )


# ────────────────────────── idp_extracted_line_items ──────────────────────────
class IdpExtractedLineItem(SQLModel, table=True):  # type: ignore[call-arg]
    __tablename__ = "idp_extracted_line_items"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    document_id: UUID = Field(
        sa_column=Column(Uuid, ForeignKey("idp_documents.id", ondelete="CASCADE"), nullable=False, index=True)
    )
    job_id: UUID = Field(
        sa_column=Column(Uuid, ForeignKey("idp_processing_jobs.id", ondelete="CASCADE"), nullable=False, index=True)
    )
    row_index: int = Field(sa_column=Column(Integer, nullable=False))
    column_name: str = Field(sa_column=Column(String(100), nullable=False))
    extracted_value: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    confidence_score: float | None = Field(default=None, sa_column=Column(Numeric(5, 4), nullable=True))
    reasoning_trace: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    source_location: dict | None = Field(default=None, sa_column=Column(JSONB, nullable=True))
    reviewed_value: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    is_reviewed: bool = Field(default=False, sa_column=Column(Boolean, nullable=False, default=False))
    extra: dict | None = Field(default=None, sa_column=Column(JSONB, nullable=True))
    created_at: datetime = Field(default_factory=_utcnow, sa_column=Column(DateTime(timezone=True), nullable=False))
    updated_at: datetime = Field(default_factory=_utcnow, sa_column=Column(DateTime(timezone=True), nullable=False))

    __table_args__ = (
        UniqueConstraint("job_id", "row_index", "column_name", name="uq_idp_ext_line_job_row_col"),
        CheckConstraint("confidence_score IS NULL OR (confidence_score >= 0 AND confidence_score <= 1)", name="ck_idp_ext_line_conf"),
        Index("ix_idp_ext_line_doc_row", "document_id", "row_index"),
    )


# ─────────────────────── idp_document_classifications ───────────────────────
class IdpDocumentClassification(SQLModel, table=True):  # type: ignore[call-arg]
    __tablename__ = "idp_document_classifications"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    document_id: UUID = Field(
        sa_column=Column(Uuid, ForeignKey("idp_documents.id", ondelete="CASCADE"), nullable=False, index=True)
    )
    predicted_type: str = Field(sa_column=Column(String(100), nullable=False, index=True))
    confidence: float = Field(sa_column=Column(Numeric(5, 4), nullable=False))
    candidates: dict | None = Field(default=None, sa_column=Column(JSONB, nullable=True))
    selected_config_id: UUID | None = Field(
        default=None,
        sa_column=Column(Uuid, ForeignKey("idp_field_configurations.id", ondelete="SET NULL"), nullable=True, index=True),
    )
    is_selected: bool = Field(default=False, sa_column=Column(Boolean, nullable=False, default=False))
    created_at: datetime = Field(default_factory=_utcnow, sa_column=Column(DateTime(timezone=True), nullable=False))
    updated_at: datetime = Field(default_factory=_utcnow, sa_column=Column(DateTime(timezone=True), nullable=False))

    __table_args__ = (
        CheckConstraint("confidence >= 0 AND confidence <= 1", name="ck_idp_classification_conf"),
    )


# ─────────────────────────── idp_detected_elements ───────────────────────────
class IdpDetectedElement(SQLModel, table=True):  # type: ignore[call-arg]
    __tablename__ = "idp_detected_elements"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    document_id: UUID = Field(
        sa_column=Column(Uuid, ForeignKey("idp_documents.id", ondelete="CASCADE"), nullable=False, index=True)
    )
    element_type: str = Field(sa_column=Column(String(40), nullable=False, index=True))  # signature|stamp|checkbox|qr|barcode|logo|annotation
    page_number: int = Field(sa_column=Column(Integer, nullable=False))
    bounding_box: dict = Field(sa_column=Column(JSONB, nullable=False))
    confidence: float = Field(sa_column=Column(Numeric(5, 4), nullable=False))
    decoded_value: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    created_at: datetime = Field(default_factory=_utcnow, sa_column=Column(DateTime(timezone=True), nullable=False))
    updated_at: datetime = Field(default_factory=_utcnow, sa_column=Column(DateTime(timezone=True), nullable=False))

    __table_args__ = (
        CheckConstraint("confidence >= 0 AND confidence <= 1", name="ck_idp_detected_conf"),
    )


# ─────────────────────────── idp_document_sections ───────────────────────────
class IdpDocumentSection(SQLModel, table=True):  # type: ignore[call-arg]
    __tablename__ = "idp_document_sections"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    document_id: UUID = Field(
        sa_column=Column(Uuid, ForeignKey("idp_documents.id", ondelete="CASCADE"), nullable=False, index=True)
    )
    parent_section_id: UUID | None = Field(
        default=None,
        sa_column=Column(Uuid, ForeignKey("idp_document_sections.id", ondelete="CASCADE"), nullable=True, index=True),
    )
    title: str | None = Field(default=None, sa_column=Column(String(300), nullable=True))
    page_start: int = Field(sa_column=Column(Integer, nullable=False))
    page_end: int = Field(sa_column=Column(Integer, nullable=False))
    order_index: int = Field(sa_column=Column(Integer, nullable=False))
    created_at: datetime = Field(default_factory=_utcnow, sa_column=Column(DateTime(timezone=True), nullable=False))
    updated_at: datetime = Field(default_factory=_utcnow, sa_column=Column(DateTime(timezone=True), nullable=False))


# ───────────────────────────── idp_entity_links ─────────────────────────────
class IdpEntityLink(SQLModel, table=True):  # type: ignore[call-arg]
    __tablename__ = "idp_entity_links"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    document_id: UUID = Field(
        sa_column=Column(Uuid, ForeignKey("idp_documents.id", ondelete="CASCADE"), nullable=False, index=True)
    )
    entity_key: str = Field(sa_column=Column(String(200), nullable=False, index=True))
    entity_type: str = Field(sa_column=Column(String(60), nullable=False))
    occurrences: dict = Field(sa_column=Column(JSONB, nullable=False))
    created_at: datetime = Field(default_factory=_utcnow, sa_column=Column(DateTime(timezone=True), nullable=False))
    updated_at: datetime = Field(default_factory=_utcnow, sa_column=Column(DateTime(timezone=True), nullable=False))
