from copy import deepcopy

from app.models.extraction import AstTemplate
from app.models.reporting import ReportRun
from app.services.extraction.candidate_store import CandidateStore
from app.services.fact_commit import FactCommitService
from app.services.ontology_instance_writer import EvidenceInstanceWriter
from app.services.reporting.contract_registry import ContractRegistry
from app.services.reporting.report_run_service import ReportRunService
from tests.test_extraction.test_fact_commit import evidence_job, prepare, real_world  # noqa: F401
from tests.test_reporting.test_output_contracts import template


def test_published_facts_coverage_and_report_keep_original_snapshot_after_conflict(
    client,
    db,
    request,
    analyst_headers,
    tmp_path,
    monkeypatch,
):
    job, world = request.getfixturevalue("evidence_job"), request.getfixturevalue("real_world")
    monkeypatch.setattr(
        "app.services.reporting.report_run_service.ARTIFACT_ROOT", tmp_path / "artifacts"
    )
    candidates = prepare(db, job)
    facts = FactCommitService(db, EvidenceInstanceWriter(world, tmp_path / "world"))
    commit = facts.request(
        job.id,
        "first",
        [{"candidate_id": c.candidate_id, "revision": c.revision} for c in candidates],
        "analyst",
    )
    assert facts.apply(commit.id).status == "succeeded"
    registry = ContractRegistry(db)
    schema = {
        "urn:test:Drug": {
            "parents": [],
            "properties": [{"iri": "urn:test:strength", "datatype": "decimal"}],
            "relationships": [{"iri": "urn:test:uses", "range": ["urn:test:Equipment"]}],
        },
        "urn:test:Equipment": {"parents": [], "properties": [], "relationships": []},
    }

    def register(kind, definition):
        row = registry.create(
            kind=kind,
            family_id=kind,
            revision_no=1,
            definition=definition,
            actor="analyst",
            server_ontology=kind == "ontology",
        )
        for decision in ("reviewed", "published"):
            registry.decide(
                row.id,
                decision,
                actor="analyst",
                reason="Synthetic integration fixture",
                expected_hash=row.content_hash,
            )
        return row.id

    value = template()
    value.update(
        ontology_release_ref=register("ontology", {"classes": schema}),
        style_profile_ref=register("style", {}),
        publication_policy_ref=register("policy", {"require_review": True}),
        source_slots=[{"source_slot_id": "doc", "kind": "document", "class_iri": "urn:test:Drug"}],
    )
    value["definitions"] = {
        "bindings": {
            "drug": {
                "binding_id": "drug",
                "kind": "facts",
                "contract_ref": {
                    "kind": "ontology",
                    "release_ref": value["ontology_release_ref"],
                    "root_class_iri": "urn:test:Drug",
                    "result_class_iri": "urn:test:Drug",
                },
                "scope": {
                    "source_slot": "doc",
                    "predicate_path": [],
                    "cardinality": {"min_count": 1, "max_count": 1},
                },
            }
        },
        "inputs": {
            "strength": {
                "input_id": "strength",
                "name": "strength",
                "label": "含量",
                "required": True,
                "binding_ref": "drug",
                "projection": {"kind": "property", "property_iri": "urn:test:strength"},
            }
        },
    }
    value["sections"][0]["groups"][0]["units"] = [
        {
            "output_id": "strength",
            "title": "含量",
            "bindings": [{"binding_ref": "drug"}],
            "inputs": [{"input_ref": "strength", "alias": "strength"}],
            "render": {
                "kind": "form",
                "fields": [
                    {"field_id": "strength", "label": "含量", "value": {"input_id": "strength"}}
                ],
            },
        }
    ]
    row = AstTemplate(
        name="Synthetic exact source",
        version="v2",
        schema_version=2,
        schema_json=value,
        status="published",
    )
    db.add(row)
    db.commit()
    job.source_config = {
        **(job.source_config or {}),
        "mode": "template_default",
        "template_id": str(row.id),
    }
    db.commit()
    endpoint = f"/api/extraction/jobs/{job.id}"
    coverage = client.get(endpoint + "/evidence/coverage", headers=analyst_headers)
    assert coverage.status_code == 200, coverage.text
    original = coverage.json()
    assert original["material_status"] == "ready", original["blocking_issues"]
    assert original["availability"] == "available"
    assert original["required_gaps"] == 0
    assert original["completion"] == "complete"
    assert original["diagnostics"] == []
    assert original["tasks"] and all(task["label"] == "含量" for task in original["tasks"])
    assert db.get(ReportRun, original["run_id"]).actor == "analyst"
    legacy = client.get(endpoint + "/ast-coverage", headers=analyst_headers)
    assert legacy.status_code == 200, legacy.text
    assert legacy.json()["input_snapshot_id"] == original["input_snapshot_id"]
    report = client.post(endpoint + "/risk-report", headers=analyst_headers)
    assert report.status_code == 202, report.text
    assert report.json()["material_status"] == "ready"
    assert "0.1234567890123456789" in str(report.json()["body_ast"])
    saved = deepcopy(
        ReportRunService(db).response(ReportRunService(db).get(report.json()["run_id"]))
    )
    updated = next(c for c in candidates if c.kind == "property").model_copy(deep=True)
    updated.candidate_id = "different-strength"
    updated.literal.raw_value = updated.literal.normalized_value = "9.5"
    store = CandidateStore(db)
    stored = store.persist_validated(job.id, [updated], actor="extractor")[0]
    store.review(
        stored.candidate_id, stored.revision, "confirmed", "Synthetic conflicting record", "analyst"
    )
    second = facts.request(
        job.id,
        "second",
        [{"candidate_id": stored.candidate_id, "revision": stored.revision}],
        "analyst",
    )
    assert facts.apply(second.id).status == "succeeded"
    changed = client.get(endpoint + "/evidence/coverage", headers=analyst_headers)
    assert changed.status_code == 200, changed.text
    assert changed.json()["material_status"] == "conflict"
    assert changed.json()["required_gaps"] > 0
    assert changed.json()["completion"] == "incomplete"
    assert changed.json()["diagnostics"]
    assert changed.json()["input_snapshot_id"] != original["input_snapshot_id"]
    unchanged = client.get("/api/report-runs/" + saved["run_id"], headers=analyst_headers).json()
    assert unchanged["body_ast"] == saved["body_ast"]
    assert unchanged["material_status"] == "ready"
