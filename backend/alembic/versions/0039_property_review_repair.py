"""Persist exact property reviews and bounded run-owned repair operations."""

import sqlalchemy as sa

from alembic import op

revision = "0039_property_review_repair"
down_revision = "0038_property_cardinality"
branch_labels = None
depends_on = None


def _run_column():
    return sa.Column(
        "recognition_run_id", sa.Uuid(),
        sa.ForeignKey("document_analysis_runs.recognition_run_id", ondelete="CASCADE"),
        nullable=False,
    )


def upgrade():
    op.create_table(
        "document_analysis_property_reviews",
        sa.Column("review_id", sa.Uuid(), primary_key=True), _run_column(),
        sa.Column("owner_id", sa.String(200), nullable=False),
        sa.Column("actor_role", sa.String(50), nullable=False),
        sa.Column("request_key", sa.String(200), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("candidate_id", sa.String(200), nullable=False),
        sa.Column("candidate_revision", sa.Integer(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("decision", sa.String(20), nullable=False),
        sa.Column("graph_snapshot_id", sa.String(200), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("receipt", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["recognition_run_id", "candidate_id", "candidate_revision"],
            ["document_analysis_run_candidates.recognition_run_id",
             "document_analysis_run_candidates.candidate_id",
             "document_analysis_run_candidates.revision"],
            ondelete="CASCADE", name="fk_doc_property_review_candidate",
        ),
        sa.UniqueConstraint("recognition_run_id", "request_key",
                            name="uq_doc_property_review_request"),
        sa.UniqueConstraint("recognition_run_id", "candidate_id", "candidate_revision", "revision",
                            name="uq_doc_property_review_revision"),
        sa.CheckConstraint("revision > 0", name="ck_doc_property_review_revision"),
        sa.CheckConstraint("decision IN ('accepted', 'rejected')",
                           name="ck_doc_property_review_decision"),
    )
    op.create_index("ix_doc_property_review_run", "document_analysis_property_reviews",
                    ["recognition_run_id", "created_at"])
    op.create_table(
        "document_analysis_property_review_heads",
        sa.Column("recognition_run_id", sa.Uuid(),
                  sa.ForeignKey("document_analysis_runs.recognition_run_id", ondelete="CASCADE"),
                  primary_key=True),
        sa.Column("candidate_id", sa.String(200), primary_key=True),
        sa.Column("candidate_revision", sa.Integer(), primary_key=True),
        sa.Column("review_id", sa.Uuid(), sa.ForeignKey(
            "document_analysis_property_reviews.review_id", ondelete="CASCADE"), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.CheckConstraint("revision > 0", name="ck_doc_property_review_head_revision"),
    )
    op.create_table(
        "document_analysis_property_repairs",
        sa.Column("operation_id", sa.Uuid(), primary_key=True), _run_column(),
        sa.Column("review_id", sa.Uuid(), sa.ForeignKey(
            "document_analysis_property_reviews.review_id", ondelete="CASCADE"), nullable=False),
        sa.Column("request_key", sa.String(200), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("base_checkpoint_artifact_id", sa.String(200),
                  sa.ForeignKey("document_analysis_artifacts.artifact_id"), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("result", sa.JSON(), nullable=False),
        sa.Column("receipt", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("recognition_run_id", "request_key",
                            name="uq_doc_property_repair_request"),
        sa.UniqueConstraint("review_id", name="uq_doc_property_repair_review"),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'completed', 'unresolved', 'failed', 'cancelled')",
            name="ck_doc_property_repair_status",
        ),
    )
    op.create_index("ix_doc_property_repair_run", "document_analysis_property_repairs",
                    ["recognition_run_id", "status"])


def downgrade():
    op.drop_table("document_analysis_property_repairs")
    op.drop_table("document_analysis_property_review_heads")
    op.drop_table("document_analysis_property_reviews")
