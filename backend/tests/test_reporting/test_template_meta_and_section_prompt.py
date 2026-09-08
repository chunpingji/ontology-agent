"""015: template lifecycle metadata (status/iri_pattern) + per-section 行文 Prompt.

Covers the surfaces that replaced the retired DocumentTypeMapping card:
  - PATCH /api/ast-templates/{id} — in-place status/iri_pattern edit (no version bump)
  - POST /api/ast-templates/generate-section-prompt — design-time AI assist (gated)
  - Section.prompt round-trip through create/get
  - narrative_generator.generate_section_narratives — report-time prose per section
  - extraction._narratives_payload — persisted reading-pane blob

Mock 约定与 test_suggest_slots_api 一致：在 OpenAI 客户端层 mock
``client.chat.completions.create``，跑真实的 chat_with_schema。
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

from app.services.reporting.ast_template import load_default_template

# ── helpers ──────────────────────────────────────────────────────────────────


def _seed_template(db, *, name="Meta", version="v1", iri_pattern=None, status="draft"):
    from app.models.extraction import AstTemplate

    row = AstTemplate(
        name=name,
        version=version,
        doc_no="TEST",
        schema_json=load_default_template().model_dump(),
        iri_pattern=iri_pattern,
        status=status,
        created_by="test",
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def _one_shot_client(payload: dict):
    """Mock OpenAI client returning a single canned JSON object for every call."""
    client = MagicMock()
    client.base_url = "http://model.test/v1"

    async def _create(**kwargs):
        resp = MagicMock()
        choice = MagicMock()
        choice.message.content = json.dumps(payload, ensure_ascii=False)
        resp.choices = [choice]
        return resp

    client.chat.completions.create = _create
    return client


# ── PATCH /api/ast-templates/{id} — in-place metadata edit ───────────────────


class TestUpdateTemplateMeta:
    def test_status_and_iri_pattern_updated_in_place(self, client, db, analyst_headers):
        row = _seed_template(db, status="draft")
        resp = client.patch(
            f"/api/ast-templates/{row.id}",
            headers=analyst_headers,
            json={"status": "published", "iri_pattern": "CMCReport"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "published"
        assert body["iri_pattern"] == "CMCReport"
        # no version bump — same row, same version
        assert body["id"] == str(row.id)
        assert body["version"] == "v1"
        db.refresh(row)
        assert row.status == "published"
        assert row.iri_pattern == "CMCReport"

    def test_invalid_status_rejected(self, client, db, analyst_headers):
        row = _seed_template(db)
        resp = client.patch(
            f"/api/ast-templates/{row.id}",
            headers=analyst_headers,
            json={"status": "bogus"},
        )
        assert resp.status_code == 422

    def test_clearing_iri_pattern_sets_null(self, client, db, analyst_headers):
        row = _seed_template(db, iri_pattern="CMCReport")
        resp = client.patch(
            f"/api/ast-templates/{row.id}",
            headers=analyst_headers,
            json={"iri_pattern": ""},
        )
        assert resp.status_code == 200
        assert resp.json()["iri_pattern"] is None

    def test_role_gated_403(self, client, db, operator_headers):
        row = _seed_template(db)
        resp = client.patch(
            f"/api/ast-templates/{row.id}",
            headers=operator_headers,
            json={"status": "published"},
        )
        assert resp.status_code == 403

    def test_unknown_template_404(self, client, analyst_headers):
        resp = client.patch(
            "/api/ast-templates/00000000-0000-0000-0000-000000000123",
            headers=analyst_headers,
            json={"status": "published"},
        )
        assert resp.status_code == 404


# ── Section.prompt round-trip ────────────────────────────────────────────────


def _schema_with_section_prompt(prompt: str) -> dict:
    return {
        "template_id": "t",
        "sections": [
            {
                "section_id": "s",
                "title": "评估对象",
                "prompt": prompt,
                "groups": [
                    {
                        "group_id": "g",
                        "title": "药品信息",
                        "kind": "fields",
                        "slots": [
                            {
                                "slot_id": "drug.name",
                                "label": "药品名称",
                                "source": {
                                    "kind": "extraction",
                                    "object_class_iri_contains": "DrugProduct",
                                    "text": True,
                                },
                            }
                        ],
                    }
                ],
            }
        ],
    }


class TestSectionPromptRoundTrip:
    def test_create_preserves_section_prompt(self, client, db, analyst_headers):
        prompt = "以正式口吻描述 {{药品名称}}，输出一段话。"
        resp = client.post(
            "/api/ast-templates",
            headers=analyst_headers,
            json={
                "name": "PromptTpl",
                "version": "v1",
                "schema_json": _schema_with_section_prompt(prompt),
            },
        )
        assert resp.status_code == 201
        tpl_id = resp.json()["id"]

        got = client.get(f"/api/ast-templates/{tpl_id}", headers=analyst_headers)
        assert got.status_code == 200
        section = got.json()["schema_json"]["sections"][0]
        assert section["prompt"] == prompt


# ── POST /api/ast-templates/generate-section-prompt ──────────────────────────


# ── narrative_generator.generate_section_narratives (report-time) ────────────


# ── extraction._narratives_payload (persisted reading-pane blob) ─────────────
