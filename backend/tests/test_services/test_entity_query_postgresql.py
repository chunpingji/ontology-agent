"""Disposable-schema verification; opt in with a dedicated *_test PostgreSQL database."""

import os
from configparser import ConfigParser
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from alembic.config import Config
from rdflib import Graph
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from alembic import command
from app.config import settings
from app.db import Base
from app.models.mock_data import MockProductionArea
from app.models.ontology_meta import OntologyClass, OntologyClassMapping, OntologyPropertyBinding
from app.schemas.entity_query import EntityQueryRequest
from app.services.entity_query import EntityQueryService


@pytest.fixture
def pg_query_engine(monkeypatch):
    raw = os.getenv("MAPPED_ENTITY_QUERY_TEST_DATABASE_URL")
    if not raw:
        pytest.skip("MAPPED_ENTITY_QUERY_TEST_DATABASE_URL 未配置（需专用 *_test 库）")
    url = make_url(raw)
    if url.get_backend_name() != "postgresql" or not (url.database or "").endswith("_test"):
        raise ValueError("Mapped query tests require a dedicated PostgreSQL *_test database")
    admin = create_engine(url)
    schema = "entity_query_" + uuid4().hex
    with admin.begin() as conn:
        conn.execute(text(f'CREATE SCHEMA "{schema}"'))
    scoped_url = url.update_query_dict({"options": f"-csearch_path={schema}"})
    engine = create_engine(scoped_url)
    monkeypatch.setattr(settings, "database_url", scoped_url.render_as_string(hide_password=False))
    try:
        yield engine
    finally:
        engine.dispose()
        with admin.begin() as conn:
            conn.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin.dispose()


def test_incremental_migration_and_partial_source_failure(pg_query_engine):
    engine = pg_query_engine
    # Construct the directly preceding schema; the historical base migration creates
    # current metadata and therefore cannot serve as a clean all-history replay.
    Base.metadata.create_all(engine)
    with engine.begin() as conn:
        conn.execute(text("DROP INDEX uq_mock_class_source_dataset"))
        conn.execute(text("ALTER TABLE ontology_class_mapping DROP COLUMN query_config"))
    cfg = Config(str(Path(__file__).resolve().parents[2] / "alembic.ini"))
    cfg.file_config = ConfigParser(interpolation=None)
    cfg.file_config.read(cfg.config_file_name)
    command.stamp(cfg, "0041_template_engine")
    command.upgrade(cfg, "head")
    assert "query_config" in {
        c["name"] for c in inspect(engine).get_columns("ontology_class_mapping")
    }
    with engine.connect() as conn:
        assert (
            conn.scalar(text("SELECT version_num FROM alembic_version")) == "0042_mock_entity_query"
        )
    command.downgrade(cfg, "0041_template_engine")
    assert "query_config" not in {
        c["name"] for c in inspect(engine).get_columns("ontology_class_mapping")
    }
    command.upgrade(cfg, "head")
    graph = Graph().parse(
        data="""
@prefix t: <urn:test:> .
@prefix owl: <http://www.w3.org/2002/07/owl#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .
t:Asset a owl:Class .
t:id a owl:DatatypeProperty ; rdfs:domain t:Asset ; rdfs:range xsd:string .
""",
        format="turtle",
    )
    with Session(engine) as db:
        cls = OntologyClass(slpra_iri="urn:test:Asset", label="Asset")
        db.add(cls)
        db.flush()
        for i, dataset in enumerate(("equipment", "production_areas")):
            m = OntologyClassMapping(
                id=UUID(int=i + 1),
                class_id=cls.id,
                mapping_type="mock_dataset",
                source_system="builtin_mock",
                target=dataset,
                query_config={"label_path": "col:label"},
            )
            db.add(m)
            db.flush()
            db.add(
                OntologyPropertyBinding(
                    class_mapping_id=m.id,
                    property_iri="urn:test:id",
                    source_path="col:code" if i else "col:equipment_id",
                )
            )
        db.add(MockProductionArea(code="001", label="A", iri="urn:test:a"))
        db.commit()
        with engine.begin() as conn:
            conn.execute(text("DROP TABLE mock_equipment"))
        result = EntityQueryService(db, SimpleNamespace(entity_query_graph=lambda: graph)).query(
            EntityQueryRequest(
                queries=[
                    {
                        "query_id": "one",
                        "class_iri": "urn:test:Asset",
                        "property_filters": [
                            {
                                "property_iri": "urn:test:id",
                                "value": "001",
                                "datatype_iri": "http://www.w3.org/2001/XMLSchema#string",
                            }
                        ],
                    }
                ]
            ),
        )["results"][0]
        assert result["outcome"] == "matches" and not result["complete"]
        assert [s["status"] for s in result["sources"]] == ["source_unavailable", "complete"]
        assert db.scalar(text("SELECT count(*) FROM mock_production_areas")) == 1
        # The partial index enforces only Mock uniqueness, not old extraction bindings.
        duplicate = OntologyClassMapping(
            class_id=cls.id,
            mapping_type="mock_dataset",
            source_system="builtin_mock",
            target="production_areas",
        )
        db.add(duplicate)
        from sqlalchemy.exc import IntegrityError

        with pytest.raises(IntegrityError):
            db.flush()
        db.rollback()


def test_concurrent_mock_property_creation_keeps_one_binding(pg_query_engine):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    from fastapi import HTTPException

    from app.schemas.ontology import PropertyBindingCreate
    from app.services.ontology_meta_store import OntologyMetaStore

    engine = pg_query_engine
    Base.metadata.create_all(engine)
    graph = Graph().parse(
        data="""
@prefix t: <urn:test:> .
@prefix owl: <http://www.w3.org/2002/07/owl#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .
t:Asset a owl:Class .
t:id a owl:DatatypeProperty ; rdfs:domain t:Asset ; rdfs:range xsd:string .
""",
        format="turtle",
    )
    ontology = SimpleNamespace(entity_query_graph=lambda: graph)
    with Session(engine) as db:
        cls = OntologyClass(slpra_iri="urn:test:Asset", label="Asset")
        db.add(cls)
        db.flush()
        mapping = OntologyClassMapping(
            class_id=cls.id,
            mapping_type="mock_dataset",
            source_system="builtin_mock",
            target="production_areas",
            query_config={"label_path": "col:label"},
        )
        db.add(mapping)
        db.commit()
        mid = str(mapping.id)
    barrier = Barrier(2)

    def create():
        with Session(engine) as db:
            store = OntologyMetaStore(db, ontology)
            barrier.wait(timeout=10)
            try:
                store.create_property_binding(
                    mid,
                    PropertyBindingCreate(
                        property_iri="urn:test:id",
                        source_path="col:code",
                    ),
                    "test",
                )
                return 201
            except HTTPException as exc:
                return exc.status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(create) for _ in range(2)]
        assert sorted(f.result(timeout=15) for f in futures) == [201, 422]
    with Session(engine) as db:
        assert db.query(OntologyPropertyBinding).count() == 1
