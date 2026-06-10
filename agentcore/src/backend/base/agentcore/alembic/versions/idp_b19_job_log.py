"""Add a flow-log column to idp_processing_jobs.

Adds a nullable JSONB ``log`` column holding the ordered per-step flow-log events
recorded by the document-processing pipeline (B19). Additive, no existing column or
table is modified. Idempotent / guarded: postgres-only, skips if the column exists.

Revision ID: idp_b19_job_log
Revises: idp_b05_merge_heads
Create Date: 2026-06-10 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "idp_b19_job_log"
down_revision: Union[str, Sequence[str], None] = "idp_b05_merge_heads"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLE = "idp_processing_jobs"
_COLUMN = "log"


def _has_column(bind, table: str, column: str) -> bool:
    inspector = sa.inspect(bind)
    if table not in inspector.get_table_names():
        return False
    return any(col["name"] == column for col in inspector.get_columns(table))


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    if _has_column(bind, _TABLE, _COLUMN):
        return
    op.add_column(
        _TABLE,
        sa.Column(_COLUMN, postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    if _has_column(bind, _TABLE, _COLUMN):
        op.drop_column(_TABLE, _COLUMN)
