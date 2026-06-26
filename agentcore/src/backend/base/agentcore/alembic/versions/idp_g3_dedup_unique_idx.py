"""G3: partial-unique index on (agent_id, dedup_key) for idp_documents

Makes connector dedup race-safe at the DB level: two concurrent pollers for the SAME agent can
both pass the app-level SELECT check, but only one INSERT can win — the other raises IntegrityError,
which the ingest paths (email/SharePoint/OneDrive) already catch, roll back, clean up, and retry
(the retry then skips via the existing dedup SELECT). The index is PARTIAL (only rows that carry a
non-empty dedup_key — uploads have none) and an EXPRESSION index on source_metadata->>'dedup_key',
which alembic autogenerate does not reflect, so `alembic check` stays clean.

Built CONCURRENTLY (no write lock) inside an autocommit block. Refuses to run if duplicate
(agent_id, dedup_key) groups already exist — it does NOT delete data; resolve duplicates first.

Revision ID: idp_g3_dedup_unique_idx
Revises: idp_g4_onedrive_source
Create Date: 2026-06-26

"""
from __future__ import annotations

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "idp_g3_dedup_unique_idx"
down_revision: Union[str, Sequence[str], None] = "idp_g4_onedrive_source"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_INDEX = "uq_idp_documents_agent_dedup_key"


def upgrade() -> None:
    conn = op.get_bind()
    dupes = conn.execute(
        sa.text(
            "SELECT count(*) FROM ("
            "  SELECT agent_id FROM idp_documents"
            "  WHERE source_metadata ? 'dedup_key' AND source_metadata->>'dedup_key' <> ''"
            "  GROUP BY agent_id, (source_metadata->>'dedup_key')"
            "  HAVING count(*) > 1"
            ") d"
        )
    ).scalar()
    if dupes:
        raise RuntimeError(
            f"Refusing to create {_INDEX}: {dupes} duplicate (agent_id, dedup_key) group(s) exist "
            "in idp_documents. Resolve duplicates first, then re-run this migration."
        )
    # CONCURRENTLY cannot run inside a transaction → autocommit block.
    with op.get_context().autocommit_block():
        op.execute(
            f"CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS {_INDEX} "
            "ON idp_documents (agent_id, ((source_metadata->>'dedup_key'))) "
            "WHERE source_metadata ? 'dedup_key' AND source_metadata->>'dedup_key' <> ''"
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {_INDEX}")
