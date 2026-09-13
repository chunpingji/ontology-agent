"""Cardinality edits preserve optionality, explicit clearing, CAS and draft authority."""

from pathlib import Path

import pytest
from rdflib import OWL, RDF, RDFS, XSD, Graph, Literal, URIRef

from app.config import settings
from app.models.ontology_meta import OntologyDataProperty, OntologyLinkType, OntologyRelease
from app.services.ontology_meta_store import OntologyMetaStore

BASE = "https://ontology.pharma-gmp.cn/slpra/core/"
PATHS = ("/api/ontology/data-properties", "/api/ontology/link-types")


def create_property(client, headers, path, **values):
    owner = BASE + "CardinalityOwner"
    response = client.post("/api/ontology/classes", headers=headers, json={
        "slpra_iri": owner, "label": "数量配置主体", "module": "drug",
    })
    assert response.status_code == 201
    response = client.post(path, headers=headers, json={
        "slpra_iri": BASE + "quantity", "label": "数量字段", "domain_iri": owner,
        **values,
    })
    return response


@pytest.mark.parametrize("path", PATHS)
@pytest.mark.parametrize("values, expected", [
    ({}, ("unspecified", None, None)),
    ({"multiplicity": "single"}, ("single", None, 1)),
    ({"multiplicity": "single", "min_cardinality": 1}, ("single", 1, 1)),
    ({"multiplicity": "multiple"}, ("multiple", None, None)),
    ({"multiplicity": "multiple", "min_cardinality": 0, "max_cardinality": 3},
     ("multiple", 0, 3)),
    ({"min_cardinality": 0, "max_cardinality": 0}, ("unspecified", 0, 0)),
])
def test_cardinality_create_roundtrip(client, analyst_headers, path, values, expected):
    response = create_property(client, analyst_headers, path, **values)
    assert response.status_code == 201, response.text
    body = response.json()
    names = ("multiplicity", "min_cardinality", "max_cardinality")
    assert tuple(body[name] for name in names) == expected
    stored = next(
        item for item in client.get(path).json() if item["slpra_iri"] == body["slpra_iri"]
    )
    assert tuple(stored[name] for name in names) == expected
    if path.endswith("link-types"):
        assert body["is_functional"] is (expected[0] == "single")


@pytest.mark.parametrize("path", PATHS)
@pytest.mark.parametrize("values", [
    {"multiplicity": "single", "max_cardinality": 2},
    {"multiplicity": "single", "min_cardinality": 2},
    {"multiplicity": "multiple", "max_cardinality": 1},
    {"multiplicity": "multiple", "max_cardinality": 0},
    {"min_cardinality": 3, "max_cardinality": 2},
])
def test_reject_inconsistent_cardinality(client, analyst_headers, path, values):
    response = create_property(client, analyst_headers, path, **values)
    assert response.status_code == 400


@pytest.mark.parametrize("path", PATHS)
@pytest.mark.parametrize("field", ["min_cardinality", "max_cardinality"])
@pytest.mark.parametrize("value", [-1, 1.5, True, "2"])
def test_counts_are_strict_nonnegative_integers(client, analyst_headers, path, field, value):
    response = create_property(client, analyst_headers, path, **{field: value})
    assert response.status_code == 422


@pytest.mark.parametrize("path", PATHS)
def test_omission_preserves_null_clears_and_cas_is_retained(client, analyst_headers, path):
    created = create_property(client, analyst_headers, path, multiplicity="multiple",
                              min_cardinality=1, max_cardinality=3).json()
    url = f"{path}/{created['slpra_iri']}"
    edited = client.put(url, headers=analyst_headers, json={
        "expected_version": created["version"], "label": "只编辑名称",
    })
    assert edited.status_code == 200, edited.text
    assert (edited.json()["min_cardinality"], edited.json()["max_cardinality"]) == (1, 3)
    assert edited.json()["multiplicity"] == "multiple"
    rejected = client.put(url, headers=analyst_headers, json={
        "expected_version": edited.json()["version"], "max_cardinality": 0,
    })
    assert rejected.status_code == 400
    cleared = client.put(url, headers=analyst_headers, json={
        "expected_version": edited.json()["version"], "multiplicity": "unspecified",
        "min_cardinality": None, "max_cardinality": None,
    })
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["min_cardinality"] is None
    assert cleared.json()["max_cardinality"] is None
    stale = client.put(url, headers=analyst_headers, json={
        "expected_version": edited.json()["version"], "multiplicity": "single",
    })
    assert stale.status_code == 409


