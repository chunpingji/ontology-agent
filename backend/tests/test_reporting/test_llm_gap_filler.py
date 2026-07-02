"""012 T029: Tests for LLM gap filling service."""

from __future__ import annotations

import json
from dataclasses import dataclass
from unittest.mock import MagicMock, patch

import pytest

from app.services.extraction.llm_gap_filler import (
    _build_response_format,
    _parse_llm_response,
    fill_coverage_gaps,
)


@dataclass
class _FakeSlot:
    slot_id: str
    label: str
    status: str = "missing_required"


@dataclass
class _FakeManifest:
    missing_required: int
    missing_required_slots: list

    @property
    def slots(self):
        return self.missing_required_slots


class TestParseLLMResponse:
    def test_valid_json_array(self):
        raw = '[{"slot_id": "subject.name", "value": "阿莫西林", "source_span": "产品：阿莫西林"}]'
        missing = [_FakeSlot("subject.name", "产品名称")]
        result = _parse_llm_response(raw, missing)
        assert len(result) == 1
        assert result[0]["slot_id"] == "subject.name"
        assert result[0]["value"] == "阿莫西林"
        assert result[0]["source_span"] == "产品：阿莫西林"
        assert result[0]["source"] == "llm"

    def test_json_in_markdown_code_fence(self):
        raw = '```json\n[{"slot_id": "subject.name", "value": "X", "source_span": "Y"}]\n```'
        missing = [_FakeSlot("subject.name", "名称")]
        result = _parse_llm_response(raw, missing)
        assert len(result) == 1
        assert result[0]["value"] == "X"

    def test_thinking_tags_stripped(self):
        raw = '<think>Let me think...</think>\n[{"slot_id": "subject.name", "value": "Z", "source_span": null}]'
        missing = [_FakeSlot("subject.name", "名称")]
        result = _parse_llm_response(raw, missing)
        assert len(result) == 1
        assert result[0]["value"] == "Z"
        assert result[0]["source_span"] is None

    def test_invalid_json_returns_empty(self):
        result = _parse_llm_response("this is not json", [])
        assert result == []

    def test_null_value_skipped(self):
        raw = '[{"slot_id": "subject.name", "value": null, "source_span": null}]'
        missing = [_FakeSlot("subject.name", "名称")]
        result = _parse_llm_response(raw, missing)
        assert result == []

    def test_unknown_slot_id_skipped(self):
        raw = '[{"slot_id": "unknown.field", "value": "X", "source_span": "Y"}]'
        missing = [_FakeSlot("subject.name", "名称")]
        result = _parse_llm_response(raw, missing)
        assert result == []

    def test_empty_array(self):
        result = _parse_llm_response("[]", [_FakeSlot("a", "A")])
        assert result == []

    def test_multiple_slots(self):
        raw = json.dumps([
            {"slot_id": "a", "value": "V1", "source_span": "S1"},
            {"slot_id": "b", "value": "V2", "source_span": "S2"},
        ])
        missing = [_FakeSlot("a", "A"), _FakeSlot("b", "B")]
        result = _parse_llm_response(raw, missing)
        assert len(result) == 2

    def test_json_schema_wrapper(self):
        """json_schema mode wraps the array in {"slots": [...]}."""
        raw = json.dumps({
            "slots": [
                {"slot_id": "subject.name", "value": "阿莫西林", "source_span": "产品：阿莫西林"},
            ]
        })
        missing = [_FakeSlot("subject.name", "产品名称")]
        result = _parse_llm_response(raw, missing)
        assert len(result) == 1
        assert result[0]["slot_id"] == "subject.name"
        assert result[0]["value"] == "阿莫西林"

    def test_json_schema_wrapper_empty_slots(self):
        raw = json.dumps({"slots": []})
        missing = [_FakeSlot("a", "A")]
        result = _parse_llm_response(raw, missing)
        assert result == []


class TestBuildResponseFormat:
    def test_schema_structure(self):
        fmt = _build_response_format()
        assert fmt["type"] == "json_schema"
        schema = fmt["json_schema"]["schema"]
        assert schema["type"] == "object"
        assert "slots" in schema["properties"]
        items_props = schema["properties"]["slots"]["items"]["properties"]
        assert set(items_props.keys()) == {"slot_id", "value", "source_span"}


def _patch_settings(**overrides):
    """Patch app.config.settings attributes for gap filler tests."""
    defaults = {
        "local_llm_enabled": False,
        "local_llm_base_url": "http://localhost:11434/v1",
        "local_llm_model": "test",
        "local_llm_api_key": "dummy",
        "local_llm_max_tokens": 1024,
        "local_llm_temperature": 0.1,
    }
    defaults.update(overrides)
    return patch("app.config.settings", **{k: v for k, v in defaults.items()})


