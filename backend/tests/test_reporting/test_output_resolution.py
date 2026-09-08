from copy import deepcopy

from app.services.extraction.evidence_identity import evidence_hash
from app.services.reporting.template_compiler import require_valid
from tests.test_extraction.test_fact_commit import entity
from tests.test_reporting.test_fact_selector import iri, property_value, relation, snapshot
from tests.test_reporting.test_output_contracts import (
    compile_example,
    contracts,
    ontology,
    template,
)


def bundle(*values, closed=True, candidates=()):
    facts = snapshot(
        entity("A", "urn:Report"),
        entity("E", "urn:Equipment"),
        relation("ae", "A", "E"),
        *values,
    )
    data = {
        "schema": ontology(),
        "contracts": contracts(),
        "applicable_at": None,
        "sources": {
            "doc": {
                "snapshot": facts,
                "root_entity_id": iri("A"),
                "candidates": list(candidates),
                "discovery": {},
            }
        },
        "records": {},
    }
    if closed:
        key = evidence_hash(
            {
                "root": iri("A"),
                "path": [{"predicate_iri": "urn:uses", "direction": "forward"}],
                "applicable_at": None,
            }
        )
        data["sources"]["doc"]["discovery"][key] = {
            "status": "complete",
            "object_ids": [iri("E")],
            "proof_ref": "review-proof",
            "snapshot_id": facts["snapshot_id"],
        }
    data["source_bundle_id"] = evidence_hash(data)
    return data


def resolve(data, value=None):
    from app.services.reporting.report_snapshot import resolve_snapshot

    plan = require_valid(compile_example(value))
    return resolve_snapshot(plan, data)


def test_optional_conflict_does_not_contaminate_code_consumer():
    data = bundle(
        property_value("code", "E", "urn:code", "E-01"),
        property_value("s1", "E", "urn:spec", "one"),
        property_value("s2", "E", "urn:spec", "two"),
    )
    result = resolve(data)
    row = result["inputs"]["rows"]["items"][0]
    assert row["state"] == "conflict"
    assert row["fields"]["code"]["value"] == "E-01"
    assert row["fields"]["spec"]["value"] is None
    assert result["material_status"] == "ready"
    value = template()
    value["sections"][0]["groups"][0]["units"][0]["render"]["columns"].append(
        {
            "column_id": "spec-col",
            "title": "规格",
            "field_ref": "spec",
        }
    )
    displayed = resolve(data, value)
    assert displayed["material_status"] == "conflict"
    assert len(displayed["inputs"]["rows"]["items"]) == 1


def test_required_missing_field_cannot_be_hidden():
    result = resolve(bundle(property_value("spec", "E", "urn:spec", "one")))
    assert result["material_status"] == "incomplete"
    assert len(result["inputs"]["rows"]["items"]) == 1


def test_optional_missing_field_does_not_block_required_container():
    result = resolve(bundle(property_value("code", "E", "urn:code", "E-01")))
    assert result["material_status"] == "ready"


def test_expected_object_cannot_disappear_from_complete_universe():
    data = bundle(property_value("code", "E", "urn:code", "E-01"))
    for proof in data["sources"]["doc"]["discovery"].values():
        proof["object_ids"].append(iri("E2"))
    result = resolve(data)
    assert result["material_status"] == "incomplete"
    assert result["inputs"]["rows"]["discovery"]["status"] == "open"


def test_projected_boolean_lists_keep_shape_and_discovery():
    from app.services.reporting.condition_resolver import evaluate_expression
    from app.services.reporting.input_resolver import Discovery, typed_value

    typ = {
        "kind": "list",
        "item_type": {
            "kind": "record",
            "fields": {"ok": {"type": {"kind": "boolean"}, "semantic_ref": "urn:ok"}},
        },
    }
    expr = {
        "op": "all",
        "args": [{"op": "input", "input_ref": {"input_id": "x", "field_path": ["ok"]}}],
    }
    for count in (0, 1, 2):
        for status in ("complete", "open"):
            value = typed_value(
                "x",
                typ,
                [{"ok": True} for _ in range(count)],
                discovery=Discovery(status=status, proof_ref="frozen"),
            )
            result = evaluate_expression(expr, {"x": value})
            assert result["result"] == ("TRUE" if status == "complete" else "UNKNOWN")


