"""Acceptance tests use real Mock tables and HTTP mapping/query routes, no LLM mocks."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from rdflib import Graph

from app.models.mock_data import MockEquipment, MockProductionArea
from app.models.ontology_meta import OntologyClass, OntologyClassMapping
from app.schemas.entity_query import EntityQueryRequest
from app.services.entity_query import EntityQueryService
from app.services.entity_query_schema import EntityQuerySchema

BASE = "urn:query-test:"
CLS, CHILD, PROP, SCOPE, ALT = [
    BASE + x for x in ("Asset", "SpecialAsset", "code", "site", "alias")
]
XSD = "http://www.w3.org/2001/XMLSchema#"


@pytest.fixture
def fake_engine():
    graph = Graph().parse(
        data="""
@prefix t: <urn:query-test:> .
@prefix owl: <http://www.w3.org/2002/07/owl#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .
@prefix integ: <https://ontology.pharma-gmp.cn/slpra/integration/> .
t:Asset a owl:Class ; owl:hasKey (t:code t:site), (t:alias), (t:code t:owner) .
t:SpecialAsset a owl:Class ; rdfs:subClassOf t:Asset .
t:Other a owl:Class .
t:Both a owl:Class ; rdfs:subClassOf t:Asset, t:Other .
t:code a owl:DatatypeProperty ; rdfs:domain t:Asset ; rdfs:range xsd:string ;
    integ:identityKey true .
t:site a owl:DatatypeProperty ; rdfs:domain t:Asset ; rdfs:range xsd:string .
t:alias a owl:DatatypeProperty ; rdfs:domain t:Asset ; rdfs:range xsd:string .
t:number a owl:DatatypeProperty ; rdfs:domain t:Asset ; rdfs:range xsd:integer .
t:owner a owl:ObjectProperty ; rdfs:domain t:Asset ; rdfs:range t:Other .
t:union a owl:DatatypeProperty ; rdfs:domain [owl:unionOf (t:Asset t:Other)] ;
    rdfs:range xsd:string .
t:intersection a owl:DatatypeProperty ;
    rdfs:domain [owl:intersectionOf (t:Asset t:Other)] ; rdfs:range xsd:string .
