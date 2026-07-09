"""add idp_documents.run_env / run_version

Lets an IDP document declare WHICH agent version it must run against, so the background pipeline can
resolve the PUBLISHED snapshot (agent_deployment_uat|prod.agent_snapshot) instead of always executing
the draft `agent.data`. NULL / 'dev' preserves the previous behavior (Playground, connectors, legacy
documents), so this migration is backwards compatible.
"""
from alembic import op
import sqlalchemy as sa

revision = "idp_h4_document_run_env_version"
down_revision = "idp_h3_document_route_label"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("idp_documents", sa.Column("run_env", sa.String(length=10), nullable=True))
    op.add_column("idp_documents", sa.Column("run_version", sa.String(length=20), nullable=True))


def downgrade() -> None:
    op.drop_column("idp_documents", "run_version")
    op.drop_column("idp_documents", "run_env")
