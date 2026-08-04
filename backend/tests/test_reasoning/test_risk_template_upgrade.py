"""Tests for the one-time, idempotent R-RA1~5 template upgrade sync.

Rule seeding is insert-only, so templatizing the R-RA1~5 defaults would NOT
reach rows already in an existing database. ``_upgrade_risk_assessment_templates``
closes that gap: it replaces the consequent of pristine (never-edited) legacy rows
with the new templated default, and leaves manually-edited rows untouched.
"""

from __future__ import annotations

import copy

from app.models.ontology_meta import OntologyDecisionRule
from app.services.reasoning.defaults import default_decision_rules
from app.services.reasoning.seed_declarative import (
    _LEGACY_RISK_ASSESSMENT_CONSEQUENTS,
    _upgrade_risk_assessment_templates,
)

_NEW_BY_KEY = {r.key: r.consequent for r in default_decision_rules()}
_ANTECEDENT = {
    "op": "some_values_from",
    "property": "hasSharedLineData",
    "filler_class": "SharedLineAssessmentData",
}


def _add_rule(db, key: str, consequent: dict) -> None:
    db.add(OntologyDecisionRule(
        slpra_iri=f"https://ontology.pharma-gmp.cn/slpra/core/DecisionRule_{key}",
        label=key,
        rule_key=key,
        rule_group="risk_assessment",
        antecedent=_ANTECEDENT,
        consequent=consequent,
        priority=100,
        status="published",
    ))
    db.commit()


def _row(db, key: str) -> OntologyDecisionRule:
    return db.query(OntologyDecisionRule).filter_by(rule_key=key).first()


def test_new_default_carries_placeholders():
    # Precondition for the whole feature: the shipped R-RA1 default is templated.
    assert "{{药物名称/代号}}" in _NEW_BY_KEY["R-RA1"]["description"]


def test_pristine_legacy_row_is_upgraded(db):
    _add_rule(db, "R-RA1", copy.deepcopy(_LEGACY_RISK_ASSESSMENT_CONSEQUENTS["R-RA1"]))
    assert _upgrade_risk_assessment_templates(db) == 1
    assert _row(db, "R-RA1").consequent == _NEW_BY_KEY["R-RA1"]
    assert "{{药物名称/代号}}" in _row(db, "R-RA1").consequent["description"]


def test_all_five_legacy_rows_upgraded(db):
    for key, legacy in _LEGACY_RISK_ASSESSMENT_CONSEQUENTS.items():
        _add_rule(db, key, copy.deepcopy(legacy))
    assert _upgrade_risk_assessment_templates(db) == 5
    for key in _LEGACY_RISK_ASSESSMENT_CONSEQUENTS:
        assert _row(db, key).consequent == _NEW_BY_KEY[key]


def test_manually_edited_row_is_not_overwritten(db):
    edited = copy.deepcopy(_LEGACY_RISK_ASSESSMENT_CONSEQUENTS["R-RA1"])
    edited["description"] = "客户自定义的风险描述"
    _add_rule(db, "R-RA1", edited)
    assert _upgrade_risk_assessment_templates(db) == 0
    assert _row(db, "R-RA1").consequent["description"] == "客户自定义的风险描述"


def test_new_default_row_is_noop(db):
    _add_rule(db, "R-RA1", copy.deepcopy(_NEW_BY_KEY["R-RA1"]))
    assert _upgrade_risk_assessment_templates(db) == 0
    assert _row(db, "R-RA1").consequent == _NEW_BY_KEY["R-RA1"]


def test_upgrade_is_idempotent(db):
    _add_rule(db, "R-RA1", copy.deepcopy(_LEGACY_RISK_ASSESSMENT_CONSEQUENTS["R-RA1"]))
    assert _upgrade_risk_assessment_templates(db) == 1
    # Second run: row now equals the new default (≠ legacy) → nothing to do.
    assert _upgrade_risk_assessment_templates(db) == 0


def test_missing_row_is_skipped(db):
    # No R-RA rows in the DB at all → upgrade touches nothing (new DBs insert
    # the new default directly via the seeding path).
    assert _upgrade_risk_assessment_templates(db) == 0
