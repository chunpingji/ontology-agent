"""Shared test harness for feature 014 (declaration-driven object mapping).

Wires a ``TestClient`` whose ontology engine + meta-store are the seeded
``DrugProduct`` test T-Box (:func:`build_drug_ontology`) and seeds the matching
``OntologyClass`` rows so ``_require_class`` resolves. Kept out of the root
conftest so these domain-gated fixtures don't perturb the existing suite.

The re-exports (constants, doubles) let each US1/US2/US4 test import a single
module. Fixtures are re-exported into ``tests/{contract,integration,unit}/
conftest.py`` so pytest registers them per directory.
"""

from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient

from app.db import get_db
from app.dependencies import get_ontology_engine, get_ontology_meta_store
from app.main import app
from app.services.ontology_meta_store import OntologyMetaStore

# Re-exported so tests import ontology constants + doubles from one place.
from tests.fixtures.extraction_doubles import (  # noqa: F401
    DEFAULT_ROWS,
    PaginatedRestStub,
    make_pages,
    make_source_table,
)
from tests.fixtures.ontology import (  # noqa: F401
    APPROVAL_NUMBER,
    BIOLOGIC,
    DRUG_NAME,
    DRUG_PRODUCT,
    MANUFACTURED_BY,
    MANUFACTURER,
    MFR_NAME,
    RISK_LEVEL,
    build_drug_ontology,
)

ANALYST = {"X-User": "analyst", "X-Role": "senior_analyst"}
OPERATOR = {"X-User": "op", "X-Role": "operator"}

CLASSES = "/api/ontology/classes"
MAPPINGS = "/api/ontology/mappings"

# The class rows the binding routes need (``_require_class`` resolves by IRI).
_SEED_CLASSES = [
    (DRUG_PRODUCT, "药品"),
    (BIOLOGIC, "生物制品"),
    (MANUFACTURER, "生产企业"),
]


@pytest.fixture()
def drug_engine():
    """A freshly seeded DrugProduct test engine (hierarchy/domain/individuals)."""
    return build_drug_ontology()


@pytest.fixture()
def drug_client(db, drug_engine):
    """A ``TestClient`` whose engine + meta-store are the DrugProduct T-Box.

    Seeds the DrugProduct/Manufacturer/BiologicalDrugProduct class rows via the
    real class route, and stashes ``drug_engine``/``drug_store``/``db`` on the
    client so tests can seed individuals/alignments the pipeline will consult.
    """
    store = OntologyMetaStore(db=db, engine=drug_engine)

    def _get_db_override():
        yield db

    app.dependency_overrides[get_db] = _get_db_override
    app.dependency_overrides[get_ontology_engine] = lambda: drug_engine
    app.dependency_overrides[get_ontology_meta_store] = lambda: store

    client = TestClient(app)
    for iri, label in _SEED_CLASSES:
        r = client.post(
            CLASSES,
            json={"slpra_iri": iri, "label": label, "module": "drug"},
            headers=ANALYST,
        )
        assert r.status_code in (201, 409), r.text

    client.drug_engine = drug_engine
    client.drug_store = store
    client.db = db
    try:
        yield client
    finally:
        app.dependency_overrides.clear()


@pytest.fixture()
def source_table(tmp_path):
    """Factory for a temp SQLite source table; cleans up published env vars.

    Usage: ``env = source_table(rows=[...])`` → returns the env-var name the
    class binding's ``source_system`` references (default ``DRUG_DB_DSN``).
    """
    created: list[str] = []

    def _make(**kwargs) -> str:
        env = make_source_table(tmp_path, **kwargs)
        created.append(env)
        return env

    yield _make
    for env in created:
        os.environ.pop(env, None)


# --- declaration helpers (shared by contract + integration tests) ----------
def declare_db_binding(client, *, target="drug_product", source="DRUG_DB_DSN", headers=ANALYST):
    """Declare a ``db_table`` class binding on DrugProduct → returns its id."""
    r = client.post(
        f"{CLASSES}/{DRUG_PRODUCT}/mappings",
        json={"mapping_type": "db_table", "target": target, "source_system": source},
        headers=headers,
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


def add_property_binding(client, mid, **payload):
    """POST one property binding under class binding ``mid`` (returns Response)."""
    return client.post(
        f"{MAPPINGS}/{mid}/property-bindings", json=payload, headers=ANALYST
    )
