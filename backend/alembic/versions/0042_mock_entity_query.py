"""Mapped Mock query configuration; no entity mirror or source data changes."""
import sqlalchemy as sa

from alembic import op

revision = "0042_mock_entity_query"
down_revision = "0041_template_engine"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("ontology_class_mapping", sa.Column("query_config", sa.JSON(), nullable=True))
    op.create_index(
        "uq_mock_class_source_dataset", "ontology_class_mapping",
        ["class_id", "source_system", "target"], unique=True,
        postgresql_where=sa.text("mapping_type = 'mock_dataset'"),
        sqlite_where=sa.text("mapping_type = 'mock_dataset'"),
    )


def downgrade():
    op.drop_index("uq_mock_class_source_dataset", table_name="ontology_class_mapping")
    op.drop_column("ontology_class_mapping", "query_config")
