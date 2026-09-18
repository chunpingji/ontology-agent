"""Apply the actual migration to disposable tables with pre-existing metadata."""

import importlib.util
from pathlib import Path

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, inspect, text


def test_quantity_migration_preserves_limits_and_initializes_only_missing_dimensions():
    path = Path(__file__).parents[2] / "alembic/versions/0038_property_cardinality.py"
    spec = importlib.util.spec_from_file_location("property_cardinality_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    assert migration.down_revision == "0037_doc_analysis_liveness"
    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        connection.execute(text(
            "CREATE TABLE ontology_link_type (id TEXT PRIMARY KEY, label TEXT, "
            "is_functional BOOLEAN, min_cardinality INTEGER, max_cardinality INTEGER)"
        ))
        connection.execute(text(
            "INSERT INTO ontology_link_type VALUES "
            "('single', '单值', true, NULL, NULL), ('bounded', '已有限制', false, 1, 5), "
            "('zero', '零值草稿', true, NULL, 0), ('conflict', '冲突草稿', true, 2, 5)"
        ))
        connection.execute(text(
            "CREATE TABLE ontology_data_property (id TEXT PRIMARY KEY, label TEXT, version INTEGER)"
        ))
        connection.execute(text("INSERT INTO ontology_data_property VALUES ('old', '旧草稿', 7)"))
        migration.op = Operations(MigrationContext.configure(connection))
        migration.upgrade()
        rows = {row.id: row for row in connection.execute(text("SELECT * FROM ontology_link_type"))}
        assert rows["single"].multiplicity == "single"
        assert rows["single"].min_cardinality is None and rows["single"].max_cardinality == 1
        assert rows["bounded"].multiplicity == "unspecified"
        assert (rows["bounded"].min_cardinality, rows["bounded"].max_cardinality) == (1, 5)
        assert (rows["zero"].multiplicity, rows["zero"].max_cardinality) == ("single", 0)
        assert (rows["conflict"].multiplicity, rows["conflict"].min_cardinality,
                rows["conflict"].max_cardinality) == ("single", 2, 5)
        data = connection.execute(text("SELECT * FROM ontology_data_property")).one()
        assert (data.label, data.version) == ("旧草稿", 7)
        assert data.multiplicity == "unspecified" and data.cardinality_seeded == 0
        assert data.min_cardinality is None and data.max_cardinality is None
        connection.execute(text("INSERT INTO ontology_data_property (id) VALUES ('new')"))
        assert connection.scalar(text(
            "SELECT cardinality_seeded FROM ontology_data_property WHERE id='new'"
        )) == 1
        migration.downgrade()
        columns = inspect(connection).get_columns("ontology_data_property")
        assert {col["name"] for col in columns} == {"id", "label", "version"}
        assert connection.execute(text(
            "SELECT label, version FROM ontology_data_property WHERE id='old'"
        )).one() == ("旧草稿", 7)
    engine.dispose()
