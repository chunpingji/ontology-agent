"""US1 端到端集成测试 —— 建类→映射→约束→校验→批次→diff→发布。

验证双存储一致（元数据草稿为真源、发布时投影 World + 外科式导出 TTL）
与 TTL 保真（导出/ diff 可用）。契约先行，端点实现前应 FAIL。
"""

from __future__ import annotations

BASE = "https://ontology.pharma-gmp.cn/slpra/core/"
CLASSES = "/api/ontology/classes"
LINKS = "/api/ontology/link-types"
ONTO = "/api/ontology"


def test_us1_end_to_end_workbench(client, analyst_headers):
    # 1) 建类（药物 + 辅料）
    drug = BASE + "SterileDrug"
    exc = BASE + "Excipient"
    assert (
        client.post(
            CLASSES,
            json={"slpra_iri": drug, "label": "无菌药物", "module": "drug"},
            headers=analyst_headers,
        ).status_code
        == 201
    )
    assert (
        client.post(
            CLASSES,
            json={"slpra_iri": exc, "label": "辅料", "module": "drug"},
            headers=analyst_headers,
        ).status_code
        == 201
    )

    # 2) 绑定 SLPRA·IRI + BFO 映射（两个类都映射，校验才不阻断）
    for iri in (drug, exc):
        client.post(
            f"{CLASSES}/{iri}/mappings",
            json={"mapping_type": "slpra_iri", "target": iri},
            headers=analyst_headers,
        )
        client.post(
            f"{CLASSES}/{iri}/mappings",
            json={"mapping_type": "bfo", "target": "BFO_0000040"},
            headers=analyst_headers,
        )

    # 3) 对象属性 + 约束（药物 contains some 辅料）
    prop = BASE + "contains"
    client.post(
        LINKS,
        json={"slpra_iri": prop, "label": "含有", "domain_iri": drug, "range_iri": exc},
        headers=analyst_headers,
    )
    r = client.post(
        f"{CLASSES}/{drug}/restrictions",
        json={
            "kind": "some",
            "property_iri": prop,
            "property_kind": "object",
            "filler_iri": exc,
        },
        headers=analyst_headers,
    )
    assert r.status_code == 201

    # 约束渲染于类详情
    detail = client.get(f"{CLASSES}/{drug}")
    assert detail.status_code == 200
    assert any(x["kind"] == "some" for x in detail.json()["restrictions"])

    # 4) 校验（无阻断）
    report = client.post(f"{ONTO}/validate", headers=analyst_headers).json()
    assert report["blocking"] == []

    # 5) 批次：创建 → 提交 → 发布
    rid = client.post(
        f"{ONTO}/releases", json={"title": "能力一首发"}, headers=analyst_headers
    ).json()["id"]
    assert (
        client.post(f"{ONTO}/releases/{rid}/submit", headers=analyst_headers).json()[
            "status"
        ]
        == "in_review"
    )
    published = client.post(f"{ONTO}/releases/{rid}/publish", headers=analyst_headers)
    assert published.status_code == 200
    assert published.json()["status"] == "published"

    # 6) 导出 + diff（TTL 保真）
    assert client.get(f"{ONTO}/export/ttl").status_code == 200
    diff = client.get(f"{ONTO}/export/diff").json()
    assert "turtle_preview" in diff

    # 7) 审计留痕（建类/映射/发布均落账）
    audit = client.get(f"{ONTO}/audit").json()
    actions = {a["action"] for a in audit}
    assert "class.create" in actions
    assert "release.publish" in actions
