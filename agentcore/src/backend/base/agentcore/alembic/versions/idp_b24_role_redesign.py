"""IDP role redesign — add submit_documents, approve_documents, view_idp_analytics permissions
and seed the document_approver system role. Also updates existing roles with correct IDP grants
and fixes display names for leader_executive, business_user, and consumer to reflect IDP personas.

Revision ID: idp_b24_role_redesign
Revises: idp_b23_doctype_skipped
Create Date: 2026-06-12
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op


revision: str = "idp_b24_role_redesign"
down_revision: Union[str, Sequence[str], None] = "idp_b23_doctype_skipped"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_NEW_PERMISSIONS = [
    ("submit_documents", "Submit Documents", "Upload and submit documents for IDP processing"),
    ("approve_documents", "Approve Documents", "Final approval of reviewed and corrected documents"),
    ("view_idp_analytics", "View IDP Analytics", "View IDP performance metrics, processing stats, and audit trail"),
]

_NEW_PERMISSION_KEYS = [p[0] for p in _NEW_PERMISSIONS]

# Which roles get which new IDP permissions (root gets all automatically).
_NEW_ROLE_GRANTS = {
    "super_admin": ["submit_documents", "approve_documents", "view_idp_analytics"],
    "department_admin": ["submit_documents", "approve_documents", "view_idp_analytics"],
    "developer": ["submit_documents"],
    "business_user": ["view_idp", "review_docs"],
    "consumer": ["view_idp", "submit_documents"],
    "leader_executive": ["view_idp", "view_idp_analytics"],
}

# Grant previous IDP perms that were missing for some roles (idempotent ON CONFLICT).
_BACKFILL_GRANTS = {
    "business_user": ["view_idp", "review_docs"],
    "consumer": ["view_idp", "submit_documents"],
    "leader_executive": ["view_idp", "view_idp_analytics"],
}

# document_approver: new system role for the final approval gate.
_APPROVER_ROLE = {
    "name": "document_approver",
    "display_name": "Document Approver",
    "description": "Final approval gate: validates reviewed documents before output to downstream systems",
}
_APPROVER_PERMISSIONS = ["view_idp", "review_docs", "approve_documents"]

# Display name updates for repurposed roles.
_DISPLAY_NAME_UPDATES = {
    "leader_executive": ("Auditor / Executive Viewer", "Read-only IDP reporting, analytics, and audit trail"),
    "business_user": ("Document Reviewer", "Reviews and corrects AI-extracted data via HITL workflow"),
    "consumer": ("Document Submitter", "Uploads documents for IDP processing and tracks their status"),
    "developer": ("IDP Configurator", "Creates document types, extraction field configs, and IDP agent pipelines"),
}


def upgrade() -> None:
    conn = op.get_bind()

    # 1. Insert the three new permission rows (idempotent).
    insert_perm = sa.text(
        "INSERT INTO permission (id, key, name, description, category, is_system, created_at, updated_at) "
        "VALUES (gen_random_uuid(), :key, :name, :description, 'IDP', true, now(), now()) "
        "ON CONFLICT (key) DO NOTHING"
    )
    for key, name, description in _NEW_PERMISSIONS:
        conn.execute(insert_perm, {"key": key, "name": name, "description": description})

    # 2. Create document_approver system role (idempotent).
    conn.execute(
        sa.text(
            "INSERT INTO role (id, name, display_name, description, is_system, is_active, created_at, updated_at) "
            "VALUES (gen_random_uuid(), :name, :display_name, :description, true, true, now(), now()) "
            "ON CONFLICT (name) DO NOTHING"
        ),
        _APPROVER_ROLE,
    )

    # 3. Grant permissions to all roles (idempotent ON CONFLICT).
    grant = sa.text(
        "INSERT INTO role_permission (id, role_id, permission_id, created_at, updated_at) "
        "SELECT gen_random_uuid(), r.id, p.id, now(), now() "
        "FROM role r JOIN permission p ON p.key IN :keys "
        "WHERE r.name = :role_name "
        "ON CONFLICT (role_id, permission_id) DO NOTHING"
    ).bindparams(sa.bindparam("keys", expanding=True))

    # New permission grants for existing roles.
    for role_name, keys in _NEW_ROLE_GRANTS.items():
        conn.execute(grant, {"role_name": role_name, "keys": keys})

    # Grant document_approver its permissions.
    conn.execute(grant, {"role_name": "document_approver", "keys": _APPROVER_PERMISSIONS})

    # 4. Update display_name and description for repurposed roles.
    update_role = sa.text(
        "UPDATE role SET display_name = :display_name, description = :description, updated_at = now() "
        "WHERE name = :name AND is_system = true"
    )
    for role_name, (display_name, description) in _DISPLAY_NAME_UPDATES.items():
        conn.execute(update_role, {"name": role_name, "display_name": display_name, "description": description})


def downgrade() -> None:
    conn = op.get_bind()

    # Revert display names.
    _ORIGINAL_DISPLAY_NAMES = {
        "leader_executive": ("Leader / Executive", "Executive-level read access to dashboards"),
        "business_user": ("Business User", "Business user with access to published agents and dashboards"),
        "consumer": ("Consumer", "Consumer with access to published agents"),
        "developer": ("Developer", "Developer with full access to build and deploy agents"),
    }
    update_role = sa.text(
        "UPDATE role SET display_name = :display_name, description = :description, updated_at = now() "
        "WHERE name = :name AND is_system = true"
    )
    for role_name, (display_name, description) in _ORIGINAL_DISPLAY_NAMES.items():
        conn.execute(update_role, {"name": role_name, "display_name": display_name, "description": description})

    # Remove document_approver role (cascade removes role_permissions via FK or delete manually).
    conn.execute(
        sa.text(
            "DELETE FROM role_permission rp USING role r "
            "WHERE rp.role_id = r.id AND r.name = 'document_approver'"
        )
    )
    conn.execute(sa.text("DELETE FROM role WHERE name = 'document_approver' AND is_system = true"))

    # Remove new permissions (also removes role_permission rows).
    conn.execute(
        sa.text(
            "DELETE FROM role_permission rp USING permission p "
            "WHERE rp.permission_id = p.id AND p.key IN :keys"
        ).bindparams(sa.bindparam("keys", expanding=True)),
        {"keys": _NEW_PERMISSION_KEYS},
    )
    conn.execute(
        sa.text("DELETE FROM permission WHERE key IN :keys").bindparams(
            sa.bindparam("keys", expanding=True)
        ),
        {"keys": _NEW_PERMISSION_KEYS},
    )
