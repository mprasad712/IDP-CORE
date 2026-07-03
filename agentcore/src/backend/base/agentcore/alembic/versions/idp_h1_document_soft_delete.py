"""idp: soft-delete column on idp_documents"""
from alembic import op
import sqlalchemy as sa

revision = "idp_h1_document_soft_delete"
down_revision = "idp_h0_invoice_prompts_backfill"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("idp_documents") as b:
        b.add_column(sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("idp_documents") as b:
        b.drop_column("deleted_at")
