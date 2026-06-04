"""merge heads pre-idp

Revision ID: e7a90c09c765
Revises: a9b8c7d6e5f3, kb1m2n3o4p5q
Create Date: 2026-06-04 13:20:48.950523

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel
from sqlalchemy.engine.reflection import Inspector
from agentcore.utils import migration


# revision identifiers, used by Alembic.
revision: str = 'e7a90c09c765'
down_revision: Union[str, None] = ('a9b8c7d6e5f3', 'kb1m2n3o4p5q')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    pass


def downgrade() -> None:
    conn = op.get_bind()
    pass
