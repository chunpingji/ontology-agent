"""Contract tests — validate + import/export/diff + release/publish (契约 §8/§9/§10).

契约先行，端点实现前应 FAIL。
"""

from __future__ import annotations

BASE = "https://ontology.pharma-gmp.cn/slpra/core/"
CLASSES = "/api/ontology/classes"
ONTO = "/api/ontology"


def _class(client, headers, name):
    iri = BASE + name
    client.post(
        CLASSES,
        json={"slpra_iri": iri, "label": name, "module": "drug"},
        headers=headers,
    )
    return iri


def _fully_mapped_class(client, headers, name):
    """A class carrying both slpra_iri + bfo mappings (passes validate)."""
    iri = _class(client, headers, name)
    client.post(
        f"{CLASSES}/{iri}/mappings",
        json={"mapping_type": "slpra_iri", "target": iri},
        headers=headers,
    )
    client.post(
        f"{CLASSES}/{iri}/mappings",
        json={"mapping_type": "bfo", "target": "BFO_0000040"},
        headers=headers,
    )
    return iri


# --- §8 validate -----------------------------------------------------------
def test_validate_flags_missing_mapping(client, analyst_headers):
    _class(client, analyst_headers, "Drug")
    resp = client.post(f"{ONTO}/validate", headers=analyst_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert "blocking" in body and "warnings" in body and "reasoner" in body
    assert any(b["code"] == "missing_mapping" for b in body["blocking"])
    assert body["reasoner"]["ran"] is False  # graceful degradation


# --- §9 export / diff ------------------------------------------------------
def test_export_ttl(client, analyst_headers):
    _class(client, analyst_headers, "Drug")
    resp = client.get(f"{ONTO}/export/ttl")
    assert resp.status_code == 200


def test_export_diff(client, analyst_headers):
    _class(client, analyst_headers, "Drug")
    resp = client.get(f"{ONTO}/export/diff")
    assert resp.status_code == 200
    body = resp.json()
    assert "triples_added" in body
    assert "turtle_preview" in body


# --- §10 release lifecycle -------------------------------------------------
def test_release_create_and_list(client, analyst_headers):
    _fully_mapped_class(client, analyst_headers, "Drug")
    resp = client.post(f"{ONTO}/releases", json={"title": "首批"}, headers=analyst_headers)
    assert resp.status_code == 201
    rid = resp.json()["id"]
    assert resp.json()["status"] == "draft"

    listing = client.get(f"{ONTO}/releases")
    assert listing.status_code == 200
    assert any(r["id"] == rid for r in listing.json())


def test_release_submit_publish_flow(client, analyst_headers):
    _fully_mapped_class(client, analyst_headers, "Drug")
    rid = client.post(
        f"{ONTO}/releases", json={"title": "发布批次"}, headers=analyst_headers
    ).json()["id"]

    sub = client.post(f"{ONTO}/releases/{rid}/submit", headers=analyst_headers)
    assert sub.status_code == 200
    assert sub.json()["status"] == "in_review"

    pub = client.post(f"{ONTO}/releases/{rid}/publish", headers=analyst_headers)
    assert pub.status_code == 200
    assert pub.json()["status"] == "published"


def test_release_submit_blocked_by_validation(client, analyst_headers):
    # class without mappings → blocking → submit 409
    _class(client, analyst_headers, "Unmapped")
    rid = client.post(
        f"{ONTO}/releases", json={"title": "坏批次"}, headers=analyst_headers
    ).json()["id"]
    sub = client.post(f"{ONTO}/releases/{rid}/submit", headers=analyst_headers)
    assert sub.status_code == 409


def test_release_rollback(client, analyst_headers):
    _fully_mapped_class(client, analyst_headers, "Drug")
    rid = client.post(
        f"{ONTO}/releases", json={"title": "回退批次"}, headers=analyst_headers
    ).json()["id"]
    client.post(f"{ONTO}/releases/{rid}/submit", headers=analyst_headers)
    rb = client.post(f"{ONTO}/releases/{rid}/rollback", headers=analyst_headers)
    assert rb.status_code == 200
    assert rb.json()["status"] == "draft"


def test_release_create_requires_role(client, operator_headers):
    resp = client.post(f"{ONTO}/releases", json={"title": "x"}, headers=operator_headers)
    assert resp.status_code == 403
