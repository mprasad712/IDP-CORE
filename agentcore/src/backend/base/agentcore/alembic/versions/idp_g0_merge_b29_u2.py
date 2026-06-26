"""merge idp_b29 and u2_all_timestamps heads

Joins the two open alembic heads so later IDP revisions (G4 source constraint, G3 dedup
unique index) have a single linear parent. No schema change.

Revision ID: idp_g0_merge_b29_u2
Revises: idp_b29_add_matching_tables, u2_all_timestamps_with_timezone
Create Date: 2026-06-26

"""
from __future__ import annotations

from collections.abc import Sequence
from typing import Union

# revision identifiers, used by Alembic.
revision: str = "idp_g0_merge_b29_u2"
down_revision: Union[str, Sequence[str], None] = (
    "idp_b29_add_matching_tables",
    "u2_all_timestamps_with_timezone",
)
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
