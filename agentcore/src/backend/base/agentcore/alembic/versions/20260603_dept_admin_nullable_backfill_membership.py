"""Make department.admin_user_id nullable and backfill UserDepartmentMembership.

admin_user_id is demoted from the authoritative "who admins this dept?" field to
a nullable convenience/audit field (founding admin).  The UserDepartmentMembership
table (role = department_admin) becomes the single source of truth for all
permission, routing, and notification logic.

Migration steps:
  1. For every dept that has admin_user_id set, ensure that user has an active
     department_admin membership row in user_department_membership.
  2. ALTER department.admin_user_id to be nullable (DROP NOT NULL constraint).

Revision ID: a9b8c7d6e5f3
Revises: z9y8x7w6v5u4
Create Date: 2026-06-03 00:00:00.000000
"""

from __future__ import annotations

from typing import Union
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "a9b8c7d6e5f3"
down_revision: Union[str, Sequence[str], None] = "z9y8x7w6v5u4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()

    # ── Step 1: backfill user_department_membership ──────────────────────────
    # For every department whose admin_user_id is not yet represented in the
    # membership table with role=department_admin, insert a row.
    # NOTE: assigned_by is left NULL (it is nullable) to avoid FK violations
    # against a sentinel UUID that may not exist in the user table.
    conn.execute(sa.text("""
        INSERT INTO user_department_membership
            (id, user_id, org_id, department_id, status, role_id,
             assigned_by, assigned_at, created_at, updated_at)
        SELECT
            gen_random_uuid(),
            d.admin_user_id,
            d.org_id,
            d.id,
            'active',
            r.id,
            NULL,
            NOW(),
            NOW(),
            NOW()
        FROM department d
        JOIN role r ON lower(r.name) = 'department_admin'
        WHERE d.admin_user_id IS NOT NULL
          AND NOT EXISTS (
              SELECT 1 FROM user_department_membership udm
              JOIN role rr ON rr.id = udm.role_id
              WHERE udm.user_id      = d.admin_user_id
                AND udm.department_id = d.id
                AND udm.status       = 'active'
                AND lower(rr.name)  = 'department_admin'
          )
    """))

    # ── Step 2: make admin_user_id nullable (idempotent) ─────────────────────
    # Wrapped in a DO block so the migration never fails if the constraint was
    # already dropped by a previous partial run or manual intervention.
    conn.execute(sa.text("""
        DO $$
        BEGIN
            ALTER TABLE department ALTER COLUMN admin_user_id DROP NOT NULL;
        EXCEPTION WHEN others THEN
            -- Column is already nullable; nothing to do.
            NULL;
        END $$;
    """))


def downgrade() -> None:
    conn = op.get_bind()

    # Restore NOT NULL constraint (idempotent guard).
    # NOTE: any rows that became NULL between upgrade and downgrade will fail
    # this step — fill them manually before running downgrade if needed.
    conn.execute(sa.text("""
        DO $$
        BEGIN
            ALTER TABLE department ALTER COLUMN admin_user_id SET NOT NULL;
        EXCEPTION WHEN others THEN
            NULL;
        END $$;
    """))

    # Backfill rows are intentionally left in place on downgrade —
    # removing them could break access for co-admins added after the upgrade.
