"""Add doc_type to idp_field_configurations and skipped status to idp_documents.

- idp_field_configurations.doc_type (VARCHAR 100, nullable): stores the canonical
  document type this config handles (e.g. "Invoice"). Populated automatically when
  global templates are seeded and inherited when configs are cloned.
- idp_documents.status: adds 'skipped' to the allowed values so the Document
  Classifier + AI Field Extractor can mark documents that have no matching
  field config as skipped rather than failed.

Revision ID: idp_b23_doctype_skipped
Revises: idp_b22_split_status
Create Date: 2026-06-12
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = "idp_b23_doctype_skipped"
down_revision: Union[str, Sequence[str], None] = "idp_b22_split_status"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_DOC_TABLE = "idp_documents"
_CFG_TABLE = "idp_field_configurations"

_WITH_SKIPPED = (
    "status IN ('queued','processing','extracted','pending_review',"
    "'auto_approved','reviewed','failed','split','skipped')"
)
_WITHOUT_SKIPPED = (
    "status IN ('queued','processing','extracted','pending_review',"
    "'auto_approved','reviewed','failed','split')"
)
_CONSTRAINT_NAMES = ("ck_idp_documents_ck_idp_documents_status", "ck_idp_documents_status")


def _drop_status_constraint() -> None:
    for name in _CONSTRAINT_NAMES:
        op.execute(f"ALTER TABLE {_DOC_TABLE} DROP CONSTRAINT IF EXISTS {name}")


def upgrade() -> None:
    # 1. Add doc_type column to field configurations
    op.add_column(
        _CFG_TABLE,
        sa.Column("doc_type", sa.String(100), nullable=True),
    )

    # 2. Backfill doc_type from name for global templates (is_template = TRUE)
    op.execute(
        f"UPDATE {_CFG_TABLE} SET doc_type = name WHERE is_template = TRUE AND doc_type IS NULL"
    )

    # 3. Expand status CHECK to include 'skipped'
    _drop_status_constraint()
    op.create_check_constraint("ck_idp_documents_status", _DOC_TABLE, _WITH_SKIPPED)


def downgrade() -> None:
    # Remove doc_type column
    op.drop_column(_CFG_TABLE, "doc_type")

    # Revert status CHECK to exclude 'skipped'
    _drop_status_constraint()
    op.create_check_constraint("ck_idp_documents_status", _DOC_TABLE, _WITHOUT_SKIPPED)
