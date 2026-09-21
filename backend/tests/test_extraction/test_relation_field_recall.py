"""Range-field recall finds record candidates without granting fact authority."""

import json
from dataclasses import replace

import pytest

from app.services.extraction.evidence_identity import evidence_hash
from app.services.extraction.ontology_guided import tool_runtime as runtime
from app.services.extraction.ontology_guided.claim_freeze import freeze_proposal
from app.services.extraction.ontology_guided.claim_protocol import (
    DiscoveryEnvelope,
    ExtractionProfile,
    compile_schema_card,
)
from app.services.extraction.ontology_guided.context import assemble_context
from app.services.extraction.ontology_guided.contracts import (
    DocumentContext,
    EdgeSpec,
    OntologySnapshot,
    SlotSpec,
    SubjectRef,
    TraversalScope,
    VerificationTarget,
    VersionedRef,
)
from app.services.extraction.ontology_guided.model_reference_projection import (
    project_reference_payload,
)
from app.services.extraction.ontology_guided.ontology_plan import compile_local_menu
from app.services.extraction.ontology_guided.records import RecordIndex
from app.services.extraction.ontology_guided.scheduler import RecognitionTask
from app.services.extraction.tool_validation.vocabulary import build_extraction_vocabulary
from app.services.llm import local_client
from tests.test_extraction.test_batch_model_adapter import batch_setup, finalize
from tests.test_extraction.test_layered_recognition import ROOT, arguments
from tests.test_extraction.test_ontology_guided_core import _definition
from tests.test_extraction.test_tool_engine_adapter import setup_adapter
from tests.test_extraction.test_tool_runtime import call
from tests.test_extraction.test_tool_runtime_recall import FakeGliner25

TARGET, BASE, OTHER = "urn:Assessment", "urn:Base", "urn:Other"
LINK, FIELD = "urn:hasData", "urn:dosingRegimen"
TEXT = "给药方案：每日一次（QD），口服"
VALUE = "每日一次（QD），口服"


@pytest.fixture
def relation_source(tmp_path):
    args = arguments(tmp_path, [TEXT])

    def slot(iri, label):
        return SlotSpec(iri=iri, label=label, description=label + "的原文记录",
                        datatype_iris=["http://www.w3.org/2001/XMLSchema#string"])

    classes = {
        ROOT: _definition(ROOT, "报告", properties=[slot("urn:rootOnly", "报告编号")],
                          relationships=[EdgeSpec(
                              iri=LINK, label="含评估数据", description="报告中的评估数据",
                              range_class_iris=[TARGET],
                          ), EdgeSpec(iri="urn:otherLink", label="其他关系",
                                      range_class_iris=[OTHER])]),
        TARGET: _definition(TARGET, "评估数据", parents=[BASE],
                            properties=[slot(FIELD, "给药方案")],
                            relationships=[EdgeSpec(iri="urn:secondHop", label="引用其他记录",
                                                    range_class_iris=[OTHER])]),
        BASE: _definition(BASE, "基础记录", properties=[slot("urn:inherited", "试验来源")]),
        OTHER: _definition(OTHER, "其他记录", properties=[slot("urn:unrelated", "其他字段")]),
    }
    classes = {iri: definition.model_copy(update={"description": definition.label})
               for iri, definition in classes.items()}
    ontology = OntologySnapshot(snapshot_id="ontology", ontology_hash=evidence_hash(classes),
                                classes=classes, created_from="frozen_fixture")
    index = RecordIndex(args["ir"])
    record = index.records[0]
    unit = record.source_units[0]
    subject = SubjectRef(entity_id="report", revision=1, class_iri=ROOT, is_document_root=True)
    menu = compile_local_menu(ontology, subject)
    predicate = next(edge for edge in menu.relationships if edge.iri == LINK)
    task = RecognitionTask.create(subject=subject, predicate_iri=LINK,
                                  predicate_kind="relationship", record_id=record.record_id,
                                  phase=1, hop=0, dependency_hash="frozen")
    root = VersionedRef(id=subject.entity_id, revision=subject.revision)
    seed = VerificationTarget.create(
        run_fingerprint="test-run", claim_ref=VersionedRef(id=task.claim_lineage_id, revision=1),
        task_id=task.task_id, check_kind="predicate_entailment",
        document_context=DocumentContext(document_hash=index.ir.document_hash,
                                         document_class_iri=ROOT, root_ref=root),
        subject_ref=subject, predicate_iri=LINK, ontology_hash=ontology.ontology_hash,
        source_scope_hash="source", context_hash="seed",
    )
    context = assemble_context(seed, record.record_id, index, predicate=predicate)
    card = compile_schema_card(menu, predicate_iri=LINK, profile=ExtractionProfile(),
                               scope=TraversalScope.create())

    def quote(text):
        return {"evidence_id": unit.evidence_id, "text": text, "context_text": None}

    proposal = dict(entities=[{
        "local_id": "record", "class_iri": TARGET, "representation": "record", "mentions": [],
        "record_components": [{"role": "field", "quote": quote("给药方案")},
                              {"role": "value", "quote": quote(VALUE)}],
        "identifier_claims": [],
    }], relations=[{
        "local_id": "relation", "subject_id": subject.entity_id, "predicate_iri": LINK,
        "object_ids": ["record"], "selection": "all", "bridge_support": [quote(TEXT)],
        "selection_support": [], "bridge_kind": "document_subject_description",
        "bridge_ref_ids": [],
        "qualifiers": {"polarity": "affirmed", "modality": "asserted",
                       "condition_support": [], "scope_qualifiers": []},
        "source_assertion": {"subject_support": [], "object_support": [
            {"object_id": "record", "support": [quote(TEXT)]},
        ], "predicate_support": [quote(TEXT)], "binding_ids": []},
    }], properties=[], external_links=[], observations=[], reference_bindings=[])
    return dict(options=dict(task=task, context=context, card=card, index=index, generation=1),
                menu=menu, ontology=ontology, predicate=predicate, quote=quote,
                source_unit=unit, proposal=proposal)


