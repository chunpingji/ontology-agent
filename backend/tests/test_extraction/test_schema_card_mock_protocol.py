"""Synthetic protocol regressions; no real report, archive, model, or numeric calibration."""

from copy import deepcopy

import pytest

from app.evaluation import schema_card_mock_protocol as protocol
from app.evaluation.schema_card_tightened import validate_schema
from app.services.extraction.tool_validation.evidence import EMPTY, resolve_citation

XSD_STRING = "http://www.w3.org/2001/XMLSchema#string"
NAME = protocol.EQ + "equipmentName"
MODEL = protocol.EQ + "modelSpecification"


def cite(ref, quote):
    return {"ref": ref, "quote": quote}


def source(ref, text, row=None, column=None, header=None):
    return {
        "evidence_id": ref, "text": text,
        "table_path": ["synthetic-table"] if row is not None else None,
        "row_index": row, "column_index": column,
        "logical_rows": [row] if row is not None else [],
        "column_indices": [column] if column is not None else [],
        "source_cell_id": f"cell:{row}:{column}" if row is not None else None,
        "cell_structure_valid": row is not None, "is_header": row == 0,
        "column_header_refs": [header] if header else [],
    }


class MockArchive:
    """Narrow versioned-identity contract stub; tests exercise protocol handling."""

    def __init__(self):
        self.rows = {
            "mock:a": {"equipment_id": "RE10001", "name": "反应釜", "specification": "900L"},
            "mock:b": {"equipment_id": "RE10002", "name": "反应釜", "specification": "500L"},
        }

    def validate_link(self, key, version, citation, sources):
        resolved = resolve_citation(sources, citation)
        supported = version == "frozen-v1" and resolved["quote"] == self.rows[key]["equipment_id"]
        return {
            "identity_status": "supported" if supported else "undetermined",
            "issues": [] if supported else ["external_identity_not_proven"],
            "record_key": key, "record_version": version,
        }

    def external_fields(self, key):
        return {"source_kind": "external_mock", "record_key": key, "record_version": "frozen-v1",
                "fields": deepcopy(self.rows[key])}


@pytest.fixture
def example(monkeypatch):
    # The metric's source/semantic gate is represented without claiming SHACL execution.
    # Real validate_metric/SHACL behavior has its own dedicated tests.
    def metric_stub(raw, slot, *, binding_issues=None, semantic_status="not_checked",
                    candidate_id="candidate", **kwargs):
        issues = list(binding_issues) if binding_issues is not None else ["binding_not_checked"]
        if semantic_status != "supported":
            issues.append("semantic_not_supported")
        return {"candidate_id": candidate_id, "validation_status": "failed" if issues else "passed",
                "issues": issues, "normalized_value": None if issues else raw.strip(),
                "quantity": None, "fact_eligible": False, "datatype_iris": slot.datatype_iris}

    monkeypatch.setattr(protocol, "validate_metric", metric_stub)
    sources = {
        "h_id": source("h_id", "设备编号", 0, 0),
        "h_name": source("h_name", "设备名称", 0, 1),
        "h_model": source("h_model", "规格型号", 0, 2),
        "id_a": source("id_a", "RE10001", 1, 0, "h_id"),
        "name_a": source("name_a", "反应釜", 1, 1, "h_name"),
        "model_a": source("model_a", "200L", 1, 2, "h_model"),
        "id_b": source("id_b", "RE10002", 2, 0, "h_id"),
        "name_b": source("name_b", "反应釜", 2, 1, "h_name"),
        "model_b": source("model_b", "500L", 2, 2, "h_model"),
        "alternative": source("alternative", "过滤时使用PF10001或PF10002。"),
        "unknown": source("unknown", "设备编号：DE10009。"),
        "merged": source("merged", "RE10001与RE10002均用于反应。"),
    }
    properties = [
        {"iri": iri, "label": label, "kind": "property", "constraint_status": "resolved",
         "datatype_iris": [XSD_STRING], "canonical_unit": None}
        for iri, label in zip(
            protocol.PROPERTIES, ["设备编号", "设备名称", "规格型号"], strict=True,
        )
    ]
    catalog = {
        protocol.REPORT: {"allowed_relations": [
            {"iri": protocol.USES, "kind": "relationship", "label": "使用设备",
             "range_class_iris": [protocol.EQUIPMENT]},
        ]},
        protocol.EQUIPMENT: {"label": "生产设备", "parents": [], "properties": properties},
    }
    mock = MockArchive()
    candidates = [{"key": key, "version": "frozen-v1"} for key in mock.rows]
    return {"sources": sources, "catalog": catalog, "mock": mock, "candidates": candidates}


