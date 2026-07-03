"""add idp_documents.route_label (Multi-Branch Router)"""
from alembic import op
import sqlalchemy as sa

revision = "idp_h3_document_route_label"
down_revision = "idp_h2_merge_heads"
branch_labels = None
depends_on = None

def upgrade() -> None:
    op.add_column("idp_documents", sa.Column("route_label", sa.String(), nullable=True))

def downgrade() -> None:
    op.drop_column("idp_documents", "route_label")
