"""merge heads: unify basu (idp review-polish/soft-delete) + ather (model cost-limits, match-index rename)

Collapses the three migration heads that branched independently off
``idp_g3_dedup_unique_idx`` into a single head so ``alembic upgrade head`` and
``alembic revision --autogenerate`` resolve unambiguously:

  - idp_h1_document_soft_delete       (backend-basu: idp_h0 invoice prompts -> idp_h1 soft-delete)
  - add_model_registry_cost_limits    (backend-ather: model registry cost limits)
  - 66820c73143d                      (backend-ather: rename idp match indexes/constraints)

This is a no-op merge revision (no schema change of its own); it exists purely to
join the three lineages under one head.
"""
from alembic import op  # noqa: F401
import sqlalchemy as sa  # noqa: F401


revision = "idp_h2_merge_heads"
down_revision = (
    "idp_h1_document_soft_delete",
    "add_model_registry_cost_limits",
    "66820c73143d",
)
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