def equipment(eid="e1", suffix="a", *, attributes=True, external_key=""):
    identifier = "RE10001" if suffix == "a" else "RE10002"
    attrs = [{"predicate_iri": NAME, "raw": "反应釜",
              "source": cite(f"name_{suffix}", "反应釜"), "field": cite("h_name", "设备名称")},
             {"predicate_iri": MODEL, "raw": "200L" if suffix == "a" else "500L",
              "source": cite(f"model_{suffix}", "200L" if suffix == "a" else "500L"),
              "field": cite("h_model", "规格型号")}]
    return {"id": eid, "class_iri": protocol.EQUIPMENT,
            "anchor": cite(f"id_{suffix}", identifier),
            "equipment_id": cite(f"id_{suffix}", identifier),
            "external_key": external_key, "attributes": attrs if attributes else []}


def proposal(*entities, relations=()):
    return {"entities": list(entities), "relations": list(relations), "observations": []}


def freeze(example, proposed):
    validate_schema(proposed, protocol.proposal_schema(example["sources"], example["mock"].rows))
    return protocol.freeze_proposal(
        proposed, example["sources"], example["catalog"], example["mock"], example["candidates"],
    )


def finalize(example, frozen, overrides=None):
    verdicts = {}
    for target in frozen["targets"]:
        proposed = target["proposal"]
        evidence = (proposed["anchor"] if target["kind"] == "entity" else
                    proposed["source"] if target["kind"] == "property" else proposed["evidence"])
        verdicts[target["id"]] = {
            "verdict": "supported", "evidence": deepcopy(evidence), "reason": "合成证据支持。",
        }
    for cid, change in (overrides or {}).items():
        verdicts[cid].update(change)
    return protocol.finalize(
        frozen, verdicts, example["sources"], example["catalog"], example["mock"],
    )


def accepted_ids(result):
    return {row["id"] for row in result["accepted"]}


def issues_for(result, cid):
    return {issue for bucket in ("unresolved", "rejected") for row in result[bucket]
            if row["id"] == cid for issue in row["issues"]}


def alternative_proposal():
    entities = [
        {"id": eid, "class_iri": protocol.EQUIPMENT, "anchor": cite("alternative", identifier),
         "equipment_id": cite("alternative", identifier), "external_key": "", "attributes": []}
        for eid, identifier in [("e1", "PF10001"), ("e2", "PF10002")]
    ]
    relation = {"subject_id": "document", "predicate_iri": protocol.USES,
                "object_ids": ["e1", "e2"], "selection": "one_of",
                "evidence": cite("alternative", "过滤时使用PF10001或PF10002。"),
                "condition": deepcopy(EMPTY), "polarity": "affirmed"}
    return proposal(*entities, relations=[relation])


@pytest.mark.parametrize("merged_role", ["anchor", "equipment_id"])
def test_one_entity_cannot_merge_two_explicit_equipment_identifiers(example, merged_role):
    entity = equipment(attributes=False)
    entity[merged_role] = cite("merged", "RE10001与RE10002")
    result = finalize(example, freeze(example, proposal(entity)))
    assert "e1" not in accepted_ids(result)
    assert issues_for(result, "e1")


def test_same_name_different_identifiers_remain_distinct_with_separate_values(example):
    result = finalize(example, freeze(example, proposal(
        equipment(external_key="mock:a"), equipment("e2", "b", external_key="mock:b"),
    )))
    assert accepted_ids(result) == {"e1", "e1.a1", "e1.a2", "e2", "e2.a1", "e2.a2"}
    assert {row["entity_id"]: row["link"]["record_key"] for row in result["external_records"]} == {
        "e1": "mock:a", "e2": "mock:b",
    }
    assert result["numeric_calibration"] == "not_evaluated_text_slots_only"
    assert result["fact_eligible"] is False


