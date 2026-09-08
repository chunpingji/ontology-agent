"""End-to-end business examples and adversarial evidence boundaries for PDE v1."""

import re
from copy import deepcopy

import pytest

from app.schemas.evidence import Candidate
from app.services.extraction.literal_normalizer import normalize_literal
from app.services.reasoning.pde_calculation import DEV, apply_decisions, evaluate_candidates


def candidates(noael="0.5", pde="1.8", suffix="", **values):
    anchor = {
        "document_hash": "a" * 64,
        "parser_version": "4",
        "structure_hash": "b" * 64,
        "evidence_id": "paragraph:1",
        "section_node_id": "section:1",
        "block_id": "paragraph:1",
    }
    provenance = [{"kind": "document", "anchors": [anchor], "excerpts": ["synthetic evidence"]}]
    common = {"provenance": provenance, "validation_status": "passed", "review_status": "confirmed"}
    root = Candidate(
        candidate_id="root" + suffix,
        kind="entity",
        class_iri=DEV + "CMCReport",
        text="CMC",
        **common,
    )
    entity = Candidate(
        candidate_id="study" + suffix,
        kind="entity",
        class_iri=DEV + "SharedLineAssessmentData",
        text="大鼠研究" + suffix,
        **common,
    )
    rows = [root, entity]

    def bind(subject, predicate, target=None):
        return [
            {
                "method": "explicit_assertion",
                "subject_candidate_id": subject,
                "predicate_iri": predicate,
                "object_candidate_id": target,
                "provenance_indexes": [0],
            }
        ]

    rows.append(
        Candidate(
            candidate_id="relation" + suffix,
            kind="relationship",
            subject={"candidate_id": root.candidate_id, "revision": 1},
            object={"candidate_id": entity.candidate_id, "revision": 1},
            predicate_iri=DEV + "hasSharedLineData",
            bindings=bind(root.candidate_id, DEV + "hasSharedLineData", entity.candidate_id),
            **common,
        )
    )
    fields = {
        "noael_mg_per_kg_per_day": (noael, "mg/kg/day"),
        "pde_mg_per_day": (pde, "mg/day"),
        "noaelSpecies": ("SD大鼠", "text"),
        "noaelDuration": ("28天重复给药", "text"),
        **values,
    }
    for name, pair in fields.items():
        if pair is None:
            continue
        raw, unit = pair
        if unit and unit != "text" and re.fullmatch(r"[-+]?\d+(?:\.\d+)?", str(raw)):
            raw = str(raw) + " " + unit
        literal = normalize_literal(
            str(raw),
            datatype="string" if unit == "text" else "decimal",
            target_unit=None if unit == "text" else unit,
        )
        rows.append(
            Candidate(
                candidate_id=name + suffix,
                kind="property",
                predicate_iri=DEV + name,
                subject={"candidate_id": entity.candidate_id, "revision": 1},
                literal=literal,
                bindings=bind(entity.candidate_id, DEV + name),
                **common,
            )
        )
    return rows


def result(rows):
    return apply_decisions(evaluate_candidates(rows), [])[0]


@pytest.mark.parametrize("pde", ["1.8 mg/day", "1800 ug/day", "1800000 ng/day"])
def test_original_different_band_example_and_units(pde):
    check = result(candidates(pde=pde))
    assert check["derived_pde_mg_day"] == "0.05"
    assert float(check["asserted_pde_mg_day"]) == 1.8
    assert check["ratio"] == "36"
    assert check["differences"] == ["pde_ratio", "oeb_band"]
    assert check["blocks_conclusion"]
    assert check["input_evidence"]["noael"][0]["provenance"][0]["anchors"]


def test_same_band_fivefold_error_and_exact_threshold():
    check = result(candidates(noael="200", pde="100"))
    assert check["derived_pde_mg_day"] == "20"
    assert check["asserted_band"] == check["derived_band"] == 1
    assert check["differences"] == ["pde_ratio"]
    assert result(candidates(noael="200", pde="40"))["status"] == "passed"
    assert result(candidates(noael="200", pde="40.00001"))["status"] == "conflict"


def test_matching_values_automatically_pass_without_hazard_inference():
    check = result(candidates(pde="0.05", f4=("1", None)))
    assert check["review_status"] == "automatic"
    assert check["effective_pde_mg_day"] == "0.05"
    assert not check["blocks_conclusion"]
    assert "不代表" in check["limitations"]


