"""US3 契约测试 — E11/E12/E13 可版本化规则数据的 CRUD + 乐观并发(409) + 角色闸(403)。

覆盖 contracts/*.md：
  • E11 分类判据：list / create / update(409) / delete(204) + RBAC;
  • E12 决策规则：list(+group 过滤) / create(400 非法 group) / update(409) / delete + RBAC;
  • E13 冲突策略：list / get / update(409) + RBAC（无 create/delete——固定维度集）。

所有写入限 senior_analyst（FR-017）；operator 越权 → 403；陈旧 expected_version → 409。
"""

from __future__ import annotations

import shutil
from pathlib import Path

from app.config import settings
from app.services.ontology_meta_store import OntologyMetaStore
from app.services.reasoning.seed_declarative import seed_declarative_rules
from tests.conftest import FakeOntologyEngine

_REAL_ONTOLOGY = Path(__file__).resolve().parents[3] / "ontology" / "slpra"
DRUG = "https://ontology.pharma-gmp.cn/slpra/drug/"

CRITERIA = "/api/ontology/classification-criteria"
RULES = "/api/ontology/decision-rules"
POLICIES = "/api/ontology/conflict-policies"


def _seed(db) -> OntologyMetaStore:
    for ttl in _REAL_ONTOLOGY.glob("*.ttl"):
        shutil.copy(ttl, Path(settings.ontology_dir) / ttl.name)
    store = OntologyMetaStore(db=db, engine=FakeOntologyEngine())
    store.project_from_ttl()
    seed_declarative_rules(db)
    return store


# ===========================================================================
# E11 分类判据
# ===========================================================================
def test_criteria_list_after_seed(client, db):
    _seed(db)
    keys = {c["criterion_key"] for c in client.get(CRITERIA).json()}
    assert {"R-DC1", "R-DC2", "R-DC3", "R-DC4"} <= keys


def test_criterion_create_requires_role(client, db, operator_headers):
    _seed(db)
    body = {
        "criterion_key": "TEST-CRIT",
        "target_class_iri": DRUG + "HighSensitizingDrug",
        "pattern": {"op": "datatype_facet", "property": "sensitizationLevel", "cmp": "gt", "value": 5},
    }
    assert client.post(CRITERIA, json=body).status_code == 403  # 无身份头
    assert client.post(CRITERIA, json=body, headers=operator_headers).status_code == 403


def test_criterion_crud_and_optimistic_concurrency(client, db, analyst_headers):
    _seed(db)
    body = {
        "criterion_key": "TEST-CRIT",
        "target_class_iri": DRUG + "HighSensitizingDrug",
        "pattern": {"op": "datatype_facet", "property": "sensitizationLevel", "cmp": "gt", "value": 5},
        "regulation_ref": "TEST §1",
    }
    created = client.post(CRITERIA, json=body, headers=analyst_headers)
    assert created.status_code == 201, created.text
    assert created.json()["version"] == 1
    assert created.json()["status"] == "draft"

    # 重复 key → 400
    assert client.post(CRITERIA, json=body, headers=analyst_headers).status_code == 400

    # 正确版本 → 200 + version 自增
    ok = client.put(
        f"{CRITERIA}/TEST-CRIT",
        json={"expected_version": 1, "regulation_ref": "TEST §2"},
        headers=analyst_headers,
    )
    assert ok.status_code == 200
    assert ok.json()["version"] == 2
    assert ok.json()["regulation_ref"] == "TEST §2"

    # 陈旧版本 → 409
    stale = client.put(
        f"{CRITERIA}/TEST-CRIT",
        json={"expected_version": 1, "regulation_ref": "TEST §3"},
        headers=analyst_headers,
    )
    assert stale.status_code == 409

    # delete 即 disable
    deleted = client.delete(f"{CRITERIA}/TEST-CRIT?expected_version=2", headers=analyst_headers)
    assert deleted.status_code == 204
    after = next(c for c in client.get(CRITERIA).json() if c["criterion_key"] == "TEST-CRIT")
    assert after["is_disabled"] is True


def test_criterion_update_missing_404(client, db, analyst_headers):
    _seed(db)
    resp = client.put(
        f"{CRITERIA}/NO-SUCH",
        json={"expected_version": 1, "regulation_ref": "x"},
        headers=analyst_headers,
    )
    assert resp.status_code == 404


