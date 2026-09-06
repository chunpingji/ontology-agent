import importlib.util
from pathlib import Path

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, inspect, text


def test_template_evidence_upgrade_preserves_legacy_rows(tmp_path):
    path = Path(__file__).parents[2] / "alembic/versions/0025_template_evidence.py"
    spec = importlib.util.spec_from_file_location("template_evidence_migration", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    engine = create_engine(f"sqlite:///{tmp_path / 'migration.db'}")
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE ast_templates (id TEXT PRIMARY KEY, name TEXT)"))
        connection.execute(text("INSERT INTO ast_templates VALUES ('old', 'legacy')"))
        module.op = Operations(MigrationContext.configure(connection))
        module.upgrade()
        assert "sample_analysis" in {c["name"] for c in inspect(connection).get_columns("ast_templates")}
        row = connection.execute(text("SELECT name, sample_analysis FROM ast_templates")).one()
        assert tuple(row) == ("legacy", None)
        module.downgrade()
        assert connection.execute(text("SELECT name FROM ast_templates")).scalar_one() == "legacy"
    engine.dispose()
