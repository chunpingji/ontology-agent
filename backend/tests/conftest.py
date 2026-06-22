"""Shared pytest fixtures for the T-Box workbench backend.

Uses a shared in-memory SQLite database (StaticPool) so the FastAPI app, the
metadata store, and the test body all see the same data without a live
PostgreSQL. The Owlready2 World is replaced by a lightweight fake so contract
tests do not require a loaded ontology — TTL export/diff is driven by rdflib
from the metadata tables (R3) and is exercised directly.
"""

from __future__ import annotations

import os

# Pin SQLite BEFORE importing app modules (app.db builds the engine at import).
os.environ["DATABASE_URL"] = "sqlite://"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

import app.models  # noqa: E402,F401  (register all tables on Base.metadata)
from app.db import Base, get_db  # noqa: E402
from app.dependencies import get_ontology_engine, get_ontology_meta_store  # noqa: E402
from app.main import app  # noqa: E402
from app.models.ontology_meta import AppRole, AppUser, ROLE_NAMES  # noqa: E402
from app.services.ontology_meta_store import OntologyMetaStore  # noqa: E402


# --- engine / session -------------------------------------------------------
test_engine = create_engine(
    "sqlite://",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestSessionLocal = sessionmaker(bind=test_engine, autoflush=False, expire_on_commit=False)


class FakeOntologyEngine:
    """No-op stand-in for OntologyEngine. Records projection calls so tests can
    assert the publish path attempted a World projection without owlready2."""

    is_loaded = True

    def __init__(self) -> None:
        self.projected: list = []

    def project_entities(self, entities):  # best-effort World projection
        self.projected.append(entities)

    # tolerate any other engine call the endpoints might make
    def __getattr__(self, name):
        def _noop(*args, **kwargs):
            return None

        return _noop


@pytest.fixture()
def db():
    Base.metadata.create_all(bind=test_engine)
    session = TestSessionLocal()
    # seed roles + a senior analyst user
    for rname in ROLE_NAMES:
        if not session.query(AppRole).filter_by(name=rname).first():
            session.add(AppRole(name=rname, description=rname))
    session.commit()
    sr = session.query(AppRole).filter_by(name="senior_analyst").first()
    if not session.query(AppUser).filter_by(username="analyst").first():
        session.add(AppUser(username="analyst", display_name="Analyst", role_id=sr.id))
    session.commit()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=test_engine)


@pytest.fixture(autouse=True)
def _isolate_ontology_dir(tmp_path, monkeypatch):
    """将 settings.ontology_dir 指向每个测试独立的临时目录，避免发布流程
    （_write_and_commit）污染真实 ontology/ 目录或在真实仓库产生提交，
    并保证 export/diff 的基线图为空、可独立复现（测试隔离）。"""
    from app.config import settings

    iso = tmp_path / "ontology"
    iso.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(settings, "ontology_dir", iso)
    yield


@pytest.fixture()
def fake_engine():
    return FakeOntologyEngine()


@pytest.fixture()
def client(db, fake_engine):
    def _get_db_override():
        try:
            yield db
        finally:
            pass

    def _get_engine_override():
        return fake_engine

    def _get_meta_store_override():
        return OntologyMetaStore(db=db, engine=fake_engine)

    app.dependency_overrides[get_db] = _get_db_override
    app.dependency_overrides[get_ontology_engine] = _get_engine_override
    app.dependency_overrides[get_ontology_meta_store] = _get_meta_store_override
    # TestClient is NOT used as a context manager, so the app lifespan
    # (which loads the real ontology) does not run.
    yield TestClient(app)
    app.dependency_overrides.clear()


# --- auth header fixtures ----------------------------------------------------
@pytest.fixture()
def analyst_headers():
    return {"X-User": "analyst", "X-Role": "senior_analyst"}


@pytest.fixture()
def operator_headers():
    return {"X-User": "op", "X-Role": "operator"}


@pytest.fixture()
def qa_headers():
    return {"X-User": "qa01", "X-Role": "qa"}