@pytest.mark.parametrize("path", PATHS)
def test_change_single_to_multiple_requires_clearing_old_upper_bound(client, analyst_headers, path):
    created = create_property(client, analyst_headers, path, multiplicity="single").json()
    url = f"{path}/{created['slpra_iri']}"
    rejected = client.put(url, headers=analyst_headers, json={
        "expected_version": created["version"], "multiplicity": "multiple",
    })
    assert rejected.status_code == 400
    response = client.put(url, headers=analyst_headers, json={
        "expected_version": created["version"], "multiplicity": "multiple",
        "max_cardinality": None,
    })
    assert response.status_code == 200
    assert response.json()["max_cardinality"] is None


def test_legacy_functional_flag_and_unrelated_flags_are_preserved(client, analyst_headers):
    path = PATHS[1]
    created = create_property(client, analyst_headers, path, is_functional=True,
                              is_symmetric=True, is_transitive=True).json()
    assert created["multiplicity"] == "single" and created["max_cardinality"] == 1
    url = f"{path}/{created['slpra_iri']}"
    response = client.put(url, headers=analyst_headers, json={
        "expected_version": created["version"], "label": "新标签",
    })
    assert response.status_code == 200
    assert all(response.json()[name] for name in ("is_functional", "is_symmetric", "is_transitive"))
    response = client.put(url, headers=analyst_headers, json={
        "expected_version": response.json()["version"], "is_functional": False,
        "max_cardinality": None,
    })
    assert response.status_code == 200
    assert response.json()["multiplicity"] == "unspecified"
    assert response.json()["max_cardinality"] is None


@pytest.mark.parametrize("mode, flag", [
    ("single", False), ("multiple", True), ("unspecified", True),
])
def test_legacy_functional_cannot_conflict_with_explicit_mode(client, analyst_headers, mode, flag):
    response = create_property(client, analyst_headers, PATHS[1], multiplicity=mode,
                               is_functional=flag)
    assert response.status_code == 400


@pytest.mark.parametrize("path", PATHS)
def test_numeric_constraints_require_named_domain_but_optional_single_does_not(
    client, analyst_headers, path,
):
    response = client.post(path, headers=analyst_headers, json={
        "slpra_iri": BASE + "globalSingle", "label": "全局单值", "multiplicity": "single",
    })
    assert response.status_code == 201
    assert response.json()["min_cardinality"] is None
    response = client.post(path, headers=analyst_headers, json={
        "slpra_iri": BASE + "missingDomain", "label": "无定义域", "min_cardinality": 0,
    })
    assert response.status_code == 400


@pytest.mark.parametrize("path", PATHS)
def test_cardinality_edits_keep_role_gate(client, operator_headers, path):
    response = client.post(path, headers=operator_headers, json={
        "slpra_iri": BASE + "restricted", "label": "限制", "multiplicity": "single",
    })
    assert response.status_code == 403


def legacy_ttl():
    graph = Graph()
    prop = URIRef(BASE + "legacyValue")
    graph.add((prop, RDF.type, OWL.DatatypeProperty))
    graph.add((prop, RDF.type, OWL.FunctionalProperty))
    graph.add((prop, RDFS.label, Literal("TTL标签")))
    graph.add((prop, RDFS.range, XSD.string))
    return graph


def test_legacy_data_initialization_fills_only_new_fields_once(db, fake_engine):
    store = OntologyMetaStore(db, fake_engine)
    prop = OntologyDataProperty(slpra_iri=BASE + "legacyValue", label="编辑中标签",
                                status="draft", version=7, cardinality_seeded=False)
    db.add(prop)
    db.commit()
    original = (prop.label, prop.status, prop.version, prop.updated_at)
    assert store._initialize_legacy_data_cardinalities(legacy_ttl()) == 1
    db.commit()
    assert (prop.multiplicity, prop.min_cardinality, prop.max_cardinality) == ("single", None, 1)
    assert (prop.label, prop.status, prop.version, prop.updated_at) == original
    prop.multiplicity = "unspecified"
    prop.max_cardinality = None
    db.commit()
    assert store._initialize_legacy_data_cardinalities(legacy_ttl()) == 0
    assert prop.multiplicity == "unspecified" and prop.max_cardinality is None