def test_partial_collection_keeps_rows_but_all_cannot_be_ready():
    value = template()
    value["definitions"]["inputs"]["rows"]["constraints"] = {
        "quantifier": "all",
        "min_count": 1,
        "require_complete_set": True,
    }
    result = resolve(bundle(property_value("code", "E", "urn:code", "E-01"), closed=False), value)
    assert result["material_status"] == "incomplete"
    assert result["inputs"]["rows"]["items"][0]["fields"]["code"]["value"] == "E-01"


def test_published_negative_is_not_a_positive_row():
    data = bundle(property_value("code", "E", "urn:code", "E-01"))
    for record in data["sources"]["doc"]["snapshot"]["assertions"]:
        if record["candidate"]["kind"] == "relationship":
            record["candidate"]["assertion_status"] = "negated"
    result = resolve(data)
    assert result["inputs"]["rows"]["items"] == []
    assert result["material_status"] == "incomplete"


def test_source_mutation_changes_snapshot_but_old_result_stays_frozen():
    data = bundle(property_value("code", "E", "urn:code", "E-01"))
    original = resolve(data)
    frozen = deepcopy(original)
    data["sources"]["doc"]["snapshot"]["assertions"][-1]["candidate"]["literal"].update(
        raw_value="E-02",
        normalized_value="E-02",
    )
    data["source_bundle_id"] = evidence_hash(data["sources"])
    changed = resolve(data)
    assert original == frozen
    assert changed["input_snapshot_id"] != original["input_snapshot_id"]


def test_scalar_types_do_not_coerce_false_zero_or_dates():
    from app.services.reporting.input_resolver import typed_value

    assert typed_value("n", {"kind": "integer"}, 0).value == 0
    assert typed_value("b", {"kind": "boolean"}, False).value is False
    assert typed_value("n", {"kind": "integer"}, False).state == "invalid"
    assert typed_value("d", {"kind": "decimal"}, 1.2).state == "invalid"
    assert typed_value("m", {"kind": "year_month"}, "2026-09").value == "2026-09"
    assert typed_value("d", {"kind": "date"}, "2026-09").state == "invalid"


def test_item_guards_only_consume_selected_row_fields():
    from app.services.reporting.input_resolver import evaluate_uses, typed_value

    typ = {
        "kind": "list",
        "item_type": {
            "kind": "record",
            "fields": {
                "show": {"type": {"kind": "boolean"}, "semantic_ref": "urn:show"},
                "value": {"type": {"kind": "integer"}, "semantic_ref": "urn:value"},
            },
        },
    }
    rows = typed_value(
        "rows",
        typ,
        [
            {"entity_id": "a", "show": False, "value": "invalid"},
            {"entity_id": "b", "show": True, "value": 0},
        ],
    )
    guard = {
        "expression": {
            "op": "input",
            "input_ref": {"input_id": "rows", "field_path": ["show"], "scope": "item"},
        },
        "branch": "true",
    }
    use = {
        "input_id": "rows",
        "field_path": ["value"],
        "scope": "item",
        "guards": [guard],
        "iterations": [{"input_id": "rows", "field_path": []}],
    }
    plan = {"uses": {"out": [use]}, "requirements": []}
    result = evaluate_uses(plan, {"rows": rows})
    assert result["material_status"] == "ready"
    assert [r["status"] for r in result["use_resolutions"]["out"]] == ["inactive", "usable"]
    rows.items[0].fields["show"] = typed_value("show", {"kind": "boolean"}, None)
    assert evaluate_uses(plan, {"rows": rows})["material_status"] == "invalid"


def test_nested_projection_does_not_inherit_parent_closed_universe():
    value = template()
    value["definitions"]["inputs"]["rows"]["projection"]["predicate_path"] = [
        {"predicate_iri": "urn:uses", "direction": "inverse"},
        {"predicate_iri": "urn:uses", "direction": "forward"},
    ]
    result = resolve(bundle(property_value("code", "E", "urn:code", "E-01")), value)
    assert result["inputs"]["rows"]["discovery"]["status"] == "open"
