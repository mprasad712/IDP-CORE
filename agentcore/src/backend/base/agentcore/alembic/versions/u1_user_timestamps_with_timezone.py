"""Convert user timestamp columns to TIMESTAMP WITH TIME ZONE.

create_at, updated_at, and last_login_at were created as TIMESTAMP WITHOUT TIME ZONE
but the application writes timezone-aware datetimes (UTC), causing asyncpg errors.

Revision ID: u1_user_timestamps_with_timezone
Revises: idp_b23_doctype_skipped
Create Date: 2026-06-12
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "u1_user_timestamps_with_timezone"
down_revision: Union[str, None] = "idp_b23_doctype_skipped"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column(
        "user", "create_at",
        type_=sa.DateTime(timezone=True),
        existing_type=sa.DateTime(),
        existing_nullable=False,
        postgresql_using="create_at AT TIME ZONE 'UTC'",
    )
    op.alter_column(
        "user", "updated_at",
        type_=sa.DateTime(timezone=True),
        existing_type=sa.DateTime(),
        existing_nullable=False,
        postgresql_using="updated_at AT TIME ZONE 'UTC'",
    )
    op.alter_column(
        "user", "last_login_at",
        type_=sa.DateTime(timezone=True),
        existing_type=sa.DateTime(),
        existing_nullable=True,
        postgresql_using="last_login_at AT TIME ZONE 'UTC'",
    )


def downgrade() -> None:
    op.alter_column(
        "user", "last_login_at",
        type_=sa.DateTime(),
        existing_type=sa.DateTime(timezone=True),
        existing_nullable=True,
        postgresql_using="last_login_at AT TIME ZONE 'UTC'",
    )
    op.alter_column(
        "user", "updated_at",
        type_=sa.DateTime(),
        existing_type=sa.DateTime(timezone=True),
        existing_nullable=False,
        postgresql_using="updated_at AT TIME ZONE 'UTC'",
    )
    op.alter_column(
        "user", "create_at",
        type_=sa.DateTime(),
        existing_type=sa.DateTime(timezone=True),
        existing_nullable=False,
        postgresql_using="create_at AT TIME ZONE 'UTC'",
    )
