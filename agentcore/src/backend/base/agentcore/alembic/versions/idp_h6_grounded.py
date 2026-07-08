"""idp: per-field `grounded` — was the extracted value actually found in the document?

`confidence_score` now stores the model's own per-field confidence. Whether the value appears in the
document is a separate, independent signal, so it needs its own column rather than being multiplied into
the score (which is what the old OCR-substring formula did).

Nullable on purpose, and NOT backfilled:

  True  = the value was located in the token stream
  False = a token stream existed and the value was not in it  -> hallucination shape
  NULL  = never checked (vision path, or a row written before this migration)

Backfilling NULL -> False would libel every historical row as a hallucination; NULL -> True would launder
them as verified. Rows extracted before this change carry an OCR-derived `confidence_score` and no
grounding, and there is no way to recover the latter without re-running extraction. NULL is the truth.

Revision ID: idp_h6_grounded
Revises: idp_h5_api_source
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "idp_h6_grounded"
down_revision: Union[str, Sequence[str], None] = "idp_h5_api_source"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLES = ("idp_extracted_headers", "idp_extracted_line_items")


def _has_column(table: str, column: str) -> bool:
    bind = op.get_bind()
    return column in {c["name"] for c in sa.inspect(bind).get_columns(table)}


def upgrade() -> None:
    for table in _TABLES:
        if not _has_column(table, "grounded"):
            op.add_column(table, sa.Column("grounded", sa.Boolean(), nullable=True))


def downgrade() -> None:
    for table in _TABLES:
        if _has_column(table, "grounded"):
            op.drop_column(table, "grounded")
