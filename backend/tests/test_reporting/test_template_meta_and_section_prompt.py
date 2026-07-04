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
        name=name, version=version, doc_no="TEST",
        schema_json=load_default_template().model_dump(),
        iri_pattern=iri_pattern, status=status, created_by="test",
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def _one_shot_client(payload: dict):
    """Mock OpenAI client returning a single canned JSON object for every call."""
    client = MagicMock()

    def _create(**kwargs):
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


class TestGenerateSectionPromptEndpoint:
    def _enable(self, monkeypatch, client_obj):
        from app.config import settings

        monkeypatch.setattr(settings, "llm_suggest_slots_enabled", True)
        monkeypatch.setattr(
            "app.services.llm.local_client.get_local_llm", lambda: client_obj
        )

    def test_happy_path_returns_prompt(self, client, analyst_headers, monkeypatch):
        self._enable(monkeypatch, _one_shot_client({"prompt": "描述 {{药品名称}}。"}))
        resp = client.post(
            "/api/ast-templates/generate-section-prompt",
            headers=analyst_headers,
            json={"section_title": "评估对象", "slot_labels": ["药品名称"], "sample_text": "本品为XX注射液"},
        )
        assert resp.status_code == 200
        assert resp.json()["prompt"] == "描述 {{药品名称}}。"

    def test_flag_off_returns_503(self, client, analyst_headers, monkeypatch):
        from app.config import settings

        monkeypatch.setattr(settings, "llm_suggest_slots_enabled", False)
        resp = client.post(
            "/api/ast-templates/generate-section-prompt",
            headers=analyst_headers,
            json={"section_title": "评估对象"},
        )
        assert resp.status_code == 503

    def test_no_local_client_returns_503(self, client, analyst_headers, monkeypatch):
        from app.config import settings

        monkeypatch.setattr(settings, "llm_suggest_slots_enabled", True)
        monkeypatch.setattr(
            "app.services.llm.local_client.get_local_llm", lambda: None
        )
        resp = client.post(
            "/api/ast-templates/generate-section-prompt",
            headers=analyst_headers,
            json={"section_title": "评估对象"},
        )
        assert resp.status_code == 503

    def test_empty_prompt_returns_502(self, client, analyst_headers, monkeypatch):
        # LLM reachable but returns an empty prompt → treated as generation failure.
        self._enable(monkeypatch, _one_shot_client({"prompt": ""}))
        resp = client.post(
            "/api/ast-templates/generate-section-prompt",
            headers=analyst_headers,
            json={"section_title": "评估对象"},
        )
        assert resp.status_code == 502

    def test_role_gated_403(self, client, operator_headers):
        resp = client.post(
            "/api/ast-templates/generate-section-prompt",
            headers=operator_headers,
            json={"section_title": "评估对象"},
        )
        assert resp.status_code == 403


# ── narrative_generator.generate_section_narratives (report-time) ────────────


class TestGenerateSectionNarratives:
    def test_only_sections_with_prompt_produce_prose(self):
        from app.services.reporting.ast_template import ReportTemplate
        from app.services.reporting.narrative_generator import (
            generate_section_narratives,
        )

        template = ReportTemplate.model_validate(_schema_with_section_prompt(
            "描述 {{药品名称}}。"
        ))
        # second section, no prompt → skipped
        template.sections.append(template.sections[0].model_copy(
            update={"section_id": "s2", "title": "无行文", "prompt": None}
        ))

        edges = [{
            "object_class_iri": "http://slpra.org/DrugProduct",
            "object_text": "XX注射液",
            "subject_text": "",
            "object_data_properties": [{"label": "药品名称", "value": "XX注射液"}],
        }]
        client_obj = _one_shot_client({"content": "本品为 XX 注射液，属注射剂。"})

        out = generate_section_narratives(edges, template, client_obj)
        assert len(out) == 1
        assert out[0]["section_id"] == "s"
        assert out[0]["title"] == "评估对象"
        assert "XX 注射液" in out[0]["text"]

    def test_no_prompt_anywhere_returns_empty(self):
        from app.services.reporting.narrative_generator import (
            generate_section_narratives,
        )

        template = load_default_template()  # ships without per-section prompts
        edges = [{"object_class_iri": "X", "object_text": "y", "subject_text": ""}]
        client_obj = _one_shot_client({"content": "should not be used"})
        assert generate_section_narratives(edges, template, client_obj) == []


# ── extraction._narratives_payload (persisted reading-pane blob) ─────────────


class TestNarrativesPayload:
    def test_none_when_no_llm_content(self):
        from app.api.extraction import _narratives_payload
        from app.services.reporting.risk_report_generator import RiskReport

        # deterministic subject only (not in llm_generated_fields) → null column
        report = RiskReport(subject_description="确定性描述")
        assert _narratives_payload(report) is None

    def test_captures_llm_subject_conclusion_and_sections(self):
        from app.api.extraction import _narratives_payload
        from app.services.reporting.risk_report_generator import RiskReport

        report = RiskReport(
            subject_description="AI 描述",
            conclusion="AI 结论",
            llm_generated_fields={"subject_description", "conclusion"},
            section_narratives=[{"section_id": "s", "title": "评估对象", "text": "正文"}],
        )
        payload = _narratives_payload(report)
        assert payload is not None
        assert payload["subject_description"] == "AI 描述"
        assert payload["conclusion"] == "AI 结论"
        assert payload["sections"][0]["title"] == "评估对象"

    def test_deterministic_subject_excluded_but_sections_kept(self):
        from app.api.extraction import _narratives_payload
        from app.services.reporting.risk_report_generator import RiskReport

        report = RiskReport(
            subject_description="确定性描述",  # NOT llm-generated
            section_narratives=[{"section_id": "s", "title": "T", "text": "正文"}],
        )
        payload = _narratives_payload(report)
        assert payload is not None
        assert payload["subject_description"] is None
        assert payload["conclusion"] is None
        assert len(payload["sections"]) == 1
