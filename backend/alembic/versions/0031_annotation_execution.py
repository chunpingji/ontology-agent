"""One durable owner for background, continue and retry recognition."""

import sqlalchemy as sa

from alembic import op

revision = "0031_annotation_execution"
down_revision = "0030_model_snapshots"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "annotation_executions",
        sa.Column("job_id", sa.Uuid(), sa.ForeignKey("extraction_jobs.id", ondelete="CASCADE"),
                  primary_key=True),
        sa.Column("run_id", sa.String(32), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("actor", sa.String(100), nullable=False),
        sa.Column("options", sa.JSON(), nullable=False),
        sa.Column("progress", sa.JSON(), nullable=False),
        sa.Column("pause_requested", sa.Boolean(), nullable=False),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade():
    op.drop_table("annotation_executions")
