import importlib.util
import uuid
from pathlib import Path

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import Session

from app.models.evidence import EvidenceCandidateRecord, EvidenceJobState


def test_evidence_upgrade_orm_and_downgrade_preserve_legacy(tmp_path):
    path = Path(__file__).parents[2] / "alembic/versions/0026_evidence_facts.py"
    spec = importlib.util.spec_from_file_location("evidence_migration", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    engine = create_engine(f"sqlite:///{tmp_path / 'migration.db'}")
    job_id = uuid.uuid4()
    with engine.begin() as connection:
        connection.execute(text("PRAGMA foreign_keys=ON"))
        connection.execute(text("CREATE TABLE extraction_jobs (id CHAR(32) PRIMARY KEY)"))
        connection.execute(text("CREATE TABLE generated_reports (id TEXT PRIMARY KEY, title TEXT)"))
        connection.execute(text("INSERT INTO extraction_jobs VALUES (:id)"), {"id": job_id.hex})
        connection.execute(text("INSERT INTO generated_reports VALUES ('old', 'legacy')"))
        module.op = Operations(MigrationContext.configure(connection))
        module.upgrade()
    with Session(engine) as db:
        db.add(EvidenceJobState(job_id=job_id, revision=0))
        db.add(
            EvidenceCandidateRecord(
                id="a" * 64,
                job_id=job_id,
                source_key="b" * 64,
                revision=1,
                kind="entity",
                review_status="pending",
                payload={},
            )
        )
        db.commit()
        assert db.get(EvidenceJobState, job_id).job_id == job_id
    with engine.begin() as connection:
        assert (
            connection.execute(
                text("SELECT evidence_snapshot_id FROM generated_reports")
            ).scalar_one()
            is None
        )
        module.op = Operations(MigrationContext.configure(connection))
        module.downgrade()
        assert "evidence_candidates" not in inspect(connection).get_table_names()
        assert (
            connection.execute(text("SELECT title FROM generated_reports")).scalar_one() == "legacy"
        )
    engine.dispose()
