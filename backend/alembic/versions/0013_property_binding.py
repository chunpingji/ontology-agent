"""E6b ontology_property_binding + E6 (class, source) uniqueness (014).

Feature 014 (ontology dynamic object mapping): add the property-binding layer
(E6b) that maps each ontology data/object property to a concrete source field,
owned by an E6 class binding (CASCADE). Also add a conditional unique index on
ontology_class_mapping (class_id, source_system) scoped to the source-entity
mapping types, so a class has at most one binding per source (C1/FR-024) while
legacy slpra_iri/bfo rows are unaffected.

The E6 `mapping_type` allow-list gains db_table/api_endpoint/doc_pattern — that
is an app-level enum (validated in the store), so no DDL is needed for it.

Revision ID: 0013_property_binding
Revises: 0012_ast_template_sample_json
Create Date: 2026-07-02
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from app.models.types import GUID

revision = "0013_property_binding"  # ≤32 chars: alembic_version.version_num is VARCHAR(32)
down_revision = "0012_ast_template_sample_json"
branch_labels = None
depends_on = None

# Source-entity mapping types the partial unique index applies to (R1/R3).
_SOURCE_ENTITY_TYPES = "'db_table', 'api_endpoint', 'doc_pattern'"


def upgrade() -> None:
    op.create_table(
        "ontology_property_binding",
        sa.Column("id", GUID(), primary_key=True),
        sa.Column(
            "class_mapping_id",
            GUID(),
            sa.ForeignKey("ontology_class_mapping.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("property_iri", sa.String(500), nullable=False),
        sa.Column("property_kind", sa.String(10), nullable=False, server_default="data"),
        sa.Column("source_path", sa.String(500), nullable=False),
        sa.Column("transform_type", sa.String(20), nullable=False, server_default="none"),
        sa.Column("transform_config", sa.JSON()),
        sa.Column("is_identifier", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("is_label", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("object_resolution", sa.String(20)),
        sa.Column("target_class_iri", sa.String(500)),
        sa.Column("target_id_path", sa.String(500)),
        sa.Column(
            "nested_binding_id",
            GUID(),
            sa.ForeignKey("ontology_class_mapping.id"),
        ),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("status", sa.String(20), nullable=False, server_default="draft"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("created_by", GUID(), sa.ForeignKey("app_user.id")),
        sa.Column("updated_by", GUID(), sa.ForeignKey("app_user.id")),
    )
    op.create_index(
        "ix_property_binding_class_mapping_id",
        "ontology_property_binding",
        ["class_mapping_id"],
    )
    # Conditional uniqueness (source-entity bindings only). Partial indexes are
    # supported on PostgreSQL (prod); the app store enforces the same rule for
    # portability (C1 → 409).
    op.create_index(
        "uq_class_mapping_source_entity",
        "ontology_class_mapping",
        ["class_id", "source_system"],
        unique=True,
        postgresql_where=sa.text(f"mapping_type IN ({_SOURCE_ENTITY_TYPES})"),
    )


def downgrade() -> None:
    op.drop_index("uq_class_mapping_source_entity", table_name="ontology_class_mapping")
    op.drop_index("ix_property_binding_class_mapping_id", table_name="ontology_property_binding")
    op.drop_table("ontology_property_binding")
