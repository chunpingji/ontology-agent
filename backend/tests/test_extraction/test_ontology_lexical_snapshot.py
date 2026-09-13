"""Published lexical annotations remain complete, scoped and replayable."""

from copy import deepcopy
from threading import Event, Thread

import pytest
from pydantic import ValidationError
from rdflib import RDFS, Literal, Namespace
from rdflib.namespace import SKOS

import app.services.ontology_engine as oe
from app.services.extraction.evidence_identity import evidence_hash
from app.services.extraction.ontology_guided.contracts import (
    ONTOLOGY_LEXICAL_SNAPSHOT_VERSION,
    ONTOLOGY_SNAPSHOT_VERSION,
    OntologySnapshot,
)
from app.services.extraction.ontology_guided.ontology_lexical import (
    RDFS_LABEL_IRI,
    SKOS_ALT_LABEL_IRI,
    OntologyLexicalContext,
    build_lexical_context,
)
from app.services.extraction.ontology_guided.ontology_plan import ontology_snapshot_from_engine

NS = Namespace("https://test.example/lexical/")
BASE = f"""
@prefix : <{NS}> .
@prefix owl: <http://www.w3.org/2002/07/owl#> .
@prefix rdfs: <{RDFS}> .
@prefix skos: <{SKOS}> .
@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .
<{NS}> a owl:Ontology .
skos:altLabel a owl:AnnotationProperty .
:altLabel a owl:AnnotationProperty .
:Root a owl:Class ; rdfs:label "主体"@zh, "Root"@en ; skos:altLabel "报告"@zh .
:Target a owl:Class ; skos:altLabel "反应釜"@zh .
:Other a owl:Class ; rdfs:label "反应釜"@zh .
:Unlabelled a owl:Class .
:field a owl:DatatypeProperty ; rdfs:domain :Root ; rdfs:range xsd:string ;
  rdfs:label "正式名称"@zh, "Official name"@en, "Neutral name" ;
  skos:altLabel "旧名称"@zh, "正式名称"@zh ;
  rdfs:comment "Do not add this comment" ;
  :altLabel "Do not accept a matching local predicate name" .
:related a owl:ObjectProperty ; rdfs:domain :Root ; rdfs:range :Target ;
  skos:altLabel "使用设备"@zh .
"""


@pytest.fixture
def lexical_engine(tmp_path, monkeypatch):
    monkeypatch.setattr(oe, "MODULE_NAMES", {"test": str(NS)})
    monkeypatch.setattr(oe, "MODULE_FILES", {"test": "test.ttl"})
    monkeypatch.setattr(oe, "_LOAD_ORDER", ["test"])
    monkeypatch.setattr(oe, "_EXTERNAL_ONTOLOGIES", {})
    (tmp_path / "test.ttl").write_text(BASE, encoding="utf-8")
    engine = oe.OntologyEngine(tmp_path, tmp_path / "world.sqlite3")
    engine.load()
    try:
        yield engine
    finally:
        engine.close()


def test_engine_uses_full_annotation_iris_and_preserves_languages(lexical_engine):
    values = lexical_engine.get_lexical_annotations([str(NS.field), str(NS.Target)])
    actual = {(v["text"], v["language"], v["predicate_iri"]) for v in values[str(NS.field)]}
    assert actual == {
        ("正式名称", "zh", RDFS_LABEL_IRI),
        ("Official name", "en", RDFS_LABEL_IRI),
        ("Neutral name", None, RDFS_LABEL_IRI),
        ("旧名称", "zh", SKOS_ALT_LABEL_IRI),
        ("正式名称", "zh", SKOS_ALT_LABEL_IRI),
    }
    assert values[str(NS.Target)] == [
        {"text": "反应釜", "language": "zh", "predicate_iri": SKOS_ALT_LABEL_IRI},
    ]


def test_engine_ignores_nonliteral_and_blank_annotations(lexical_engine):
    with lexical_engine.modules["test"]:
        graph = lexical_engine._world.as_rdflib_graph()
        graph.add((NS.Unlabelled, RDFS.label, NS.NotATerm))
        graph.add((NS.Unlabelled, SKOS.altLabel, Literal("  ")))
    assert lexical_engine.get_lexical_annotations([str(NS.Unlabelled)]) == {
        str(NS.Unlabelled): [],
    }


