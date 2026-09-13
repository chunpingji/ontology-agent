"""Keep durable progress watermarks across document-analysis worker replacement."""

import sqlalchemy as sa

from alembic import op

revision = "0037_doc_analysis_liveness"
down_revision = "0036_report_expert_opinions"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "document_analysis_executions",
        sa.Column("recovery_event_head", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "document_analysis_executions",
        sa.Column("recovery_attempts", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "document_analysis_executions",
        sa.Column("last_progress_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade():
    op.drop_column("document_analysis_executions", "last_progress_at")
    op.drop_column("document_analysis_executions", "recovery_attempts")
    op.drop_column("document_analysis_executions", "recovery_event_head")
