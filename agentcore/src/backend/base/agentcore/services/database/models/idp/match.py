"""IDP two-way / three-way match tables.

Three new additive ``idp_*`` tables that store SAP matching results alongside
existing extracted document data. Schema changes must go through Alembic migrations.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    Uuid,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, SQLModel


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ─────────────────────────── idp_match_results ───────────────────────────
class IdpMatchResult(SQLModel, table=True):  # type: ignore[call-arg]
    """One row per match run for a document. Re-runs (via the manual trigger) create a new row."""

    __tablename__ = "idp_match_results"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    document_id: UUID = Field(
        sa_column=Column(Uuid, ForeignKey("idp_documents.id", ondelete="CASCADE"), nullable=False, index=True)
    )

    # "2way" | "3way"
    match_type: str = Field(sa_column=Column(String(10), nullable=False))
    # "full_match" | "partial_match" | "mismatch" | "error"
    overall_status: str = Field(sa_column=Column(String(20), nullable=False))
    # Percentage of fields that passed (0-100)
    match_score: float = Field(default=0.0, sa_column=Column(Float, nullable=False, server_default="0"))

    po_number: str = Field(sa_column=Column(String(100), nullable=False))
    # GR document number — null for 2-way
    gr_document_number: str | None = Field(default=None, sa_column=Column(String(100), nullable=True))

    # Snapshot of SAP data at the time of the match, for audit purposes
    sap_po_snapshot: dict | None = Field(default=None, sa_column=Column(JSONB, nullable=True))
    sap_gr_snapshot: dict | None = Field(default=None, sa_column=Column(JSONB, nullable=True))

    # SAP connector used — null when triggered manually without an agent config
    sap_connector_id: UUID | None = Field(
        default=None,
        sa_column=Column(Uuid, ForeignKey("connector_catalogue.id", ondelete="SET NULL"), nullable=True),
    )
    # Snapshot of tolerance config used
    tolerance_config: dict | None = Field(default=None, sa_column=Column(JSONB, nullable=True))

    # Set when a reviewer has reviewed this match result
    reviewed_by: UUID | None = Field(
        default=None,
        sa_column=Column(Uuid, ForeignKey("user.id", ondelete="SET NULL"), nullable=True),
    )
    reviewed_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True), nullable=True))

    created_at: datetime = Field(default_factory=_utcnow, sa_column=Column(DateTime(timezone=True), nullable=False, index=True))
    updated_at: datetime = Field(default_factory=_utcnow, sa_column=Column(DateTime(timezone=True), nullable=False))

    __table_args__ = (
        CheckConstraint("match_type IN ('2way','3way')", name="ck_idp_match_results_match_type"),
        CheckConstraint(
            "overall_status IN ('full_match','partial_match','mismatch','error')",
            name="ck_idp_match_results_overall_status",
        ),
        Index("ix_idp_match_results_document_id", "document_id"),
    )


# ──────────────────────── idp_match_discrepancies ────────────────────────
class IdpMatchDiscrepancy(SQLModel, table=True):  # type: ignore[call-arg]
    """One row per compared field that has a recorded value (match or not)."""

    __tablename__ = "idp_match_discrepancies"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    match_result_id: UUID = Field(
        sa_column=Column(Uuid, ForeignKey("idp_match_results.id", ondelete="CASCADE"), nullable=False, index=True)
    )
    document_id: UUID = Field(
        sa_column=Column(Uuid, ForeignKey("idp_documents.id", ondelete="CASCADE"), nullable=False, index=True)
    )

    # "header" | "line_item"
    scope: str = Field(sa_column=Column(String(20), nullable=False))
    # null for header-level fields
    line_item_index: int | None = Field(default=None, sa_column=Column(Integer, nullable=True))

    # Human-readable field identifier, e.g. "vendor_name", "total_amount", "line[0].quantity"
    field_name: str = Field(sa_column=Column(String(150), nullable=False))

    invoice_value: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    po_value: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    gr_value: str | None = Field(default=None, sa_column=Column(Text, nullable=True))

    variance_pct: float | None = Field(default=None, sa_column=Column(Float, nullable=True))
    # "full_match" | "partial_match" | "mismatch"
    status: str = Field(sa_column=Column(String(20), nullable=False))

    # Reviewer can accept a discrepancy with an optional note
    is_accepted: bool = Field(default=False, sa_column=Column(Boolean, nullable=False, server_default="false"))
    accepted_by: UUID | None = Field(
        default=None,
        sa_column=Column(Uuid, ForeignKey("user.id", ondelete="SET NULL"), nullable=True),
    )
    accepted_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True), nullable=True))
    reviewer_note: str | None = Field(default=None, sa_column=Column(Text, nullable=True))

    created_at: datetime = Field(default_factory=_utcnow, sa_column=Column(DateTime(timezone=True), nullable=False))

    __table_args__ = (
        CheckConstraint("scope IN ('header','line_item')", name="ck_idp_match_disc_scope"),
        CheckConstraint(
            "status IN ('full_match','partial_match','mismatch')",
            name="ck_idp_match_disc_status",
        ),
        Index("ix_idp_match_disc_match_result_id", "match_result_id"),
        Index("ix_idp_match_disc_document_id", "document_id"),
    )


# ─────────────────────── idp_agent_match_configs ─────────────────────────
class IdpAgentMatchConfig(SQLModel, table=True):  # type: ignore[call-arg]
    """Per-IDP-agent SAP matching configuration. One row per agent (unique constraint)."""

    __tablename__ = "idp_agent_match_configs"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    agent_id: UUID = Field(
        sa_column=Column(Uuid, ForeignKey("idp_agents.id", ondelete="CASCADE"), nullable=False, unique=True, index=True)
    )

    match_enabled: bool = Field(default=False, sa_column=Column(Boolean, nullable=False, server_default="false"))
    # "2way" | "3way"
    match_type: str = Field(default="2way", sa_column=Column(String(10), nullable=False, server_default="2way"))

    # Percentage tolerances (e.g. 2.0 = within 2% is acceptable)
    amount_tolerance_pct: float = Field(default=2.0, sa_column=Column(Float, nullable=False, server_default="2.0"))
    quantity_tolerance_pct: float = Field(default=0.0, sa_column=Column(Float, nullable=False, server_default="0.0"))
    unit_price_tolerance_pct: float = Field(default=2.0, sa_column=Column(Float, nullable=False, server_default="2.0"))
    # SequenceMatcher ratio 0-1 (e.g. 0.85 = 85% similarity required)
    vendor_name_fuzzy_threshold: float = Field(default=0.85, sa_column=Column(Float, nullable=False, server_default="0.85"))

    sap_connector_id: UUID | None = Field(
        default=None,
        sa_column=Column(Uuid, ForeignKey("connector_catalogue.id", ondelete="SET NULL"), nullable=True),
    )

    created_at: datetime = Field(default_factory=_utcnow, sa_column=Column(DateTime(timezone=True), nullable=False))
    updated_at: datetime = Field(default_factory=_utcnow, sa_column=Column(DateTime(timezone=True), nullable=False))

    __table_args__ = (
        CheckConstraint("match_type IN ('2way','3way')", name="ck_idp_agent_match_configs_match_type"),
    )
