"""Add visibility column to idp_field_configurations.

Adds a VARCHAR(10) ``visibility`` column that controls who can see a field
configuration beyond the template / org-membership rules:

  private  – only the creator (created_by) can see it
  org      – all members of the owning org can see it  (default / backward-compat)
  dept     – all members of the owning department can see it

Existing rows are backfilled to 'org' (preserves current behaviour).
A CHECK constraint enforces the allowed values.

Revision ID: idp_b21_field_config_visibility
Revises: idp_b20_invoice_prompt_fields
Create Date: 2026-06-11
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = "idp_b21_field_config_visibility"
down_revision: Union[str, Sequence[str], None] = "idp_b20_invoice_prompt_fields"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _column_exists(conn: sa.engine.Connection, table: str, column: str) -> bool:
    result = conn.execute(
        sa.text(
            "SELECT 1 FROM information_schema.columns "
            "WHERE table_name = :table AND column_name = :column LIMIT 1"
        ),
        {"table": table, "column": column},
    )
    return result.fetchone() is not None


def upgrade() -> None:
    conn = op.get_bind()

    if not _column_exists(conn, "idp_field_configurations", "visibility"):
        op.add_column(
            "idp_field_configurations",
            sa.Column(
                "visibility",
                sa.String(10),
                nullable=False,
                server_default="org",
            ),
        )
        # Backfill existing rows to 'org' (same as default; belt-and-suspenders)
        conn.execute(
            sa.text("UPDATE idp_field_configurations SET visibility = 'org' WHERE visibility IS NULL OR visibility = ''")
        )
        op.create_check_constraint(
            "ck_idp_field_config_visibility",
            "idp_field_configurations",
            "visibility IN ('private','org','dept')",
        )


def downgrade() -> None:
    try:
        op.drop_constraint("ck_idp_field_config_visibility", "idp_field_configurations", type_="check")
    except Exception:
        pass
    op.drop_column("idp_field_configurations", "visibility")
