from copy import deepcopy
from uuid import uuid4

from app.models.extraction import AstTemplate
from tests.test_reporting.test_output_contracts import template
from tests.test_reporting.test_report_signing import reporting_run  # noqa: F401


def test_registered_contracts_records_compile_publish_and_frozen_run(
    client,
    db,
    analyst_headers,
    monkeypatch,
    tmp_path,
):
    from sqlalchemy import select

    from app.auth import hash_password
    from app.models.ontology_meta import AppRole, AppUser
    from tests.test_reporting.test_output_contracts import ontology

    monkeypatch.setattr("app.services.reporting.report_run_service.ARTIFACT_ROOT", tmp_path)
    monkeypatch.setattr(
        "app.services.extraction.extraction_tasks.semantic_schema_from_engine",
        lambda engine: ontology(),
    )
    qa_role = db.scalar(select(AppRole).where(AppRole.name == "qa"))
    db.add(AppUser(username="qa02", role_id=qa_role.id, password_hash=hash_password("fixture")))
    db.commit()
    qa = {"X-User": "qa02", "X-Role": "qa"}

    def post(path, payload, headers=analyst_headers, expected=201):
        response = client.post("/api/" + path, json=payload, headers=headers)
        assert response.status_code == expected, response.text
        return response.json()

    def publish_contract(row, *, review=True):
        path = "report-contracts/" + row["id"] + "/decisions"
        for decision in ["reviewed", "published"] if review else ["published"]:
            post(
                path,
                {
                    "expected_hash": row["content_hash"],
                    "decision": decision,
                    "reason": "synthetic integration contract",
                },
            )
        return row["id"]

    ontology_ref = publish_contract(post("report-contracts/ontology-snapshots", {}))

    def register(kind, definition):
        return publish_contract(
            post(
                "report-contracts",
                {"kind": kind, "family_id": kind, "revision_no": 1, "definition": definition},
            )
        )

    style_ref = register("style", {"font": "Arial"})
    policy_ref = register("policy", {"require_review": True})
    workflow_ref = register(
        "workflow",
        {
            "description": "Synthetic reviewed roster",
            "applicable_scope": {},
            "output_type": {
                "kind": "list",
                "item_identity": "entity_id",
                "item_type": {
                    "kind": "record",
                    "fields": {
                        "code": {
                            "type": {"kind": "string"},
                            "semantic_ref": "urn:code",
                            "required": True,
                        },
                    },
                },
            },
        },
    )
    record = post(
        "report-contracts/" + workflow_ref + "/records",
        {
            "record_key": "roster",
            "revision_no": 1,
            "subject_id": "urn:subject",
            "applicable_at": None,
            "values": [{"entity_id": "person-1", "code": "confirmed-code"}],
        },
    )
    value = template()
    value.update(
        source_slots=[],
        ontology_release_ref=ontology_ref,
        style_profile_ref=style_ref,
        publication_policy_ref=policy_ref,
    )
    value["definitions"]["bindings"]["equipment"] = {
        "kind": "workflow",
        "binding_id": "equipment",
        "contract_ref": workflow_ref,
        "scope": {"record_slot": "roster", "subject_ref": "urn:subject"},
    }
    value["definitions"]["inputs"]["rows"]["projection"] = {"kind": "identity"}
    row = post("ast-templates", {"name": "Integrated V2", "version": "v1", "schema_json": value})
    template_id, expected_hash = row["id"], row["schema_hash"]
    plan = post(
        "ast-templates/" + template_id + "/compile", {"expected_hash": expected_hash}, expected=200
    )
    assert plan["valid"], plan["diagnostics"]
    post(
        "ast-templates/" + template_id + "/publish",
        {
            "expected_hash": expected_hash,
            "compilation_id": plan["compilation_id"],
        },
        expected=200,
    )
    request = {
        "template_id": template_id,
        "idempotency_key": "pending-record",
        "record_refs": {"roster": record["id"]},
    }
    pending = post("report-runs", request)
    assert pending["material_status"] == "incomplete"
    post(
        "report-records/" + record["id"] + "/reviews",
        {
            "expected_hash": record["content_hash"],
            "decision": "approved",
            "reason": "fixture checked",
        },
        headers=qa,
    )
    ready = post("report-runs", {**request, "idempotency_key": "approved-record"})
    assert ready["material_status"] == "ready"
    assert "confirmed-code" in str(ready["body_ast"])
    assert ready["input_snapshot_id"] != pending["input_snapshot_id"]
    assert post("report-runs", request)["run_id"] == pending["run_id"]
    assert (
        client.get("/api/report-runs/" + pending["run_id"], headers=analyst_headers).json()[
            "material_status"
        ]
        == "incomplete"
    )
    draft = client.get("/api/ast-templates/" + template_id, headers=analyst_headers).json()[
        "schema_json"
    ]
    draft["sections"][0]["title"] = "unsaved revision"
    preview = post(
        "report-previews",
        {**request, "draft_schema": draft, "mode": "report", "idempotency_key": "unsaved"},
        expected=200,
    )
    assert preview["template_status"] == "draft"