def test_first_legacy_data_edit_reads_ttl_before_merging(client, db, analyst_headers):
    legacy_ttl().serialize(destination=settings.ontology_dir / "legacy.ttl", format="turtle")
    prop = OntologyDataProperty(slpra_iri=BASE + "legacyValue", label="旧草稿",
                                cardinality_seeded=False)
    db.add(prop)
    db.commit()
    response = client.put(f"{PATHS[0]}/{prop.slpra_iri}", headers=analyst_headers, json={
        "expected_version": prop.version, "label": "修改标签",
    })
    assert response.status_code == 200, response.text
    assert response.json()["multiplicity"] == "single"
    assert response.json()["max_cardinality"] == 1


def test_existing_new_quantity_draft_is_never_overwritten(db, fake_engine):
    store = OntologyMetaStore(db, fake_engine)
    prop = OntologyDataProperty(slpra_iri=BASE + "legacyValue", label="已有数量草稿",
                                multiplicity="multiple", cardinality_seeded=False)
    db.add(prop)
    db.commit()
    assert store._initialize_legacy_data_cardinalities(legacy_ttl()) == 1
    assert prop.multiplicity == "multiple" and prop.max_cardinality is None


def test_ttl_seeding_preserves_existing_link_draft_quantities(db, fake_engine):
    store = OntologyMetaStore(db, fake_engine)
    prop = OntologyLinkType(
        slpra_iri=BASE + "legacyLink", label="已有关系草稿", status="draft", version=7,
        multiplicity="unspecified", min_cardinality=1, max_cardinality=5,
        is_functional=False,
    )
    db.add(prop)
    db.commit()
    original = (prop.label, prop.status, prop.version, prop.updated_at)
    graph = Graph()
    graph.add((URIRef(prop.slpra_iri), RDF.type, OWL.ObjectProperty))
    graph.add((URIRef(prop.slpra_iri), RDF.type, OWL.FunctionalProperty))
    assert store._seed_link_types(graph) == 0
    assert (prop.multiplicity, prop.min_cardinality, prop.max_cardinality) == (
        "unspecified", 1, 5,
    )
    assert prop.is_functional is False
    assert (prop.label, prop.status, prop.version, prop.updated_at) == original


@pytest.mark.parametrize("maximum", [0, 5])
def test_legacy_conflicting_single_draft_is_explicitly_blocked(db, fake_engine, maximum):
    prop = OntologyLinkType(
        slpra_iri=BASE + "legacyConflict", label="迁移前冲突草稿",
        multiplicity="single", max_cardinality=maximum, is_functional=True,
    )
    db.add(prop)
    db.commit()
    blockers = OntologyMetaStore(db, fake_engine).validate()["blocking"]
    assert any(
        item["code"] == "cardinality_conflict" and item["entity_iri"] == prop.slpra_iri
        for item in blockers
    )
    assert prop.max_cardinality == maximum


def test_projection_and_release_audit_include_quantity(client, db, fake_engine, analyst_headers):
    prop = create_property(client, analyst_headers, PATHS[1], multiplicity="multiple",
                           min_cardinality=1, max_cardinality=4, is_symmetric=True).json()
    store = OntologyMetaStore(db, fake_engine)
    projected = next(
        item for item in store._projection_payloads() if item["iri"] == prop["slpra_iri"]
    )
    assert projected["multiplicity"] == "multiple"
    assert projected["min_cardinality"] == 1 and projected["max_cardinality"] == 4
    assert projected["is_symmetric"] is True
    release = store.create_release("数量变更", "analyst")
    entry = next(
        item for item in release["change_log"] if item["entity_table"] == "ontology_link_type"
    )
    assert entry["after"]["multiplicity"] == "multiple"
    assert entry["after"]["max_cardinality"] == 4


def test_projection_failure_stops_publish_after_diff_without_writing(db, fake_engine, monkeypatch):
    store = OntologyMetaStore(db, fake_engine)
    release = OntologyRelease(release_no="R-cardinality-test", title="投影失败", status="in_review")
    db.add(release)
    db.commit()
    order = []
    monkeypatch.setattr(store, "validate", lambda: {"blocking": [], "warnings": []})
    def export(*_args):
        order.append("diff")
        return "", [], []
    def project(_entities, *, published_graph=None, publish_callback=None):
        assert isinstance(published_graph, Graph)
        order.append("projection")
        raise RuntimeError("synthetic projection failure")
    monkeypatch.setattr("app.services.ontology_meta_store.ttl_merge.export_diff", export)
    monkeypatch.setattr(fake_engine, "project_entities", project)
    monkeypatch.setattr(store, "_write_ttl", lambda *_args: order.append("write"))
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as caught:
        store.publish_release(str(release.id), "analyst")
    assert caught.value.status_code == 409
    assert order == ["diff", "projection"]
    assert release.status == "in_review" and release.ttl_commit_sha is None


