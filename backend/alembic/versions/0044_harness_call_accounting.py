"""Lightweight attempt accounting for the independent document harness."""

import sqlalchemy as sa

from alembic import op

revision = "0044_harness_accounting"
down_revision = "0043_interpretation"
branch_labels = None
depends_on = None

COUNTS = (
    "call_attempts", "call_duration_us", "call_input_tokens", "call_output_tokens",
    "call_unknown_input", "call_unknown_output", "call_unmeasured_attempts",
)


def upgrade():
    with op.batch_alter_table("document_analysis_requests") as batch:
        batch.add_column(sa.Column("call_stage", sa.String(64)))
        batch.add_column(sa.Column("call_status", sa.String(24)))
        batch.add_column(sa.Column("call_started_at", sa.DateTime(timezone=True)))
        batch.add_column(sa.Column("call_finished_at", sa.DateTime(timezone=True)))
        batch.add_column(sa.Column("call_error", sa.String(200)))
        for name in COUNTS:
            kind = sa.BigInteger if name in {
                "call_duration_us", "call_input_tokens", "call_output_tokens",
            } else sa.Integer
            batch.add_column(sa.Column(name, kind()))
            batch.create_check_constraint(
                f"ck_doc_request_{name}", f"{name} IS NULL OR {name} >= 0",
            )


def downgrade():
    with op.batch_alter_table("document_analysis_requests") as batch:
        for name in COUNTS:
            batch.drop_constraint(f"ck_doc_request_{name}", type_="check")
        for name in (*COUNTS, "call_stage", "call_status", "call_started_at",
                     "call_finished_at", "call_error"):
            batch.drop_column(name)
