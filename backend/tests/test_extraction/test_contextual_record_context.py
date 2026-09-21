"""Joint reading preserves per-record facts and real, non-fact heading context."""

import pytest
from pydantic import ValidationError

from app.services.extraction.ontology_guided.claim_protocol import ExtractionProfile
from app.services.extraction.ontology_guided.context import assemble_record_discovery_context
from app.services.extraction.ontology_guided.contracts import (
    DocumentContext,
    OntologyClassDefinition,
    OntologySnapshot,
    VersionedRef,
)
from app.services.extraction.ontology_guided.reading_groups import build_reading_groups
from app.services.extraction.ontology_guided.record_discovery import (
    RecordDiscoveryPolicy,
    RecordDiscoveryTask,
    compile_record_schema_card,
)
from tests.test_extraction.test_reading_groups import index_for


def context_for(index, group, *, field=False):
    ontology = OntologySnapshot(
        snapshot_id="ontology", ontology_hash="frozen",
        classes={"urn:T": OntologyClassDefinition(
            iri="urn:T", label="产品", source_hash="frozen",
        )},
    )
    card = compile_record_schema_card(
        ontology, class_iris=["urn:T"], analysis_scope_ref="scope", profile=ExtractionProfile(),
    )
    task = RecordDiscoveryTask.create(
        run_fingerprint="run", record_id=group.record_ids[0],
        schema_card_id=card.schema_card_id, analysis_scope_ref="scope", dependency_hash="source",
        source_record_ids=group.record_ids[:1] if field else group.record_ids,
        reading_section_ids=group.section_node_ids, field_id="field" if field else None,
    )
    context = assemble_record_discovery_context(
        task, card, index, ontology_hash=ontology.ontology_hash,
        document_context=DocumentContext(
            document_hash=index.ir.document_hash, document_class_iri="urn:Document",
            root_ref=VersionedRef(id="root", revision=1),
        ),
    )
    return task, context


def test_group_has_all_original_facts_and_headings_only_bind(tmp_path):
    index = index_for(tmp_path, [("分子量", ["537.18"]), ("分子式", ["C23H21N7O3"])])
    group = build_reading_groups(index, field_labels=["分子量", "分子式"])[0]
    before = index.ir.model_dump_json()
    task, context = context_for(index, group)
    assert task.source_record_ids == list(group.record_ids)
    assert {fragment.text for fragment in context.fragments if fragment.fact_eligible} == {
        "537.18", "C23H21N7O3",
    }
    assert {fragment.text for fragment in context.fragments if not fragment.fact_eligible} == {
        "产品基本性质", "分子量", "分子式",
    }
    for fragment in context.fragments:
        assert index.ir.resolve(fragment.anchor) == fragment.text
    assert index.ir.model_dump_json() == before


def test_field_can_read_group_without_authorizing_other_field_values(tmp_path):
    index = index_for(tmp_path, [("编号", ["D-01"]), ("密级", ["机密"])])
    group = build_reading_groups(index, field_labels=["编号", "密级"])[0]
    _, context = context_for(index, group, field=True)
    assert {f.text for f in context.fragments if f.fact_eligible} == {"D-01"}
    assert "机密" in {f.text for f in context.fragments if not f.fact_eligible}


def test_empty_preceding_label_heading_is_real_context(tmp_path):
    index = index_for(tmp_path, [("分子量：", []), ("数值", ["537.18"])])
    group = build_reading_groups(index, field_labels=["分子量"])[0]
    _, context = context_for(index, group)
    assert "分子量：" in {f.text for f in context.fragments if not f.fact_eligible}
    assert [f.text for f in context.fragments if f.fact_eligible] == ["537.18"]


def test_record_policy_freezes_sparse_selection_and_task_preserves_source_membership():
    policy = {"version": "record-discovery-v2", "max_classes_per_card": 4,
              "endpoint_page_size": 8, "candidate_cards_per_record": 2,
              "minimum_similarity": 0.25, "attribute_calibration": "source-observations-v1",
              "table_reading": "bounded-table-rows-v2", "max_feedback_reopens": 8}
    assert RecordDiscoveryPolicy.model_validate(policy).model_dump(mode="json") == policy
    task = RecordDiscoveryTask.create(
        run_fingerprint="run", record_id="record", schema_card_id="card",
        analysis_scope_ref="scope", dependency_hash="source",
    )
    old = task.model_dump(mode="json")
    assert not {"source_record_ids", "reading_section_ids", "purpose", "field_id"} & old.keys()
    assert RecordDiscoveryTask.model_validate(old).model_dump(mode="json") == old
    with pytest.raises(ValidationError, match="record_source_membership_invalid"):
        RecordDiscoveryTask.model_validate({**old, "source_record_ids": ["foreign-record"]})


def test_physical_field_lineage_is_independent_of_class_card():
    options = dict(run_fingerprint="run", record_id="record", analysis_scope_ref="scope",
                   dependency_hash="source", field_id="physical-field")
    a = RecordDiscoveryTask.create(schema_card_id="card-a", **options)
    b = RecordDiscoveryTask.create(schema_card_id="card-b", **options)
    assert a.claim_lineage_id == b.claim_lineage_id
    assert a.purpose == "property_disambiguation"
