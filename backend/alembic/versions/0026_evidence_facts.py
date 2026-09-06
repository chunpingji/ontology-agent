"""Versioned evidence candidates and transactional fact publication.

Revision ID: 0026_evidence_facts
Revises: 0025_template_evidence
"""

import sqlalchemy as sa

from alembic import op

revision = "0026_evidence_facts"
down_revision = "0025_template_evidence"
branch_labels = None
depends_on = None


def _id():
    return sa.Column("id", sa.String(64), primary_key=True)


def _job(primary=False):
    return sa.Column(
        "job_id",
        sa.Uuid(),
        sa.ForeignKey("extraction_jobs.id"),
        primary_key=primary,
        nullable=False,
    )


def _created():
    return sa.Column("created_at", sa.DateTime(timezone=True), nullable=False)


def upgrade():
    op.create_table(
        "evidence_candidates",
        _id(),
        _job(),
        sa.Column("source_key", sa.String(64), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("review_status", sa.String(20), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        _created(),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("job_id", "source_key", name="uq_evidence_candidate_source"),
    )
    op.create_table(
        "evidence_candidate_revisions",
        _id(),
        sa.Column(
            "candidate_id", sa.String(64), sa.ForeignKey("evidence_candidates.id"), nullable=False
        ),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        _created(),
        sa.UniqueConstraint("candidate_id", "revision", name="uq_evidence_revision"),
    )
    op.create_table(
        "evidence_reviews",
        _id(),
        sa.Column(
            "candidate_id", sa.String(64), sa.ForeignKey("evidence_candidates.id"), nullable=False
        ),
        sa.Column("expected_revision", sa.Integer(), nullable=False),
        sa.Column("actor", sa.String(100), nullable=False),
        sa.Column("decision", sa.String(20), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        _created(),
    )
    op.create_table(
        "document_analyses",
        _id(),
        sa.Column("document_hash", sa.String(64), nullable=False),
        sa.Column("structure_hash", sa.String(64), nullable=False),
        sa.Column("role", sa.String(30), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        _created(),
    )
    op.create_index("ix_document_analyses_document_hash", "document_analyses", ["document_hash"])
    op.create_table(
        "evidence_commits",
        _id(),
        _job(),
        sa.Column("idempotency_key", sa.String(200), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("manifest", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("actor", sa.String(100), nullable=False),
        sa.Column("error", sa.Text()),
        sa.Column("world_path", sa.Text()),
        sa.Column("snapshot_id", sa.String(64)),
        sa.Column("lease_token", sa.String(64)),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True)),
        _created(),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("job_id", "idempotency_key", name="uq_evidence_commit_key"),
    )
    op.create_index("ix_evidence_commits_status", "evidence_commits", ["status"])
    op.create_table(
        "evidence_assertions",
        _id(),
        _job(),
        sa.Column("commit_id", sa.String(64), sa.ForeignKey("evidence_commits.id"), nullable=False),
        sa.Column(
            "candidate_id", sa.String(64), sa.ForeignKey("evidence_candidates.id"), nullable=False
        ),
        sa.Column("candidate_revision", sa.Integer(), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
    )
    op.create_table(
        "evidence_snapshots",
        _id(),
        _job(),
        sa.Column("parent_id", sa.String(64), sa.ForeignKey("evidence_snapshots.id")),
        sa.Column("assertion_ids", sa.JSON(), nullable=False),
        _created(),
    )
    op.create_table(
        "evidence_job_states",
        _job(primary=True),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("snapshot_id", sa.String(64), sa.ForeignKey("evidence_snapshots.id")),
        sa.Column("analysis_id", sa.String(64), sa.ForeignKey("document_analyses.id")),
        sa.Column("extraction_run", sa.JSON()),
    )
    op.create_table(
        "evidence_coverage",
        _id(),
        _job(),
        sa.Column("snapshot_id", sa.String(64), sa.ForeignKey("evidence_snapshots.id")),
        sa.Column("template_id", sa.String(100), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        _created(),
    )
    for table in (
        "evidence_candidates",
        "evidence_commits",
        "evidence_assertions",
        "evidence_snapshots",
        "evidence_coverage",
    ):
        op.create_index(f"ix_{table}_job_id", table, ["job_id"])
    for column, size in (
        ("evidence_snapshot_id", 64),
        ("coverage_manifest_id", 64),
        ("selector_version", 50),
        ("source_discovery_hash", 64),
    ):
        op.add_column("generated_reports", sa.Column(column, sa.String(size), nullable=True))


def downgrade():
    for column in (
        "evidence_snapshot_id",
        "coverage_manifest_id",
        "selector_version",
        "source_discovery_hash",
    ):
        op.drop_column("generated_reports", column)
    for table in (
        "evidence_coverage",
        "evidence_job_states",
        "evidence_snapshots",
        "evidence_assertions",
        "evidence_commits",
        "document_analyses",
        "evidence_reviews",
        "evidence_candidate_revisions",
        "evidence_candidates",
    ):
        op.drop_table(table)
