"""Upgrade preserves frozen runs and permits current receipts without snapshots."""

import importlib.util
from pathlib import Path

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, inspect, text


def test_current_state_migration_preserves_legacy_references():
    path = Path(__file__).parents[2] / "alembic/versions/0040_current_recognition_state.py"
    spec = importlib.util.spec_from_file_location("current_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        connection.exec_driver_sql("PRAGMA foreign_keys=ON")
        for sql in (
            "CREATE TABLE document_analysis_runs (recognition_run_id CHAR(32) PRIMARY KEY)",
            "CREATE TABLE document_analysis_artifacts (artifact_id VARCHAR(200) PRIMARY KEY)",
            "CREATE TABLE document_analysis_event_batches (batch_id VARCHAR(200) PRIMARY KEY, "
            "checkpoint_artifact_id VARCHAR(200) NOT NULL REFERENCES document_analysis_artifacts)",
            "CREATE TABLE document_analysis_property_repairs "
            "(operation_id VARCHAR(200) PRIMARY KEY, base_checkpoint_artifact_id VARCHAR(200) "
            "NOT NULL REFERENCES document_analysis_artifacts)",
            "INSERT INTO document_analysis_runs VALUES ('12345678123456781234567812345678')",
            "INSERT INTO document_analysis_artifacts VALUES ('old-checkpoint')",
            "INSERT INTO document_analysis_event_batches VALUES ('old-batch', 'old-checkpoint')",
            "INSERT INTO document_analysis_property_repairs "
            "VALUES ('old-repair', 'old-checkpoint')",
        ):
            connection.exec_driver_sql(sql)
        migration.op = Operations(MigrationContext.configure(connection))
        migration.upgrade()
        assert connection.execute(
            text(
                "SELECT work_version, request_version, ranking_version FROM document_analysis_runs"
            )
        ).one() == (0, 0, 0)
        assert (
            connection.execute(
                text("SELECT checkpoint_artifact_id FROM document_analysis_event_batches")
            ).scalar()
            == "old-checkpoint"
        )
        connection.execute(
            text(
                "INSERT INTO document_analysis_event_batches "
                "(batch_id, committed_work_version) VALUES ('current', 1)"
            )
        )
        assert {
            "document_analysis_current_state",
            "document_analysis_results",
            "document_analysis_requests",
        } <= set(inspect(connection).get_table_names())
        connection.execute(
            text("DELETE FROM document_analysis_event_batches WHERE batch_id='current'")
        )
        migration.downgrade()
        assert "document_analysis_current_state" not in inspect(connection).get_table_names()
    engine.dispose()
