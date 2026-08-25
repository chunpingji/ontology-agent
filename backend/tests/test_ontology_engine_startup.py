"""Startup integrity checks for the authoritative T-Box module set."""

from __future__ import annotations

from pathlib import Path

import pytest

import app.services.ontology_engine as oe
from app.services.ontology_engine import OntologyEngine, OntologyIntegrityError

REAL_ONTOLOGY_DIR = Path(__file__).resolve().parents[2] / "ontology" / "slpra"


def test_real_tbox_loads_every_registered_module(tmp_path):
    engine = OntologyEngine(
        ontology_dir=REAL_ONTOLOGY_DIR,
        store_path=tmp_path / "complete-world.sqlite3",
    )
    try:
        engine.load()

        assert engine.is_loaded is True
        assert set(engine.modules) == set(oe.MODULE_NAMES)
    finally:
        engine.close()


def test_module_load_failure_aborts_startup_and_discards_partial_world(
    tmp_path, monkeypatch,
):
    ontology_dir = tmp_path / "ontology"
    ontology_dir.mkdir(exist_ok=True)
    (ontology_dir / "ok.ttl").write_text(
        """
        @prefix owl: <http://www.w3.org/2002/07/owl#> .
        <http://test.example.com/ok/> a owl:Ontology .
        <http://test.example.com/ok/Entity> a owl:Class .
        """,
        encoding="utf-8",
    )
    monkeypatch.setattr(
        oe,
        "MODULE_NAMES",
        {
            "ok": "http://test.example.com/ok/",
            "broken": "http://test.example.com/broken/",
        },
    )
    monkeypatch.setattr(
        oe,
        "MODULE_FILES",
        {"ok": "ok.ttl", "broken": "missing.ttl"},
    )
    monkeypatch.setattr(oe, "_LOAD_ORDER", ["ok", "broken"])
    monkeypatch.setattr(oe, "_EXTERNAL_ONTOLOGIES", {})
    engine = OntologyEngine(
        ontology_dir=ontology_dir,
        store_path=tmp_path / "partial-world.sqlite3",
    )

    with pytest.raises(OntologyIntegrityError) as caught:
        engine.load()

    message = str(caught.value)
    assert "application startup aborted" in message
    assert "broken" in message
    assert "missing.ttl" in message
    assert engine.is_loaded is False
    assert engine.modules == {}
    assert engine._world is None


def test_registry_drift_aborts_before_loading(tmp_path, monkeypatch):
    monkeypatch.setattr(oe, "MODULE_NAMES", {"document": "http://example.com/document/"})
    monkeypatch.setattr(oe, "MODULE_FILES", {"document": "document.ttl"})
    monkeypatch.setattr(oe, "_LOAD_ORDER", [])
    engine = OntologyEngine(
        ontology_dir=tmp_path,
        store_path=tmp_path / "unused-world.sqlite3",
    )

    with pytest.raises(OntologyIntegrityError, match="document"):
        engine.load()

    assert engine.is_loaded is False
    assert engine._world is None