@pytest.mark.parametrize("anchor", [cite("id_a", "RE10001"), cite("name_a", "反应釜")])
def test_anchor_cannot_borrow_a_different_equipment_identifier_from_another_row(example, anchor):
    entity = equipment(attributes=False, external_key="mock:b")
    entity["anchor"] = anchor
    entity["equipment_id"] = cite("id_b", "RE10002")
    result = finalize(example, freeze(example, proposal(entity)))
    assert "e1" not in accepted_ids(result)
    assert result["external_records"] == []


def test_same_name_does_not_authorize_a_different_external_identifier(example):
    result = finalize(example, freeze(example, proposal(
        equipment(attributes=False, external_key="mock:b"),
    )))
    assert "e1" in accepted_ids(result)
    assert result["external_records"] == []
    assert issues_for(result, "e1.external")


def test_name_only_without_identifier_does_not_authorize_an_external_link(example):
    entity = equipment(attributes=False, external_key="mock:a")
    entity["anchor"] = cite("name_a", "反应釜")
    entity["equipment_id"] = deepcopy(EMPTY)
    result = finalize(example, freeze(example, proposal(entity)))
    assert result["external_records"] == []
    assert issues_for(result, "e1.external")


def test_duplicate_local_slots_with_one_identifier_stay_unresolved(example):
    result = finalize(example, freeze(example, proposal(
        equipment(attributes=False), equipment("e2", attributes=False),
    )))
    assert accepted_ids(result) == set()
    assert all(issues_for(result, cid) for cid in ["e1", "e2"])


def test_preserved_alternative_group_remains_a_qualified_relation(example):
    result = finalize(example, freeze(example, alternative_proposal()))
    assert "r1" in accepted_ids(result)
    relation = next(row["proposal"] for row in result["accepted"] if row["id"] == "r1")
    assert relation["selection"] == "one_of"
    assert relation["object_ids"] == ["e1", "e2"]
    assert "或" in relation["evidence"]["quote"]


@pytest.mark.parametrize("split", [False, True])
def test_alternative_cannot_be_promoted_to_all_or_two_unconditional_relations(example, split):
    proposed = alternative_proposal()
    proposed["relations"][0]["selection"] = "all"
    if split:
        second = deepcopy(proposed["relations"][0])
        proposed["relations"][0]["object_ids"] = ["e1"]
        second["object_ids"] = ["e2"]
        proposed["relations"].append(second)
    result = finalize(example, freeze(example, proposed))
    assert not any(row["kind"] == "relation" for row in result["accepted"])
    assert "alternative_group_not_preserved" in issues_for(result, "r1")


def test_alternative_cannot_drop_other_member_from_evidence_quote(example):
    proposed = alternative_proposal()
    proposed["relations"][0]["evidence"] = cite("alternative", "PF10001")
    result = finalize(example, freeze(example, proposed))
    assert "r1" not in accepted_ids(result)


@pytest.mark.parametrize("bad_quote", [cite("missing", "RE10001"),
                                      cite("id_a", "RE99999"), cite("id_a", "")])
def test_invalid_entity_quote_cannot_be_rescued_by_supported_verifier(example, bad_quote):
    entity = equipment(attributes=False)
    entity["anchor"] = bad_quote
    # Bypass only schema ref enumeration to test the replay gate's own boundary.
    frozen = protocol.freeze_proposal(proposal(entity), example["sources"], example["catalog"],
                                      example["mock"], example["candidates"])
    result = finalize(example, frozen)
    assert "e1" not in accepted_ids(result)
    assert issues_for(result, "e1")


def test_invalid_verification_evidence_cannot_mark_a_valid_candidate_supported(example):
    frozen = freeze(example, proposal(equipment(attributes=False)))
    result = finalize(example, frozen, {"e1": {"evidence": cite("id_a", "不存在的引文")}})
    assert "e1" not in accepted_ids(result)


def test_mock_only_model_specification_cannot_replace_document_property(example):
    entity = equipment(external_key="mock:a")
    entity["attributes"][1]["raw"] = "900L"
    result = finalize(example, freeze(example, proposal(entity)))
    assert "e1.a2" not in accepted_ids(result)
    assert "raw_value_not_in_quote" in issues_for(result, "e1.a2")
    assert result["external_records"][0]["fields"]["fields"]["specification"] == "900L"
    assert not any(row["kind"] == "property" and row["proposal"]["raw"] == "900L"
                   for row in result["accepted"])


