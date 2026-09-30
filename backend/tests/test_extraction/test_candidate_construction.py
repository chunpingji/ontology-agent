"""Physical fields stay observations unless a source-owned legal claim is proposed."""

from copy import deepcopy

import pytest
from docx import Document

from app.services.extraction.ontology_guided.attribute_disambiguation import (
    extract_attribute_fields,
    field_payload,
)
from app.services.extraction.ontology_guided.candidate_construction import (
    bound_discovery_schema,
    field_reading_groups,
    physical_entity_issues,
    retain_unbound_fields,
)
from app.services.extraction.ontology_guided.claim_protocol import DiscoveryEnvelope
from app.services.extraction.ontology_guided.context import assemble_record_discovery_context
from app.services.extraction.ontology_guided.records import RecordIndex
from app.services.extraction.word_analysis import analyze_word_core
from tests.test_extraction.test_record_model_adapter import setup_record
from tests.test_extraction.test_record_model_adapter import source as source

pytest_plugins = ["tests.test_extraction.test_record_model_adapter"]


def test_field_groups_retain_unmapped_values_without_inventing_entities(
    tmp_path, monkeypatch, source,
):
    document = Document()
    document.add_paragraph("名称：对象乙；未知测项：蓝紫；缺省项：N/A；对象丙")
    path = tmp_path / "fields.docx"
    document.save(path)
    index = RecordIndex(analyze_word_core(path).ir)
    fields = [field_payload(f) for f in extract_attribute_fields(
        index, property_labels=["名称"], include_unmapped=True,
    )]
    assert {f["label"] for f in fields} == {"名称", "未知测项", "缺省项"}
    groups = field_reading_groups([*fields, fields[0]])
    assert len(groups) == 1 and len(groups[0]["fields"]) == 3
    assert groups[0]["shared_subject_established"] is False
    adapter, task, old, card, *_ = setup_record(source, monkeypatch)
    task = task.model_copy(update={"record_id": index.records[0].record_id})
    context = assemble_record_discovery_context(
        task, card, index, ontology_hash=adapter.ontology.ontology_hash,
        document_context=old.target.document_context.model_copy(
            update={"document_hash": index.ir.document_hash},
        ),
    )
    context.tool_inputs = {"property_fields": fields}
    empty = DiscoveryEnvelope(entities=[], properties=[], relations=[], external_links=[],
                              observations=[])
    result = retain_unbound_fields(empty, context)
    assert result.entities == result.properties == []
    assert [(o.quote.text, o.kind) for o in result.observations] == [
        ("对象乙", "unbound"), ("蓝紫", "unbound"), ("N/A", "missing"),
    ]
    assert all(o.predicate_iri is None and o.subject_id is None for o in result.observations)
    assert retain_unbound_fields(result, context) == result
    # Two roles can use the same physical name, and separate mentions remain separate.
    quote = dict(evidence_id=index.records[0].source_units[0].evidence_id,
                 text="对象乙", context_text=None)
    entity = dict(local_id="one", class_iri="urn:RoleA", representation="mention",
                  mentions=[quote], record_components=[], identifier_claims=[])
    candidates = empty.model_copy(update={"entities": []})
    from app.services.extraction.ontology_guided.claim_protocol import EntityProposal

    candidates.entities = [EntityProposal(**entity), EntityProposal(**{
        **entity, "local_id": "two", "class_iri": "urn:RoleB",
    }), EntityProposal(**{**entity, "local_id": "duplicate"})]
    assert physical_entity_issues(candidates, context) == {
        "duplicate": ["duplicate_physical_referent"],
    }


def test_schema_bounds_volume_and_mutually_exclusive_referent_representation(source, monkeypatch):
    from app.services.extraction.ontology_guided.claim_protocol import compile_stage_schema

    validator = pytest.importorskip("jsonschema").Draft202012Validator
    adapter, task, context, card, *_ = setup_record(source, monkeypatch)
    schema = compile_stage_schema("discovery", card=card,
                                  evidence_ids=[source["source_unit"].evidence_id], targets=[])
    bounded = bound_discovery_schema(schema, 16384)
    small = bound_discovery_schema(schema, 8192)
    assert bounded["properties"]["entities"]["maxItems"] == 4
    assert small["properties"]["entities"]["maxItems"] == 2
    answer = deepcopy(source["proposal"])
    answer.pop("reference_bindings")
    validator(bounded).validate(answer)
    answer["entities"][0]["record_components"] = [
        {"role": "subject", "quote": answer["entities"][0]["mentions"][0]},
    ]
    assert not validator(bounded).is_valid(answer)
    answer["entities"][0]["record_components"] = []
    answer["entities"] *= 10
    assert not validator(bounded).is_valid(answer)


def test_bound_reached_is_explicitly_incomplete_but_keeps_verified_entities(source, monkeypatch):
    adapter, task, context, card, _, requests, _ = setup_record(source, monkeypatch)
    adapter.max_output_tokens = 8192  # Two entities exhaust this smaller physical candidate budget.
    outcome = adapter.inspect_record(task, context, card)
    assert not outcome.complete and outcome.reason_code == "candidate_output_budget_reached"
    assert len(outcome.nodes) == 2 and len(requests) == 2
