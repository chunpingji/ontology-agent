"""Contract tests — link-type / data-property / action (契约 §3/§4/§7).

契约先行，端点实现前应 FAIL。
"""

from __future__ import annotations

BASE = "https://ontology.pharma-gmp.cn/slpra/core/"
CLASSES = "/api/ontology/classes"
LINKS = "/api/ontology/link-types"
DATAP = "/api/ontology/data-properties"
ACTIONS = "/api/ontology/actions"


def _class(client, headers, name, **extra):
    iri = BASE + name
    client.post(
        CLASSES,
        json={"slpra_iri": iri, "label": name, "module": "drug", **extra},
        headers=headers,
    )
    return iri


# --- §3 object property / relation -----------------------------------------
def test_create_link_type(client, analyst_headers):
    d = _class(client, analyst_headers, "Equipment")
    r = _class(client, analyst_headers, "Drug")
    resp = client.post(
        LINKS,
        json={
            "slpra_iri": BASE + "usedFor",
            "label": "用于",
            "domain_iri": d,
            "range_iri": r,
            "min_cardinality": 1,
            "max_cardinality": 3,
        },
        headers=analyst_headers,
    )
    assert resp.status_code == 201
    assert resp.json()["domain_iri"] == d
    assert resp.json()["range_iri"] == r


def test_link_type_cardinality_conflict_400(client, analyst_headers):
    d = _class(client, analyst_headers, "A")
    r = _class(client, analyst_headers, "B")
    resp = client.post(
        LINKS,
        json={
            "slpra_iri": BASE + "rel",
            "label": "rel",
            "domain_iri": d,
            "range_iri": r,
            "min_cardinality": 5,
            "max_cardinality": 2,
        },
        headers=analyst_headers,
    )
    assert resp.status_code == 400


def test_link_type_requires_role(client, operator_headers):
    resp = client.post(
        LINKS,
        json={"slpra_iri": BASE + "x", "label": "x"},
        headers=operator_headers,
    )
    assert resp.status_code == 403


# --- §4 data property -------------------------------------------------------
def test_create_data_property(client, analyst_headers):
    dom = _class(client, analyst_headers, "Drug")
    resp = client.post(
        DATAP,
        json={
            "slpra_iri": BASE + "batchNo",
            "label": "批号",
            "domain_iri": dom,
            "datatype": "string",
        },
        headers=analyst_headers,
    )
    assert resp.status_code == 201
    assert resp.json()["datatype"] == "string"


def test_data_property_bad_datatype_400(client, analyst_headers):
    resp = client.post(
        DATAP,
        json={"slpra_iri": BASE + "p", "label": "p", "datatype": "blob"},
        headers=analyst_headers,
    )
    assert resp.status_code == 400


def test_risk_vocabularies_and_wizard(client, analyst_headers):
    vocabs = client.get("/api/ontology/risk-vocabularies", headers=analyst_headers)
    assert vocabs.status_code == 200
    keys = {v["key"] for v in vocabs.json()}
    assert "OEB" in keys

    dom = _class(client, analyst_headers, "Drug")
    resp = client.post(
        DATAP + "/risk",
        json={
            "slpra_iri": BASE + "oebLevel",
            "label": "OEB等级",
            "domain_iri": dom,
            "datatype": "string",
            "vocab": "OEB",
        },
        headers=analyst_headers,
    )
    assert resp.status_code == 201
    assert resp.json()["controlled_vocab"]["vocab"] == "OEB"


# --- §7 action (definition only) -------------------------------------------
def test_create_and_list_action(client, analyst_headers):
    actor = _class(client, analyst_headers, "Operator")
    target = _class(client, analyst_headers, "Line")
    resp = client.post(
        ACTIONS,
        json={
            "slpra_iri": BASE + "cleanLine",
            "label": "清洁产线",
            "actor_iri": actor,
            "target_iri": target,
        },
        headers=analyst_headers,
    )
    assert resp.status_code == 201

    listing = client.get(ACTIONS, headers=analyst_headers)
    assert listing.status_code == 200
    assert any(a["slpra_iri"] == BASE + "cleanLine" for a in listing.json())
