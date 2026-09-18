"""Actual migration keeps existing runs and exact candidate foreign keys."""

import importlib.util
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, inspect
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.document_analysis import (
    DocumentAnalysisArtifact,
    DocumentAnalysisRun,
    DocumentRunCandidate,
)
from app.models.document_analysis_review import DocumentPropertyReview


def test_property_review_migration_has_exact_foreign_keys_and_preserves_existing_data():
    path = Path(__file__).parents[2] / "alembic/versions/0039_property_review_repair.py"
    spec = importlib.util.spec_from_file_location("review_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    assert migration.down_revision == "0038_property_cardinality"
    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        connection.exec_driver_sql("PRAGMA foreign_keys = ON")
        for model in (DocumentAnalysisRun, DocumentAnalysisArtifact, DocumentRunCandidate):
            model.__table__.create(connection)
        migration.op = Operations(MigrationContext.configure(connection))
        migration.upgrade()
        session = Session(connection, join_transaction_mode="create_savepoint")
        run = DocumentAnalysisRun(
            owner_id="owner", request_key="before-review", request_hash="a" * 64,
            filename="preserved.docx", document_hash="b" * 64, root_class_iri="urn:test:Report",
        )
        session.add(run)
        session.flush()
        run_id = run.recognition_run_id
        session.add(DocumentRunCandidate(
            recognition_run_id=run_id, candidate_id="property", revision=3,
            kind="property", payload_hash="c" * 64, payload={},
        ))
        session.flush()
        values = dict(
            recognition_run_id=run_id, owner_id="owner", actor_role="qa",
            request_key="review", request_hash="d" * 64, candidate_id="property",
            revision=1, decision="rejected", graph_snapshot_id="graph", payload={}, receipt={},
        )
        with pytest.raises(IntegrityError), session.begin_nested():
            session.add(DocumentPropertyReview(**values, candidate_revision=2))
            session.flush()
        session.add(DocumentPropertyReview(**values, candidate_revision=3))
        session.commit()
        session.close()
        migration.downgrade()
        names = inspect(connection).get_table_names()
        assert "document_analysis_property_reviews" not in names
        with Session(connection) as read:
            assert read.get(DocumentAnalysisRun, run_id).filename == "preserved.docx"
            assert read.get(DocumentRunCandidate, (run_id, "property", 3)) is not None
    engine.dispose()
