"""Integer source choices constrain lexical form, never attribute truth."""

import pytest

from app.services.extraction.ontology_guided.context import assemble_context
from app.services.extraction.ontology_guided.contracts import SlotSpec
from app.services.extraction.ontology_guided.evidence_work import EvidenceWorkQueue
from app.services.extraction.ontology_guided.executor import TaskOutcome
from app.services.extraction.ontology_guided.repair_adapter import PropertyDiscovery
from app.services.extraction.ontology_guided.task_citations import TaskCitationProtocol
from app.services.extraction.ontology_guided.value_constraints import XSD
from tests.test_extraction.test_joint_evidence_validation import _fixture


def test_integer_choices_use_only_exact_current_source_numbers(tmp_path):
    text = "本次计划生产3批；备用设备编号RE99，旧批数２。"
    _analysis, index, _task, target, binding = _fixture(tmp_path, extra_text=text)
    record = next(r for r in index.records if r.text == text)
    context = assemble_context(
        target, record.record_id, index, repair_enabled=True, required_context_refs=[binding],
    )
    predicate = SlotSpec(iri="urn:batchCount", label="批数", datatype_iris=[XSD + "integer"])
    request = {"stage": "discovery", "protocol_version": "evidence-repair-v1",
               "predicate": predicate.model_dump(mode="json"),
               "proof_menu": {"allowed_bridges": ["explicit_assertion"]}}
    wire = TaskCitationProtocol(context, request, PropertyDiscovery, "")
    choices = [value for variant in wire.schema["$defs"]["LocatedValueQuote"]["oneOf"]
               for value in variant["properties"]["text"]["enum"]]
    assert choices == ["3", "99", "２"]
    assert "3批" not in choices
    assert all(value in text for value in choices)
    # A datatype union is unresolved; it must not be silently narrowed to integers.
    request["predicate"]["datatype_iris"].append(XSD + "string")
    union = TaskCitationProtocol(context, request, PropertyDiscovery, "")
    assert "enum" not in union.schema["$defs"]["LocatedValueQuote"]["properties"]["text"]


def test_no_integer_source_does_not_invent_a_value(tmp_path):
    _analysis, index, task, target, _binding = _fixture(tmp_path)
    context = assemble_context(target, task.record_id, index, repair_enabled=True)
    predicate = SlotSpec(iri="urn:batchCount", label="批数", datatype_iris=[XSD + "integer"])
    request = {"stage": "discovery", "protocol_version": "evidence-repair-v1",
               "predicate": predicate.model_dump(mode="json"),
               "proof_menu": {"allowed_bridges": ["explicit_assertion"]}}
    wire = TaskCitationProtocol(context, request, PropertyDiscovery, "")
    assert wire.schema["properties"]["proposals"]["maxItems"] == 0


def test_integer_wire_choices_bind_source_value_and_unique_context(tmp_path):
    text = "在611号车间生产，本次计划生产1批，复核批数2。"
    _analysis, index, _task, target, _binding = _fixture(tmp_path, extra_text=text)
    record = next(r for r in index.records if r.text == text)
    context = assemble_context(target, record.record_id, index, repair_enabled=True)
    predicate = SlotSpec(iri="urn:batchCount", label="批数", datatype_iris=[XSD + "integer"])
    request = {"stage": "discovery", "protocol_version": "evidence-repair-v1",
               "predicate": predicate.model_dump(mode="json"),
               "proof_menu": {"allowed_bridges": ["explicit_assertion"]}}
    wire = TaskCitationProtocol(context, request, PropertyDiscovery, "")
    alternatives = wire.schema["$defs"]["LocatedValueQuote"].get("oneOf")
    assert alternatives, "independent value/context enums permit invalid citation combinations"
    quotes = []
    for alternative in alternatives:
        props = alternative["properties"]
        for value in props["text"]["enum"]:
            quote = {"evidence_id": props["evidence_id"]["const"], "text": value,
                     "context_text": props["context_text"]["const"]}
            quotes.append(quote)
            wire.decode({"proposals": [{"kind": "property", "bridge_kind": "explicit_assertion",
                                        "value_quote": quote}]})
    count = next(q for q in quotes if q["text"] == "1")
    assert count["context_text"] == "本次计划生产1批"
    with pytest.raises(ValueError):
        wire.decode({"proposals": [{"kind": "property", "bridge_kind": "explicit_assertion",
                                    "value_quote": {**count, "context_text": None}}]})


def test_datatype_failure_uses_one_reproposal_instead_of_searching_unrelated_records(tmp_path):
    text = "本次计划生产3批。"
    _analysis, index, task, target, _binding = _fixture(tmp_path, extra_text=text)
    record = next(r for r in index.records if r.text == text)
    task = task.model_copy(update={"record_id": record.record_id, "predicate_kind": "property"})
    context = assemble_context(target, record.record_id, index, repair_enabled=True)
    predicate = SlotSpec(iri=task.predicate_iri, label="批数", datatype_iris=[XSD + "integer"])
    protocol = {"gate_issues": {"candidate": ["datatype_mismatch"]}, "assertion_generation": 0,
                "evidence_revision": 1, "discovery": {"proposals": [{"kind": "property",
                    "value_quote": {"evidence_id": record.source_units[0].evidence_id,
                                    "text": "3批"}}]}}
    queue = EvidenceWorkQueue(index)
    outcome = TaskOutcome(semantic_outcome="undetermined", reason_code="record_undetermined",
                          reason="原文引用含单位，不符合整数类型。")
    retry = queue.observe(task, outcome, context, predicate, protocol)
    assert retry.retry_kind == "rediscovery:1"
    assert retry.claim_lineage_id == task.claim_lineage_id
    assert queue.source_refs(retry) == []
