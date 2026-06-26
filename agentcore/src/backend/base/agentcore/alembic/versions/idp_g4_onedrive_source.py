"""G4: allow source='onedrive' on idp_documents

Adds 'onedrive' to the ck_idp_documents_source CHECK constraint so OneDrive-ingested documents
can be stored with their true source (instead of the generic 'other'), which makes report
provenance show "OneDrive" rather than "other". 'other' is kept for backward compatibility.

Revision ID: idp_g4_onedrive_source
Revises: idp_g0_merge_b29_u2
Create Date: 2026-06-26

"""
from __future__ import annotations

from collections.abc import Sequence
from typing import Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "idp_g4_onedrive_source"
down_revision: Union[str, Sequence[str], None] = "idp_g0_merge_b29_u2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_NEW = "source IN ('upload','mail_connector','sharepoint','onedrive','other')"
_OLD = "source IN ('upload','mail_connector','sharepoint','other')"


def upgrade() -> None:
    op.drop_constraint("ck_idp_documents_source", "idp_documents", type_="check")
    op.create_check_constraint("ck_idp_documents_source", "idp_documents", _NEW)
    # Bridge any pre-existing OneDrive docs that were stored as 'other' (none expected locally).
    op.execute(
        "UPDATE idp_documents SET source='onedrive' "
        "WHERE source='other' AND source_metadata->>'dedup_key' LIKE 'onedrive:%'"
    )


def downgrade() -> None:
    op.execute("UPDATE idp_documents SET source='other' WHERE source='onedrive'")
    op.drop_constraint("ck_idp_documents_source", "idp_documents", type_="check")
    op.create_check_constraint("ck_idp_documents_source", "idp_documents", _OLD)
