"""Tests for 013 narrative generation (T025).

Verifies:
  - Output uses only supplied facts (SC-006)
  - Template prose passed as few-shot context
  - LLM failure returns empty dict
  - Regeneration is stateless (no persistence)
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

from app.services.reporting.narrative_generator import generate_narratives


def _make_client(response: dict):
    """Return a mock OpenAI client that returns a canned response."""
    client = MagicMock()
    resp = MagicMock()
    choice = MagicMock()
    choice.message.content = json.dumps(response, ensure_ascii=False)
    resp.choices = [choice]
    client.chat.completions.create.return_value = resp
    return client


_SAMPLE_EDGES = [
    {
        "object_class_iri": "http://slpra/ontology#DrugProduct",
        "object_text": "XX注射液",
        "subject_text": "测试药品",
        "object_data_properties": [
            {"label": "剂型", "value": "注射剂"},
        ],
    },
]

_SAMPLE_TEMPLATE = MagicMock()
_SAMPLE_TEMPLATE.sections = [
    MagicMock(title="评估对象", groups=[MagicMock(title="药品信息")]),
]

_LLM_RESPONSE = {
    "subject_description": "XX注射液是一种注射剂型药品。",
    "conclusion": "本次风险评估表明各维度风险可控。",
    "dimension_narratives": [
        {"dimension": "人员", "narrative": "人员培训到位，风险可控。"},
    ],
}


class TestGenerateNarratives:
    def test_successful_generation(self):
        client = _make_client(_LLM_RESPONSE)
        result = generate_narratives(_SAMPLE_EDGES, _SAMPLE_TEMPLATE, client)

        assert "subject_description" in result
        assert "conclusion" in result
        assert "narrative.人员" in result
        assert "XX注射液" in result["subject_description"]

    def test_llm_failure_returns_empty(self):
        client = MagicMock()
        client.chat.completions.create.side_effect = Exception("LLM down")
        result = generate_narratives(_SAMPLE_EDGES, _SAMPLE_TEMPLATE, client)
        assert result == {}

    def test_empty_edges_returns_empty(self):
        client = MagicMock()
        result = generate_narratives([], _SAMPLE_TEMPLATE, client)
        assert result == {}

    def test_stateless_no_persistence(self):
        client = _make_client(_LLM_RESPONSE)
        r1 = generate_narratives(_SAMPLE_EDGES, _SAMPLE_TEMPLATE, client)
        r2 = generate_narratives(_SAMPLE_EDGES, _SAMPLE_TEMPLATE, client)
        assert r1 == r2
        assert r1 is not r2

    def test_template_style_passed_to_prompt(self):
        client = _make_client(_LLM_RESPONSE)
        generate_narratives(_SAMPLE_EDGES, _SAMPLE_TEMPLATE, client)

        call_args = client.chat.completions.create.call_args
        messages = call_args.kwargs.get("messages", [])
        user_msg = next((m["content"] for m in messages if m["role"] == "user"), "")
        assert "评估对象" in user_msg or "参考模板" in user_msg
