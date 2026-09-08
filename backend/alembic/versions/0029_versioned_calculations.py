"""Append-only entity calculation decisions and explicit CMC template checks."""

from copy import deepcopy

import sqlalchemy as sa

from alembic import op
from app.models.types import GUID

revision = "0029_versioned_calculations"
down_revision = "0028_automatic_evidence_review"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "calculation_decisions",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("actor", sa.String(100), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True)),
        sa.Column("job_id", GUID(), sa.ForeignKey("extraction_jobs.id"), nullable=False),
        sa.Column("subject_candidate_id", sa.String(64), nullable=False),
        sa.Column("calculation_id", sa.String(64), nullable=False),
        sa.Column("revision_no", sa.Integer(), nullable=False),
        sa.UniqueConstraint(
            "job_id", "subject_candidate_id", "revision_no", name="uq_calculation_decision_revision"
        ),
    )
    op.create_index("ix_calculation_decisions_job_id", "calculation_decisions", ["job_id"])
    if op.get_bind().dialect.name == "postgresql":
        op.execute(
            "CREATE TRIGGER immutable_calculation_decision BEFORE UPDATE OR DELETE "
            "ON calculation_decisions "
            "FOR EACH ROW EXECUTE FUNCTION report_immutable()"
        )
    # Only editable drafts are amended. Published definitions and report bundles stay immutable.
    from app.services.extraction.evidence_identity import evidence_hash
    from app.services.reasoning.pde_calculation import default_checks
    from app.services.reporting.template_v2 import TemplateV2

    table = sa.table(
        "ast_templates",
        sa.column("id", GUID()),
        sa.column("schema_json", sa.JSON()),
        sa.column("schema_hash"),
        sa.column("status"),
    )
    for row in op.get_bind().execute(sa.select(table).where(table.c.status == "draft")).mappings():
        schema = deepcopy(row["schema_json"])
        if schema.get("schema_version") != 2:
            continue
        checks = default_checks(schema.get("source_slots", []))
        existing = schema.setdefault("calculation_checks", [])
        added = [
            c
            for c in checks
            if not any(
                e["source_slot"] == c["source_slot"] and e["contract_ref"] == c["contract_ref"]
                for e in existing
            )
        ]
        if not added:
            continue
        existing.extend(added)
        schema = TemplateV2.model_validate(schema).model_dump(mode="json")
        op.get_bind().execute(
            table.update()
            .where(table.c.id == row["id"])
            .values(schema_json=schema, schema_hash=evidence_hash(schema))
        )


def downgrade():
    # Calculation decisions are audit history. Downgrading cannot discard them.
    raise RuntimeError("Calculation audit history must be preserved; use a forward migration")
