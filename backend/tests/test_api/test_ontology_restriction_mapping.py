"""Contract tests — restriction + mapping/health (契约 §5/§6).

契约先行，端点实现前应 FAIL。
"""

from __future__ import annotations

BASE = "https://ontology.pharma-gmp.cn/slpra/core/"
CLASSES = "/api/ontology/classes"
LINKS = "/api/ontology/link-types"


def _class(client, headers, name):
    iri = BASE + name
    client.post(
        CLASSES,
        json={"slpra_iri": iri, "label": name, "module": "drug"},
        headers=headers,
    )
    return iri


def _link(client, headers, name, domain, rng):
    iri = BASE + name
    client.post(
        LINKS,
        json={"slpra_iri": iri, "label": name, "domain_iri": domain, "range_iri": rng},
        headers=headers,
    )
    return iri


# --- §5 restriction --------------------------------------------------------
def test_create_restriction_some(client, analyst_headers):
    owner = _class(client, analyst_headers, "Drug")
    rng = _class(client, analyst_headers, "Excipient")
    prop = _link(client, analyst_headers, "contains", owner, rng)
    resp = client.post(
        f"{CLASSES}/{owner}/restrictions",
        json={
            "kind": "some",
            "property_iri": prop,
            "property_kind": "object",
            "filler_iri": rng,
        },
        headers=analyst_headers,
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["kind"] == "some"
    assert body["property_iri"] == prop


def test_restriction_invalid_kind_400(client, analyst_headers):
    owner = _class(client, analyst_headers, "Drug")
    resp = client.post(
        f"{CLASSES}/{owner}/restrictions",
        json={"kind": "nonsense"},
        headers=analyst_headers,
    )
    assert resp.status_code == 400


def test_restriction_some_requires_filler_400(client, analyst_headers):
    owner = _class(client, analyst_headers, "Drug")
    resp = client.post(
        f"{CLASSES}/{owner}/restrictions",
        json={"kind": "some"},
        headers=analyst_headers,
    )
    assert resp.status_code == 400


def test_restriction_appears_in_class_detail(client, analyst_headers):
    owner = _class(client, analyst_headers, "Drug")
    rng = _class(client, analyst_headers, "Excipient")
    prop = _link(client, analyst_headers, "contains", owner, rng)
    client.post(
        f"{CLASSES}/{owner}/restrictions",
        json={
            "kind": "some",
            "property_iri": prop,
            "property_kind": "object",
            "filler_iri": rng,
        },
        headers=analyst_headers,
    )
    detail = client.get(f"{CLASSES}/{owner}")
    assert detail.status_code == 200
    assert any(r["kind"] == "some" for r in detail.json()["restrictions"])


def test_restriction_update_and_delete(client, analyst_headers):
    owner = _class(client, analyst_headers, "Drug")
    rng = _class(client, analyst_headers, "Excipient")
    prop = _link(client, analyst_headers, "contains", owner, rng)
    created = client.post(
        f"{CLASSES}/{owner}/restrictions",
        json={"kind": "min", "property_iri": prop, "cardinality": 1},
        headers=analyst_headers,
    ).json()
    rid = created["id"]
    upd = client.put(
        f"/api/ontology/restrictions/{rid}",
        json={"cardinality": 2, "expected_version": created["version"]},
        headers=analyst_headers,
    )
    assert upd.status_code == 200
    assert upd.json()["cardinality"] == 2
    dele = client.delete(
        f"/api/ontology/restrictions/{rid}?expected_version={upd.json()['version']}",
        headers=analyst_headers,
    )
    assert dele.status_code == 204


def test_restriction_requires_role(client, operator_headers):
    resp = client.post(
        f"{CLASSES}/{BASE}Drug/restrictions",
        json={"kind": "some"},
        headers=operator_headers,
    )
    assert resp.status_code == 403


# --- §6 mapping + health ---------------------------------------------------
def test_create_and_list_mapping(client, analyst_headers):
    c = _class(client, analyst_headers, "Drug")
    resp = client.post(
        f"{CLASSES}/{c}/mappings",
        json={"mapping_type": "bfo", "target": "BFO_0000040"},
        headers=analyst_headers,
    )
    assert resp.status_code == 201
    assert resp.json()["mapping_type"] == "bfo"

    listing = client.get(f"{CLASSES}/{c}/mappings")
    assert listing.status_code == 200
    assert any(m["target"] == "BFO_0000040" for m in listing.json())


def test_mapping_bad_type_400(client, analyst_headers):
    c = _class(client, analyst_headers, "Drug")
    resp = client.post(
        f"{CLASSES}/{c}/mappings",
        json={"mapping_type": "bogus", "target": "x"},
        headers=analyst_headers,
    )
    assert resp.status_code == 400


def test_mapping_update_delete_and_health(client, analyst_headers):
    c = _class(client, analyst_headers, "Drug")
    created = client.post(
        f"{CLASSES}/{c}/mappings",
        json={"mapping_type": "bfo", "target": "BFO_0000040"},
        headers=analyst_headers,
    ).json()
    mid = created["id"]
    upd = client.put(
        f"/api/ontology/mappings/{mid}",
        json={
            "mapping_type": "bfo",
            "target": "BFO_0000002",
            "expected_version": created["version"],
        },
        headers=analyst_headers,
    )
    assert upd.status_code == 200
    assert upd.json()["target"] == "BFO_0000002"

    health = client.get("/api/ontology/mappings/health")
    assert health.status_code == 200
    body = health.json()
    assert "unmapped" in body and "ok" in body
    # only an slpra_iri+bfo pair counts as ok; this class lacks slpra_iri mapping
    assert c in body["unmapped"]

    dele = client.delete(
        f"/api/ontology/mappings/{mid}?expected_version={upd.json()['version']}",
        headers=analyst_headers,
    )
    assert dele.status_code == 204
