"""Immutable report definitions, runs, output and independent signing protocol."""

import json
from hashlib import sha256

import sqlalchemy as sa

from alembic import op

revision = "0027_report_output_semantics"
down_revision = "0026_evidence_facts"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "ast_templates",
        sa.Column("schema_version", sa.Integer(), nullable=False, server_default="1"),
    )
    op.add_column("ast_templates", sa.Column("template_family_id", sa.String(100)))
    op.add_column("ast_templates", sa.Column("revision_no", sa.Integer()))
    op.add_column("ast_templates", sa.Column("schema_hash", sa.String(64)))
    op.add_column("generated_reports", sa.Column("report_run_id", sa.String(64)))
    op.add_column("generated_reports", sa.Column("report_artifact_id", sa.String(64)))
    templates = sa.table(
        "ast_templates",
        sa.column("id"),
        sa.column("schema_json", sa.JSON()),
        sa.column("template_family_id"),
        sa.column("revision_no"),
        sa.column("schema_hash"),
    )
    for row in op.get_bind().execute(sa.select(templates)).mappings():
        digest = sha256(
            json.dumps(
                row["schema_json"], ensure_ascii=False, sort_keys=True, separators=(",", ":")
            ).encode()
        ).hexdigest()
        op.get_bind().execute(
            templates.update()
            .where(templates.c.id == row["id"])
            .values(template_family_id=str(row["id"]), revision_no=1, schema_hash=digest)
        )
    with op.batch_alter_table("ast_templates") as batch:
        batch.create_unique_constraint(
            "uq_template_family_revision", ["template_family_id", "revision_no"]
        )
    op.create_table(
        "report_contract_revisions",
        sa.Column("family_id", sa.String(length=500), nullable=False),
        sa.Column("revision_no", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=40), nullable=False),
        sa.Column("id", sa.String(length=64), primary_key=True, nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("actor", sa.String(length=100), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("family_id", "revision_no", name="uq_report_contract_revision"),
    )
    op.create_table(
        "report_input_snapshots",
        sa.Column("source_hash", sa.String(length=64), nullable=False),
        sa.Column("id", sa.String(length=64), primary_key=True, nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("actor", sa.String(length=100), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "report_requests",
        sa.Column("operation", sa.String(length=100), nullable=False),
        sa.Column("idempotency_key", sa.String(length=200), nullable=False),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column("id", sa.String(length=64), primary_key=True, nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("actor", sa.String(length=100), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("actor", "operation", "idempotency_key", name="uq_report_request_key"),
    )
    op.create_table(
        "condition_definitions",
        sa.Column(
            "id",
            sa.String(length=64),
            sa.ForeignKey("report_contract_revisions.id"),
            primary_key=True,
            nullable=False,
        ),
    )
    op.create_table(
        "report_contract_decisions",
        sa.Column(
            "contract_id",
            sa.String(length=64),
            sa.ForeignKey("report_contract_revisions.id"),
            nullable=False,
        ),
        sa.Column("decision", sa.String(length=20), nullable=False),
        sa.Column("revision_no", sa.Integer(), nullable=False),
        sa.Column("id", sa.String(length=64), primary_key=True, nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("actor", sa.String(length=100), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("contract_id", "revision_no", name="uq_contract_decision"),
    )
    op.create_index(
        "ix_report_contract_decisions_contract_id", "report_contract_decisions", ["contract_id"]
    )
    op.create_table(
        "report_contract_records",
        sa.Column(
            "contract_id",
            sa.String(length=64),
            sa.ForeignKey("report_contract_revisions.id"),
            nullable=False,
        ),
        sa.Column("record_key", sa.String(length=500), nullable=False),
        sa.Column("revision_no", sa.Integer(), nullable=False),
        sa.Column("id", sa.String(length=64), primary_key=True, nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("actor", sa.String(length=100), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "contract_id", "record_key", "revision_no", name="uq_contract_record_revision"
        ),
    )
    op.create_table(
        "report_contract_record_reviews",
        sa.Column(
            "record_id",
            sa.String(length=64),
            sa.ForeignKey("report_contract_records.id"),
            nullable=False,
        ),
        sa.Column("decision", sa.String(length=20), nullable=False),
        sa.Column("id", sa.String(length=64), primary_key=True, nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("actor", sa.String(length=100), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "template_compilations",
        sa.Column("template_id", sa.Uuid(), sa.ForeignKey("ast_templates.id"), nullable=True),
        sa.Column("schema_hash", sa.String(length=64), nullable=False),
        sa.Column("id", sa.String(length=64), primary_key=True, nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("actor", sa.String(length=100), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "template_migration_plans",
        sa.Column("template_id", sa.Uuid(), sa.ForeignKey("ast_templates.id"), nullable=False),
        sa.Column("id", sa.String(length=64), primary_key=True, nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("actor", sa.String(length=100), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "conditional_assertion_bindings",
        sa.Column(
            "id",
            sa.String(length=64),
            sa.ForeignKey("report_contract_revisions.id"),
            primary_key=True,
            nullable=False,
        ),
        sa.Column(
            "condition_id",
            sa.String(length=64),
            sa.ForeignKey("condition_definitions.id"),
            nullable=False,
        ),
        sa.Column(
            "assertion_id",
            sa.String(length=64),
            sa.ForeignKey("evidence_assertions.id"),
            nullable=False,
        ),
    )
    op.create_table(
        "ontology_decision_rule_revisions",
        sa.Column(
            "id",
            sa.String(length=64),
            sa.ForeignKey("report_contract_revisions.id"),
            primary_key=True,
            nullable=False,
        ),
        sa.Column("rule_id", sa.Uuid(), sa.ForeignKey("ontology_decision_rule.id"), nullable=False),
        sa.Column("revision_no", sa.Integer(), nullable=False),
        sa.UniqueConstraint("rule_id", "revision_no", name="uq_rule_revision"),
    )
    op.create_table(
        "report_runs",
        sa.Column("id", sa.String(length=64), primary_key=True, nullable=False),
        sa.Column("actor", sa.String(length=100), nullable=False),
        sa.Column("idempotency_key", sa.String(length=200), nullable=False),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column("template_id", sa.Uuid(), sa.ForeignKey("ast_templates.id"), nullable=True),
        sa.Column(
            "compilation_id",
            sa.String(length=64),
            sa.ForeignKey("template_compilations.id"),
            nullable=False,
        ),
        sa.Column("source_bundle", sa.JSON(), nullable=False),
        sa.Column("source_hash", sa.String(length=64), nullable=False),
        sa.Column("input_snapshot_id", sa.String(length=64), nullable=True),
        sa.Column("purpose", sa.String(length=20), nullable=False),
        sa.Column("phase", sa.String(length=30), nullable=False),
        sa.Column("execution_status", sa.String(length=20), nullable=False),
        sa.Column("material_status", sa.String(length=20), nullable=False),
        sa.Column("review_status", sa.String(length=20), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("revision_no", sa.Integer(), nullable=False),
        sa.Column("error", sa.JSON(), nullable=True),
        sa.Column("worker_token", sa.String(length=64), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("actor", "idempotency_key", name="uq_report_run_request"),
    )
    op.create_table(
        "rule_migration_plans",
        sa.Column("rule_id", sa.Uuid(), sa.ForeignKey("ontology_decision_rule.id"), nullable=False),
        sa.Column("id", sa.String(length=64), primary_key=True, nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("actor", sa.String(length=100), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "report_bodies",
        sa.Column("run_id", sa.String(length=64), sa.ForeignKey("report_runs.id"), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("id", sa.String(length=64), primary_key=True, nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("actor", sa.String(length=100), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("run_id", "attempt", name="uq_report_body_attempt"),
    )
    op.create_table(
        "report_output_results",
        sa.Column("run_id", sa.String(length=64), sa.ForeignKey("report_runs.id"), nullable=False),
        sa.Column("output_id", sa.String(length=500), nullable=False),
        sa.Column("execution_scope_id", sa.String(length=64), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("id", sa.String(length=64), primary_key=True, nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("actor", sa.String(length=100), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "run_id", "output_id", "execution_scope_id", "attempt", name="uq_report_output_attempt"
        ),
    )
    op.create_index("ix_report_output_results_run_id", "report_output_results", ["run_id"])
    op.create_table(
        "report_content_versions",
        sa.Column("run_id", sa.String(length=64), sa.ForeignKey("report_runs.id"), nullable=False),
        sa.Column(
            "body_id", sa.String(length=64), sa.ForeignKey("report_bodies.id"), nullable=False
        ),
        sa.Column(
            "input_snapshot_id",
            sa.String(length=64),
            sa.ForeignKey("report_input_snapshots.id"),
            nullable=False,
        ),
        sa.Column("id", sa.String(length=64), primary_key=True, nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("actor", sa.String(length=100), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "report_content_reviews",
        sa.Column(
            "content_version_id",
            sa.String(length=64),
            sa.ForeignKey("report_content_versions.id"),
            nullable=False,
        ),
        sa.Column("decision", sa.String(length=20), nullable=False),
        sa.Column("id", sa.String(length=64), primary_key=True, nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("actor", sa.String(length=100), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "report_signing_sessions",
        sa.Column("id", sa.String(length=64), primary_key=True, nullable=False),
        sa.Column(
            "content_version_id",
            sa.String(length=64),
            sa.ForeignKey("report_content_versions.id"),
            nullable=False,
        ),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("actor", sa.String(length=100), nullable=False),
        sa.Column(
            "parent_id",
            sa.String(length=64),
            sa.ForeignKey("report_signing_sessions.id"),
            nullable=True,
        ),
        sa.Column("frozen_context", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("revision_no", sa.Integer(), nullable=False),
        sa.Column("frozen_signature_id", sa.String(length=64), nullable=True),
        sa.Column("envelope_request", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "report_signature_snapshots",
        sa.Column(
            "session_id",
            sa.String(length=64),
            sa.ForeignKey("report_signing_sessions.id"),
            nullable=False,
        ),
        sa.Column("signature_revision", sa.Integer(), nullable=False),
        sa.Column("id", sa.String(length=64), primary_key=True, nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("actor", sa.String(length=100), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("session_id", "signature_revision", name="uq_signature_snapshot"),
    )
    op.create_table(
        "report_signatures",
        sa.Column(
            "session_id",
            sa.String(length=64),
            sa.ForeignKey("report_signing_sessions.id"),
            nullable=False,
        ),
        sa.Column("signature_slot_id", sa.String(length=500), nullable=False),
        sa.Column("signature_revision", sa.Integer(), nullable=False),
        sa.Column("id", sa.String(length=64), primary_key=True, nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("actor", sa.String(length=100), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("session_id", "signature_slot_id", name="uq_signature_slot"),
    )
    op.create_table(
        "report_signature_events",
        sa.Column(
            "session_id",
            sa.String(length=64),
            sa.ForeignKey("report_signing_sessions.id"),
            nullable=False,
        ),
        sa.Column(
            "signature_id",
            sa.String(length=64),
            sa.ForeignKey("report_signatures.id"),
            nullable=False,
        ),
        sa.Column("signature_revision", sa.Integer(), nullable=False),
        sa.Column("id", sa.String(length=64), primary_key=True, nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("actor", sa.String(length=100), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("session_id", "signature_revision", name="uq_signature_event"),
    )
    op.create_table(
        "report_signing_envelopes",
        sa.Column(
            "session_id",
            sa.String(length=64),
            sa.ForeignKey("report_signing_sessions.id"),
            nullable=False,
        ),
        sa.Column(
            "content_version_id",
            sa.String(length=64),
            sa.ForeignKey("report_content_versions.id"),
            nullable=False,
        ),
        sa.Column(
            "signature_snapshot_id",
            sa.String(length=64),
            sa.ForeignKey("report_signature_snapshots.id"),
            nullable=False,
        ),
        sa.Column(
            "parent_id",
            sa.String(length=64),
            sa.ForeignKey("report_signing_envelopes.id"),
            nullable=True,
        ),
        sa.Column("id", sa.String(length=64), primary_key=True, nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("actor", sa.String(length=100), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("session_id"),
    )
    op.create_table(
        "report_artifacts",
        sa.Column("run_id", sa.String(length=64), sa.ForeignKey("report_runs.id"), nullable=False),
        sa.Column(
            "body_id", sa.String(length=64), sa.ForeignKey("report_bodies.id"), nullable=True
        ),
        sa.Column(
            "envelope_id",
            sa.String(length=64),
            sa.ForeignKey("report_signing_envelopes.id"),
            nullable=True,
        ),
        sa.Column("format", sa.String(length=20), nullable=False),
        sa.Column("file_hash", sa.String(length=64), nullable=False),
        sa.Column("file_path", sa.Text(), nullable=False),
        sa.Column("purpose", sa.String(length=20), nullable=False),
        sa.Column("id", sa.String(length=64), primary_key=True, nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("actor", sa.String(length=100), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_report_artifacts_run_id", "report_artifacts", ["run_id"])

    # History is append-only even for bulk SQL writers.
    if op.get_bind().dialect.name == "postgresql":
        op.execute(
            "CREATE FUNCTION report_immutable() RETURNS trigger LANGUAGE plpgsql AS $$ "
            "BEGIN RAISE EXCEPTION 'immutable report record'; END $$"
        )
        for table in (
            "report_contract_revisions",
            "report_contract_decisions",
            "report_contract_records",
            "report_contract_record_reviews",
            "ontology_decision_rule_revisions",
            "condition_definitions",
            "conditional_assertion_bindings",
            "template_compilations",
            "report_input_snapshots",
            "report_output_results",
            "report_bodies",
            "report_content_versions",
            "report_content_reviews",
            "report_signatures",
            "report_signature_events",
            "report_signature_snapshots",
            "report_signing_envelopes",
            "report_artifacts",
            "template_migration_plans",
            "rule_migration_plans",
            "report_requests",
        ):
            op.execute(
                "CREATE TRIGGER immutable_report_row BEFORE UPDATE OR DELETE ON "
                + table
                + " FOR EACH ROW EXECUTE FUNCTION report_immutable()"
            )
        op.execute("""
            CREATE FUNCTION report_run_guard() RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN
              IF TG_OP = 'DELETE' THEN RAISE EXCEPTION 'immutable report source'; END IF;
              IF (to_jsonb(NEW) - ARRAY['phase','execution_status','material_status',
                    'review_status','attempt','revision_no','error','input_snapshot_id',
                    'worker_token','lease_expires_at'])
                 IS DISTINCT FROM
                 (to_jsonb(OLD) - ARRAY['phase','execution_status','material_status',
                    'review_status','attempt','revision_no','error','input_snapshot_id',
                    'worker_token','lease_expires_at'])
                 OR (OLD.input_snapshot_id IS NOT NULL AND
                     NEW.input_snapshot_id IS DISTINCT FROM OLD.input_snapshot_id)
              THEN RAISE EXCEPTION 'immutable report source'; END IF;
              RETURN NEW;
            END $$
        """)
        op.execute("""
            CREATE FUNCTION report_session_guard() RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN
              IF TG_OP = 'DELETE' THEN RAISE EXCEPTION 'immutable signing context'; END IF;
              IF (to_jsonb(NEW) - ARRAY['revision_no','status',
                      'frozen_signature_id','envelope_request'])
                 IS DISTINCT FROM
                 (to_jsonb(OLD) - ARRAY['revision_no','status',
                      'frozen_signature_id','envelope_request'])
                 OR NEW.revision_no < OLD.revision_no
                 OR (OLD.status <> 'open' AND NEW.status = 'open')
                 OR (OLD.frozen_signature_id IS NOT NULL AND
                     NEW.frozen_signature_id IS DISTINCT FROM OLD.frozen_signature_id)
                 OR (OLD.envelope_request IS NOT NULL AND
                     NEW.envelope_request::jsonb IS DISTINCT FROM OLD.envelope_request::jsonb)
              THEN RAISE EXCEPTION 'immutable signing context'; END IF;
              RETURN NEW;
            END $$
        """)
        op.execute(
            "CREATE TRIGGER immutable_report_head BEFORE UPDATE OR DELETE ON report_runs "
            "FOR EACH ROW EXECUTE FUNCTION report_run_guard()"
        )
        op.execute(
            "CREATE TRIGGER immutable_signing_head BEFORE UPDATE OR DELETE "
            "ON report_signing_sessions "
            "FOR EACH ROW EXECUTE FUNCTION report_session_guard()"
        )

        op.execute("""
            CREATE FUNCTION published_rule_guard() RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN
              IF OLD.status = 'published' THEN
                RAISE EXCEPTION 'published rule requires a new revision';
              END IF;
              IF TG_OP = 'DELETE' THEN RETURN OLD; END IF;
              RETURN NEW;
            END $$
        """)
        op.execute(
            "CREATE TRIGGER immutable_published_rule BEFORE UPDATE OR DELETE ON "
            "ontology_decision_rule FOR EACH ROW EXECUTE FUNCTION published_rule_guard()"
        )


def downgrade():
    if op.get_bind().dialect.name == "postgresql":
        op.execute("DROP TRIGGER IF EXISTS immutable_published_rule ON ontology_decision_rule")
        op.execute("DROP FUNCTION IF EXISTS published_rule_guard()")
    op.drop_table("report_artifacts")
    op.drop_table("report_signing_envelopes")
    op.drop_table("report_signature_events")
    op.drop_table("report_signatures")
    op.drop_table("report_signature_snapshots")
    op.drop_table("report_signing_sessions")
    op.drop_table("report_content_reviews")
    op.drop_table("report_content_versions")
    op.drop_table("report_output_results")
    op.drop_table("report_bodies")
    op.drop_table("rule_migration_plans")
    op.drop_table("report_runs")
    op.drop_table("ontology_decision_rule_revisions")
    op.drop_table("conditional_assertion_bindings")
    op.drop_table("template_migration_plans")
    op.drop_table("template_compilations")
    op.drop_table("report_contract_record_reviews")
    op.drop_table("report_contract_records")
    op.drop_table("report_contract_decisions")
    op.drop_table("condition_definitions")
    op.drop_table("report_requests")
    op.drop_table("report_input_snapshots")
    op.drop_table("report_contract_revisions")
    if op.get_bind().dialect.name == "postgresql":
        op.execute("DROP FUNCTION report_immutable()")
        op.execute("DROP FUNCTION IF EXISTS report_run_guard()")
        op.execute("DROP FUNCTION IF EXISTS report_session_guard()")
    with op.batch_alter_table("ast_templates") as batch:
        batch.drop_constraint("uq_template_family_revision", type_="unique")
        batch.drop_column("schema_hash")
        batch.drop_column("revision_no")
        batch.drop_column("template_family_id")
        batch.drop_column("schema_version")
    op.drop_column("generated_reports", "report_run_id")
    op.drop_column("generated_reports", "report_artifact_id")