@pytest.mark.parametrize(
    "field,update",
    [
        ("noael_mg_per_kg_per_day", {"review_status": "rejected"}),
        ("noael_mg_per_kg_per_day", {"review_status": "pending"}),
        (
            "noael_mg_per_kg_per_day",
            {
                "literal": normalize_literal(
                    "0 mg/kg/day", datatype="decimal", target_unit="mg/kg/day"
                )
            },
        ),
        (
            "noael_mg_per_kg_per_day",
            {"literal": normalize_literal("XXmg/kg/天（28天重复给药）", datatype="string")},
        ),
        (
            "noael_mg_per_kg_per_day",
            {"literal": normalize_literal("2 mg/day", datatype="decimal", target_unit="mg/day")},
        ),
        ("noael_mg_per_kg_per_day", {"literal": normalize_literal("0.5", datatype="decimal")}),
        ("noaelSpecies", {"literal": normalize_literal("报告标题中出现犬", datatype="string")}),
        ("noaelDuration", {"literal": normalize_literal("未提供", datatype="string")}),
    ],
)
def test_missing_unreviewed_placeholder_wrong_unit_and_species_never_pass(field, update):
    rows = [c.model_copy(update=update) if c.candidate_id == field else c for c in candidates()]
    check = result(rows)
    assert check["status"] == "incomplete"
    assert check["derived_pde_mg_day"] is None
    assert check["blocks_conclusion"]


def test_explicit_factors_override_versioned_defaults():
    check = result(
        candidates(
            noael="2",
            pde="1",
            f1=("2", None),
            f2=("5", None),
            f3=("10", None),
            f4=("1", None),
            f5=("1", None),
        )
    )
    assert check["derived_pde_mg_day"] == "1"
    assert all(check["factors"][f"f{i}"]["source"] == "document" for i in range(1, 6))


def test_studies_never_share_parameters_and_ambiguous_values_block():
    rows = [c for c in candidates(suffix="a") if not c.candidate_id.startswith("noaelDuration")]
    rows += candidates(suffix="b", noael="200", pde="100")
    checks = apply_decisions(evaluate_candidates(rows), [])
    assert checks[0]["status"] == "incomplete"
    assert checks[1]["derived_pde_mg_day"] == "20"
    duplicate = next(c for c in rows if c.candidate_id == "pde_mg_per_dayb").model_copy(
        update={
            "candidate_id": "duplicate",
            "literal": normalize_literal("20 mg/day", datatype="decimal", target_unit="mg/day"),
        }
    )
    checks = evaluate_candidates([*rows, duplicate])
    assert checks[1]["status"] == "incomplete"


def test_rejection_and_changed_inputs_invalidate_effective_value():
    rows = candidates()
    check = result(rows)
    decision = {
        "subject_candidate_id": "study",
        "calculation_id": check["calculation_id"],
        "revision": 1,
        "choice": "derived",
    }
    chosen = apply_decisions(evaluate_candidates(rows), [decision])[0]
    assert chosen["effective_pde_mg_day"] == "0.05"
    assert not chosen["blocks_conclusion"]
    changed = deepcopy(rows)
    changed[-1].revision += 1
    expired = apply_decisions(evaluate_candidates(changed), [decision])[0]
    assert expired["stale_decision"] and expired["blocks_conclusion"]
    rejected = apply_decisions(evaluate_candidates(rows), [{**decision, "choice": "rejected"}])[0]
    assert rejected["effective_pde_mg_day"] is None
    assert rejected["blocks_conclusion"]


def test_disconnected_entity_cannot_supply_report_value():
    check = result([c for c in candidates(pde="0.05") if c.kind != "relationship"])
    assert check["status"] == "passed" and check["blocks_conclusion"]


def test_rejected_factor_cannot_silently_fall_back_to_default():
    rows = candidates(pde="0.05", f4=("1", None))
    rows = [
        c.model_copy(update={"review_status": "rejected"}) if c.candidate_id == "f4" else c
        for c in rows
    ]
    check = result(rows)
    assert check["status"] == "incomplete" and check["blocks_conclusion"]
    assert any("不能回退" in i["message"] for i in check["issues"])


def test_even_matching_new_input_requires_reprocessing_a_stale_decision():
    rows = candidates(pde="0.05")
    check = result(rows)
    previous = {
        "subject_candidate_id": "study",
        "calculation_id": check["calculation_id"],
        "revision": 1,
        "choice": "rejected",
    }
    rows[-1].revision += 1
    check = apply_decisions(evaluate_candidates(rows), [previous])[0]
    assert check["status"] == "passed" and check["stale_decision"] and check["blocks_conclusion"]


def test_explicit_oeb_is_checked_separately_from_pde_arithmetic():
    check = result(candidates(pde="0.05", oebBand=("OEB1", "text")))
    assert check["status"] == "conflict" and check["differences"] == ["oeb_assertion"]


def test_calculation_inputs_are_requested_by_source_extraction():
    from app.services.extraction.template_priorities import template_priority_paths
    from app.services.reasoning.pde_calculation import default_checks

    slots = [{"source_slot_id": "cmc", "class_iri": DEV + "CMCReport"}]
    paths = template_priority_paths(
        {"schema_version": 2, "calculation_checks": default_checks(slots)}, DEV + "CMCReport"
    )
    assert (DEV + "hasSharedLineData", DEV + "pde_mg_per_day") in paths
    assert (DEV + "hasSharedLineData", DEV + "noael_mg_per_kg_per_day") in paths
