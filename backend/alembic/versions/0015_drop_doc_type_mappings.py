"""Drop document_type_mappings (015).

Feature 015 retires DocumentTypeMapping: per-template ``iri_pattern`` is now the
functional resolution key (no back-compat). The table, its endpoints, and the
resolve_template tier that used it are removed.

Revision ID: 0015_drop_doc_type_mappings
Revises: 0014_ast_template_status_iri
Create Date: 2026-07-04
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from app.models.types import GUID

revision = "0015_drop_doc_type_mappings"  # ≤32 chars
down_revision = "0014_ast_template_status_iri"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_table("document_type_mappings")


def downgrade() -> None:
    op.create_table(
        "document_type_mappings",
        sa.Column("id", GUID(), primary_key=True),
        sa.Column("doc_class_iri_pattern", sa.String(500), nullable=False),
        sa.Column(
            "template_id",
            GUID(),
            sa.ForeignKey("ast_templates.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("priority", sa.Integer(), server_default="0"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now()
        ),
    )
