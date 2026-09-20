"""Record discovery authorizes class-owned properties, never an invented subject."""

import pytest
from pydantic import ValidationError

from app.services.extraction.ontology_guided.claim_protocol import ExtractionProfile
from app.services.extraction.ontology_guided.contracts import (
    OntologyClassDefinition,
    OntologySnapshot,
    SlotSpec,
)
from app.services.extraction.ontology_guided.record_discovery import (
    RecordDiscoveryTask,
    compile_record_schema_card,
    resolve_record_property,
)


def test_discovery_task_has_no_assumed_subject_or_predicate():
    task = RecordDiscoveryTask.create(
        run_fingerprint="run", record_id="row-1", schema_card_id="card",
        analysis_scope_ref="scope", dependency_hash="sources",
    )
    assert "subject" not in task.model_dump()
    assert "predicate_iri" not in task.model_dump()
    with pytest.raises(ValidationError):
        RecordDiscoveryTask.model_validate({**task.model_dump(), "subject": {}})
    assert task == RecordDiscoveryTask.create(
        run_fingerprint="run", record_id="row-1", schema_card_id="card",
        analysis_scope_ref="scope", dependency_hash="sources",
    )


def test_same_property_iri_keeps_each_class_datatype_and_unit():
    ontology = OntologySnapshot(
        snapshot_id="ontology", ontology_hash="frozen",
        classes={iri: OntologyClassDefinition(
            iri=iri, label=iri, source_hash="fixture", declared_properties=[SlotSpec(
                iri="urn:value", label="value", declared_by=[iri],
                datatype_iris=[datatype], canonical_unit=unit,
            )],
        ) for iri, datatype, unit in (
            ("urn:A", "http://www.w3.org/2001/XMLSchema#decimal", "mg"),
            ("urn:B", "http://www.w3.org/2001/XMLSchema#string", None),
        )},
    )
    card = compile_record_schema_card(
        ontology, class_iris=["urn:A", "urn:B"],
        analysis_scope_ref="scope", profile=ExtractionProfile(),
    )
    assert resolve_record_property(card, "urn:A", "urn:value").canonical_unit == "mg"
    assert resolve_record_property(card, "urn:B", "urn:value").datatype_iris == [
        "http://www.w3.org/2001/XMLSchema#string"
    ]
    with pytest.raises(ValueError, match="class_outside_menu"):
        resolve_record_property(card, "urn:foreign", "urn:value")
    with pytest.raises(ValueError, match="predicate_outside_menu"):
        resolve_record_property(card, "urn:A", "urn:foreign")


def test_catalog_preserves_inherited_fields_and_limits_formal_focus_routes():
    from app.services.extraction.ontology_guided.contracts import EdgeSpec
    from app.services.extraction.ontology_guided.record_discovery import compile_discovery_catalog
    from tests.test_extraction.test_ontology_guided_core import _definition

    classes = {
        "urn:R": _definition("urn:R", "报告", relationships=[EdgeSpec(
            iri="urn:describes", label="描述", declared_by=["urn:R"],
            range_class_iris=["urn:Parent"],
        ), EdgeSpec(iri="urn:other", label="其他", declared_by=["urn:R"],
                    range_class_iris=["urn:Foreign"])]),
        "urn:Parent": _definition("urn:Parent", "父类", properties=[SlotSpec(
            iri="urn:value", label="值", declared_by=["urn:Parent"],
            datatype_iris=["http://www.w3.org/2001/XMLSchema#string"],
        )]),
        "urn:Child": _definition("urn:Child", "子类", parents=["urn:Parent"]),
        "urn:Foreign": _definition("urn:Foreign", "其他"),
    }
    ontology = OntologySnapshot(snapshot_id="types", ontology_hash="types", classes=classes)
    catalog = compile_discovery_catalog(
        ontology, "urn:R", max_hops=1, focus_paths=[("urn:describes",)],
    )
    assert set(catalog.class_depths) == {"urn:R", "urn:Parent", "urn:Child"}
    card = compile_record_schema_card(ontology, class_iris=["urn:Child"],
                                      analysis_scope_ref=catalog.analysis_scope_ref,
                                      profile=ExtractionProfile())
    assert card.for_class("urn:Child").properties[0].iri == "urn:value"
    root_only = compile_discovery_catalog(ontology, "urn:R", max_hops=0)
    assert set(root_only.class_depths) == {"urn:R"}


@pytest.mark.parametrize("labels", [
    ("同名甲", "同名甲"), ("同名甲 / 甲型装置", "甲型装置"),
])
def test_reference_pages_keep_local_groups_and_same_name_ambiguity_intact(labels):
    from types import SimpleNamespace as NS

    from app.services.extraction.ontology_guided.contracts import TraversalScope
    from app.services.extraction.ontology_guided.record_discovery import reference_pages

    scope = TraversalScope.create()
    current = NS(record_id="r2", section_node_id="s", text="同名甲、甲型装置、乙、丙",
                 source_units=[NS(evidence_id="local")])
    earlier = NS(record_id="r1", section_node_id="s")
    index = NS(records=[earlier, current], by_id={"r2": current},
               records_by_evidence={"old": [earlier], "local": [current]})
    nodes = {identity: NS(entity_id=identity, label=name, class_iri="urn:T", root=False,
                         evidence_refs=[NS(evidence_id=source)])
             for identity, name, source in (("a", labels[0], "local"),
                                            ("a2", labels[1], "old"),
                                            ("b", "乙", "old"), ("c", "丙", "old"))}
    origins = {identity: {"scope": scope.model_dump(mode="json")} for identity in nodes}
    pages = reference_pages(NS(record_id="r2", scope=scope), index, nodes, origins, {},
                            {"urn:T"}, page_size=1)
    assert set().union(*(set(page) for page in pages)) == set(nodes)
    assert all({"a", "a2"} <= set(page) for page in pages)
    assert len(pages) == 2