def test_snapshot_covers_self_predicates_ranges_and_keeps_homonyms(lexical_engine):
    snapshot = ontology_snapshot_from_engine(lexical_engine)
    assert snapshot.version == ONTOLOGY_LEXICAL_SNAPSHOT_VERSION
    lexical = snapshot.lexical_context
    assert lexical is not None
    assert set(lexical.annotations) == {
        str(NS.Root), str(NS.Target), str(NS.Other), str(NS.Unlabelled),
        str(NS.field), str(NS.related),
    }
    assert lexical.annotations[str(NS.Root)]
    assert lexical.annotations[str(NS.Target)][0].text == "反应釜"
    assert lexical.annotations[str(NS.Other)][0].text == "反应釜"
    assert lexical.annotations[str(NS.Unlabelled)] == []
    root = snapshot.classes[str(NS.Root)]
    # Alias-only values remain query vocabulary and do not change the labels
    # already consumed by H0/H1 or named-object context expansion.
    assert root.declared_relationships[0].label == "related"
    assert snapshot.classes[str(NS.Target)].label == "Target"
    assert OntologySnapshot.model_validate_json(snapshot.model_dump_json()) == snapshot


def test_context_dedup_order_and_hash_preserve_source(lexical_engine):
    values = lexical_engine.get_lexical_annotations([str(NS.field), str(NS.Target)])
    context = build_lexical_context(values)
    shuffled = {iri: list(reversed(terms)) * 2 for iri, terms in reversed(list(values.items()))}
    assert build_lexical_context(shuffled).model_dump() == context.model_dump()
    duplicated_text = [t for t in context.annotations[str(NS.field)] if t.text == "正式名称"]
    assert {t.predicate_iri for t in duplicated_text} == {RDFS_LABEL_IRI, SKOS_ALT_LABEL_IRI}
    corrupted = context.model_dump()
    corrupted["annotations"][str(NS.Target)][0]["text"] = "Changed"
    with pytest.raises(ValidationError, match="hash"):
        OntologyLexicalContext.model_validate(corrupted)


@pytest.mark.parametrize("predicate", ["altLabel", "skos:altLabel", str(RDFS.comment)])
def test_non_authorized_lexical_sources_are_rejected(predicate):
    with pytest.raises(ValidationError):
        build_lexical_context({str(NS.Root): [
            {"text": "Anything", "language": None, "predicate_iri": predicate},
        ]})


def test_alias_changes_hash_and_frozen_restore_does_not_read_live_ontology(lexical_engine):
    before = ontology_snapshot_from_engine(lexical_engine)
    frozen = before.model_dump(mode="json")
    with lexical_engine.modules["test"]:
        lexical_engine._world.as_rdflib_graph().add((NS.field, SKOS.altLabel, Literal("New alias")))
    after = ontology_snapshot_from_engine(lexical_engine)
    assert after.ontology_hash != before.ontology_hash
    assert after.snapshot_id != before.snapshot_id
    assert after.lexical_context.context_hash != before.lexical_context.context_hash
    assert OntologySnapshot.model_validate(frozen).model_dump(mode="json") == frozen


def test_snapshot_hash_is_stable_under_annotation_enumeration(lexical_engine, monkeypatch):
    before = ontology_snapshot_from_engine(lexical_engine)
    reader = lexical_engine.get_lexical_annotations

    def reverse(iris):
        return {iri: list(reversed(terms)) for iri, terms in reversed(list(reader(iris).items()))}

    monkeypatch.setattr(lexical_engine, "get_lexical_annotations", reverse)
    assert ontology_snapshot_from_engine(lexical_engine) == before


def test_arbitrary_display_label_selection_does_not_change_snapshot(lexical_engine, monkeypatch):
    before = ontology_snapshot_from_engine(lexical_engine)
    original = lexical_engine._get_label

    def last_label(entity):
        values = list(entity.label or [])
        return str(values[-1]) if len(values) > 1 else original(entity)

    monkeypatch.setattr(lexical_engine, "_get_label", last_label)
    assert ontology_snapshot_from_engine(lexical_engine) == before


