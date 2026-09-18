"""Engine enumeration order cannot change a frozen semantic snapshot identity."""

from copy import deepcopy
from types import SimpleNamespace

import pytest

from app.services.extraction.ontology_guided.contracts import OntologySnapshot, SubjectRef
from app.services.extraction.ontology_guided.ontology_plan import (
    compile_local_menu,
    ontology_snapshot_from_engine,
)

ROOT, FIRST, SECOND = "urn:ordered:Root", "urn:ordered:First", "urn:ordered:Second"
MULTI, PARALLEL = "urn:ordered:multi", "urn:ordered:parallel"
XSD = "http://www.w3.org/2001/XMLSchema#"


class EnumeratedEngine:
    def __init__(self, *, reverse=False):
        self.reverse = reverse
        self.data = [
            {"iri": "urn:ordered:code", "label": "编号",
             "range": [XSD + "string", XSD + "integer"], "identity_key": True},
            {"iri": "urn:ordered:amount", "label": "数量", "range": [XSD + "decimal"]},
        ]
        self.relationships = [
            {"iri": MULTI, "label": "多个range", "range": [FIRST, SECOND]},
            {"iri": "urn:ordered:single", "label": "单个range", "range": [SECOND]},
            {"iri": PARALLEL, "label": "并行声明", "range": [FIRST]},
            {"iri": PARALLEL, "label": "并行声明", "range": [SECOND]},
        ]

    def ordered(self, values):
        items = deepcopy(values)
        return list(reversed(items)) if self.reverse else items

    def get_modules(self):
        return self.ordered([{"key": "root"}, {"key": "ranges"}])

    def get_class_hierarchy(self, module):
        nodes = [{"iri": ROOT, "label": "根类型", "children": []}] if module == "root" else [
            {"iri": FIRST, "label": "类型甲", "children": []},
            {"iri": SECOND, "label": "类型乙", "children": []},
        ]
        return self.ordered(nodes)

    def get_class_detail(self, _iri):
        return SimpleNamespace(comment="固定的类型说明")

    def get_data_properties_by_domain(self, iri):
        return self._properties(self.data) if iri == ROOT else []

    def get_object_properties_by_domain(self, iri):
        return self._properties(self.relationships) if iri == ROOT else []

    def _properties(self, values):
        items = self.ordered(values)
        if self.reverse:
            for item in items:
                item["range"].reverse()
        return items


def _root_menu(snapshot):
    return compile_local_menu(
        snapshot,
        SubjectRef(entity_id="root", revision=1, class_iri=ROOT, is_document_root=True),
    )


def test_engine_reverse_order_preserves_class_hash_snapshot_id_and_local_menu():
    forward = ontology_snapshot_from_engine(EnumeratedEngine())
    backward = ontology_snapshot_from_engine(EnumeratedEngine(reverse=True))
    assert forward.classes == backward.classes
    assert forward.ontology_hash == backward.ontology_hash
    assert forward.snapshot_id == backward.snapshot_id
    assert _root_menu(forward) == _root_menu(backward)


def test_parallel_same_predicate_declarations_are_sorted_without_union_or_loss():
    engine = EnumeratedEngine()
    # Keep the outer predicate order stable while reversing two same-IRI rows.
    other = EnumeratedEngine()
    other.relationships[-2:] = list(reversed(other.relationships[-2:]))
    first = ontology_snapshot_from_engine(engine)
    second = ontology_snapshot_from_engine(other)
    assert first == second
    declarations = first.classes[ROOT].declared_relationships
    parallel = [item for item in declarations if item.iri == PARALLEL]
    assert len(parallel) == 2
    assert {tuple(item.range_class_iris) for item in parallel} == {(FIRST,), (SECOND,)}
    assert next(item for item in declarations if item.iri == MULTI).range_class_iris == [
        FIRST, SECOND,
    ]
    menu = _root_menu(first)
    constrained = next(item for item in menu.relationships if item.iri == PARALLEL)
    assert constrained.constraint_status == "constraint_unresolved"
    assert len(constrained.range_class_iris) == 1
    assert f"relationship_constraint_unresolved:{PARALLEL}" in menu.diagnostics


@pytest.mark.parametrize("field, value", [
    ("range", [FIRST]), ("label", "语义标签改变"),
    ("description", "适用说明改变"), ("max_count", 1),
])
def test_semantic_relationship_changes_still_change_snapshot_identity(field, value):
    baseline = ontology_snapshot_from_engine(EnumeratedEngine())
    changed = EnumeratedEngine()
    changed.relationships[0][field] = value
    snapshot = ontology_snapshot_from_engine(changed)
    assert baseline.classes[ROOT].source_hash != snapshot.classes[ROOT].source_hash
    assert baseline.ontology_hash != snapshot.ontology_hash
    assert baseline.snapshot_id != snapshot.snapshot_id


def test_property_semantics_are_not_erased_by_order_canonicalization():
    baseline = ontology_snapshot_from_engine(EnumeratedEngine())
    changed = EnumeratedEngine(reverse=True)
    changed.data[0]["identity_key"] = False
    snapshot = ontology_snapshot_from_engine(changed)
    assert baseline.ontology_hash != snapshot.ontology_hash


class LegacyEngine(EnumeratedEngine):
    get_object_properties_by_domain = None

    def get_relation_schema(self, iri, max_hops):
        assert max_hops == 1
        if iri != ROOT:
            return []
        return self.ordered([
            {"predicate_iri": "urn:ordered:legacy-a", "predicate_label": "旧声明甲",
             "domain_class_iri": ROOT, "range_class_iri": FIRST, "hop": 1,
             "range_subclasses": self.ordered([{"iri": SECOND}, {"iri": FIRST}])},
            {"predicate_iri": "urn:ordered:legacy-b", "predicate_label": "旧声明乙",
             "domain_class_iri": ROOT, "range_class_iri": SECOND, "hop": 1},
            {"predicate_iri": "urn:ordered:forbidden-hop", "domain_class_iri": ROOT,
             "range_class_iri": SECOND, "hop": 2},
            {"predicate_iri": "urn:ordered:foreign-owner", "domain_class_iri": FIRST,
             "range_class_iri": SECOND, "hop": 1},
        ])


def test_legacy_direct_relation_projection_is_stable_and_stays_one_hop():
    forward = ontology_snapshot_from_engine(LegacyEngine())
    backward = ontology_snapshot_from_engine(LegacyEngine(reverse=True))
    assert forward == backward
    declarations = forward.classes[ROOT].declared_relationships
    assert {item.iri for item in declarations} == {
        "urn:ordered:legacy-a", "urn:ordered:legacy-b",
    }
    legacy = next(item for item in declarations if item.iri.endswith("legacy-a"))
    assert legacy.range_class_iris == [FIRST, SECOND]


def test_existing_frozen_snapshot_roundtrip_keeps_its_recorded_order_and_identity():
    snapshot = ontology_snapshot_from_engine(EnumeratedEngine())
    raw = snapshot.model_dump(mode="json")
    raw["classes"][ROOT]["declared_properties"].reverse()
    raw["classes"][ROOT]["declared_relationships"].reverse()
    # Existing artifacts remain immutable; canonicalization belongs at source
    # construction and must not silently recalculate a restored snapshot.
    raw["snapshot_id"] = "historical-snapshot"
    raw["ontology_hash"] = "historical-hash"
    restored = OntologySnapshot.model_validate(raw, strict=True)
    assert restored.model_dump(mode="json") == raw
