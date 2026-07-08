"""H5: allow source='api' on idp_documents

Documents submitted by calling a PUBLISHED agent over ``POST /api/run/{agent_id}`` (with a base64
attachment) are neither an interactive 'upload' nor connector ingest. Giving them their own source keeps
report/metric provenance honest — API traffic is distinguishable from UI uploads without digging into the
``source_metadata`` JSONB. 'other' is kept for backward compatibility.

Revision ID: idp_h5_api_source
Revises: idp_h4_document_run_env_version
Create Date: 2026-07-08

"""
from __future__ import annotations

from collections.abc import Sequence
from typing import Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "idp_h5_api_source"
down_revision: Union[str, Sequence[str], None] = "idp_h4_document_run_env_version"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_NEW = "source IN ('upload','mail_connector','sharepoint','onedrive','api','other')"
_OLD = "source IN ('upload','mail_connector','sharepoint','onedrive','other')"


def upgrade() -> None:
    op.drop_constraint("ck_idp_documents_source", "idp_documents", type_="check")
    op.create_check_constraint("ck_idp_documents_source", "idp_documents", _NEW)


def downgrade() -> None:
    # Fold API documents back into 'other' BEFORE narrowing the constraint, or the CREATE fails.
    op.execute("UPDATE idp_documents SET source='other' WHERE source='api'")
    op.drop_constraint("ck_idp_documents_source", "idp_documents", type_="check")
    op.create_check_constraint("ck_idp_documents_source", "idp_documents", _OLD)
