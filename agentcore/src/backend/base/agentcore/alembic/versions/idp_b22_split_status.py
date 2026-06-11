"""Allow 'split' status on idp_documents (multi-document parent container).

When the multi-doc split feature (B32) detects that an uploaded file holds several
documents, it materialises one child document + job per sub-document and marks the
PARENT as a container with status ``split`` (it is not itself extracted/reviewed).

The original ``ck_idp_documents_status`` check constraint did not include ``split``,
so committing the parent would violate it. This migration drops and recreates the
constraint with ``split`` added. Existing rows are unaffected.

Revision ID: idp_b22_split_status
Revises: idp_b21_field_config_visibility
Create Date: 2026-06-11
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = "idp_b22_split_status"
down_revision: Union[str, Sequence[str], None] = "idp_b21_field_config_visibility"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLE = "idp_documents"
_WITH_SPLIT = "status IN ('queued','processing','extracted','pending_review','auto_approved','reviewed','failed','split')"
_WITHOUT_SPLIT = "status IN ('queued','processing','extracted','pending_review','auto_approved','reviewed','failed')"

# The runtime constraint name is doubled by SQLAlchemy's naming convention
# (ck_%(table_name)s_%(constraint_name)s applied to an already-prefixed name); older/fresh
# DBs may carry either form, so drop both defensively before recreating.
_NAMES = ("ck_idp_documents_ck_idp_documents_status", "ck_idp_documents_status")


def _drop_existing() -> None:
    for name in _NAMES:
        try:
            op.drop_constraint(name, _TABLE, type_="check")
        except Exception:
            pass


def upgrade() -> None:
    _drop_existing()
    op.create_check_constraint("ck_idp_documents_status", _TABLE, _WITH_SPLIT)


def downgrade() -> None:
    _drop_existing()
    op.create_check_constraint("ck_idp_documents_status", _TABLE, _WITHOUT_SPLIT)
