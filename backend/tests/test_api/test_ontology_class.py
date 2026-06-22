"""Contract tests — Class CRUD + 乐观并发(409) + disable/review (契约 §2).

Written first per Constitution 原则 IV (测试纪律，契约先行)。在端点实现前应 FAIL。
"""

from __future__ import annotations

BASE = "https://ontology.pharma-gmp.cn/slpra/core/"
CLASSES = "/api/ontology/classes"


def _make_class(client, headers, iri=BASE + "TestDrug", label="测试药物", **extra):
    body = {"slpra_iri": iri, "label": label, "module": "drug", **extra}
    return client.post(CLASSES, json=body, headers=headers)


def test_create_class_requires_role(client, operator_headers):
    # operator 角色不足 → 403
    resp = _make_class(client, operator_headers)
    assert resp.status_code == 403


def test_create_class_missing_headers(client):
    resp = _make_class(client, {})
    assert resp.status_code == 403


def test_create_class_success(client, analyst_headers):
    resp = _make_class(client, analyst_headers)
    assert resp.status_code == 201
    data = resp.json()
    assert data["slpra_iri"] == BASE + "TestDrug"
    assert data["version"] == 1
    assert data["status"] == "draft"
    assert data["is_disabled"] is False


def test_create_duplicate_iri_400(client, analyst_headers):
    assert _make_class(client, analyst_headers).status_code == 201
    assert _make_class(client, analyst_headers).status_code == 400


def test_create_class_rejects_unmanaged_iri(client, analyst_headers):
    resp = _make_class(client, analyst_headers, iri="http://example.org/Foo")
    assert resp.status_code == 400


def test_update_class_optimistic_concurrency(client, analyst_headers):
    created = _make_class(client, analyst_headers).json()
    iri = created["slpra_iri"]
    # 正确版本 → 200，version 自增
    ok = client.put(
        f"{CLASSES}/{iri}",
        json={"label": "改名", "expected_version": 1},
        headers=analyst_headers,
    )
    assert ok.status_code == 200
    assert ok.json()["version"] == 2
    assert ok.json()["label"] == "改名"
    # 陈旧版本 → 409
    stale = client.put(
        f"{CLASSES}/{iri}",
        json={"label": "再改", "expected_version": 1},
        headers=analyst_headers,
    )
    assert stale.status_code == 409


def test_update_missing_class_404(client, analyst_headers):
    resp = client.put(
        f"{CLASSES}/{BASE}Ghost",
        json={"label": "x", "expected_version": 1},
        headers=analyst_headers,
    )
    assert resp.status_code == 404


def test_delete_class_disables(client, analyst_headers):
    created = _make_class(client, analyst_headers).json()
    iri = created["slpra_iri"]
    resp = client.delete(
        f"{CLASSES}/{iri}?expected_version=1", headers=analyst_headers
    )
    assert resp.status_code == 204


def test_disable_and_review(client, analyst_headers):
    created = _make_class(client, analyst_headers).json()
    iri = created["slpra_iri"]
    dis = client.post(
        f"{CLASSES}/{iri}/disable",
        json={"expected_version": 1},
        headers=analyst_headers,
    )
    assert dis.status_code == 200
    assert dis.json()["is_disabled"] is True

    rev = client.post(
        f"{CLASSES}/{iri}/review",
        json={"expected_version": dis.json()["version"]},
        headers=analyst_headers,
    )
    assert rev.status_code == 200
    assert rev.json()["is_reviewed"] is True