t:multiDomain a owl:DatatypeProperty ; rdfs:domain t:Asset, t:Other ; rdfs:range xsd:string .
""",
        format="turtle",
    )
    return SimpleNamespace(entity_query_graph=lambda: graph)


@pytest.fixture
def setup_query(db, client, analyst_headers, fake_engine):
    db.add(OntologyClass(slpra_iri=CLS, label="Asset"))
    db.commit()

    def configure(dataset="production_areas", prop=PROP, path="col:code", config=None):
        if config is None:
            config = {
                "label_path": "col:label",
                "entity_iri_path": "col:iri",
                "identifier_namespace": "urn:test:source",
                "lookup_key_groups": [
                    {"property_iris": [prop], "scope_property_iris": []},
                ],
            }
        response = client.post(
            f"/api/ontology/classes/{CLS}/mappings",
            headers=analyst_headers,
            json={
                "mapping_type": "mock_dataset",
                "target": dataset,
                "source_system": "builtin_mock",
                "query_config": config,
            },
        )
        assert response.status_code == 201, response.text
        mapping = response.json()
        if prop:
            response = client.post(
                f"/api/ontology/mappings/{mapping['id']}/property-bindings",
                headers=analyst_headers,
                json={"property_iri": prop, "source_path": path},
            )
            assert response.status_code == 201, response.text
        return mapping

    return configure


def seed(db, codes=("642", "644", "646"), **kwargs):
    rows = [
        MockProductionArea(code=c, label=c + " item", iri="urn:row:" + c, **kwargs) for c in codes
    ]
    db.add_all(rows)
    db.commit()
    return rows


def query_item(value=None, **kwargs):
    return {
        "query_id": str(uuid4()),
        "class_iri": CLS,
        "property_filters": [
            {"property_iri": PROP, "value": value, "datatype_iri": XSD + "string"},
        ]
        if value is not None
        else [],
        **kwargs,
    }


def query(client, headers, *items):
    r = client.post("/api/entities/query", headers=headers, json={"queries": list(items)})
    assert r.status_code == 200, r.text
    return r.json()["results"]


def test_exact_identifiers_current_data_and_read_only(db, client, operator_headers, setup_query):
    setup_query()
    rows = seed(db)
    # No operation in the query service is allowed to INSERT/UPDATE/DELETE or invoke a model.
    from sqlalchemy import event

    statements = []

    def record(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    event.listen(db.get_bind(), "before_cursor_execute", record)
    try:
        out = query(
            client,
            operator_headers,
            *(
                query_item(x)
                for x in (
                    "611/642/646",
                    "611",
                    "642",
                    "646",
                    "0642",
                )
            ),
        )
        catalog = client.get("/api/entities/sources", headers=operator_headers)
        assert catalog.status_code == 200, catalog.text
    finally:
        event.remove(db.get_bind(), "before_cursor_execute", record)
    assert all(
        s.lstrip().split()[0].upper() in {"SELECT", "SAVEPOINT", "RELEASE"} for s in statements
    )
    assert [r["outcome"] for r in out] == ["no_match", "no_match", "matches", "matches", "no_match"]
    assert all(r["complete"] for r in out)
    c = out[2]["candidates"][0]
    assert c["record_ref"]["record_id"] == str(rows[0].id)
    assert c["identity_status"] == "not_checked" and c["matched_lookup_groups"] == [0]
    assert c["business_scope_status"] == "unspecified"
    rows[0].label = "new name"
    db.commit()
    new = query(client, operator_headers, query_item("642"))[0]["candidates"][0]
    assert new["label"] == "new name" and new["record_ref"] == c["record_ref"]
    assert new["record_version"] != c["record_version"]
    db.delete(rows[0])
    db.commit()
    assert query(client, operator_headers, query_item("642"))[0]["outcome"] == "no_match"


def test_whole_string_is_a_record_not_a_split(db, client, operator_headers, setup_query):
    setup_query()
    seed(db, ("611/642/646", "611", "642", "646", "0642"))
    results = query(
        client,
        operator_headers,
        *(
            query_item(v)
            for v in (
                "611/642/646",
                "611",
                "642",
                "646",
                "0642",
            )
        ),
    )
    assert all(r["complete"] and len(r["candidates"]) == 1 for r in results)
    assert len({r["candidates"][0]["record_ref"]["record_id"] for r in results}) == 5


def test_source_browse_pages_are_scoped_and_keep_identity_unchecked(
    db, client, operator_headers, setup_query
):
    mapping = setup_query()
    setup_query(dataset="equipment", path="col:equipment_id")
    rows = seed(db, tuple(f"item-{i}" for i in range(63)))
    db.add(MockEquipment(
        equipment_id="other-source", label="other", iri="urn:other",
        equipment_class_iri=CLS, workshop_code="site-a",
    ))
    db.commit()
    pages = query(
        client, operator_headers,
        *(query_item(mapping_ids=[mapping["id"]], limit=50, offset=n) for n in (0, 50, 100)),
    )
    assert [len(p["candidates"]) for p in pages] == [50, 13, 0]
    assert [p["next_offset"] for p in pages] == [50, None, None]
    assert all(p["total"] == 63 and p["outcome"] == "matches" for p in pages)
    assert all(p["truncated"] and not p["complete"] for p in pages)
    candidates = pages[0]["candidates"] + pages[1]["candidates"]
    assert [c["record_ref"]["record_id"] for c in candidates] == sorted(str(r.id) for r in rows)
    assert all(c["mapping_id"] == mapping["id"] for c in candidates)
    assert all(c["matches"] == [] and c["matched_lookup_groups"] == [] for c in candidates)
    assert all(c["identity_status"] == "not_checked" for c in candidates)
    filtered = query(
        client, operator_headers, query_item("item-62", mapping_ids=[mapping["id"]])
    )[0]
    assert filtered["total"] == 1 and filtered["complete"]
    assert filtered["next_offset"] is None


def test_unfiltered_browse_requires_one_existing_mapping(client, operator_headers, setup_query):
    mapping = setup_query()
    for extra in (
        {}, {"mapping_ids": [mapping["id"], str(uuid4())]},
        {"mapping_ids": [str(uuid4())]}, {"mapping_ids": [mapping["id"]], "offset": -1},
        {"mapping_ids": [mapping["id"]], "limit": 51},
    ):
        response = client.post(
            "/api/entities/query", headers=operator_headers,
            json={"queries": [query_item(**extra)]},
        )
        assert response.status_code == 422, response.text
    empty = query(client, operator_headers, query_item(mapping_ids=[mapping["id"]]))[0]
    assert empty["outcome"] == "no_match" and empty["complete"]
    assert empty["total"] == 0 and empty["next_offset"] is None


def test_browse_incomplete_source_has_unknown_total(
    db, client, operator_headers, setup_query, monkeypatch
):
    mapping = setup_query()
    seed(db)
    import app.services.integration.mock_entity_reader as reader

    monkeypatch.setattr(reader, "SCAN_LIMIT", 2)
    result = query(client, operator_headers, query_item(mapping_ids=[mapping["id"]], limit=1))[0]
    assert result["total"] is None and result["next_offset"] == 1
    assert result["truncated"] and not result["complete"]
    assert result["sources"][0]["issues"][0]["code"] == "scan_limit"
    final = query(
        client, operator_headers, query_item(mapping_ids=[mapping["id"]], limit=1, offset=1)
    )[0]
    assert final["total"] is None and final["next_offset"] is None
    assert not final["complete"]


def test_multivalue_keys_and_array_positions(db, client, operator_headers, setup_query):
    setup_query(path="attr-label:identifiers")
    seed(
        db,
        ("one",),
        data_properties=[
            {"iri": None, "label": "identifiers", "value": "642"},
            {"iri": None, "label": "identifiers", "value": "646"},
        ],
    )
    results = query(client, operator_headers, query_item("642"), query_item("646"))
    assert results[0]["candidates"][0]["record_ref"] == results[1]["candidates"][0]["record_ref"]
    candidate = results[0]["candidates"][0]
    assert candidate["matched_lookup_groups"] == []
    assert [v["source_index"] for v in candidate["properties"][0]["values"]] == [0, 1]


def test_composite_keys_scope_and_ontology_guidance(
    db, client, analyst_headers, operator_headers, setup_query
):
    config = {
        "label_path": "col:label",
        "identifier_namespace": "urn:test:keys",
        "lookup_key_groups": [
            {"property_iris": [PROP], "scope_property_iris": [SCOPE]},
            {"property_iris": [ALT]},
        ],
    }
    m = setup_query(config=config)
    for p, path in ((SCOPE, "col:description"), (ALT, "col:label")):
        r = client.post(
            f"/api/ontology/mappings/{m['id']}/property-bindings",
            headers=analyst_headers,
            json={"property_iri": p, "source_path": path},
        )
        assert r.status_code == 201, r.text
    seed(db, ("642",), description="site-a")
    seed(db, ("646",), description="site-b")
    a = query_item("642")
    assert query(client, operator_headers, a)[0]["candidates"][0]["matched_lookup_groups"] == []
    a["property_filters"].append(
        {"property_iri": SCOPE, "value": "site-b", "datatype_iri": XSD + "string"}
    )
    assert query(client, operator_headers, a)[0]["outcome"] == "no_match"
    a["property_filters"][1]["value"] = "site-a"
    c = query(client, operator_headers, a)[0]["candidates"][0]
    assert c["matched_lookup_groups"] == [0] and c["business_scope_status"] == "provided"
    source = client.get("/api/entities/sources", headers=operator_headers).json()["sources"][0]
    guidance = source["mappings"][0]
    assert guidance["identity_properties"][0]["property_iri"] == PROP
    group = next(g for g in guidance["identity_key_groups"] if BASE + "owner" in g["property_iris"])
    assert not group["available"] and BASE + "owner" in group["unavailable_property_iris"]


def test_duplicate_iri_and_same_code_across_sources(db, client, operator_headers, setup_query):
    setup_query()
    setup_query(dataset="equipment", path="col:equipment_id")
    row = seed(db, ("642",))[0]
    db.add(
        MockEquipment(
            equipment_id="642",
            iri=row.iri,
            label="also 642",
            equipment_class_iri=CLS,
            workshop_code="site-b",
        )
    )
    db.commit()
    out = query(client, operator_headers, query_item("642"))[0]
    assert len(out["candidates"]) == 2 and out["complete"]
    assert all("source_iri_conflict" in [i["code"] for i in c["issues"]] for c in out["candidates"])


def test_dynamic_parent_mapping_child_query(db, client, operator_headers, setup_query):
    config = {"label_path": "col:label", "class_path": "col:equipment_class_iri"}
    setup_query(dataset="equipment", path="col:equipment_id", config=config)
    for code, cls in (("child", CHILD), ("parent", CLS)):
        db.add(
            MockEquipment(
                equipment_id=code,
                label=code,
                iri="urn:row:" + code,
                equipment_class_iri=cls,
                workshop_code="site-a",
            )
        )
    db.commit()
    results = query(
        client,
        operator_headers,
        query_item("child", class_iri=CHILD),
        query_item("parent", class_iri=CHILD),
        query_item("child"),
        query_item("child", include_subclasses=True),
    )
    assert [r["outcome"] for r in results] == ["matches", "no_match", "no_match", "matches"]


def test_partial_failures_unsupported_filters_and_limits(
    db, client, operator_headers, setup_query, monkeypatch, fake_engine
):
    setup_query()
    setup_query(dataset="equipment", prop=ALT, path="col:label")
    seed(db)
    out = query(client, operator_headers, query_item("642"))[0]
    assert out["outcome"] == "matches" and not out["complete"]
    assert "unsupported_filter" in [s["status"] for s in out["sources"]]
    out = query(
        client,
        operator_headers,
        query_item(None, name={"value": "item", "match": "contains"}, limit=1),
    )[0]
    assert out["truncated"] and not out["complete"]
    import app.services.integration.mock_entity_reader as reader

    monkeypatch.setattr(reader, "SCAN_LIMIT", 1)
    out = query(client, operator_headers, query_item("missing"))[0]
    assert out["truncated"] and out["outcome"] == "unresolved"
    service = EntityQueryService(db, fake_engine)

    def fail(dataset):
        raise RuntimeError("sensitive credentials must not be exposed")

    monkeypatch.setattr(service.reader, "read", fail)
    out = service.query(EntityQueryRequest(queries=[query_item("642")]))["results"][0]
    assert out["outcome"] == "unresolved" and "sensitive" not in json.dumps(out)


def test_one_read_per_batch_and_binding_revision(
    db, client, operator_headers, analyst_headers, setup_query, fake_engine, monkeypatch
):
    m = setup_query()
    seed(db)
    service = EntityQueryService(db, fake_engine)
    original, calls = service.reader.read, []

    def read(dataset):
        calls.append(dataset)
        return original(dataset)

    monkeypatch.setattr(service.reader, "read", read)
    result = service.query(EntityQueryRequest(queries=[query_item("642"), query_item("646")]))
    assert calls == ["production_areas"]
    revision = result["results"][0]["candidates"][0]["mapping_revision"]
    bindings = client.get(f"/api/ontology/mappings/{m['id']}/property-bindings").json()
    b = bindings[0]
    r = client.put(
        f"/api/ontology/property-bindings/{b['id']}",
        headers=analyst_headers,
        json={
            "expected_version": b["version"],
            "property_iri": PROP,
            "source_path": "col:label",
        },
    )
    assert r.status_code == 200, r.text
    c = query(client, operator_headers, query_item("642 item"))[0]["candidates"][0]
    assert c["mapping_revision"] != revision
    assert query(client, operator_headers, query_item("642"))[0]["outcome"] == "no_match"


@pytest.mark.parametrize(
    "patch",
    [
        {"source_path": "col:password"},
        {"source_path": "$.code"},
        {"property_iri": BASE + "missing"},
        {"property_iri": BASE + "intersection"},
        {"property_iri": BASE + "multiDomain"},
        {"property_iri": BASE + "owner", "property_kind": "object"},
        {"transform_type": "controlled_vocab", "transform_config": {"vocab": "oeb"}},
        {"transform_type": "pattern", "transform_config": {"pattern": ".*"}},
        {"transform_type": "cast", "transform_config": {"to": "integer"}},
    ],
)
def test_invalid_binding_rejected(db, client, analyst_headers, setup_query, patch):
    m = setup_query(prop=None, config={"label_path": "col:label"})
    payload = {"property_iri": PROP, "source_path": "col:code", **patch}
    r = client.post(
        f"/api/ontology/mappings/{m['id']}/property-bindings", headers=analyst_headers, json=payload
    )
    assert r.status_code == 422, r.text


def test_auth_cas_draft_invalidated_mapping_and_no_mapping(
    db, client, analyst_headers, operator_headers, setup_query
):
    assert query(client, operator_headers, query_item("642"))[0]["outcome"] == "unresolved"
    m = setup_query(prop=None, config={})
    payload = {
        "mapping_type": "mock_dataset",
        "target": "production_areas",
        "source_system": "builtin_mock",
        "query_config": {"label_path": "col:label"},
        "expected_version": m["version"],
    }
    assert (
        client.put(
            f"/api/ontology/mappings/{m['id']}", headers=operator_headers, json=payload
        ).status_code
        == 403
    )
    assert (
        client.put(
            f"/api/ontology/mappings/{m['id']}", headers=analyst_headers, json=payload
        ).status_code
        == 200
    )
    assert (
        client.put(
            f"/api/ontology/mappings/{m['id']}", headers=analyst_headers, json=payload
        ).status_code
        == 409
    )
    assert query(client, operator_headers, query_item("642"))[0]["outcome"] == "unresolved"
    mapping = db.get(OntologyClassMapping, UUID(m["id"]))
    mapping.query_config = {"label_path": "col:no-longer-exists"}
    db.commit()
    result = query(client, operator_headers, query_item("642"))[0]
    assert result["sources"][0]["status"] == "invalid_mapping"
    assert (
        client.post(
            "/api/entities/query",
            headers=operator_headers,
            json={
                "queries": [
                    query_item("642", mapping_ids=[str(uuid4())]),
                ]
            },
        ).status_code
        == 422
    )
    from unittest.mock import patch

    from app.config import settings

    with patch.object(settings, "auth_required", True):
        assert client.get("/api/entities/sources").status_code == 401
        assert (
            client.post("/api/entities/query", json={"queries": [query_item("642")]}).status_code
            == 401
        )


def test_domain_semantics(db, fake_engine):
    schema = EntityQuerySchema(db, fake_engine)
    assert schema.property(CLS, BASE + "union")
    assert not schema.property(CLS, BASE + "intersection")
    assert not schema.property(CLS, BASE + "multiDomain")
    assert schema.property(BASE + "Both", BASE + "intersection")
    assert schema.property(BASE + "Both", BASE + "multiDomain")


def test_initial_configuration_against_current_authoritative_ontology(db):
    root = Path(__file__).resolve().parents[2]
    graph = Graph()
    for path in (root.parent / "ontology" / "slpra").glob("*.ttl"):
        graph.parse(path, format="turtle")
    schema = EntityQuerySchema(db, SimpleNamespace(entity_query_graph=lambda: graph))
    templates = json.loads((root / "app/resources/mock_entity_mappings.json").read_text())
    from app.services.entity_query_mapping import mapping_issues

    for template in templates:
        cfg = SimpleNamespace(**template)
        bindings = [
            SimpleNamespace(**b, transform_config=None) for b in template["property_bindings"]
        ]
        assert not mapping_issues(schema, template["class_iri"], cfg, bindings)
        guidance = schema.identity_guidance(
            template["class_iri"], {b["property_iri"] for b in template["property_bindings"]}
        )
        assert guidance["identity_properties"]


def test_conversion_failure_never_completes_a_partial_key(
    db,
    client,
    analyst_headers,
    operator_headers,
    setup_query,
):
    m = setup_query(
        prop=None,
        config={
            "label_path": "col:label",
            "identifier_namespace": "urn:test:numbers",
            "lookup_key_groups": [{"property_iris": [BASE + "number"]}],
        },
    )
    r = client.post(
        f"/api/ontology/mappings/{m['id']}/property-bindings",
        headers=analyst_headers,
        json={
            "property_iri": BASE + "number",
            "source_path": "attr-label:numbers",
            "transform_type": "cast",
            "transform_config": {"to": "integer"},
        },
    )
    assert r.status_code == 201, r.text
    seed(
        db,
        ("one",),
        data_properties=[
            {"label": "numbers", "value": "7"},
            {"label": "numbers", "value": "broken"},
        ],
    )
    result = query(
        client,
        operator_headers,
        query_item(
            None,
            property_filters=[
                {
                    "property_iri": BASE + "number",
                    "value": 7,
                    "datatype_iri": XSD + "integer",
                }
            ],
        ),
    )[0]
    assert result["outcome"] == "matches" and not result["complete"]
    assert result["candidates"][0]["matched_lookup_groups"] == []
    assert result["candidates"][0]["properties"][0]["values"][0]["raw_value"] == "7"


def test_explicit_value_map_miss_and_absent_array_field(
    db,
    client,
    analyst_headers,
    operator_headers,
    setup_query,
):
    m = setup_query(prop=None, config={"label_path": "col:label"})
    r = client.post(
        f"/api/ontology/mappings/{m['id']}/property-bindings",
        headers=analyst_headers,
        json={
            "property_iri": PROP,
            "source_path": "attr-iri:urn:source:identifier",
            "transform_type": "controlled_vocab",
            "transform_config": {"map": {"A": "a"}},
        },
    )
    assert r.status_code == 201, r.text
    seed(db, ("missing",))
    seed(
        db,
        ("unmapped",),
        data_properties=[{"iri": "urn:source:identifier", "label": "ignored", "value": "B"}],
    )
    result = query(client, operator_headers, query_item("B"))[0]
    assert result["outcome"] == "unresolved" and not result["complete"]
    assert {i["code"] for s in result["sources"] for i in s["issues"]} == {
        "conversion_failed",
        "value_missing",
    }


def test_invalid_dynamic_type_and_disabled_binding(db, client, operator_headers, setup_query):
    from app.models.ontology_meta import OntologyDataProperty

    setup_query(
        dataset="equipment",
        path="col:equipment_id",
        config={
            "label_path": "col:label",
            "class_path": "col:equipment_class_iri",
        },
    )
    db.add(
        MockEquipment(
            equipment_id="invalid",
            label="record",
            iri="urn:test:row",
            equipment_class_iri=BASE + "Other",
            workshop_code="a",
        )
    )
    db.commit()
    result = query(client, operator_headers, query_item("invalid", include_subclasses=True))[0]
    assert result["outcome"] == "unresolved"
    assert result["sources"][0]["issues"][0]["code"] == "invalid_record_type"
    # A valid query property not disabled itself; the source's bound property can become disabled.
    db.add(OntologyDataProperty(slpra_iri=PROP, label="id", is_disabled=True))
    db.commit()
    result = query(client, operator_headers, query_item(None, name={"value": "record"}))[0]
    assert result["sources"][0]["status"] == "invalid_mapping"


@pytest.mark.parametrize(
    "queries",
    [
        [],
        [query_item("x")] * 2,
        [query_item("x", limit=0)],
        [query_item("x", limit=51)],
        [query_item("x", class_iri=BASE + "missing")],
        [query_item(None)],
        [query_item(642)],
        [query_item(True)],
        [
            query_item(
                "x",
                property_filters=[
                    {"property_iri": PROP, "value": "x", "datatype_iri": XSD + "integer"}
                ],
            )
        ],
    ],
)
def test_query_contract_rejects_invalid_inputs(client, operator_headers, queries):
    response = client.post(
        "/api/entities/query", headers=operator_headers, json={"queries": queries}
    )
    assert response.status_code == 422, response.text


def test_source_catalog_has_no_values_and_never_seeds(db, client, operator_headers, setup_query):
    seed(
        db,
        ("SECRET-SOURCE-VALUE",),
        data_properties=[
            {"iri": None, "label": "external-id", "value": "SECRET-ATTRIBUTE-VALUE"},
        ],
    )
    source = client.get("/api/entities/sources", headers=operator_headers)
    assert source.status_code == 200, source.text
    assert "SECRET-SOURCE-VALUE" not in source.text and "SECRET-ATTRIBUTE-VALUE" not in source.text
    assert len(source.json()["sources"]) == 5
    assert db.query(OntologyClassMapping).count() == 0
    assert all(not s["mappings"] for s in source.json()["sources"])
