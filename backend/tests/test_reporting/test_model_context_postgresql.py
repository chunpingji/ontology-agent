from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from sqlalchemy import select, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.models.ontology_meta import OntologyRelease
from app.models.reporting import OntologySchemaSnapshot
from app.services.ontology_model_context import capture_schema
from tests.test_reporting.test_reporting_postgresql import db as _postgresql_db


@pytest.fixture
def db():
    yield from _postgresql_db.__wrapped__()


def test_concurrent_model_capture_preserves_both_outer_transactions(db):
    barrier = Barrier(2)

    def capture(number):
        with Session(db.bind) as session:
            session.add(OntologyRelease(release_no=f"concurrent-{number}", title="test"))
            barrier.wait()
            identity = capture_schema(session, {"urn:Class": {"parents": []}})
            session.commit()
            return identity

    with ThreadPoolExecutor(max_workers=2) as pool:
        identities = list(pool.map(capture, range(2)))
    assert identities[0] == identities[1]
    assert len(list(db.scalars(select(OntologySchemaSnapshot)))) == 1
    assert len(list(db.scalars(select(OntologyRelease)))) == 2


def test_model_snapshots_cannot_be_rewritten_with_bulk_sql(db):
    identity = capture_schema(db, {"urn:Class": {"parents": []}})
    db.commit()
    with pytest.raises(DBAPIError, match="immutable"):
        db.execute(
            update(OntologySchemaSnapshot)
            .where(OntologySchemaSnapshot.id == identity)
            .values(payload={})
        )
    db.rollback()
    assert db.get(OntologySchemaSnapshot, identity).payload == {"urn:Class": {"parents": []}}
