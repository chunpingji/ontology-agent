"""Create the independent ontology-guided document-analysis run domain."""

import sqlalchemy as sa

from alembic import op
from app.models.types import GUID

revision = "0033_document_analysis_runs"
down_revision = "0032_local_model_requests"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "document_analysis_runs",
        sa.Column("recognition_run_id", GUID(), primary_key=True),
        sa.Column("contract_version", sa.String(64), nullable=False),
        sa.Column("owner_id", sa.String(200), nullable=False),
        sa.Column("request_key", sa.String(200), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("filename", sa.String(500), nullable=False),
        sa.Column("document_hash", sa.String(64), nullable=False),
        sa.Column("source_artifact_ref", sa.String(200)),
        sa.Column("root_class_iri", sa.String(1000), nullable=False),
        sa.Column("root_class_label", sa.String(500), nullable=False),
        sa.Column("ontology_snapshot_hash", sa.String(64), nullable=False),
        sa.Column("metadata_mode", sa.String(32), nullable=False),
        sa.Column("scope_mode", sa.String(32), nullable=False),
        sa.Column("focus_path", sa.JSON(), nullable=False),
        sa.Column("provisional_fingerprint", sa.String(64)),
        sa.Column("run_fingerprint", sa.String(64)),
        sa.Column("analysis_id", sa.String(200)),
        sa.Column("metadata_snapshot_id", sa.String(200)),
        sa.Column("graph_snapshot_id", sa.String(200)),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("event_head", sa.Integer(), nullable=False),
        sa.Column("artifact_revision", sa.Integer(), nullable=False),
        sa.Column("stage", sa.String(32), nullable=False),
        sa.Column("execution_status", sa.String(32), nullable=False),
        sa.Column("coverage_status", sa.String(32), nullable=False),
        sa.Column("semantic_status", sa.String(32), nullable=False),
        sa.Column("stop_reason", sa.String(100)),
        sa.Column("control_action", sa.String(32)),
        sa.Column("control_version", sa.Integer(), nullable=False),
        sa.Column("deletion_state", sa.String(32), nullable=False),
        sa.Column("progress", sa.JSON(), nullable=False),
        sa.Column("artifact_manifest", sa.JSON(), nullable=False),
        sa.Column("error", sa.JSON()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("paused_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("expires_at", sa.DateTime(timezone=True)),
        sa.Column("deleted_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint("revision >= 0", name="ck_doc_analysis_run_revision"),
        sa.CheckConstraint("event_head >= 0", name="ck_doc_analysis_event_head"),
        sa.CheckConstraint("artifact_revision >= 0", name="ck_doc_analysis_artifact_revision"),
        sa.CheckConstraint("control_version >= 0", name="ck_doc_analysis_control_version"),
        sa.UniqueConstraint("owner_id", "request_key", name="uq_doc_analysis_owner_request"),
    )
    op.create_index(
        "ix_doc_analysis_run_owner_status",
        "document_analysis_runs",
        ["owner_id", "execution_status"],
    )
    op.create_index(
        "ix_doc_analysis_run_expiry",
        "document_analysis_runs",
        ["deletion_state", "expires_at"],
    )

    op.create_table(
        "document_analysis_executions",
        sa.Column(
            "recognition_run_id",
            GUID(),
            sa.ForeignKey("document_analysis_runs.recognition_run_id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("execution_token_hash", sa.String(64)),
        sa.Column("generation", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("actor", sa.String(200)),
        sa.Column("worker_id", sa.String(200)),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True)),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True)),
        sa.Column("pause_requested", sa.Boolean(), nullable=False),
        sa.Column("cancel_requested", sa.Boolean(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("generation >= 0", name="ck_doc_analysis_execution_generation"),
    )
    op.create_index(
        "ix_doc_analysis_execution_claim",
        "document_analysis_executions",
        ["status", "lease_expires_at"],
    )

    op.create_table(
        "document_analysis_artifacts",
        sa.Column("artifact_id", sa.String(200), primary_key=True),
        sa.Column("artifact_kind", sa.String(64), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("storage_uri", sa.String(2000)),
        sa.Column("media_type", sa.String(200)),
        sa.Column("size_bytes", sa.Integer()),
        sa.Column("payload", sa.JSON()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_doc_analysis_artifact_hash",
        "document_analysis_artifacts",
        ["content_hash", "artifact_kind"],
    )

    op.create_table(
        "document_analysis_run_artifacts",
        sa.Column(
            "recognition_run_id",
            GUID(),
            sa.ForeignKey("document_analysis_runs.recognition_run_id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("artifact_kind", sa.String(64), primary_key=True),
        sa.Column("revision", sa.Integer(), primary_key=True),
        sa.Column(
            "artifact_id",
            sa.String(200),
            sa.ForeignKey("document_analysis_artifacts.artifact_id"),
            nullable=False,
        ),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("event_head", sa.Integer(), nullable=False),
        sa.Column("is_exclusive", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("revision > 0", name="ck_doc_run_artifact_revision"),
    )
    op.create_index("ix_doc_run_artifact_id", "document_analysis_run_artifacts", ["artifact_id"])

    op.create_table(
        "document_analysis_artifact_heads",
        sa.Column("recognition_run_id", GUID(), primary_key=True),
        sa.Column("artifact_kind", sa.String(64), primary_key=True),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("artifact_id", sa.String(200), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("event_head", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("revision > 0", name="ck_doc_artifact_head_revision"),
        sa.ForeignKeyConstraint(
            ["recognition_run_id", "artifact_kind", "revision"],
            [
                "document_analysis_run_artifacts.recognition_run_id",
                "document_analysis_run_artifacts.artifact_kind",
                "document_analysis_run_artifacts.revision",
            ],
            name="fk_doc_artifact_head_revision",
            ondelete="CASCADE",
        ),
    )

    op.create_table(
        "document_analysis_events",
        sa.Column(
            "recognition_run_id",
            GUID(),
            sa.ForeignKey("document_analysis_runs.recognition_run_id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("sequence", sa.Integer(), primary_key=True),
        sa.Column("event_key", sa.String(200), nullable=False),
        sa.Column("event_type", sa.String(64), nullable=False),
        sa.Column("payload_hash", sa.String(64), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("sequence > 0", name="ck_doc_analysis_event_sequence"),
        sa.UniqueConstraint("recognition_run_id", "event_key", name="uq_doc_analysis_event_key"),
    )
    op.create_index(
        "ix_doc_analysis_event_type",
        "document_analysis_events",
        ["recognition_run_id", "event_type"],
    )

    op.create_table(
        "document_analysis_event_batches",
        sa.Column(
            "recognition_run_id",
            GUID(),
            sa.ForeignKey("document_analysis_runs.recognition_run_id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("batch_id", sa.String(200), primary_key=True),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("first_sequence", sa.Integer(), nullable=False),
        sa.Column("last_sequence", sa.Integer(), nullable=False),
        sa.Column(
            "checkpoint_artifact_id",
            sa.String(200),
            sa.ForeignKey("document_analysis_artifacts.artifact_id"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("first_sequence > 0", name="ck_doc_batch_first_sequence"),
        sa.CheckConstraint("last_sequence >= first_sequence", name="ck_doc_batch_sequence_range"),
    )
    op.create_index(
        "ix_doc_batch_watermark",
        "document_analysis_event_batches",
        ["recognition_run_id", "last_sequence"],
    )

    op.create_table(
        "document_analysis_run_candidates",
        sa.Column(
            "recognition_run_id",
            GUID(),
            sa.ForeignKey("document_analysis_runs.recognition_run_id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("candidate_id", sa.String(200), primary_key=True),
        sa.Column("revision", sa.Integer(), primary_key=True),
        sa.Column("kind", sa.String(64), nullable=False),
        sa.Column("payload_hash", sa.String(64), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("proof_refs", sa.JSON(), nullable=False),
        sa.Column("event_sequence", sa.Integer()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("revision > 0", name="ck_doc_candidate_revision"),
    )
    op.create_index(
        "ix_doc_candidate_kind",
        "document_analysis_run_candidates",
        ["recognition_run_id", "kind"],
    )

    op.create_table(
        "document_analysis_candidate_heads",
        sa.Column("recognition_run_id", GUID(), primary_key=True),
        sa.Column("candidate_id", sa.String(200), primary_key=True),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("payload_hash", sa.String(64), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("revision > 0", name="ck_doc_candidate_head_revision"),
        sa.ForeignKeyConstraint(
            ["recognition_run_id", "candidate_id", "revision"],
            [
                "document_analysis_run_candidates.recognition_run_id",
                "document_analysis_run_candidates.candidate_id",
                "document_analysis_run_candidates.revision",
            ],
            name="fk_doc_candidate_head_revision",
            ondelete="CASCADE",
        ),
    )

    op.create_table(
        "document_analysis_verification_proofs",
        sa.Column(
            "recognition_run_id",
            GUID(),
            sa.ForeignKey("document_analysis_runs.recognition_run_id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("proof_id", sa.String(200), primary_key=True),
        sa.Column("proof_revision", sa.Integer(), primary_key=True),
        sa.Column("target_id", sa.String(300), nullable=False),
        sa.Column("payload_hash", sa.String(64), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("event_sequence", sa.Integer()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("proof_revision > 0", name="ck_doc_proof_revision"),
    )
    op.create_index(
        "ix_doc_proof_target",
        "document_analysis_verification_proofs",
        ["recognition_run_id", "target_id"],
    )

    op.create_table(
        "document_analysis_proof_heads",
        sa.Column("recognition_run_id", GUID(), primary_key=True),
        sa.Column("proof_id", sa.String(200), primary_key=True),
        sa.Column("proof_revision", sa.Integer(), nullable=False),
        sa.Column("payload_hash", sa.String(64), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("proof_revision > 0", name="ck_doc_proof_head_revision"),
        sa.ForeignKeyConstraint(
            ["recognition_run_id", "proof_id", "proof_revision"],
            [
                "document_analysis_verification_proofs.recognition_run_id",
                "document_analysis_verification_proofs.proof_id",
                "document_analysis_verification_proofs.proof_revision",
            ],
            name="fk_doc_proof_head_revision",
            ondelete="CASCADE",
        ),
    )

    op.create_table(
        "document_analysis_control_operations",
        sa.Column(
            "recognition_run_id",
            GUID(),
            sa.ForeignKey("document_analysis_runs.recognition_run_id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("action", sa.String(32), primary_key=True),
        sa.Column("request_key", sa.String(200), primary_key=True),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("expected_revision", sa.Integer(), nullable=False),
        sa.Column("result_revision", sa.Integer(), nullable=False),
        sa.Column("result_payload_hash", sa.String(64)),
        sa.Column("result_payload", sa.JSON()),
        sa.Column("reason", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("expected_revision >= 0", name="ck_doc_control_expected_revision"),
        sa.CheckConstraint("result_revision > 0", name="ck_doc_control_result_revision"),
    )

    op.create_table(
        "document_analysis_tombstones",
        # Deliberately no FK: this minimal marker survives later run cleanup.
        sa.Column("recognition_run_id", GUID(), primary_key=True),
        sa.Column("owner_hash", sa.String(64), nullable=False),
        sa.Column("token_generation", sa.Integer(), nullable=False),
        sa.Column("final_state", sa.String(24), nullable=False),
        sa.Column("reason", sa.Text()),
        sa.Column("delete_request_key", sa.String(200)),
        sa.Column("delete_request_hash", sa.String(64)),
        sa.Column("delete_result_payload_hash", sa.String(64)),
        sa.Column("delete_result_payload", sa.JSON()),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("token_generation >= 0", name="ck_doc_tombstone_generation"),
        sa.CheckConstraint(
            "(delete_request_key IS NULL AND delete_request_hash IS NULL "
            "AND delete_result_payload_hash IS NULL AND delete_result_payload IS NULL) "
            "OR (delete_request_key IS NOT NULL AND delete_request_hash IS NOT NULL "
            "AND delete_result_payload_hash IS NOT NULL AND delete_result_payload IS NOT NULL)",
            name="ck_doc_tombstone_delete_receipt_complete",
        ),
    )
    op.create_index(
        "ix_doc_tombstone_owner",
        "document_analysis_tombstones",
        ["owner_hash", "deleted_at"],
    )


def downgrade():
    op.drop_table("document_analysis_tombstones")
    op.drop_table("document_analysis_control_operations")
    op.drop_table("document_analysis_proof_heads")
    op.drop_table("document_analysis_verification_proofs")
    op.drop_table("document_analysis_candidate_heads")
    op.drop_table("document_analysis_run_candidates")
    op.drop_table("document_analysis_event_batches")
    op.drop_table("document_analysis_events")
    op.drop_table("document_analysis_artifact_heads")
    op.drop_table("document_analysis_run_artifacts")
    op.drop_table("document_analysis_artifacts")
    op.drop_table("document_analysis_executions")
    op.drop_table("document_analysis_runs")