# ===========================================================================
# E12 决策规则
# ===========================================================================
def test_rules_list_and_group_filter(client, db):
    _seed(db)
    allr = client.get(RULES).json()
    assert allr
    groups = {r["rule_group"] for r in allr}
    assert {"equipment_dedication", "scenario_identification", "contamination_risk"} <= groups

    ded = client.get(RULES, params={"rule_group": "equipment_dedication"}).json()
    assert ded
    assert all(r["rule_group"] == "equipment_dedication" for r in ded)


def test_rule_create_requires_role(client, db, operator_headers):
    _seed(db)
    body = {
        "rule_key": "TEST-RULE",
        "rule_group": "equipment_dedication",
        "antecedent": {"op": "class_membership", "property": "self", "classes": ["PenicillinDrug"]},
        "consequent": {"requires_dedication": True},
    }
    assert client.post(RULES, json=body).status_code == 403
    assert client.post(RULES, json=body, headers=operator_headers).status_code == 403


def test_rule_create_rejects_unknown_group(client, db, analyst_headers):
    _seed(db)
    body = {
        "rule_key": "BAD-GROUP",
        "rule_group": "not_a_group",
        "antecedent": {"op": "boolean_has_value", "property": "hasPrionRisk", "value": True},
        "consequent": {"requires_dedication": True},
    }
    assert client.post(RULES, json=body, headers=analyst_headers).status_code == 400


def test_rule_crud_and_optimistic_concurrency(client, db, analyst_headers):
    _seed(db)
    body = {
        "rule_key": "TEST-RULE",
        "rule_group": "equipment_dedication",
        "antecedent": {"op": "boolean_has_value", "property": "hasPrionRisk", "value": True},
        "consequent": {"requires_dedication": True},
        "priority": 50,
        "regulation_ref": "TEST §1",
    }
    created = client.post(RULES, json=body, headers=analyst_headers)
    assert created.status_code == 201, created.text
    assert created.json()["version"] == 1
    assert created.json()["rule_key"] == "TEST-RULE"
    assert created.json()["slpra_iri"].endswith("DecisionRule_TEST-RULE")

    # 重复 key → 400
    assert client.post(RULES, json=body, headers=analyst_headers).status_code == 400

    ok = client.put(
        f"{RULES}/TEST-RULE",
        json={"expected_version": 1, "priority": 75},
        headers=analyst_headers,
    )
    assert ok.status_code == 200
    assert ok.json()["version"] == 2
    assert ok.json()["priority"] == 75

    stale = client.put(
        f"{RULES}/TEST-RULE",
        json={"expected_version": 1, "priority": 99},
        headers=analyst_headers,
    )
    assert stale.status_code == 409

    deleted = client.delete(f"{RULES}/TEST-RULE?expected_version=2", headers=analyst_headers)
    assert deleted.status_code == 204
    after = next(r for r in client.get(RULES).json() if r["rule_key"] == "TEST-RULE")
    assert after["is_disabled"] is True


# ===========================================================================
# E13 冲突策略（固定维度集，仅 GET/PUT）
# ===========================================================================
def test_policies_list_and_get(client, db):
    _seed(db)
    dims = {p["dimension"] for p in client.get(POLICIES).json()}
    assert {"dedication", "risk_level"} <= dims

    one = client.get(f"{POLICIES}/dedication")
    assert one.status_code == 200
    assert one.json()["dimension"] == "dedication"
    assert one.json()["strategy"] == "safety_override"


def test_policy_get_missing_404(client, db):
    _seed(db)
    assert client.get(f"{POLICIES}/no_such_dim").status_code == 404


def test_policy_update_requires_role(client, db, operator_headers):
    _seed(db)
    body = {"expected_version": 1, "override_direction": "permissive_wins"}
    assert client.put(f"{POLICIES}/dedication", json=body).status_code == 403
    assert client.put(f"{POLICIES}/dedication", json=body, headers=operator_headers).status_code == 403


def test_policy_update_and_optimistic_concurrency(client, db, analyst_headers):
    _seed(db)
    ok = client.put(
        f"{POLICIES}/dedication",
        json={"expected_version": 1, "override_direction": "permissive_wins"},
        headers=analyst_headers,
    )
    assert ok.status_code == 200, ok.text
    assert ok.json()["version"] == 2
    assert ok.json()["override_direction"] == "permissive_wins"
    assert ok.json()["status"] == "draft"

    stale = client.put(
        f"{POLICIES}/dedication",
        json={"expected_version": 1, "override_direction": "restrictive_wins"},
        headers=analyst_headers,
    )
    assert stale.status_code == 409
