"""013: parse-sample + suggest-slots endpoint contract tests.

- parse-sample: 确定性离线解析（角色门控 senior_analyst，但**不**受 llm 门控——
  LLM 关闭时也能预览结构）；
- suggest-slots: 三选一输入校验、flag/client 503、角色 403，以及 sample_content_json
  分支走服务端 tiptap→text（mock 本地 LLM，零云端调用），返回 {document_summary,
  coverage, sections} 三键契约（sections = Round-1 结构骨架逐字回传, Option A）。

Mock 约定与 test_slot_suggester 一致：在 OpenAI 客户端层 mock
``client.chat.completions.create``，跑真实的 suggest_slots → chat_with_schema。
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import docx
import pytest
from pydantic import ValidationError

from app.schemas.extraction import SuggestSlotsRequest


# ── canned LLM rounds + mock client ──────────────────────────────────────────

_R1_OK = {
    "document_summary": "GMP 风险评估报告",
    "sections": [
        {
            "title": "评估对象",
            "groups": [
                {
                    "title": "药品信息",
                    "candidates": [{"label": "药品名称", "evidence_span": "XX注射液"}],
                },
            ],
        },
    ],
}

# 016 收敛：round-2 只产出所选本体覆盖边。此端点走 sample_content_json（无
# doc_class_iri、无本体接地）→ coverage 必空，故内容仅需符合 schema 形状。
_R2_OK = {
    "coverage": [],
}


def _make_llm_client(round1: dict, round2: dict):
    """Mock OpenAI client: canned JSON for the two chat.completions.create calls."""
    client = MagicMock()
    responses = [round1, round2]
    n = {"i": 0}

    def _create(**kwargs):
        idx = min(n["i"], len(responses) - 1)
        n["i"] += 1
        resp = MagicMock()
        choice = MagicMock()
        choice.message.content = json.dumps(responses[idx], ensure_ascii=False)
        resp.choices = [choice]
        return resp

    client.chat.completions.create = _create
    return client


# 忠于结构的样例：标题 + 含证据片段的段落（tiptap→text 分支的最小输入）。
_SAMPLE_DOC = {
    "type": "doc",
    "content": [
        {
            "type": "heading",
            "attrs": {"level": 1},
            "content": [{"type": "text", "text": "评估对象"}],
        },
        {
            "type": "paragraph",
            "content": [{"type": "text", "text": "本品为XX注射液，剂型为注射剂。"}],
        },
    ],
}

_DOCX_MIME = (
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
)


def _docx_bytes(tmp_path) -> bytes:
    path = tmp_path / "sample.docx"
    d = docx.Document()
    d.add_heading("评估对象", level=1)
    d.add_paragraph("本品为XX注射液，剂型为注射剂。")
    d.save(str(path))
    return path.read_bytes()


def _enable_llm(monkeypatch, client):
    """Flag on + local LLM returns the given mock client (endpoint imports at call time)."""
    from app.config import settings

    monkeypatch.setattr(settings, "llm_suggest_slots_enabled", True)
    monkeypatch.setattr(
        "app.services.llm.local_client.get_local_llm", lambda: client
    )


# ── SuggestSlotsRequest.model_post_init — 3-way "exactly one" (pure schema) ──

class TestSuggestSlotsRequestValidation:
    def test_zero_sources_rejected(self):
        with pytest.raises(ValidationError):
            SuggestSlotsRequest()

    def test_two_sources_rejected(self):
        with pytest.raises(ValidationError):
            SuggestSlotsRequest(document_text="x", sample_content_json={"type": "doc"})

    def test_exactly_one_ok(self):
        assert SuggestSlotsRequest(sample_content_json=_SAMPLE_DOC).sample_content_json
        assert SuggestSlotsRequest(document_text="hi").document_text == "hi"
        jid = "00000000-0000-0000-0000-000000000001"
        assert str(SuggestSlotsRequest(job_id=jid).job_id) == jid


# ── POST /api/ast-templates/parse-sample (role-gated, NOT flag-gated) ────────

class TestParseSampleEndpoint:
    def test_happy_path_returns_content_and_text(self, client, analyst_headers, tmp_path):
        resp = client.post(
            "/api/ast-templates/parse-sample",
            headers=analyst_headers,
            files={"file": ("sample.docx", _docx_bytes(tmp_path), _DOCX_MIME)},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["content_json"]["type"] == "doc"
        assert "评估对象" in body["plain_text"]

    def test_non_docx_rejected(self, client, analyst_headers):
        resp = client.post(
            "/api/ast-templates/parse-sample",
            headers=analyst_headers,
            files={"file": ("notes.txt", b"hello", "text/plain")},
        )
        assert resp.status_code == 422

    def test_role_gated(self, client, operator_headers, tmp_path):
        # 发送合法 docx，确保 403 来自角色门控而非缺参/文件类型。
        resp = client.post(
            "/api/ast-templates/parse-sample",
            headers=operator_headers,
            files={"file": ("sample.docx", _docx_bytes(tmp_path), _DOCX_MIME)},
        )
        assert resp.status_code == 403


# ── POST /api/ast-templates/suggest-slots ────────────────────────────────────

class TestSuggestSlotsEndpoint:
    def test_sample_content_json_path_returns_coverage_contract(
        self, client, analyst_headers, monkeypatch
    ):
        # 端点走 sample_content_json→tiptap→text 分支，跑真实两轮 LLM，返回
        # {document_summary, coverage, sections}。未传 doc_class_iri（也无本体接地）→
        # coverage 必为空；sections 是 Round-1 骨架的端到端逐字透传（Option A）。
        _enable_llm(monkeypatch, _make_llm_client(_R1_OK, _R2_OK))
        resp = client.post(
            "/api/ast-templates/suggest-slots",
            headers=analyst_headers,
            json={"sample_content_json": _SAMPLE_DOC},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert set(body.keys()) == {"document_summary", "coverage", "sections"}
        assert body["document_summary"] == "GMP 风险评估报告"
        assert body["coverage"] == []
        # 骨架逐字透传（端点无 response_model，raw dict 直出）。
        assert body["sections"] == _R1_OK["sections"]

    def test_flag_off_returns_503(self, client, analyst_headers, monkeypatch):
        from app.config import settings

        monkeypatch.setattr(settings, "llm_suggest_slots_enabled", False)
        resp = client.post(
            "/api/ast-templates/suggest-slots",
            headers=analyst_headers,
            json={"document_text": "some text"},
        )
        assert resp.status_code == 503

    def test_no_local_client_returns_503(self, client, analyst_headers, monkeypatch):
        from app.config import settings

        monkeypatch.setattr(settings, "llm_suggest_slots_enabled", True)
        monkeypatch.setattr(
            "app.services.llm.local_client.get_local_llm", lambda: None
        )
        resp = client.post(
            "/api/ast-templates/suggest-slots",
            headers=analyst_headers,
            json={"document_text": "some text"},
        )
        assert resp.status_code == 503

    def test_role_gated_403(self, client, operator_headers):
        # 角色依赖先于 flag/client 检查触发——无需 mock LLM。
        resp = client.post(
            "/api/ast-templates/suggest-slots",
            headers=operator_headers,
            json={"document_text": "some text"},
        )
        assert resp.status_code == 403

    def test_two_sources_returns_422(self, client, analyst_headers):
        resp = client.post(
            "/api/ast-templates/suggest-slots",
            headers=analyst_headers,
            json={"document_text": "x", "sample_content_json": {"type": "doc"}},
        )
        assert resp.status_code == 422


# ── POST /api/ast-templates/coverage-doc-classes (bugfix: 仅启用已建模类型) ────

class TestCoverageDocClassesEndpoint:
    """S10: the probe returns exactly the input IRIs the ontology models coverage for.

    Under the conftest ``FakeOntologyEngine``, ``coverage_capable`` is True only
    for CMCReport — its ``get_relation_schema`` returns 3 hop-1 edges (equipment,
    storage condition, degradation pathway). This mirrors production, where
    CMCReport is the sole modeled document type. Per contracts/suggest-slots-api.md.
    """

    def test_returns_modeled_subset(self, client, analyst_headers):
        from app.services.extraction.relation_extractor import CMC_REPORT_IRI

        _DEV = "https://ontology.pharma-gmp.cn/slpra/drug-development/"
        payload = [
            _DEV + "StabilityReport",   # unmodeled — no object properties
            CMC_REPORT_IRI,             # modeled — the only capable type
            _DEV + "MethodValidationReport",
        ]
        resp = client.post(
            "/api/ast-templates/coverage-doc-classes",
            headers=analyst_headers,
            json={"doc_class_iris": payload},
        )
        assert resp.status_code == 200
        assert resp.json() == {"capable": [CMC_REPORT_IRI]}

    def test_empty_list_returns_empty(self, client, analyst_headers):
        resp = client.post(
            "/api/ast-templates/coverage-doc-classes",
            headers=analyst_headers,
            json={"doc_class_iris": []},
        )
        assert resp.status_code == 200
        assert resp.json() == {"capable": []}

    def test_role_gated_403(self, client, operator_headers):
        # 同 suggest-slots：senior_analyst 门控先于本体查询触发。
        resp = client.post(
            "/api/ast-templates/coverage-doc-classes",
            headers=operator_headers,
            json={"doc_class_iris": []},
        )
        assert resp.status_code == 403
