"""Preserve complete run identities in shared local-model request telemetry."""

import sqlalchemy as sa

from alembic import op

revision = "0034_model_request_run_id"
down_revision = "0033_document_analysis_runs"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("local_model_requests") as batch:
        batch.alter_column(
            "run_id", existing_type=sa.String(32), type_=sa.String(64), existing_nullable=True,
        )


def downgrade():
    connection = op.get_bind()
    # Hold the write exclusion through the check and ALTER. A concurrent writer
    # must not add a long identity after we have decided shrinking is safe.
    if connection.dialect.name == "postgresql":
        connection.execute(sa.text("LOCK TABLE local_model_requests IN ACCESS EXCLUSIVE MODE"))
    elif connection.dialect.name == "sqlite":
        connection.execute(sa.text("UPDATE local_model_requests SET run_id = run_id WHERE 1 = 0"))
    if connection.scalar(sa.text(
        "SELECT EXISTS (SELECT 1 FROM local_model_requests WHERE length(run_id) > 32)"
    )):
        raise RuntimeError(
            "Cannot downgrade local_model_requests.run_id: identities longer than 32 characters "
            "exist; retain the current schema to preserve their complete values."
        )
    with op.batch_alter_table("local_model_requests") as batch:
        batch.alter_column(
            "run_id", existing_type=sa.String(64), type_=sa.String(32), existing_nullable=True,
        )
