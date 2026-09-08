"""Retirement refuses old execution rather than silently rebuilding business facts."""

import importlib.util

import pytest
from rdflib import Graph

from app.services.extraction.equipment_source import get_equipment_source
from app.services.extraction.retired_config import reject_execution_annotations
from app.services.extraction.transforms import apply_transform, validate_transform_config


@pytest.mark.parametrize(
    "module",
    [
        "reporting.risk_report_generator",
        "reporting.narrative_generator",
        "reporting.snapshot_report",
        "reporting.aps_equipment_resolver",
        "reporting.product_report_edges",
        "extraction.document_profile",
        "extraction.llm_gap_filler",
    ],
)
def test_retired_modules_are_unimportable(module):
    assert importlib.util.find_spec("app.services." + module) is None


@pytest.mark.parametrize(
    "kind,config",
    [
        ("pattern", {"pattern": "[0-9]+"}),
        ("cast", {"to": "decimal", "number_pattern": ".*"}),
        ("none", {"script": "return true"}),
    ],
)
def test_old_transform_config_rejected_even_with_missing_value(kind, config):
    assert "RETIRED" in validate_transform_config(kind, config)
    with pytest.raises(ValueError, match="RETIRED"):
        apply_transform(kind, config, None)


def test_ontology_loader_rejects_executable_annotation():
    graph = Graph().parse(
        data="""
        <urn:Class> <https://ontology.pharma-gmp.cn/slpra/integration/extractionProfile>
        "{old executable config}" .
    """,
        format="turtle",
    )
    with pytest.raises(ValueError, match="EXECUTABLE_EXTRACTION_CONFIG_RETIRED"):
        reject_execution_annotations(graph)


def test_equipment_archive_does_not_derive_classes_workshops_or_materials():
    record = get_equipment_source().evidence_record("RE64202")
    assert record is not None
    assert record.class_iri.endswith("/ProcessEquipment")
    assert set(record.field_predicates) == {"equipment_id", "name", "specification"}
    assert get_equipment_source().evidence_record(" RE64202 ") is None
    assert get_equipment_source().evidence_record("unknown") is None


def test_startup_does_not_seed_or_mutate_historical_risk_claims(db):
    from copy import deepcopy

    from app.models.ontology_meta import OntologyDecisionRule
    from app.services.reasoning.defaults import default_decision_rules
    from app.services.reasoning.seed_declarative import _seed_decision_rules

    row = OntologyDecisionRule(
        rule_key="R-RA2",
        slpra_iri="urn:rule:R-RA2",
        label="Historical rule",
        rule_group="risk_assessment",
        version=7,
        status="published",
        antecedent={"op": "literal_eq", "key": "review", "value": True},
        consequent={"control_measure": "historical reviewed wording"},
    )
    db.add(row)
    db.commit()
    before = deepcopy(row.consequent)
    _seed_decision_rules(db)
    db.commit()
    db.refresh(row)
    assert row.consequent == before and row.version == 7
    assert all(r.rule_group != "risk_assessment" for r in default_decision_rules())
