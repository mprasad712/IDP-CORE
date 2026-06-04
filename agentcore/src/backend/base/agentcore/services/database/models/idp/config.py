"""IDP (Intelligent Document Processing) field-configuration, agent, rules, review and batch tables.

These are new ``idp_*`` tables added alongside the existing schema; no existing table is
modified. Any schema change must be applied through an Alembic migration.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, SQLModel, Relationship


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ─────────────────────── idp_field_configurations ───────────────────────
class IdpFieldConfiguration(SQLModel, table=True):  # type: ignore[call-arg]
    __tablename__ = "idp_field_configurations"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    name: str = Field(sa_column=Column(String(200), nullable=False))
    description: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    org_id: UUID | None = Field(
        default=None, sa_column=Column(Uuid, ForeignKey("organization.id", ondelete="SET NULL"), nullable=True, index=True)
    )
    is_template: bool = Field(default=False, sa_column=Column(Boolean, nullable=False, default=False))
    is_active: bool = Field(default=True, sa_column=Column(Boolean, nullable=False, default=True))
    deleted_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True), nullable=True))
    extra: dict | None = Field(default=None, sa_column=Column(JSONB, nullable=True))
    created_by: UUID | None = Field(default=None, sa_column=Column(Uuid, ForeignKey("user.id", ondelete="SET NULL"), nullable=True, index=True))
    updated_by: UUID | None = Field(default=None, sa_column=Column(Uuid, ForeignKey("user.id", ondelete="SET NULL"), nullable=True))
    created_at: datetime = Field(default_factory=_utcnow, sa_column=Column(DateTime(timezone=True), nullable=False))
    updated_at: datetime = Field(default_factory=_utcnow, sa_column=Column(DateTime(timezone=True), nullable=False))

    headers: list[IdpFieldConfigHeader] = Relationship(
        back_populates="field_config",
        sa_relationship_kwargs={"cascade": "all, delete-orphan"},
    )
    line_items: list[IdpFieldConfigLineItem] = Relationship(
        back_populates="field_config",
        sa_relationship_kwargs={"cascade": "all, delete-orphan"},
    )

    __table_args__ = (
        UniqueConstraint("org_id", "name", name="uq_idp_field_config_org_name"),
    )


# ─────────────────────── idp_field_config_headers ───────────────────────
class IdpFieldConfigHeader(SQLModel, table=True):  # type: ignore[call-arg]
    __tablename__ = "idp_field_config_headers"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    config_id: UUID = Field(
        sa_column=Column(Uuid, ForeignKey("idp_field_configurations.id", ondelete="CASCADE"), nullable=False, index=True)
    )
    field_name: str = Field(sa_column=Column(String(100), nullable=False))
    field_type: str = Field(sa_column=Column(String(20), nullable=False))  # text|number|date|boolean
    is_required: bool = Field(default=False, sa_column=Column(Boolean, nullable=False, default=False))
    display_order: int = Field(sa_column=Column(Integer, nullable=False))
    description: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    created_at: datetime = Field(default_factory=_utcnow, sa_column=Column(DateTime(timezone=True), nullable=False))
    updated_at: datetime = Field(default_factory=_utcnow, sa_column=Column(DateTime(timezone=True), nullable=False))

    field_config: IdpFieldConfiguration = Relationship(back_populates="headers")

    __table_args__ = (
        UniqueConstraint("config_id", "field_name", name="uq_idp_cfg_header_config_field"),
        CheckConstraint("field_type IN ('text','number','date','boolean')", name="ck_idp_cfg_header_type"),
    )


# ─────────────────────── idp_field_config_line_items ───────────────────────
class IdpFieldConfigLineItem(SQLModel, table=True):  # type: ignore[call-arg]
    __tablename__ = "idp_field_config_line_items"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    config_id: UUID = Field(
        sa_column=Column(Uuid, ForeignKey("idp_field_configurations.id", ondelete="CASCADE"), nullable=False, index=True)
    )
    column_name: str = Field(sa_column=Column(String(100), nullable=False))
    column_type: str = Field(sa_column=Column(String(20), nullable=False))  # text|number|date
    is_required: bool = Field(default=False, sa_column=Column(Boolean, nullable=False, default=False))
    display_order: int = Field(sa_column=Column(Integer, nullable=False))
    created_at: datetime = Field(default_factory=_utcnow, sa_column=Column(DateTime(timezone=True), nullable=False))
    updated_at: datetime = Field(default_factory=_utcnow, sa_column=Column(DateTime(timezone=True), nullable=False))

    field_config: IdpFieldConfiguration = Relationship(back_populates="line_items")

    __table_args__ = (
        UniqueConstraint("config_id", "column_name", name="uq_idp_cfg_line_config_col"),
        CheckConstraint("column_type IN ('text','number','date')", name="ck_idp_cfg_line_type"),
    )


# ──────────────────────────────── idp_agents ────────────────────────────────
class IdpAgent(SQLModel, table=True):  # type: ignore[call-arg]
    __tablename__ = "idp_agents"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    agent_id: UUID = Field(
        sa_column=Column(Uuid, ForeignKey("agent.id", ondelete="CASCADE"), nullable=False, unique=True, index=True)
    )  # → existing base agent
    extraction_mode: str = Field(sa_column=Column(String(30), nullable=False))  # dynamic_prompting|named_config|multimodal
    field_config_id: UUID | None = Field(
        default=None,
        sa_column=Column(Uuid, ForeignKey("idp_field_configurations.id", ondelete="SET NULL"), nullable=True, index=True),
    )
    dynamic_prompt: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    preprocessing_steps: dict | None = Field(default=None, sa_column=Column(JSONB, nullable=True))
    multi_doc_split: bool = Field(default=False, sa_column=Column(Boolean, nullable=False, default=False))
    default_rule_action: str = Field(
        default="pending_review", sa_column=Column(String(20), nullable=False, default="pending_review")
    )
    auto_stop_after_batch: bool = Field(default=True, sa_column=Column(Boolean, nullable=False, default=True))
    is_active: bool = Field(default=True, sa_column=Column(Boolean, nullable=False, default=True))
    deleted_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True), nullable=True))
    extra: dict | None = Field(default=None, sa_column=Column(JSONB, nullable=True))
    created_by: UUID | None = Field(default=None, sa_column=Column(Uuid, ForeignKey("user.id", ondelete="SET NULL"), nullable=True))
    updated_by: UUID | None = Field(default=None, sa_column=Column(Uuid, ForeignKey("user.id", ondelete="SET NULL"), nullable=True))
    created_at: datetime = Field(default_factory=_utcnow, sa_column=Column(DateTime(timezone=True), nullable=False))
    updated_at: datetime = Field(default_factory=_utcnow, sa_column=Column(DateTime(timezone=True), nullable=False))

    __table_args__ = (
        CheckConstraint("extraction_mode IN ('dynamic_prompting','named_config','multimodal')", name="ck_idp_agents_mode"),
        CheckConstraint("default_rule_action IN ('auto_approve','pending_review')", name="ck_idp_agents_default_action"),
    )


# ───────────────────────────── idp_agent_rules ─────────────────────────────
class IdpAgentRule(SQLModel, table=True):  # type: ignore[call-arg]
    __tablename__ = "idp_agent_rules"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    idp_agent_id: UUID = Field(  # renamed from agent_id to avoid confusion with idp_agents.agent_id
        sa_column=Column(Uuid, ForeignKey("idp_agents.id", ondelete="CASCADE"), nullable=False, index=True)
    )
    rule_group: int = Field(sa_column=Column(Integer, nullable=False, index=True))
    condition_type: str = Field(sa_column=Column(String(40), nullable=False))
    field_name: str | None = Field(default=None, sa_column=Column(String(100), nullable=True))
    operator: str = Field(sa_column=Column(String(20), nullable=False))
    value: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    field_b: str | None = Field(default=None, sa_column=Column(String(100), nullable=True))
    pattern: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    combinator: str = Field(default="AND", sa_column=Column(String(3), nullable=False, default="AND"))
    action: str = Field(sa_column=Column(String(20), nullable=False))  # auto_approve|pending_review
    display_order: int = Field(sa_column=Column(Integer, nullable=False))
    extra: dict | None = Field(default=None, sa_column=Column(JSONB, nullable=True))
    created_at: datetime = Field(default_factory=_utcnow, sa_column=Column(DateTime(timezone=True), nullable=False))
    updated_at: datetime = Field(default_factory=_utcnow, sa_column=Column(DateTime(timezone=True), nullable=False))

    __table_args__ = (
        CheckConstraint(
            "condition_type IN ('confidence_overall','confidence_field','field_value_numeric','field_value_text',"
            "'field_value_date','field_comparison','field_presence','pattern_regex','visual_element')",
            name="ck_idp_rules_condition_type",
        ),
        CheckConstraint("combinator IN ('AND','OR')", name="ck_idp_rules_combinator"),
        CheckConstraint("action IN ('auto_approve','pending_review')", name="ck_idp_rules_action"),
        Index("ix_idp_rules_agent_group_order", "idp_agent_id", "rule_group", "display_order"),
    )


# ──────────────────────────── idp_review_sessions ────────────────────────────
class IdpReviewSession(SQLModel, table=True):  # type: ignore[call-arg]
    __tablename__ = "idp_review_sessions"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    document_id: UUID = Field(
        sa_column=Column(Uuid, ForeignKey("idp_documents.id", ondelete="CASCADE"), nullable=False, index=True)
    )
    reviewed_by: UUID = Field(sa_column=Column(Uuid, ForeignKey("user.id", ondelete="RESTRICT"), nullable=False, index=True))
    review_started_at: datetime = Field(default_factory=_utcnow, sa_column=Column(DateTime(timezone=True), nullable=False))
    review_completed_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True), nullable=True))
    final_status: str | None = Field(default=None, sa_column=Column(String(20), nullable=True))  # approved|corrections_made
    notes: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    extra: dict | None = Field(default=None, sa_column=Column(JSONB, nullable=True))
    created_at: datetime = Field(default_factory=_utcnow, sa_column=Column(DateTime(timezone=True), nullable=False))
    updated_at: datetime = Field(default_factory=_utcnow, sa_column=Column(DateTime(timezone=True), nullable=False))

    __table_args__ = (
        CheckConstraint("final_status IS NULL OR final_status IN ('approved','corrections_made')", name="ck_idp_review_final_status"),
        Index("ix_idp_review_doc_completed", "document_id", "review_completed_at"),
    )


# ─────────────────────── idp_bulk_processing_batches ───────────────────────
class IdpBulkProcessingBatch(SQLModel, table=True):  # type: ignore[call-arg]
    __tablename__ = "idp_bulk_processing_batches"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    agent_id: UUID = Field(
        sa_column=Column(Uuid, ForeignKey("idp_agents.id", ondelete="CASCADE"), nullable=False, index=True)
    )
    connector_id: UUID | None = Field(
        default=None, sa_column=Column(Uuid, ForeignKey("connector_catalogue.id", ondelete="SET NULL"), nullable=True, index=True)
    )
    trigger_type: str = Field(sa_column=Column(String(10), nullable=False))  # auto|manual
    total_documents: int = Field(default=0, sa_column=Column(Integer, nullable=False, default=0))
    processed_documents: int = Field(default=0, sa_column=Column(Integer, nullable=False, default=0))
    failed_documents: int = Field(default=0, sa_column=Column(Integer, nullable=False, default=0))
    status: str = Field(default="queued", sa_column=Column(String(20), nullable=False, default="queued", index=True))
    started_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True), nullable=True))
    completed_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True), nullable=True))
    extra: dict | None = Field(default=None, sa_column=Column(JSONB, nullable=True))
    created_at: datetime = Field(default_factory=_utcnow, sa_column=Column(DateTime(timezone=True), nullable=False))
    updated_at: datetime = Field(default_factory=_utcnow, sa_column=Column(DateTime(timezone=True), nullable=False))

    __table_args__ = (
        CheckConstraint("trigger_type IN ('auto','manual')", name="ck_idp_batch_trigger"),
        CheckConstraint(
            "status IN ('queued','running','completed','partial','failed','cancelled')", name="ck_idp_batch_status"
        ),
    )


# ──────────────────────────── idp_document_batches ────────────────────────────
class IdpDocumentBatch(SQLModel, table=True):  # type: ignore[call-arg]
    __tablename__ = "idp_document_batches"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    batch_id: UUID = Field(
        sa_column=Column(Uuid, ForeignKey("idp_bulk_processing_batches.id", ondelete="CASCADE"), nullable=False, index=True)
    )
    document_id: UUID = Field(
        sa_column=Column(Uuid, ForeignKey("idp_documents.id", ondelete="CASCADE"), nullable=False, index=True)
    )
    sequence_order: int = Field(sa_column=Column(Integer, nullable=False))
    created_at: datetime = Field(default_factory=_utcnow, sa_column=Column(DateTime(timezone=True), nullable=False))

    __table_args__ = (
        UniqueConstraint("batch_id", "document_id", name="uq_idp_doc_batch_batch_doc"),
    )