class FieldExtractor(FakeGliner25):
    """Only the actual field labels/values can hit; entity labels always miss."""

    def __init__(self, ontology, *, hits=True):
        self.entries = build_extraction_vocabulary(ontology, [ROOT, TARGET])["entries"]
        self.seen = []
        self.hits = hits

    def extract_batch_with_spans_strict(self, texts, labels, threshold):
        self.seen.extend((self.entries[label]["iri"], self.entries[label]["role"])
                         for label in labels)
        result = []
        for text in texts:
            spans = []
            for label in labels:
                entry = self.entries[label]
                if not self.hits or entry["iri"] != FIELD:
                    continue
                value = "给药方案" if entry["role"] == "field_label" else VALUE
                if value in text:
                    start = text.index(value)
                    spans.append(dict(start=start, end=start + len(value), text=value,
                                      label=label, score=0.9))
            result.append(spans)
        return result


def recall_context(source):
    options = source["options"]
    return runtime.ToolContext(
        task=options["task"], context=options["context"], index=options["index"],
        menu=source["menu"], profile=ExtractionProfile(), scope=TraversalScope.create(),
        stage="discovery", recovery_kind="none", frozen_claims={}, local_ref_map={},
        entity_dependencies={}, limits=runtime.ToolLimits(20000), measure_result_tokens=len,
        cards={options["card"].schema_card_id: options["card"]},
        ontology_snapshot=source["ontology"], mention_extractor=FieldExtractor(source["ontology"]),
    )


def recall(source, ctx):
    return runtime.dispatch_tool(call(
        "propose_mentions", evidence_ids=[source["source_unit"].evidence_id],
        schema_card_id=source["options"]["card"].schema_card_id,
    ), ctx)


def test_relation_recalls_direct_and_inherited_fields_without_expanding_fact_menu(relation_source):
    ctx = recall_context(relation_source)
    card_before = relation_source["options"]["card"].model_dump()
    result = recall(relation_source, ctx)
    assert result.status == "ok"
    assert {(hit.role, hit.text) for hit in result.data.mentions} == {
        ("field_label", "给药方案"), ("field_value", VALUE),
    }
    assert all(hit.predicate_iris == [FIELD] and hit.class_iris == []
               for hit in result.data.mentions)
    for iri in (FIELD, "urn:inherited"):
        assert {(iri, "field_label"), (iri, "field_value")} <= set(ctx.mention_extractor.seen)
    assert not {"urn:rootOnly", "urn:otherLink", "urn:secondHop", "urn:unrelated"} & {
        iri for iri, _ in ctx.mention_extractor.seen
    }
    assert relation_source["options"]["card"].model_dump() == card_before
    assert result.data.coverage.unprocessed_units == []
    assert not ctx.frozen_claims and not ctx.entity_dependencies


def test_missing_range_field_definition_is_incomplete_not_no_match(relation_source):
    ctx = recall_context(relation_source)
    ontology = ctx.ontology_snapshot.model_copy(deep=True)
    ontology.classes[TARGET].declared_properties[0].description = ""
    result = recall(relation_source, replace(ctx, ontology_snapshot=ontology))
    assert result.status == "ok" and result.data.mentions == []
    assert result.data.coverage.unprocessed_units == [relation_source["source_unit"].evidence_id]
    assert [issue.code for issue in result.data.omissions] == ["definition_missing"]
    assert FIELD in result.data.omissions[0].message


