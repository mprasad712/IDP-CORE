"""Remove unused global IDP field configuration templates.

Keeps only the 14 approved document types and deletes the rest from the database.
Headers and line items are removed automatically via CASCADE.

Revision ID: idp_b25_remove_unused_templates
Revises: idp_b24_role_redesign
Create Date: 2026-06-12
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = "idp_b25_remove_unused_templates"
down_revision: Union[str, Sequence[str], None] = "idp_b24_role_redesign"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Global templates to remove (not in the approved list)
_REMOVE_NAMES = [
    "Receipt",
    "Bank Statement",
    "Delivery Note",
    "Remittance Advice",
    "Vendor Statement",
    "Wire Transfer Slip",
    "Loan Agreement",
    "Mortgage Document",
    "National ID Card",
    "Tax Return",
    "Tax Certificate",
    "Withholding Certificate",
    "Customs Declaration",
    "Tax Notice",
    "NDA",
    "MSA",
    "MoU",
    "Lease Agreement",
    "Power of Attorney",
    "Policy Document",
    "Claim Form",
    "Bill of Lading",
]


def upgrade() -> None:
    conn = op.get_bind()
    conn.execute(
        sa.text(
            "DELETE FROM idp_field_configurations "
            "WHERE is_template = TRUE AND org_id IS NULL AND name = ANY(:names)"
        ),
        {"names": _REMOVE_NAMES},
    )


def downgrade() -> None:
    # Removed templates are not restored on downgrade — re-run catalogue seeding if needed.
    pass