def test_range_annotations_are_read_even_when_class_is_not_in_hierarchy(
    lexical_engine, monkeypatch,
):
    original = lexical_engine.get_class_hierarchy

    def without_target(key):
        return [node for node in original(key) if node.iri != str(NS.Target)]

    monkeypatch.setattr(lexical_engine, "get_class_hierarchy", without_target)
    snapshot = ontology_snapshot_from_engine(lexical_engine)
    assert str(NS.Target) not in snapshot.classes
    assert snapshot.lexical_context.annotations[str(NS.Target)][0].text == "反应釜"


def test_snapshot_rejects_missing_context_and_rehashed_context_with_stale_ontology_hash(
    lexical_engine,
):
    snapshot = ontology_snapshot_from_engine(lexical_engine).model_dump(mode="json")
    missing = deepcopy(snapshot)
    missing.pop("lexical_context")
    with pytest.raises(ValidationError, match="requires its frozen lexical context"):
        OntologySnapshot.model_validate(missing)
    changed = deepcopy(snapshot)
    terms = changed["lexical_context"]["annotations"]
    terms[str(NS.field)][0]["text"] = "A new name"
    changed["lexical_context"] = build_lexical_context(terms).model_dump()
    with pytest.raises(ValidationError, match="snapshot hash"):
        OntologySnapshot.model_validate(changed)


class LegacyEngine:
    def get_modules(self):
        return [{"key": "test"}]

    def get_class_hierarchy(self, key):
        return [{"iri": str(NS.Root), "label": "旧标签"}]

    def __getattr__(self, _name):
        return lambda *_args, **_kwargs: None


def test_legacy_catchall_adapter_retains_original_snapshot_hash_and_shape():
    snapshot = ontology_snapshot_from_engine(LegacyEngine())
    assert snapshot.version == ONTOLOGY_SNAPSHOT_VERSION
    assert snapshot.lexical_context is None
    assert "lexical_context" not in snapshot.model_dump(mode="json")
    assert snapshot.ontology_hash == evidence_hash(snapshot.classes)
    restored = OntologySnapshot.model_validate(snapshot.model_dump())
    assert restored.model_dump() == snapshot.model_dump()


@pytest.mark.parametrize("result", [None, {}, {str(NS.Root): []}])
def test_incomplete_or_failed_lexical_reads_do_not_become_empty_context(
    lexical_engine, monkeypatch, result,
):
    monkeypatch.setattr(lexical_engine, "get_lexical_annotations", lambda _: result)
    with pytest.raises(ValueError, match="explicitly cover every requested IRI"):
        ontology_snapshot_from_engine(lexical_engine)


def test_unloaded_and_failing_source_cannot_freeze(lexical_engine, monkeypatch):
    def fail():
        raise RuntimeError("source unavailable")

    with monkeypatch.context() as patch:
        patch.setattr(lexical_engine._world, "as_rdflib_graph", fail)
        with pytest.raises(RuntimeError, match="source unavailable"):
            ontology_snapshot_from_engine(lexical_engine)
    lexical_engine.close()
    with pytest.raises(oe.OntologyIntegrityError, match="World not loaded"):
        ontology_snapshot_from_engine(lexical_engine)


def test_entire_snapshot_blocks_concurrent_publication(lexical_engine, monkeypatch):
    entered = Event()
    attempting = Event()
    published = Event()
    original = lexical_engine.get_modules

    def paused_read():
        entered.set()
        assert attempting.wait(2)
        assert not published.wait(0.05)
        return original()

    def publish():
        assert entered.wait(2)
        attempting.set()
        with lexical_engine._lock:
            published.set()

    writer = Thread(target=publish)
    monkeypatch.setattr(lexical_engine, "get_modules", paused_read)
    writer.start()
    try:
        snapshot = ontology_snapshot_from_engine(lexical_engine)
        assert snapshot.lexical_context is not None
    finally:
        writer.join(timeout=2)
    assert not writer.is_alive()
    assert published.is_set()
