"""add model registry cost limits

Revision ID: add_model_registry_cost_limits
Revises: idp_g3_dedup_unique_idx
Create Date: 2026-07-01
"""

from __future__ import annotations
from typing import Union
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "add_model_registry_cost_limits"
down_revision: Union[str, Sequence[str], None] = "idp_g3_dedup_unique_idx"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _table_exists(bind, table_name: str) -> bool:
    return table_name in sa.inspect(bind).get_table_names()


def _column_exists(bind, table_name: str, column_name: str) -> bool:
    if not _table_exists(bind, table_name):
        return False
    cols = [c["name"] for c in sa.inspect(bind).get_columns(table_name)]
    return column_name in cols


def upgrade() -> None:
    bind = op.get_bind()
    if _table_exists(bind, "model_registry"):
        if not _column_exists(bind, "model_registry", "cost_limit"):
            op.add_column("model_registry", sa.Column("cost_limit", sa.Float(), nullable=True))
        if not _column_exists(bind, "model_registry", "current_cost"):
            op.add_column("model_registry", sa.Column("current_cost", sa.Float(), nullable=False, server_default="0.0"))


def downgrade() -> None:
    bind = op.get_bind()
    if _table_exists(bind, "model_registry"):
        if _column_exists(bind, "model_registry", "cost_limit"):
            op.drop_column("model_registry", "cost_limit")
        if _column_exists(bind, "model_registry", "current_cost"):
            op.drop_column("model_registry", "current_cost")
