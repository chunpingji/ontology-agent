"""Add the budget control without changing existing identities or accounting data."""

import importlib.util
from pathlib import Path

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, inspect, text


def test_budget_control_upgrade_defaults_existing_and_new_rows_without_rewriting_inputs():
    path = Path(__file__).parents[2] / "alembic/versions/0035_ranking_budget_control.py"
    spec = importlib.util.spec_from_file_location("ranking_budget_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    assert migration.down_revision == "0034_model_request_run_id"
    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        connection.execute(text(
            "CREATE TABLE document_analysis_runs "
            "(recognition_run_id TEXT PRIMARY KEY, run_fingerprint TEXT, progress TEXT)"
        ))
        connection.execute(text(
            "INSERT INTO document_analysis_runs VALUES (:id, :fingerprint, :progress)"
        ), {"id": "old-run", "fingerprint": "frozen-input", "progress": '{"model_calls": 4}'})
        before = connection.execute(text("SELECT * FROM document_analysis_runs")).one()
        migration.op = Operations(MigrationContext.configure(connection))
        migration.upgrade()
        current = connection.execute(text("SELECT * FROM document_analysis_runs")).one()
        assert tuple(current[:3]) == tuple(before)
        assert current.ranking_budget_enabled == 1
        column = next(
            item for item in inspect(connection).get_columns("document_analysis_runs")
            if item["name"] == "ranking_budget_enabled"
        )
        assert not column["nullable"]
        connection.execute(text(
            "INSERT INTO document_analysis_runs (recognition_run_id) VALUES ('new-run')"
        ))
        assert connection.scalar(text(
            "SELECT ranking_budget_enabled FROM document_analysis_runs "
            "WHERE recognition_run_id = 'new-run'"
        )) == 1
        migration.downgrade()
        assert connection.execute(text(
            "SELECT * FROM document_analysis_runs WHERE recognition_run_id = 'old-run'"
        )).one() == before
    engine.dispose()
