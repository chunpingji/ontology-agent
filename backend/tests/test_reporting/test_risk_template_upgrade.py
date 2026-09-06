import json
from copy import deepcopy
from pathlib import Path

import pytest

from app.services.extraction.extraction_tasks import semantic_schema_from_engine
from app.services.ontology_engine import OntologyEngine
from app.services.reporting.ast_template import ReportTemplate
from app.services.reporting.template_upgrade import prepare_template_upgrade

ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture(scope="module")
def risk_upgrade(tmp_path_factory):
    engine = OntologyEngine(
        ROOT / "ontology/slpra", tmp_path_factory.mktemp("risk-schema") / "world.sqlite3"
    )
    engine.load()
    original = json.loads((ROOT / "backend/tests/fixtures/risk_template_dea037a2.json").read_text())
    plan = json.loads(
        (ROOT / "specs/019-evidence-semantic-extraction/risk-template-upgrade.json").read_text()
    )
    yield original, plan, semantic_schema_from_engine(engine)
    engine._world.close()


def test_real_template_upgrade_preserves_original_and_validates_all_paths(risk_upgrade):
    original, plan, schema = risk_upgrade
    before = deepcopy(original)
    upgraded = prepare_template_upgrade(original, plan, schema)
    assert original == before
    assert upgraded["revision"] == "v19"
    template = ReportTemplate.model_validate(upgraded)
    assert len(template.sections[0].coverage) == 12
    assert len(list(template.iter_slots())) == len(
        list(ReportTemplate.model_validate(original).iter_slots())
    )
    assert all(
        (c.subject_root_class_iri or c.doc_class_iri) == plan["input_class_iri"]
        and c.quantifier == "all"
        for c in template.sections[0].coverage
    )
    sources = [slot.source.kind for _, _, slot in template.iter_slots()]
    assert sources.count("snapshot") == 14
    assert sources.count("rule") == 7
    assert "semantic" not in sources
    assert template.sections[-1].coverage  # do not erase missing output-report team evidence
    assert template.diagnostics


def test_upgrade_refuses_changed_source_schema(risk_upgrade):
    original, plan, schema = risk_upgrade
    with pytest.raises(ValueError, match="changed"):
        prepare_template_upgrade({**original, "revision": "edited"}, plan, schema)


def test_actual_subclasses_inherit_and_union_range_is_not_a_fake_iri(risk_upgrade):
    _, _, schema = risk_upgrade
    base = "https://ontology.pharma-gmp.cn/slpra/"
    assert schema[base + "drug/SterileDrugProduct"]["properties"]
    roles = next(
        p
        for p in schema[base + "equipment/Equipment"]["relationships"]
        if p["iri"].endswith("/hasEquipmentRole")
    )
    assert set(roles["range"]) == {
        base + "equipment/SharedEquipmentRole",
        base + "equipment/DedicatedEquipmentRole",
    }
    assert all(iri in schema for iri in roles["range"])