def review_release(db):
    release = OntologyRelease(release_no="R-publish-failure", title="写入发布", status="in_review")
    db.add(release)
    db.commit()
    return release


@pytest.mark.parametrize("has_previous", [False, True])
def test_ttl_replace_failure_rejects_publish_without_changing_old_file(
    client, db, analyst_headers, fake_engine, monkeypatch, has_previous,
):
    release = review_release(db)
    out_file = settings.ontology_dir / "slpra_managed.ttl"
    old_ttl = b"# previously published\n"
    if has_previous:
        out_file.write_bytes(old_ttl)
    original_replace = Path.replace

    def reject_replace(path, target):
        if Path(target) == out_file:
            raise OSError("synthetic filesystem failure")
        return original_replace(path, target)

    monkeypatch.setattr(Path, "replace", reject_replace)
    response = client.post(
        f"/api/ontology/releases/{release.id}/publish", headers=analyst_headers,
    )
    assert response.status_code == 409, response.text
    db.refresh(release)
    assert release.status == "in_review" and release.published_at is None
    assert release.ttl_commit_sha is None and fake_engine.projected == []
    assert out_file.read_bytes() == old_ttl if has_previous else not out_file.exists()
    assert not list(settings.ontology_dir.glob(".slpra_managed-*.tmp"))


@pytest.mark.parametrize("has_previous", [False, True])
def test_world_save_failure_restores_previous_ttl_after_callback(
    client, db, analyst_headers, fake_engine, monkeypatch, has_previous,
):
    release = review_release(db)
    out_file = settings.ontology_dir / "slpra_managed.ttl"
    old_ttl = b"# previously published\n"
    if has_previous:
        out_file.write_bytes(old_ttl)

    def reject_save(_entities, *, published_graph=None, publish_callback=None):
        publish_callback()
        assert out_file.exists() and out_file.read_bytes() != old_ttl
        raise RuntimeError("synthetic World save failure")

    def unexpected_git(*_args):
        pytest.fail("Git must not run after a rolled-back publication")

    monkeypatch.setattr(fake_engine, "project_entities", reject_save)
    monkeypatch.setattr(OntologyMetaStore, "_commit_ttl", unexpected_git)
    response = client.post(
        f"/api/ontology/releases/{release.id}/publish", headers=analyst_headers,
    )
    assert response.status_code == 409, response.text
    db.refresh(release)
    assert release.status == "in_review" and release.published_at is None
    assert out_file.read_bytes() == old_ttl if has_previous else not out_file.exists()


def test_optional_git_failure_occurs_only_after_durable_publish(
    client, db, analyst_headers, fake_engine, monkeypatch,
):
    release = review_release(db)
    out_file = settings.ontology_dir / "slpra_managed.ttl"
    git_attempts = []

    def reject_git(*_args, **_kwargs):
        assert out_file.exists() and fake_engine.projected
        git_attempts.append(True)
        raise OSError("synthetic Git unavailable")

    monkeypatch.setattr("app.services.ontology_meta_store.subprocess.run", reject_git)
    response = client.post(
        f"/api/ontology/releases/{release.id}/publish", headers=analyst_headers,
    )
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "published"
    assert response.json()["ttl_commit_sha"] is None
    assert git_attempts == [True]


def test_failed_ttl_restoration_is_explicit_and_never_marks_release_published(
    client, db, analyst_headers, fake_engine, monkeypatch,
):
    release = review_release(db)
    out_file = settings.ontology_dir / "slpra_managed.ttl"
    old_ttl = b"# previously published\n"
    out_file.write_bytes(old_ttl)
    original_write = OntologyMetaStore._write_ttl

    def reject_restore(store, content):
        if content == old_ttl:
            raise OSError("synthetic restoration failure")
        original_write(store, content)

    def reject_save(_entities, *, published_graph=None, publish_callback=None):
        publish_callback()
        raise RuntimeError("synthetic World save failure")

    monkeypatch.setattr(OntologyMetaStore, "_write_ttl", reject_restore)
    monkeypatch.setattr(fake_engine, "project_entities", reject_save)
    response = client.post(
        f"/api/ontology/releases/{release.id}/publish", headers=analyst_headers,
    )
    assert response.status_code == 500, response.text
    assert "TTL 恢复失败" in response.json()["detail"]
    db.refresh(release)
    assert release.status == "in_review" and release.published_at is None
