"""Add password_hash to app_user for real authentication.

Introduces the credential column backing /api/auth/login. Nullable so existing
rows and gateway-injected users remain valid; only rows with a PBKDF2 digest
(seeded via app.auth.hash_password) can authenticate by password.

Revision ID: 0022_user_pw
Revises: 0021_pde_conflict_decision
Create Date: 2026-07-07
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0022_user_pw"
down_revision = "0021_pde_conflict_decision"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "app_user",
        sa.Column("password_hash", sa.String(255), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("app_user", "password_hash")
