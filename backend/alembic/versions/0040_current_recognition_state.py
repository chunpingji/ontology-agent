"""Current recognition partitions and independent result/request storage."""

import sqlalchemy as sa

from alembic import op

revision = "0040_current_recognition_state"
down_revision = "0039_property_review_repair"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "document_analysis_runs",
        sa.Column("work_version", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column("document_analysis_runs", sa.Column("work_batch_id", sa.String(200)))
    for name in ("request_version", "ranking_version"):
        op.add_column(
            "document_analysis_runs",
            sa.Column(name, sa.Integer(), nullable=False, server_default="0"),
        )
    for table, key in (
        ("document_analysis_current_state", "business_key"),
        ("document_analysis_results", "result_key"),
        ("document_analysis_requests", "request_key"),
    ):
        columns = [
            sa.Column(
                "recognition_run_id",
                sa.Uuid(),
                sa.ForeignKey("document_analysis_runs.recognition_run_id", ondelete="CASCADE"),
                primary_key=True,
            ),
            sa.Column("domain", sa.String(64), primary_key=True),
            sa.Column(key, sa.String(200), primary_key=True),
            sa.Column("content_hash", sa.String(64), nullable=False),
            sa.Column("payload", sa.JSON(), nullable=False),
        ]
        if key == "business_key":
            columns.append(sa.Column("work_version", sa.Integer(), nullable=False))
        op.create_table(table, *columns)
    with op.batch_alter_table("document_analysis_event_batches") as batch:
        batch.alter_column("checkpoint_artifact_id", existing_type=sa.String(200), nullable=True)
        batch.add_column(sa.Column("committed_work_version", sa.Integer()))
    with op.batch_alter_table("document_analysis_property_repairs") as batch:
        batch.alter_column(
            "base_checkpoint_artifact_id", existing_type=sa.String(200), nullable=True
        )
        batch.add_column(sa.Column("base_work_version", sa.Integer()))


def downgrade():
    # A v4 run has no historical checkpoint to invent during downgrade.
    bind = op.get_bind()
    if bind.execute(
        sa.text("SELECT 1 FROM document_analysis_runs WHERE work_version > 0 LIMIT 1")
    ).first():
        raise RuntimeError("current-state runs exist; downgrade would lose their resume state")
    with op.batch_alter_table("document_analysis_property_repairs") as batch:
        batch.drop_column("base_work_version")
        batch.alter_column(
            "base_checkpoint_artifact_id", existing_type=sa.String(200), nullable=False
        )
    with op.batch_alter_table("document_analysis_event_batches") as batch:
        batch.drop_column("committed_work_version")
        batch.alter_column("checkpoint_artifact_id", existing_type=sa.String(200), nullable=False)
    for table in (
        "document_analysis_requests",
        "document_analysis_results",
        "document_analysis_current_state",
    ):
        op.drop_table(table)
    op.drop_column("document_analysis_runs", "work_batch_id")
    op.drop_column("document_analysis_runs", "request_version")
    op.drop_column("document_analysis_runs", "ranking_version")
    op.drop_column("document_analysis_runs", "work_version")
