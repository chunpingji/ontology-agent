"""Unify editable property cardinality without reclassifying unspecified values."""

import sqlalchemy as sa

from alembic import op

revision = "0038_property_cardinality"
down_revision = "0037_doc_analysis_liveness"
branch_labels = None
depends_on = None


def upgrade():
    for table in ("ontology_link_type", "ontology_data_property"):
        op.add_column(table, sa.Column(
            "multiplicity", sa.String(20), nullable=False, server_default="unspecified",
        ))
        op.add_column(table, sa.Column(
            "cardinality_seeded", sa.Boolean(), nullable=False, server_default=sa.true(),
        ))
    for name in ("min_cardinality", "max_cardinality"):
        op.add_column("ontology_data_property", sa.Column(name, sa.Integer(), nullable=True))
    # Existing numeric limits remain independent draft declarations. Preserve
    # conflicting values for explicit validation instead of silently rewriting
    # them; only fill an absent maximum from the legacy functional flag.
    op.execute(sa.text(
        "UPDATE ontology_link_type SET multiplicity = 'single', "
        "max_cardinality = COALESCE(max_cardinality, 1) "
        "WHERE is_functional = true"
    ))
    # Legacy data metadata did not represent FunctionalProperty. The first seed
    # can recover it from authoritative TTL, provided the row is still pristine.
    op.execute(sa.text("UPDATE ontology_data_property SET cardinality_seeded = false"))


def downgrade():
    for name in ("max_cardinality", "min_cardinality"):
        op.drop_column("ontology_data_property", name)
    for table in ("ontology_data_property", "ontology_link_type"):
        op.drop_column(table, "cardinality_seeded")
        op.drop_column(table, "multiplicity")
