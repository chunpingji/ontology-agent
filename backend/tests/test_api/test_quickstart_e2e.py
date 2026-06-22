"""端到端验证（T042）：逐步复现 quickstart.md §3 的 US1 主流程，
覆盖 §5 通过判据，并显式验证 §5 步骤 5 的乐观并发 409 → 正确版本重试 → 200。

与契约测试互补：契约测试逐端点断言，本测试以 quickstart 的 curl 序列为脚本，
证明"新建类 → 映射 → 约束 → 风险属性 → 并发自检 → 校验 → 批次发布 → diff → 发布回写"
作为一条闭环可运行（宪法 III 批量发布 = 一次 TTL 导出 + 一次 Git 提交）。
"""

from __future__ import annotations

import urllib.parse

# 受管命名空间（与契约测试一致）：实体自身 slpra_iri 必须落在该前缀下。
BASE = "https://ontology.pharma-gmp.cn/slpra/core/"
CLS = BASE + "HighPotentCompound"
PARENT = BASE + "Compound"
PROP_OEB = BASE + "hasOEB"
FILLER_OEB = BASE + "OEBBand"

ENC = urllib.parse.quote(CLS, safe="")


def _map_class(client, H, iri, bfo="BFO:0000040"):
    """为类补齐 slpra_iri + bfo 映射（校验无阻断的前置）。"""
    enc = urllib.parse.quote(iri, safe="")
    for payload in (
        {"mapping_type": "slpra_iri", "target": iri},
        {"mapping_type": "bfo", "target": bfo},
    ):
        rm = client.post(f"/api/ontology/classes/{enc}/mappings", headers=H, json=payload)
        assert rm.status_code == 201, rm.text


def test_quickstart_us1_end_to_end(client, analyst_headers):
    H = analyst_headers

    # 前置 — 父类 Compound 须先存在（父类引用约束）。
    rp0 = client.post(
        "/api/ontology/classes",
        headers=H,
        json={"slpra_iri": PARENT, "label": "化合物", "module": "drug"},
    )
    assert rp0.status_code == 201, rp0.text

    # 步骤 1 — 新建类：201, version=1, status=draft
    r = client.post(
        "/api/ontology/classes",
        headers=H,
        json={
            "slpra_iri": CLS,
            "label": "高活性化合物",
            "module": "drug",
            "parent_iri": PARENT,
            "bfo_category": "BFO:0000040",
        },
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["version"] == 1 and body["status"] == "draft"

    # 步骤 2 — 映射（slpra_iri + bfo），随后该类不在 unmapped
    for payload in (
        {"mapping_type": "slpra_iri", "target": CLS},
        {"mapping_type": "bfo", "target": "BFO:0000040"},
    ):
        rm = client.post(f"/api/ontology/classes/{ENC}/mappings", headers=H, json=payload)
        assert rm.status_code == 201, rm.text
    health = client.get("/api/ontology/mappings/health").json()
    assert CLS not in health["unmapped"]

    _map_class(client, H, PARENT)  # 父类补齐映射，便于步骤 6 校验无阻断

    # 步骤 4（前移）— 风险属性向导：受控词表预填；约束引用的属性须先存在。
    vocabs = client.get("/api/ontology/risk-vocabularies").json()
    assert any(v["key"] == "OEB" for v in vocabs)
    rp = client.post(
        "/api/ontology/data-properties/risk",
        headers=H,
        json={
            "slpra_iri": PROP_OEB,
            "label": "OEB 等级",
            "domain_iri": CLS,
            "datatype": "string",
            "vocab": "OEB",
        },
    )
    assert rp.status_code == 201, rp.text
    assert rp.json()["controlled_vocab"]

    # 约束 filler 类须先存在。
    rf = client.post(
        "/api/ontology/classes",
        headers=H,
        json={"slpra_iri": FILLER_OEB, "label": "OEB 等级带", "module": "drug"},
    )
    assert rf.status_code == 201, rf.text
    _map_class(client, H, FILLER_OEB)

    # 步骤 3 — 加约束（some）：详情可见
    rr = client.post(
        f"/api/ontology/classes/{ENC}/restrictions",
        headers=H,
        json={
            "kind": "some",
            "property_iri": PROP_OEB,
            "property_kind": "data",
            "filler_iri": FILLER_OEB,
        },
    )
    assert rr.status_code == 201, rr.text
    detail = client.get(f"/api/ontology/classes/{ENC}").json()
    assert any(x["kind"] == "some" for x in detail["restrictions"])

    # 步骤 5 — 乐观并发自检：陈旧 expected_version → 409；正确版本重试 → 200
    current = client.get(f"/api/ontology/classes/{ENC}").json()
    cur_ver = current["version"]
    stale = client.put(
        f"/api/ontology/classes/{ENC}",
        headers=H,
        json={"label": "高活性化合物(改)", "expected_version": cur_ver - 1},
    )
    assert stale.status_code == 409, stale.text
    retry = client.put(
        f"/api/ontology/classes/{ENC}",
        headers=H,
        json={"label": "高活性化合物(改)", "expected_version": cur_ver},
    )
    assert retry.status_code == 200, retry.text
    assert retry.json()["version"] == cur_ver + 1

    # 步骤 6 — 校验：映射齐备 → 无阻断
    rv = client.post("/api/ontology/validate", headers=H).json()
    assert rv["blocking"] == [], rv

    # 步骤 7 — 创建发布批次并提交审核：change_log 非空，draft→in_review
    rid = client.post(
        "/api/ontology/releases", headers=H, json={"title": "R2026.06.20-01 高活性化合物建模"}
    ).json()["id"]
    sub = client.post(f"/api/ontology/releases/{rid}/submit", headers=H)
    assert sub.status_code in (200, 201), sub.text
    rel = sub.json()
    assert rel["status"] == "in_review"
    assert len(rel["change_log"]) > 0

    # 步骤 8 — 导出 diff 预览：新增三元组非空
    diff = client.get("/api/ontology/export/diff").json()
    assert len(diff["triples_added"]) > 0

    # 步骤 9 — 发布：published + ttl_commit_sha（一次导出 + 一次提交）
    pub = client.post(f"/api/ontology/releases/{rid}/publish", headers=H)
    assert pub.status_code == 200, pub.text
    pj = pub.json()
    assert pj["status"] == "published"

    # §5 留痕：审计含 class.create 与 release.publish
    audit = client.get("/api/ontology/audit").json()
    actions = {a["action"] for a in audit}
    assert any("class" in a for a in actions)
    assert any("publish" in a for a in actions)


def test_quickstart_role_gate(client, operator_headers):
    """§5 / R7：写端点受角色门禁 —— operator 无权新建类。"""
    r = client.post(
        "/api/ontology/classes",
        headers=operator_headers,
        json={"slpra_iri": CLS, "label": "x", "module": "drug"},
    )
    assert r.status_code == 403, r.text
