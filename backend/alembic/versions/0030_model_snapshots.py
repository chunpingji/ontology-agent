"""Capture model content independently of business review and template revisions."""

import sqlalchemy as sa

from alembic import op

revision = "0030_model_snapshots"
down_revision = "0029_versioned_calculations"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "ontology_schema_snapshots",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("actor", sa.String(100), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    with op.batch_alter_table("ontology_release") as batch:
        batch.add_column(sa.Column("semantic_snapshot_ref", sa.String(64), nullable=True))
        batch.create_foreign_key(
            "fk_release_semantic_snapshot",
            "ontology_schema_snapshots",
            ["semantic_snapshot_ref"],
            ["id"],
        )
    if op.get_bind().dialect.name == "postgresql":
        op.execute(
            "CREATE TRIGGER immutable_ontology_schema BEFORE UPDATE OR DELETE "
            "ON ontology_schema_snapshots FOR EACH ROW EXECUTE FUNCTION report_immutable()"
        )
    # Existing exact definitions remain readable by the extraction schema hash.
    from app.services.extraction.evidence_identity import evidence_hash

    contracts = sa.table(
        "report_contract_revisions",
        sa.column("kind"),
        sa.column("payload", sa.JSON()),
        sa.column("content_hash"),
    )
    snapshots = sa.table(
        "ontology_schema_snapshots",
        sa.column("id"),
        sa.column("payload", sa.JSON()),
        sa.column("content_hash"),
        sa.column("actor"),
    )
    seen = set()
    for row in op.get_bind().execute(sa.select(contracts).where(contracts.c.kind == "ontology")):
        if evidence_hash(row.payload) != row.content_hash:
            raise RuntimeError("Cannot migrate corrupt ontology contract")
        classes = row.payload["classes"]
        identity = evidence_hash(classes)
        if identity not in seen:
            op.get_bind().execute(
                snapshots.insert().values(
                    id=identity,
                    payload=classes,
                    content_hash=identity,
                    actor="model-snapshot-migration",
                )
            )
            seen.add(identity)


def downgrade():
    raise RuntimeError("Model snapshots are replay evidence; use a forward migration")
