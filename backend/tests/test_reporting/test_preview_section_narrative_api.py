"""015+: POST /api/ast-templates/preview-section-narrative.

Design-time 行文 Prompt「预览」——用已关联真实文档的**真实抽取事实**（annotation 缓存）
走报告期叙述路径生成本节正文。门控与 generate-section-prompt / suggest-slots 一致：
_maintainer 角色 + llm_suggest_slots_enabled(503) + get_local_llm()(503)。

Mock 约定同 test_template_meta_and_section_prompt：在 OpenAI 客户端层 mock
``client.chat.completions.create``，跑真实的 chat_with_schema。
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

# a valid UUID for the (mocked) annotation-cache job
JOB = "11111111-1111-1111-1111-111111111111"
_ENDPOINT = "/api/ast-templates/preview-section-narrative"


# ── helpers ──────────────────────────────────────────────────────────────────


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


def _enable(monkeypatch, client_obj):
    from app.config import settings

    monkeypatch.setattr(settings, "llm_suggest_slots_enabled", True)
    monkeypatch.setattr(
        "app.services.llm.local_client.get_local_llm", lambda: client_obj
    )


def _schema(section_id: str = "s") -> dict:
    """A ReportTemplate-valid schema with one extraction slot under ``section_id``."""
    return {
        "template_id": "t",
        "sections": [
            {
                "section_id": section_id,
                "title": "评估对象",
                "prompt": "",  # overridden by the request's (unsaved) prompt
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


def _seed_template(db, *, schema: dict | None = None):
    from app.models.extraction import AstTemplate

    row = AstTemplate(
        name="PreviewTpl", version="v1", doc_no="TEST",
        schema_json=schema or _schema(), iri_pattern=None,
        status="draft", created_by="test",
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def _write_cache(monkeypatch, tmp_path, *, edges: list[dict]) -> None:
    """Point the endpoint's annotation-cache lookup at a temp file (real facts source)."""
    cache = tmp_path / f"{JOB}.annotated.json"
    cache.write_text(
        json.dumps({"relationships": edges}, ensure_ascii=False), encoding="utf-8"
    )
    monkeypatch.setattr(
        "app.api.ast_templates._annotation_cache_path", lambda _job_id: cache
    )


_DRUG_EDGE = {
    "subject_class_iri": "https://ontology.pharma-gmp.cn/slpra/drug-development/CMCReport",
    "predicate_iri": "https://ontology.pharma-gmp.cn/slpra/drug-development/describes",
    "object_class_iri": "https://ontology.pharma-gmp.cn/slpra/drug/DrugProduct",
    "object_text": "XX注射液",
    "subject_text": "",
    "object_data_properties": [{"label": "药品名称", "value": "XX注射液"}],
    "source_ref": "§ 产品信息",
}


def _body(template_id, *, section_id="s", prompt="以正式口吻描述 {{药品名称}}。") -> dict:
    return {
        "job_id": JOB,
        "template_id": str(template_id),
        "section_id": section_id,
        "prompt": prompt,
    }


# ── tests ────────────────────────────────────────────────────────────────────


class TestPreviewSectionNarrativeEndpoint:
    def test_happy_path_returns_narrative(
        self, client, db, analyst_headers, monkeypatch, tmp_path
    ):
        _enable(monkeypatch, _one_shot_client({"content": "本品为 XX 注射液，风险经确定性评估为可控。"}))
        _write_cache(monkeypatch, tmp_path, edges=[_DRUG_EDGE])
        row = _seed_template(db)

        resp = client.post(_ENDPOINT, headers=analyst_headers, json=_body(row.id))
        assert resp.status_code == 200
        assert "XX 注射液" in resp.json()["narrative"]

    def test_flag_off_returns_503(self, client, db, analyst_headers, monkeypatch):
        from app.config import settings

        monkeypatch.setattr(settings, "llm_suggest_slots_enabled", False)
        row = _seed_template(db)
        resp = client.post(_ENDPOINT, headers=analyst_headers, json=_body(row.id))
        assert resp.status_code == 503

    def test_no_local_client_returns_503(
        self, client, db, analyst_headers, monkeypatch
    ):
        from app.config import settings

        monkeypatch.setattr(settings, "llm_suggest_slots_enabled", True)
        monkeypatch.setattr("app.services.llm.local_client.get_local_llm", lambda: None)
        row = _seed_template(db)
        resp = client.post(_ENDPOINT, headers=analyst_headers, json=_body(row.id))
        assert resp.status_code == 503

    def test_empty_prompt_returns_400(
        self, client, db, analyst_headers, monkeypatch
    ):
        # LLM enabled, but a blank prompt is nothing to preview (frontend also greys out).
        _enable(monkeypatch, _one_shot_client({"content": "不应被调用"}))
        row = _seed_template(db)
        resp = client.post(
            _ENDPOINT, headers=analyst_headers, json=_body(row.id, prompt="   ")
        )
        assert resp.status_code == 400

    def test_missing_annotation_cache_returns_422(
        self, client, db, analyst_headers, monkeypatch, tmp_path
    ):
        _enable(monkeypatch, _one_shot_client({"content": "x"}))
        # cache path resolves to a non-existent file → document not annotated
        monkeypatch.setattr(
            "app.api.ast_templates._annotation_cache_path",
            lambda _job_id: tmp_path / "nope.annotated.json",
        )
        row = _seed_template(db)
        resp = client.post(_ENDPOINT, headers=analyst_headers, json=_body(row.id))
        assert resp.status_code == 422

    def test_unknown_template_returns_404(
        self, client, analyst_headers, monkeypatch, tmp_path
    ):
        _enable(monkeypatch, _one_shot_client({"content": "x"}))
        _write_cache(monkeypatch, tmp_path, edges=[_DRUG_EDGE])
        resp = client.post(
            _ENDPOINT,
            headers=analyst_headers,
            json=_body("00000000-0000-0000-0000-000000000123"),
        )
        assert resp.status_code == 404

    def test_unknown_section_returns_404(
        self, client, db, analyst_headers, monkeypatch, tmp_path
    ):
        _enable(monkeypatch, _one_shot_client({"content": "x"}))
        _write_cache(monkeypatch, tmp_path, edges=[_DRUG_EDGE])
        row = _seed_template(db)
        resp = client.post(
            _ENDPOINT,
            headers=analyst_headers,
            json=_body(row.id, section_id="does-not-exist"),
        )
        assert resp.status_code == 404

    def test_role_gated_403(self, client, operator_headers):
        # Body is fully valid; only the role gate should fire.
        resp = client.post(
            _ENDPOINT,
            headers=operator_headers,
            json=_body("00000000-0000-0000-0000-000000000123"),
        )
        assert resp.status_code == 403

    def test_empty_llm_output_returns_502(
        self, client, db, analyst_headers, monkeypatch, tmp_path
    ):
        # LLM reachable but yields no content → preview generation failure.
        _enable(monkeypatch, _one_shot_client({"content": "   "}))
        _write_cache(monkeypatch, tmp_path, edges=[_DRUG_EDGE])
        row = _seed_template(db)
        resp = client.post(_ENDPOINT, headers=analyst_headers, json=_body(row.id))
        assert resp.status_code == 502
