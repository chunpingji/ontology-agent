"""Store template sample evidence identity and complete analysis snapshot.

Revision ID: 0025_template_evidence
Revises: 0024_mock_schedule_override
"""

import sqlalchemy as sa

from alembic import op

revision = "0025_template_evidence"
down_revision = "0024_mock_schedule_override"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("ast_templates", sa.Column("sample_analysis", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("ast_templates", "sample_analysis")
