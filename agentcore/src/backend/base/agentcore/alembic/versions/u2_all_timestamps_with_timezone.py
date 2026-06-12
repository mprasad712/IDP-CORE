"""Convert all remaining TIMESTAMP WITHOUT TIME ZONE columns to WITH TIME ZONE.

All existing values are treated as UTC (which they are, given the app always
writes datetime.now(timezone.utc)), so AT TIME ZONE 'UTC' is safe for every column.

Revision ID: u2_all_timestamps_with_timezone
Revises: u1_user_timestamps_with_timezone
Create Date: 2026-06-12
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "u2_all_timestamps_with_timezone"
down_revision: Union[str, None] = "u1_user_timestamps_with_timezone"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TZ = sa.DateTime(timezone=True)
_NO_TZ = sa.DateTime()
_CAST = "AT TIME ZONE 'UTC'"


def _up(table: str, *cols: tuple[str, bool]) -> None:
    """Alter columns to TIMESTAMPTZ. cols = (name, nullable) pairs."""
    for col, nullable in cols:
        op.alter_column(
            table, col,
            type_=_TZ,
            existing_type=_NO_TZ,
            existing_nullable=nullable,
            postgresql_using=f"{col} {_CAST}",
        )


def _down(table: str, *cols: tuple[str, bool]) -> None:
    for col, nullable in cols:
        op.alter_column(
            table, col,
            type_=_NO_TZ,
            existing_type=_TZ,
            existing_nullable=nullable,
            postgresql_using=f"{col} {_CAST}",
        )


def upgrade() -> None:
    _up("agent",              ("updated_at", True))
    _up("agent_api_key",      ("created_at", False), ("last_used_at", True), ("expires_at", True))
    _up("agent_registry",     ("listed_at", False), ("created_at", False), ("updated_at", False))
    _up("agent_registry_rating", ("created_at", False), ("updated_at", False))
    _up("approval_request",   ("requested_at", False), ("reviewed_at", True), ("created_at", False), ("updated_at", False))
    _up("agent_deployment_uat",  ("deployed_at", False), ("created_at", False), ("updated_at", False))
    _up("agent_deployment_prod", ("deployed_at", False), ("created_at", False), ("updated_at", False))
    _up("dataset",            ("created_at", False), ("updated_at", False))
    _up("dataset_item",       ("created_at", False), ("updated_at", False))
    _up("dataset_run",        ("created_at", False), ("updated_at", False))
    _up("department",         ("created_at", False), ("updated_at", False))
    _up("file",               ("created_at", False), ("updated_at", False))
    _up("help_support_question", ("created_at", False), ("updated_at", False))
    _up("mcp_registry",       ("created_at", False), ("updated_at", False))
    _up("teams_app",          ("created_at", False), ("updated_at", False))


def downgrade() -> None:
    _down("teams_app",          ("created_at", False), ("updated_at", False))
    _down("mcp_registry",       ("created_at", False), ("updated_at", False))
    _down("help_support_question", ("created_at", False), ("updated_at", False))
    _down("file",               ("created_at", False), ("updated_at", False))
    _down("department",         ("created_at", False), ("updated_at", False))
    _down("dataset_run",        ("created_at", False), ("updated_at", False))
    _down("dataset_item",       ("created_at", False), ("updated_at", False))
    _down("dataset",            ("created_at", False), ("updated_at", False))
    _down("agent_deployment_prod", ("deployed_at", False), ("created_at", False), ("updated_at", False))
    _down("agent_deployment_uat",  ("deployed_at", False), ("created_at", False), ("updated_at", False))
    _down("approval_request",   ("requested_at", False), ("reviewed_at", True), ("created_at", False), ("updated_at", False))
    _down("agent_registry_rating", ("created_at", False), ("updated_at", False))
    _down("agent_registry",     ("listed_at", False), ("created_at", False), ("updated_at", False))
    _down("agent_api_key",      ("created_at", False), ("last_used_at", True), ("expires_at", True))
    _down("agent",              ("updated_at", True))
