from copy import deepcopy
from uuid import UUID

from app.models.extraction import AstTemplate
from tests.test_reporting.test_model_context import automatic_template
from tests.test_reporting.test_output_contracts import ontology


def test_type_selection_create_compile_revision_and_metadata_are_one_path(
    client, db, analyst_headers, monkeypatch
):
    from app.services.extraction import extraction_tasks

    classes = ontology()
    classes["urn:ReportB"] = deepcopy(classes["urn:Report"])
    monkeypatch.setattr(extraction_tasks, "semantic_schema_from_engine", lambda _: classes)
    context = client.get("/api/report-model-context", headers=analyst_headers)
    assert context.status_code == 200, context.text
    assert context.json()["status"] == "captured"
    response = client.post(
        "/api/ast-templates",
        headers=analyst_headers,
        json={
            "name": "Automatic template",
            "version": "v1",
            "iri_pattern": "urn:Report",
            "schema_json": automatic_template(),
        },
    )
    assert response.status_code == 201, response.text
    original = response.json()
    saved_schema = db.get(AstTemplate, UUID(original["id"])).schema_json
    assert saved_schema["ontology_release_ref"] == context.json()["contract_id"]
    path = "/api/ast-templates/" + original["id"]
    compiled = client.post(
        path + "/compile", headers=analyst_headers, json={"expected_hash": original["schema_hash"]}
    )
    assert compiled.status_code == 200, compiled.text
    assert compiled.json()["valid"]
    rejected = client.patch(path, headers=analyst_headers, json={"iri_pattern": "urn:ReportB"})
    assert rejected.status_code == 409
    draft = deepcopy(saved_schema)
    draft["source_slots"][0]["class_iri"] = "urn:ReportB"
    revised = client.post(
        path + "/revisions",
        headers=analyst_headers,
        json={
            "schema": draft,
            "expected_revision": original["revision_no"],
            "expected_hash": original["schema_hash"],
        },
    )
    assert revised.status_code == 201, revised.text
    assert revised.json()["iri_pattern"] == "urn:ReportB"
    assert db.get(AstTemplate, UUID(original["id"])).iri_pattern == "urn:Report"
    stale = client.post(
        path + "/revisions",
        headers=analyst_headers,
        json={
            "schema": draft,
            "expected_revision": original["revision_no"],
            "expected_hash": original["schema_hash"],
        },
    )
    assert stale.status_code == 409


def test_builtin_rules_and_profiles_are_read_only_and_do_not_create_review_events(
    client, analyst_headers
):
    response = client.get("/api/graph-rules", headers=analyst_headers)
    assert response.status_code == 200
    rule = response.json()[0]
    assert rule["origin"] == "bundled_executor"
    decision = client.post(
        "/api/report-contracts/" + rule["contract_id"] + "/decisions",
        headers=analyst_headers,
        json={
            "expected_hash": rule["definition_hash"],
            "decision": "published",
            "reason": "must not publish a bundled rule",
        },
    )
    assert decision.status_code in {400, 404, 422}


def test_failed_model_projection_does_not_approve_the_previous_runtime_schema(
    client, db, fake_engine, analyst_headers, monkeypatch
):
    from app.models.ontology_meta import OntologyRelease
    from tests.test_api.test_ontology_release import ONTO, _fully_mapped_class

    _fully_mapped_class(client, analyst_headers, "Drug")
    created = client.post(ONTO + "/releases", headers=analyst_headers, json={"title": "projection"})
    identity = created.json()["id"]
    assert (
        client.post(f"{ONTO}/releases/{identity}/submit", headers=analyst_headers).status_code
        == 200
    )

    def fail(*args):
        raise RuntimeError("projection unavailable")

    monkeypatch.setattr(fake_engine, "project_entities", fail)
    response = client.post(f"{ONTO}/releases/{identity}/publish", headers=analyst_headers)
    assert response.status_code == 200  # Existing release workflow is preserved.
    assert db.get(OntologyRelease, UUID(identity)).semantic_snapshot_ref is None
