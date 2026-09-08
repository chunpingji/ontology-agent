"""Global local-model admission and actual HTTP attempt telemetry."""

import sqlalchemy as sa

from alembic import op

revision = "0032_local_model_requests"
down_revision = "0031_annotation_execution"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "local_model_pools",
        sa.Column("pool_id", sa.String(64), primary_key=True),
        sa.Column("capacity", sa.Integer(), nullable=False),
    )
    op.create_table(
        "local_model_requests",
        sa.Column("sequence", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("request_id", sa.String(32), unique=True, nullable=False),
        sa.Column(
            "pool_id", sa.String(64), sa.ForeignKey("local_model_pools.pool_id"), nullable=False
        ),
        sa.Column("logical_call_id", sa.String(32), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("job_id", sa.String(36)),
        sa.Column("run_id", sa.String(32)),
        sa.Column("task_id", sa.String(64)),
        sa.Column("stage", sa.String(64), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("deadline_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("metrics", sa.JSON(), nullable=False),
    )
    op.create_index(
        "ix_local_model_requests_admission",
        "local_model_requests",
        ["pool_id", "status", "sequence"],
    )
    op.create_index("ix_local_model_requests_job", "local_model_requests", ["job_id", "created_at"])


def downgrade():
    op.drop_table("local_model_requests")
    op.drop_table("local_model_pools")
