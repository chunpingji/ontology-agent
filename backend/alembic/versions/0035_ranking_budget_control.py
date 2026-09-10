"""Persist the audited per-run ranking budget control independently of input identity."""

import sqlalchemy as sa

from alembic import op

revision = "0035_ranking_budget_control"
down_revision = "0034_model_request_run_id"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "document_analysis_runs",
        sa.Column("ranking_budget_enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
    )


def downgrade():
    with op.batch_alter_table("document_analysis_runs") as batch:
        batch.drop_column("ranking_budget_enabled")