def test_report_reads_and_download_require_auth(client, analyst_headers, request):
    fixture_run = request.getfixturevalue("reporting_run")
    path = "/api/report-runs/" + fixture_run.id
    assert client.get(path).status_code == 403
    response = client.get(path, headers=analyst_headers)
    assert response.status_code == 200, response.text
    result = response.json()
    inputs = client.get(path + "/inputs", headers=analyst_headers).json()
    coverage = client.get(path + "/coverage", headers=analyst_headers).json()
    assert (
        result["input_snapshot_id"] == inputs["input_snapshot_id"] == coverage["input_snapshot_id"]
    )
    artifact = result["artifacts"][0]["artifact_id"]
    assert client.get(path + "/artifacts/" + artifact).status_code == 403
    download = client.get(path + "/artifacts/" + artifact, headers=analyst_headers)
    assert download.status_code == 200
    assert download.headers["etag"].strip('"') == result["artifacts"][0]["file_hash"]


def test_layout_preview_has_no_business_values(client, analyst_headers):
    response = client.post(
        "/api/report-previews",
        headers=analyst_headers,
        json={
            "mode": "layout",
            "draft_schema": template(),
            "idempotency_key": "layout",
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["business_values"] is False
    assert "E-01" not in response.text


def test_client_cannot_register_ontology_or_review_flags(client, analyst_headers):
    for kind, definition in [
        ("ontology", {"classes": {}}),
        ("rule", {"claims_reviewed": True, "review_ref": "forged"}),
    ]:
        response = client.post(
            "/api/report-contracts",
            headers=analyst_headers,
            json={
                "kind": kind,
                "family_id": kind,
                "revision_no": 1,
                "definition": definition,
            },
        )
        assert response.status_code == 422, response.text


def test_v2_cannot_use_legacy_mutation_or_publish(db, client, analyst_headers):
    value = template()
    row = AstTemplate(
        id=uuid4(), name="V2", version="v2", schema_json=value, schema_version=2, status="draft"
    )
    db.add(row)
    db.commit()
    path = "/api/ast-templates/" + str(row.id)
    assert client.put(path, headers=analyst_headers, json={"schema_json": value}).status_code == 409
    assert (
        client.patch(path, headers=analyst_headers, json={"status": "published"}).status_code == 409
    )


def test_template_detail_does_not_create_source_job(db, client, analyst_headers, tmp_path):
    from app.models.extraction import ExtractionJob

    source = tmp_path / "source.docx"
    source.write_bytes(b"synthetic-file")
    row = AstTemplate(
        id=uuid4(),
        name="old",
        version="v1",
        schema_json={"template_id": "old", "sections": []},
        default_source_path=str(source),
    )
    db.add(row)
    db.commit()
    count = db.query(ExtractionJob).count()
    original = deepcopy(row.schema_json)
    response = client.get("/api/ast-templates/" + str(row.id), headers=analyst_headers)
    assert response.status_code == 200, response.text
    assert db.query(ExtractionJob).count() == count
    assert db.get(AstTemplate, row.id).default_source_job_id is None
    assert db.get(AstTemplate, row.id).schema_json == original


def test_new_rule_revision_freezes_legacy_definition_without_mutating_it(
    db, client, analyst_headers
):
    from app.models.ontology_meta import OntologyDecisionRule
    from app.models.reporting import OntologyDecisionRuleRevision
    from app.services.reporting.contract_registry import ContractRegistry
    from tests.test_reporting.test_output_rules import rule_fixture

    legacy = OntologyDecisionRule(
        rule_key="old",
        slpra_iri="urn:old",
        label="Historical",
        rule_group="risk_assessment",
        status="published",
        version=7,
        antecedent={},
        consequent={"text": "Original"},
    )
    db.add(legacy)
    db.commit()
    _, data = rule_fixture()
    definition = data["contracts"]["rule"]["definition"]
    definition.pop("claims_reviewed")
    definition.pop("review_ref")
    definition.update(description="Synthetic migration", applicable_scope={}, legacy_ref="old")
    definition["parameters"]["verified"]["semantic_ref"] = "urn:verified"
    result = client.post(
        "/api/report-contracts",
        headers=analyst_headers,
        json={
            "kind": "rule",
            "family_id": "rule-family",
            "revision_no": 1,
            "definition": definition,
        },
    )
    assert result.status_code == 201, result.text
    revision = result.json()["id"]
    assert db.get(OntologyDecisionRuleRevision, revision).rule_id == legacy.id
    frozen = ContractRegistry(db).load(revision, published=False)["definition"]["legacy_snapshot"]
    assert frozen["version"] == 7 and frozen["consequent"] == {"text": "Original"}
    db.refresh(legacy)
    assert legacy.version == 7 and legacy.consequent == {"text": "Original"}


def test_compiled_template_and_historical_published_sample_cannot_be_deleted_or_replaced(
    db, client, analyst_headers, request
):
    run = request.getfixturevalue("reporting_run")
    row = db.get(AstTemplate, run.template_id)
    response = client.delete("/api/ast-templates/" + str(row.id), headers=analyst_headers)
    assert response.status_code == 409, response.text
    assert db.get(AstTemplate, row.id) is not None
    # Existing published V1 artifacts receive the same preservation guard.
    legacy = AstTemplate(
        name="legacy", version="v1", schema_json={"sections": []}, status="published"
    )
    db.add(legacy)
    db.commit()
    endpoint = "/api/ast-templates/" + str(legacy.id)
    assert client.delete(endpoint, headers=analyst_headers).status_code == 409
