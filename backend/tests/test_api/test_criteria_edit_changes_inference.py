"""US3-AS1 / SC-003 — 规则即数据：改一个阈值即改变推断，全程未改源码。

R-DC3（HighSensitizingDrug 的充要判据）默认模式为 `sensitizationLevel > 3`。
一个致敏等级 = 3 的药物因 `3 > 3 = False` 不点亮 HighSensitizingDrug。仅通过
受工作台保护的 PUT 把阈值改成 `> 2`（纯数据写入，无任何源码改动），再用同一
`active_classification_criteria()` 加载器驱动评估，致敏 3 的药物即被推断为
HighSensitizingDrug——证明判据是可编辑、可版本化的数据而非硬编码逻辑（FR-016）。

`client` fixture 的 `FakeOntologyEngine.get_individual` 恒为 None（真实 /assess 会
400），故此处把「可编辑路径」（TestClient PUT）与「推断变化」（matrix.StubEngine
直驱 run_assessment）组合验证，两者共享同一 `db` 会话。
"""

from __future__ import annotations

import shutil
from pathlib import Path

from app.config import settings
from app.services.ontology_meta_store import OntologyMetaStore
from app.services.reasoning import engine as reasoning_engine
from app.services.reasoning.seed_declarative import seed_declarative_rules
from tests.conftest import FakeOntologyEngine
from tests.test_reasoning import matrix

_REAL_ONTOLOGY = Path(__file__).resolve().parents[3] / "ontology" / "slpra"
_RDC3 = "/api/ontology/classification-criteria/R-DC3"


def _seed(db) -> OntologyMetaStore:
    """Full pipeline seed: real TTL → project_from_ttl → declarative rule layer."""
    for ttl in _REAL_ONTOLOGY.glob("*.ttl"):
        shutil.copy(ttl, Path(settings.ontology_dir) / ttl.name)
    store = OntologyMetaStore(db=db, engine=FakeOntologyEngine())
    store.project_from_ttl()
    seed_declarative_rules(db)
    return store


def _fired_classes(result) -> set[str]:
    return {
        r["conclusion"].get("add_class")
        for r in result.rules_fired
        if r["rule_group"] == "drug_classification"
    }


def test_lowering_rdc3_threshold_lights_up_sensitization_3(client, db, analyst_headers):
    _seed(db)
    store = OntologyMetaStore(db=db, engine=FakeOntologyEngine())
    stub = matrix.StubEngine({"drug:s3": matrix._drug(sensitization_level=3)})

    def _assess():
        # 数据驱动：判据/规则/策略全部来自可编辑草稿，镜像 /assess 路由的装载方式。
        return reasoning_engine.run_assessment(
            stub,
            "drug:s3",
            [],
            criteria=store.active_classification_criteria(),
            decision_rules=store.active_decision_rules(),
            policies=store.active_conflict_policies(),
        )

    # 改前：gt 3 → 3>3 False → 不点亮
    before = _assess()
    assert "HighSensitizingDrug" not in _fired_classes(before)
    assert "R-DC3" not in {r["rule_id"] for r in before.rules_fired}

    # 纯数据写入：把阈值 gt 3 → gt 2（无源码改动）。
    resp = client.put(
        _RDC3,
        headers=analyst_headers,
        json={
            "expected_version": 1,
            "pattern": {
                "op": "datatype_facet",
                "property": "sensitizationLevel",
                "cmp": "gt",
                "value": 2,
            },
        },
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["pattern"]["value"] == 2
    assert resp.json()["version"] == 2
    # 草稿改动即时参与推断（draft 即真源），无需发布。
    assert resp.json()["status"] == "draft"

    # 强制下次查询从库重读，杜绝 identity-map 陈旧值干扰判定。
    db.expire_all()

    # 改后：gt 2 → 3>2 True → 点亮 HighSensitizingDrug
    after = _assess()
    assert "HighSensitizingDrug" in _fired_classes(after)
    assert "R-DC3" in {r["rule_id"] for r in after.rules_fired}
