"""AST template status + iri_pattern (015).

Feature 015 (frontend restyle / AST template rebuild): add a lifecycle ``status``
(draft|published|archived) and a per-template ``iri_pattern`` (the functional
document-class resolution key that replaces the retired DocumentTypeMapping).

Existing rows are backfilled to ``status='published'`` (they are live templates);
the model default ``'draft'`` governs newly created rows.

Revision ID: 0014_ast_template_status_iri
Revises: 0013_property_binding
Create Date: 2026-07-04
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0014_ast_template_status_iri"  # ≤32 chars: version_num is VARCHAR(32)
down_revision = "0013_property_binding"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "ast_templates",
        sa.Column("status", sa.String(20), nullable=False, server_default="published"),
    )
    op.add_column("ast_templates", sa.Column("iri_pattern", sa.String(500)))


def downgrade() -> None:
    op.drop_column("ast_templates", "iri_pattern")
    op.drop_column("ast_templates", "status")