class TestFillCoverageGaps:
    def test_disabled_returns_empty(self):
        manifest = _FakeManifest(missing_required=3, missing_required_slots=[
            _FakeSlot("a", "A"),
        ])
        with _patch_settings(local_llm_enabled=False):
            result = fill_coverage_gaps(manifest, None, MagicMock())
        assert result == []

    def test_no_missing_returns_empty(self):
        manifest = _FakeManifest(missing_required=0, missing_required_slots=[])
        with _patch_settings(local_llm_enabled=True):
            result = fill_coverage_gaps(manifest, None, MagicMock())
        assert result == []

    def test_llm_client_none_returns_empty(self):
        manifest = _FakeManifest(missing_required=1, missing_required_slots=[
            _FakeSlot("a", "A"),
        ])
        with _patch_settings(local_llm_enabled=True), \
             patch("app.services.llm.local_client.get_local_llm", return_value=None):
            result = fill_coverage_gaps(manifest, None, MagicMock())
        assert result == []

    def test_successful_extraction(self):
        manifest = _FakeManifest(missing_required=1, missing_required_slots=[
            _FakeSlot("subject.name", "产品名称"),
        ])
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = json.dumps([
            {"slot_id": "subject.name", "value": "阿莫西林胶囊", "source_span": "产品名称：阿莫西林胶囊"},
        ])
        mock_client.chat.completions.create.return_value = mock_response

        with _patch_settings(local_llm_enabled=True), \
             patch("app.services.llm.local_client.get_local_llm", return_value=mock_client), \
             patch("app.services.extraction.llm_gap_filler._build_document_text", return_value="产品名称：阿莫西林胶囊"):
            result = fill_coverage_gaps(manifest, "/fake/path.docx", MagicMock())

        assert len(result) == 1
        assert result[0]["slot_id"] == "subject.name"
        assert result[0]["value"] == "阿莫西林胶囊"
        assert result[0]["source"] == "llm"

    def test_llm_exception_returns_empty(self):
        manifest = _FakeManifest(missing_required=1, missing_required_slots=[
            _FakeSlot("a", "A"),
        ])
        mock_client = MagicMock()
        mock_client.chat.completions.create.side_effect = Exception("connection error")

        with _patch_settings(local_llm_enabled=True), \
             patch("app.services.llm.local_client.get_local_llm", return_value=mock_client), \
             patch("app.services.extraction.llm_gap_filler._build_document_text", return_value="some text"):
            result = fill_coverage_gaps(manifest, "/fake.docx", MagicMock())

        assert result == []

    def test_llm_invalid_json_returns_empty(self):
        manifest = _FakeManifest(missing_required=1, missing_required_slots=[
            _FakeSlot("a", "A"),
        ])
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "I cannot extract anything"
        mock_client.chat.completions.create.return_value = mock_response

        with _patch_settings(local_llm_enabled=True), \
             patch("app.services.llm.local_client.get_local_llm", return_value=mock_client), \
             patch("app.services.extraction.llm_gap_filler._build_document_text", return_value="text"):
            result = fill_coverage_gaps(manifest, "/f.docx", MagicMock())

        assert result == []

    def test_json_schema_fallback_on_unsupported(self):
        """If server rejects json_schema, falls back to prompt-only."""
        manifest = _FakeManifest(missing_required=1, missing_required_slots=[
            _FakeSlot("subject.name", "产品名称"),
        ])
        mock_client = MagicMock()
        call_count = 0

        def side_effect(**kwargs):
            nonlocal call_count
            call_count += 1
            if "response_format" in kwargs:
                raise Exception("Unsupported response_format")
            resp = MagicMock()
            resp.choices = [MagicMock()]
            resp.choices[0].message.content = json.dumps([
                {"slot_id": "subject.name", "value": "X", "source_span": "Y"},
            ])
            return resp

        mock_client.chat.completions.create.side_effect = side_effect

        with _patch_settings(local_llm_enabled=True), \
             patch("app.services.llm.local_client.get_local_llm", return_value=mock_client), \
             patch("app.services.extraction.llm_gap_filler._build_document_text", return_value="text"):
            result = fill_coverage_gaps(manifest, "/f.docx", MagicMock())

        assert call_count == 2
        assert len(result) == 1
        assert result[0]["value"] == "X"

    def test_response_format_passed_to_client(self):
        """Verify response_format is included in the LLM call."""
        manifest = _FakeManifest(missing_required=1, missing_required_slots=[
            _FakeSlot("subject.name", "产品名称"),
        ])
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = json.dumps(
            {"slots": [{"slot_id": "subject.name", "value": "V", "source_span": "S"}]}
        )
        mock_client.chat.completions.create.return_value = mock_response

        with _patch_settings(local_llm_enabled=True), \
             patch("app.services.llm.local_client.get_local_llm", return_value=mock_client), \
             patch("app.services.extraction.llm_gap_filler._build_document_text", return_value="text"):
            result = fill_coverage_gaps(manifest, "/f.docx", MagicMock())

        call_kwargs = mock_client.chat.completions.create.call_args.kwargs
        assert "response_format" in call_kwargs
        assert call_kwargs["response_format"]["type"] == "json_schema"
        assert len(result) == 1
