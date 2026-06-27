"""实体详情端点对 A-Box 事实个体的解析（回归）。

研发文档/事实源个体（``facts#…``）仅落 EntityShadow 影子表，不入 owlready2 World
（TTL = T-Box only, A-Box in DB）。``GET /api/entities/{iri}`` 须先查 World、未命中
回退影子行，否则点击实体列表中的事实个体会 404（"Entity not found: facts#…"）。
"""

from __future__ import annotations

from urllib.parse import quote

from app.models.entity_shadow import EntityShadow

FACTS = "http://slpra.org/facts#"


def _seed_shadow(db, eid: str, **kw) -> EntityShadow:
    s = EntityShadow(
        iri=f"{FACTS}{eid}",
        class_iri=kw.get("class_iri", f"{FACTS}StabilityReport"),
        label_zh=kw.get("label_zh", "稳定性报告"),
        label_en=kw.get("label_en"),
        module=kw.get("module", "document"),
        properties_json=kw.get("properties_json", {"approvalStatus": "approved", "_version": 1}),
    )
    db.add(s)
    db.commit()
    db.refresh(s)
    return s


def test_get_entity_resolves_abox_shadow(client, db, analyst_headers):
    """引擎无该个体（A-Box 仅在影子表）→ 回退影子行，返回 200 而非 404。"""
    _seed_shadow(db, "HRS-1234")
    iri = f"{FACTS}HRS-1234"

    res = client.get(f"/api/entities/{quote(iri, safe='')}", headers=analyst_headers)

    assert res.status_code == 200, res.text
    body = res.json()
    assert body["iri"] == iri
    assert body["name"] == "HRS-1234"          # 由 IRI 局部名派生
    assert body["class_iris"] == [f"{FACTS}StabilityReport"]
    assert body["label_zh"] == "稳定性报告"
    assert body["properties"]["approvalStatus"] == "approved"


def test_get_entity_unknown_iri_still_404(client, db, analyst_headers):
    """既不在 World 也无影子行 → 仍 404（回退不掩盖真正不存在的个体）。"""
    iri = f"{FACTS}does-not-exist"
    res = client.get(f"/api/entities/{quote(iri, safe='')}", headers=analyst_headers)
    assert res.status_code == 404
