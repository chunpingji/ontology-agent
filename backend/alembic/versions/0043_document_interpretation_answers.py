"""Human answers to source expression ambiguity questions."""

import sqlalchemy as sa

from alembic import op

revision = "0043_interpretation"
down_revision = "0042_mock_entity_query"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "document_interpretation_answers",
        sa.Column("scope_key", sa.String(64), primary_key=True),
        sa.Column("scope", sa.String(16), nullable=False),
        sa.Column("meaning", sa.String(32), nullable=False),
        sa.Column("actor_id", sa.String(200), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade():
    op.drop_table("document_interpretation_answers")