def test_recall_field_does_not_authorize_target_property_claim(relation_source):
    proposal = DiscoveryEnvelope.model_validate(relation_source["proposal"])
    proposal.properties = [{
        "local_id": "forbidden-property", "subject_id": "report", "predicate_iri": FIELD,
        "value_quote": relation_source["quote"](VALUE),
        "field_support": [relation_source["quote"]("给药方案")], "unit_support": [],
        "qualifiers": proposal.relations[0].qualifiers.model_dump(),
        "bridge_kind": "explicit_assertion", "bridge_ref_ids": [],
    }]
    frozen = freeze_proposal(
        DiscoveryEnvelope.model_validate(proposal.model_dump()), **relation_source["options"],
    )
    assert "predicate_outside_menu" in frozen.claim_issues["forbidden-property"]


@pytest.mark.parametrize("batching", [False, True])
@pytest.mark.parametrize("unsupported_facet,recall_hits", [
    (None, True), (None, False), ("referent", True), ("subject_binding", True),
    ("object_binding", True), ("predicate", True),
])
def test_field_recall_record_requires_independent_source_verification(
    relation_source, monkeypatch, batching, unsupported_facet, recall_hits,
):
    source = relation_source
    if batching:
        adapter, unit, context, menu, _stored, requests = batch_setup(
            source, monkeypatch, size=1, relation=True,
        )
        task_id = unit.members[0].task_id
        card = context.members[0].card
    else:
        adapter, task, context, predicate, menu, _stored, requests = setup_adapter(
            source, monkeypatch,
        )
        task_id = task.task_id
        card = compile_schema_card(menu, predicate_iri=LINK, profile=adapter.profile,
                                   scope=TraversalScope.create())
    adapter.ontology = source["ontology"]
    adapter.mention_extractor = FieldExtractor(source["ontology"], hits=recall_hits)
    adapter.reference_resolution = True
    transport = local_client.responses_create
    tools_seen = []
    verifier_targets = []

    def controlled_transport(client, **kwargs):
        # Reuse the fixture's request accounting and independently built verifier.
        response = transport(client, **kwargs)
        for item in kwargs["input_items"]:
            if item.get("type") == "function_call_output":
                tools_seen.append(json.loads(item["output"]))
        if len(requests) == 1:
            args = project_reference_payload(dict(
                evidence_ids=[source["source_unit"].evidence_id],
                schema_card_id=card.schema_card_id,
            ))
            if batching:
                args["member_task_id"] = project_reference_payload({
                    "task_id": task_id,
                })["task_id"]
            response = replace(response, output_items=[{
                "type": "function_call", "call_id": "recall-fields",
                "name": "propose_mentions", "arguments": json.dumps(args),
            }])
        view = json.loads(kwargs["input_items"][0]["content"][0]["text"])
        if view["stage"] == "verification" and kwargs.get("tool_choice") != "required":
            assert "recall-fields" not in json.dumps(kwargs["input_items"])
            for member in view.get("members", [view]):
                verifier_targets.extend(member["verification_input"]["targets"])
            if unsupported_facet:
                output = response.output_items[0]["content"][0]
                answer = json.loads(output["text"])
                for member in answer.get("members", [{"result": answer}]):
                    for verification in member["result"]["verifications"]:
                        for facet in verification["facets"]:
                            if facet["name"] == unsupported_facet:
                                facet.update(verdict="unsupported", reason="原文不足以支持此归属")
                output["text"] = json.dumps(answer)
        return response

    monkeypatch.setattr(local_client, "responses_create", controlled_transport)
    if batching:
        adapter.inspect_work_unit(unit, context, menu)
        outcomes = finalize(adapter, unit, context)
        assert len(outcomes) == 1
        outcome = outcomes[0]
    else:
        outcome = adapter.inspect(task, context, predicate, menu)
    recalls = [result for result in tools_seen if "units" in result.get("data", {})]
    assert recalls, tools_seen
    roles = [role for unit in recalls[0]["data"]["units"]
             for mention in unit["mentions"] for role in mention["roles"]]
    if recall_hits:
        assert roles
        assert all(role["role"] in ("field_label", "field_value") for role in roles)
    else:
        assert recalls[0]["status"] == "no_match"
    assert any(target["target_kind"] == "entity" and target["payload"]["representation"] == "record"
               and {"type", "referent", "subject_role"} <= set(target["required_facets"])
               for target in verifier_targets)
    relation_facets = {"subject_binding", "object_binding", "predicate"}
    assert any(target["target_kind"] == "relation"
               and relation_facets <= set(target["required_facets"]) for target in verifier_targets)
    assert not outcome.properties
    if unsupported_facet is None:
        assert len(outcome.edges) == 1
        assert outcome.nodes[0].grounding_kind == "record"
    else:
        assert not outcome.edges and not outcome.relationship_groups
