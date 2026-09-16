"""Per-revision graph recognition engine selection."""

import sqlalchemy as sa

from alembic import op

revision = "0041_template_engine"
down_revision = "0040_current_recognition_state"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("ast_templates", sa.Column("recognition_mode", sa.String(32), nullable=True))
    op.add_column("ast_templates", sa.Column("finder_profile_id", sa.String(100), nullable=True))


def downgrade():
    op.drop_column("ast_templates", "finder_profile_id")
    op.drop_column("ast_templates", "recognition_mode")
