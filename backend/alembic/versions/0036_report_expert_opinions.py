"""Append-only report preview expert opinions with scoped idempotency."""

import sqlalchemy as sa

from alembic import op
from app.models.types import GUID

revision = "0036_report_expert_opinions"
down_revision = "0035_ranking_budget_control"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "report_expert_opinions",
        sa.Column("opinion_id", GUID(), primary_key=True),
        sa.Column("owner_id", sa.String(200), nullable=False),
        sa.Column("actor_role", sa.String(50), nullable=False),
        sa.Column("target_hash", sa.String(64), nullable=False),
        sa.Column("request_key", sa.String(200), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("owner_id", "request_key", name="uq_expert_opinion_request"),
        sa.CheckConstraint("revision = 1", name="ck_expert_opinion_immutable"),
    )
    op.create_index("ix_expert_opinion_target", "report_expert_opinions",
                    ["owner_id", "target_hash", "created_at"])


def downgrade():
    op.drop_index("ix_expert_opinion_target", table_name="report_expert_opinions")
    op.drop_table("report_expert_opinions")
