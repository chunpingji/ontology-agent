"""Membership, complete ordering and temporal proof are separate decisions."""

from copy import deepcopy

import jsonschema
import pytest
from test_work_evidence import (  # noqa: F401
    add_review,
    create_engine,
    relation,
    review_answer,
)
from test_work_evidence import evidence_case as evidence_case

from app.services.document_harness.protocols import INSTRUCTIONS, stage_schema, validate_paid_output
from app.services.document_harness.work_execution import drain_work


@pytest.mark.parametrize(
    "order,evidence",
    [
        (["E1"], True),
        (["E1", "E1"], True),
        (["E1", "E2"], False),
        (["E1", "E9"], True),
    ],
)
def test_sequential_contract_requires_full_unique_set_and_source(order, evidence):
    items = [{"candidate_id": "g", "object_ids": ["E1", "E2"]}]
    schema = stage_schema("relation_alignment", source_ids=["S1"], relation_items=items)
    output = {
        "proposals": {
            "g": {
                "verdict": "proposed",
                "confidence": 0.95,
                "evidence": ["S1"],
                "reason": "order",
                "polarity": "positive",
                "conditions": [],
                "missing_context": "none",
                "participation": "all",
                "selection": "unspecified",
                "timing": "sequential",
                "ordered_object_ids": order,
                "order_evidence": [{"source_id": "S1", "text": "乙之后甲", "occurrence": None}]
                if evidence
                else [],
            }
        }
    }
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(output, schema)
    with pytest.raises(ValueError):
        validate_paid_output("relation_alignment", {"items": items}, output, schema)


def test_all_participation_does_not_prove_parallel_from_table_or_slash(evidence_case):
    calls = []

    def invoke(stage, payload, schema):
        calls.append(deepcopy(payload))
        output = review_answer(payload)
        for candidate in payload["candidates"]:
            if candidate["kind"] == "relation_timing":
                output["judgments"][candidate["id"]].update(
                    verdict="unresolved", evidence=[], reason="行号与斜杠不证明同时发生"
                )
        return output

    engine = create_engine(evidence_case, invoke)
    row = relation(engine, group=True)
    row.update(participation="all", timing="parallel")
    add_review(engine, row, domain="relation_groups")
    assert drain_work(engine, None)
    result = engine.state["relation_groups"][row["id"]]
    assert result["state"] == "accepted" and result["timing_state"] == "unresolved"
    assert {c["kind"] for c in calls[0]["candidates"]} == {"relation_groups", "relation_timing"}
    assert "一般先于不证明紧邻nextStep" in INSTRUCTIONS["evidence_review"]
    assert "表格行号" in INSTRUCTIONS["evidence_review"]
    assert not engine.state.get("relations")
