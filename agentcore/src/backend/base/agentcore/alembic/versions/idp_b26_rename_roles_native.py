"""Rename role keys to IDP-native names.

Revision ID: idp_b26_roles_native
Revises: idp_b25_remove_unused_templates
Create Date: 2026-06-12

Pure rename - permissions and role assignments do not change.
department_admin key is kept as-is; only its display_name is updated.
"""

from alembic import op
from sqlalchemy import text

revision = "idp_b26_roles_native"
down_revision = "idp_b25_remove_unused_templates"
branch_labels = None
depends_on = None

# (old_key, new_key, new_display_name)
_RENAMES = [
    ("developer", "idp_configurator", "IDP Configurator"),
    ("leader_executive", "idp_auditor", "IDP Auditor"),
    ("business_user", "doc_reviewer", "Document Reviewer"),
    ("document_approver", "doc_approver", "Document Approver"),
    ("consumer", "doc_submitter", "Document Submitter"),
]

# department_admin key stays; only display_name changes.
_DISPLAY_ONLY = [
    ("department_admin", "IDP Admin"),
]


def upgrade() -> None:
    conn = op.get_bind()

    for old_key, new_key, display in _RENAMES:
        conn.execute(
            text("UPDATE role SET name = :new, display_name = :display WHERE name = :old"),
            {"new": new_key, "display": display, "old": old_key},
        )
        conn.execute(
            text('UPDATE "user" SET role = :new WHERE role = :old'),
            {"new": new_key, "old": old_key},
        )

    for key, display in _DISPLAY_ONLY:
        conn.execute(
            text("UPDATE role SET display_name = :display WHERE name = :key"),
            {"display": display, "key": key},
        )


def downgrade() -> None:
    conn = op.get_bind()

    # Reverse the display_name-only update
    for key, _display in _DISPLAY_ONLY:
        original_display = {
            "department_admin": "Department Admin",
        }[key]
        conn.execute(
            text("UPDATE role SET display_name = :display WHERE name = :key"),
            {"display": original_display, "key": key},
        )

    # Reverse key renames (new -> old)
    for old_key, new_key, _display in reversed(_RENAMES):
        original_display = {
            "developer": "IDP Configurator",
            "leader_executive": "Auditor / Executive Viewer",
            "business_user": "Document Reviewer",
            "document_approver": "Document Approver",
            "consumer": "Document Submitter",
        }[old_key]
        conn.execute(
            text("UPDATE role SET name = :old, display_name = :display WHERE name = :new"),
            {"old": old_key, "display": original_display, "new": new_key},
        )
        conn.execute(
            text('UPDATE "user" SET role = :old WHERE role = :new'),
            {"old": old_key, "new": new_key},
        )
