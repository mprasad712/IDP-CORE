"""Add SAP two-way / three-way match tables.

Three new tables:
  - idp_match_results      — one row per match run per document
  - idp_match_discrepancies — one row per compared field (match or variance)
  - idp_agent_match_configs — per-IDP-agent SAP matching tolerance configuration

Revision ID: idp_b29_add_matching_tables
Revises: idp_b28_catalogue_prompt_fields
Create Date: 2026-06-24
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision: str = "idp_b29_add_matching_tables"
down_revision: Union[str, Sequence[str], None] = "idp_b28_catalogue_prompt_fields"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── idp_match_results ───────────────────────────────────────────────
    op.create_table(
        "idp_match_results",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "document_id",
            UUID(as_uuid=True),
            sa.ForeignKey("idp_documents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("match_type", sa.String(10), nullable=False),
        sa.Column("overall_status", sa.String(20), nullable=False),
        sa.Column("match_score", sa.Float, nullable=False, server_default="0"),
        sa.Column("po_number", sa.String(100), nullable=False),
        sa.Column("gr_document_number", sa.String(100), nullable=True),
        sa.Column("sap_po_snapshot", JSONB, nullable=True),
        sa.Column("sap_gr_snapshot", JSONB, nullable=True),
        sa.Column(
            "sap_connector_id",
            UUID(as_uuid=True),
            sa.ForeignKey("connector_catalogue.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("tolerance_config", JSONB, nullable=True),
        sa.Column(
            "reviewed_by",
            UUID(as_uuid=True),
            sa.ForeignKey("user.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_check_constraint(
        "ck_idp_match_results_match_type",
        "idp_match_results",
        "match_type IN ('2way','3way')",
    )
    op.create_check_constraint(
        "ck_idp_match_results_overall_status",
        "idp_match_results",
        "overall_status IN ('full_match','partial_match','mismatch','error')",
    )
    op.create_index("ix_idp_match_results_document_id", "idp_match_results", ["document_id"])
    op.create_index("ix_idp_match_results_created_at", "idp_match_results", ["created_at"])

    # ── idp_match_discrepancies ──────────────────────────────────────────
    op.create_table(
        "idp_match_discrepancies",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "match_result_id",
            UUID(as_uuid=True),
            sa.ForeignKey("idp_match_results.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "document_id",
            UUID(as_uuid=True),
            sa.ForeignKey("idp_documents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("scope", sa.String(20), nullable=False),
        sa.Column("line_item_index", sa.Integer, nullable=True),
        sa.Column("field_name", sa.String(150), nullable=False),
        sa.Column("invoice_value", sa.Text, nullable=True),
        sa.Column("po_value", sa.Text, nullable=True),
        sa.Column("gr_value", sa.Text, nullable=True),
        sa.Column("variance_pct", sa.Float, nullable=True),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("is_accepted", sa.Boolean, nullable=False, server_default="false"),
        sa.Column(
            "accepted_by",
            UUID(as_uuid=True),
            sa.ForeignKey("user.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reviewer_note", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_check_constraint(
        "ck_idp_match_disc_scope",
        "idp_match_discrepancies",
        "scope IN ('header','line_item')",
    )
    op.create_check_constraint(
        "ck_idp_match_disc_status",
        "idp_match_discrepancies",
        "status IN ('full_match','partial_match','mismatch')",
    )
    op.create_index("ix_idp_match_disc_match_result_id", "idp_match_discrepancies", ["match_result_id"])
    op.create_index("ix_idp_match_disc_document_id", "idp_match_discrepancies", ["document_id"])

    # ── idp_agent_match_configs ──────────────────────────────────────────
    op.create_table(
        "idp_agent_match_configs",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "agent_id",
            UUID(as_uuid=True),
            sa.ForeignKey("idp_agents.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column("match_enabled", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("match_type", sa.String(10), nullable=False, server_default="2way"),
        sa.Column("amount_tolerance_pct", sa.Float, nullable=False, server_default="2.0"),
        sa.Column("quantity_tolerance_pct", sa.Float, nullable=False, server_default="0.0"),
        sa.Column("unit_price_tolerance_pct", sa.Float, nullable=False, server_default="2.0"),
        sa.Column("vendor_name_fuzzy_threshold", sa.Float, nullable=False, server_default="0.85"),
        sa.Column(
            "sap_connector_id",
            UUID(as_uuid=True),
            sa.ForeignKey("connector_catalogue.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_check_constraint(
        "ck_idp_agent_match_configs_match_type",
        "idp_agent_match_configs",
        "match_type IN ('2way','3way')",
    )
    op.create_index("ix_idp_agent_match_configs_agent_id", "idp_agent_match_configs", ["agent_id"])


def downgrade() -> None:
    op.drop_table("idp_match_discrepancies")
    op.drop_table("idp_match_results")
    op.drop_table("idp_agent_match_configs")
