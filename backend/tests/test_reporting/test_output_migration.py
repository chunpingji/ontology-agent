import json
from pathlib import Path
from uuid import UUID

from app.services.reporting.template_compiler import compile_template
from app.services.reporting.template_migration import BASELINE_ID, RISK_SLOTS, migrate_template


def test_uuid_and_string_ids_produce_the_same_migration_plan():
    original = json.loads(
        (Path(__file__).parents[1] / "fixtures/risk_template_dea037a2.json").read_text()
    )
    expected = migrate_template(original, template_id=BASELINE_ID, version="v18")
    actual = migrate_template(original, template_id=UUID(BASELINE_ID), version="v18")
    assert actual == expected


def test_32_slot_migration_is_lossless_draft_with_one_risk_table():
    original = json.loads(
        (Path(__file__).parents[1] / "fixtures/risk_template_dea037a2.json").read_text()
    )
    plan = migrate_template(original, template_id=BASELINE_ID, version="v18")
    target = plan["target_schema"]
    assert plan["original_schema"] == original
    assert plan["mapping_count"] == 32
    assert plan["baseline_mapping_applied"]
    assert not plan["business_reviewed"]
    assert [s["section_id"] for s in target["sections"]] == [
        s["section_id"] for s in original["sections"]
    ]
    mapping = {m["old_id"]: m for m in plan["mappings"]}
    assert len({mapping[key]["output_ids"][0] for key in RISK_SLOTS}) == 1
    assert (
        mapping["grp_1783352185921_0_1.ai_0"]["input_ids"]
        == mapping["grp_report_teams.assessment"]["input_ids"]
    )
    units = [u for s in target["sections"] for g in s["groups"] for u in g["units"]]
    table = next(u for u in units if u["output_id"] == "output:risk_matrix")
    assert len(table["render"]["columns"]) == 7
    assert not compile_template(target, {}, {})["valid"]
