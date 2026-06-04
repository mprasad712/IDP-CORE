"""Seed IDP permissions + grant them to non-root roles (B03).

Inserts the four IDP permission keys into the ``permission`` table and grants them
to the relevant roles via ``role_permission`` (non-root roles resolve permissions
from the DB, not from ROLE_PERMISSIONS defaults — root gets them automatically).
Idempotent: ON CONFLICT on the unique key / unique (role_id, permission_id) pair.

Revision ID: idp_b03perm01
Revises: ef20f6472bed
Create Date: 2026-06-04 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op


revision: str = "idp_b03perm01"
down_revision: Union[str, Sequence[str], None] = "ef20f6472bed"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_IDP_PERMISSIONS = [
    ("view_idp", "View IDP", "Access IDP pages and read processed documents / field configurations"),
    ("manage_idp", "Manage IDP", "Create, edit and delete IDP field configurations and agents"),
    ("review_docs", "Review Documents", "Review, correct and approve extracted documents (HITL)"),
    ("admin_idp", "Administer IDP", "Full IDP administration (settings, all configs, overrides)"),
]

# Which roles get which IDP permissions (root resolves all permissions automatically,
# so it is intentionally omitted here).
_ROLE_GRANTS = {
    "super_admin": ["view_idp", "manage_idp", "review_docs", "admin_idp"],
    "department_admin": ["view_idp", "manage_idp", "review_docs", "admin_idp"],
    "developer": ["view_idp", "manage_idp", "review_docs"],
}

_ALL_KEYS = [p[0] for p in _IDP_PERMISSIONS]


def upgrade() -> None:
    conn = op.get_bind()

    # 1. Seed the permission rows (bound params; idempotent).
    insert_perm = sa.text(
        "INSERT INTO permission (id, key, name, description, category, is_system, created_at, updated_at) "
        "VALUES (gen_random_uuid(), :key, :name, :description, 'IDP', true, now(), now()) "
        "ON CONFLICT (key) DO NOTHING"
    )
    for key, name, description in _IDP_PERMISSIONS:
        conn.execute(insert_perm, {"key": key, "name": name, "description": description})

    # 2. Grant to non-root roles (only where the role exists; idempotent).
    grant = sa.text(
        "INSERT INTO role_permission (id, role_id, permission_id, created_at, updated_at) "
        "SELECT gen_random_uuid(), r.id, p.id, now(), now() "
        "FROM role r JOIN permission p ON p.key IN :keys "
        "WHERE r.name = :role_name "
        "ON CONFLICT (role_id, permission_id) DO NOTHING"
    ).bindparams(sa.bindparam("keys", expanding=True))
    for role_name, keys in _ROLE_GRANTS.items():
        conn.execute(grant, {"role_name": role_name, "keys": keys})


def downgrade() -> None:
    conn = op.get_bind()
    # Remove grants first (FK), then the permission rows.
    conn.execute(
        sa.text(
            "DELETE FROM role_permission rp USING permission p "
            "WHERE rp.permission_id = p.id AND p.key IN :keys"
        ).bindparams(sa.bindparam("keys", expanding=True)),
        {"keys": _ALL_KEYS},
    )
    conn.execute(
        sa.text("DELETE FROM permission WHERE key IN :keys").bindparams(
            sa.bindparam("keys", expanding=True)
        ),
        {"keys": _ALL_KEYS},
    )