def test_external_citation_namespace_is_not_document_evidence(example):
    entity = equipment(external_key="mock:a")
    entity["attributes"][1].update(raw="900L", source=cite("mock:a", "900L"))
    frozen = protocol.freeze_proposal(proposal(entity), example["sources"], example["catalog"],
                                      example["mock"], example["candidates"])
    result = finalize(example, frozen)
    assert "e1.a2" not in accepted_ids(result)
    assert "citation_ref_missing" in issues_for(result, "e1.a2")


@pytest.mark.parametrize("subject_verdict", ["unsupported", "undetermined"])
def test_supported_attributes_do_not_survive_an_unsupported_subject(example, subject_verdict):
    frozen = freeze(example, proposal(equipment(external_key="mock:a")))
    result = finalize(example, frozen, {"e1": {"verdict": subject_verdict}})
    assert accepted_ids(result) == set()
    assert "subject_not_supported" in issues_for(result, "e1.a1")
    assert "subject_not_supported" in issues_for(result, "e1.a2")
    assert result["external_records"] == []


@pytest.mark.parametrize("external_key", ["", "mock:a"])
def test_unknown_document_equipment_is_retained_without_an_external_link(example, external_key):
    entity = equipment(attributes=False, external_key=external_key)
    entity.update(anchor=cite("unknown", "DE10009"), equipment_id=cite("unknown", "DE10009"))
    result = finalize(example, freeze(example, proposal(entity)))
    assert accepted_ids(result) == {"e1"}
    assert result["external_records"] == []


def test_external_record_not_returned_as_a_candidate_cannot_be_linked(example):
    example["candidates"] = [row for row in example["candidates"] if row["key"] != "mock:a"]
    result = finalize(example, freeze(example, proposal(
        equipment(attributes=False, external_key="mock:a"),
    )))
    assert result["external_records"] == []
    assert "external_key_not_returned_by_tool" in issues_for(result, "e1")


def test_cross_row_property_cannot_be_attached_to_another_equipment(example):
    entity = equipment()
    entity["attributes"][1].update(raw="500L", source=cite("model_b", "500L"))
    result = finalize(example, freeze(example, proposal(entity)))
    assert "e1.a2" not in accepted_ids(result)
    assert "owner_row_mismatch" in issues_for(result, "e1.a2")


def test_missing_relation_object_and_unverified_object_cannot_be_retained(example):
    proposed = alternative_proposal()
    proposed["relations"][0]["object_ids"] = ["e1", "e3"]
    result = finalize(example, freeze(example, proposed))
    assert "r1" not in accepted_ids(result)
    assert "relation_object_identity_invalid" in issues_for(result, "r1")
    proposed = alternative_proposal()
    result = finalize(example, freeze(example, proposed), {"e2": {"verdict": "undetermined"}})
    assert "r1" not in accepted_ids(result)
    assert "object_not_supported" in issues_for(result, "r1")


def test_schema_prevents_relation_direction_predicate_and_external_key_invention(example):
    proposed = alternative_proposal()
    schema = protocol.proposal_schema(example["sources"], example["mock"].rows)
    for key, value in [("subject_id", "e1"), ("predicate_iri", protocol.EQ + "hasPart")]:
        changed = deepcopy(proposed)
        changed["relations"][0][key] = value
        with pytest.raises(ValueError):
            validate_schema(changed, schema)
    proposed["entities"][0]["external_key"] = "invented:archive-record"
    with pytest.raises(ValueError):
        validate_schema(proposed, schema)


def test_duplicate_local_entity_ids_fail_before_any_entity_can_be_overwritten(example):
    with pytest.raises(ValueError, match="duplicate_local_entity_id"):
        freeze(example, proposal(equipment(), equipment("e1", "b")))


def test_verification_contract_requires_exact_frozen_target_ids(example):
    frozen = freeze(example, proposal(equipment(attributes=False)))
    schema = protocol.verification_schema(frozen["targets"], example["sources"])
    verdict = {"verdict": "supported", "evidence": cite("id_a", "RE10001"), "reason": "设备编号"}
    validate_schema({"e1": verdict}, schema)
    for invalid in [{}, {"e1": verdict, "e2": verdict}]:
        with pytest.raises(ValueError):
            validate_schema(invalid, schema)
