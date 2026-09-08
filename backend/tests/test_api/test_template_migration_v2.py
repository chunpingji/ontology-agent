import json
from pathlib import Path
from uuid import UUID

from sqlalchemy import select

from app.models.extraction import AstTemplate
from app.models.reporting import TemplateMigrationPlan
from app.services.reporting.template_migration import BASELINE_ID


def test_legacy_template_migration_creates_a_separate_v2_draft(client, db, analyst_headers):
    original_schema = json.loads(
        (Path(__file__).parents[1] / "fixtures/risk_template_dea037a2.json").read_text()
    )
    original = AstTemplate(
        id=UUID(BASELINE_ID),
        name="风险评估文档",
        version="v18",
        schema_json=original_schema,
        schema_version=1,
        template_family_id=BASELINE_ID,
        revision_no=1,
        status="published",
    )
    db.add(original)
    db.commit()
    path = "/api/ast-templates/" + BASELINE_ID
    response = client.get(path, headers=analyst_headers)
    assert response.status_code == 200, response.text
    source = response.json()
    request = {"expected_hash": source["schema_hash"]}

    response = client.post(path + "/migration-plan", json=request, headers=analyst_headers)
    assert response.status_code == 200, response.text
    frozen = response.json()
    plan = frozen["payload"]
    assert plan["original_schema"] == original_schema
    assert plan["original_template_id"] == BASELINE_ID
    assert plan["baseline_mapping_applied"] is True
    assert plan["mapping_count"] == 32
    assert plan["business_reviewed"] is False

    repeated = client.post(path + "/migration-plan", json=request, headers=analyst_headers)
    assert repeated.status_code == 200, repeated.text
    assert repeated.json()["id"] == frozen["id"]
    assert repeated.json()["content_hash"] == frozen["content_hash"]
    assert repeated.json()["payload"] == plan
    assert len(list(db.scalars(select(TemplateMigrationPlan)))) == 1

    response = client.post(
        path + "/revisions",
        json={
            **request,
            "expected_revision": source["revision_no"],
            "schema": plan["target_schema"],
        },
        headers=analyst_headers,
    )
    assert response.status_code == 201, response.text
    draft = response.json()
    assert draft["id"] != BASELINE_ID
    assert draft["schema_version"] == 2
    assert draft["revision_no"] == 2
    assert draft["template_family_id"] == BASELINE_ID
    assert draft["status"] == "draft"
    saved = db.get(AstTemplate, UUID(draft["id"]))
    assert saved.schema_json["legacy"]["migration_plan_ref"] == frozen["id"]
    assert saved.schema_json["migration_issues"]
    db.refresh(original)
    assert original.schema_json == original_schema
    assert original.version == "v18"
    assert original.status == "published"
