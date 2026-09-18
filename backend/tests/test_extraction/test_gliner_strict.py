"""Strict tools must distinguish missing/incomplete inference from no entities."""

from types import SimpleNamespace

import pytest

from app.services.extraction.gliner_extractor import (
    GlinerExtractionError,
    GlinerExtractor,
    _CJKAwareWordsSplitter,
)


def extractor_with(predict, *, max_len=384):
    extractor = GlinerExtractor()
    extractor._model = SimpleNamespace(
        batch_predict_entities=predict,
        config=SimpleNamespace(max_len=max_len, max_width=12),
        data_processor=SimpleNamespace(
            words_splitter=SimpleNamespace(splitter=_CJKAwareWordsSplitter())
        ),
    )
    return extractor


def test_strict_load_failure_is_distinct_from_legacy_empty(monkeypatch):
    extractor = GlinerExtractor()
    monkeypatch.setattr(extractor, "_ensure_model", lambda: None)
    assert extractor.extract_batch_with_spans(["药品"], ["实体"]) == [[]]
    with pytest.raises(GlinerExtractionError, match="model_unavailable") as error:
        extractor.extract_text_with_spans_strict("药品", ["实体"])
    assert error.value.stage == "load"


def test_strict_inference_failure_is_not_a_successful_empty_result():
    def failed(*args, **kwargs):
        raise RuntimeError("simulated failure")

    extractor = extractor_with(failed)
    with pytest.raises(GlinerExtractionError, match="inference_failed") as error:
        extractor.extract_batch_with_spans_strict(["药品"], ["实体"])
    assert error.value.stage == "inference"


def test_strict_rejects_short_batch_instead_of_silent_zip_loss():
    extractor = extractor_with(lambda *args, **kwargs: [[]])
    with pytest.raises(GlinerExtractionError, match="batch_alignment_invalid"):
        extractor.extract_batch_with_spans_strict(["大鼠", "犬"], ["实体"])


@pytest.mark.parametrize("change", [
    {"start": -1}, {"end": 50}, {"text": "错误"}, {"label": "越权"},
    {"score": float("nan")}, {"start": True},
])
def test_strict_rejects_invalid_span_before_overlap_filter(change):
    span = {"start": 0, "end": 2, "text": "药品", "label": "实体", "score": 0.9} | change
    extractor = extractor_with(lambda *args, **kwargs: [[span]])
    with pytest.raises(GlinerExtractionError, match="span_invalid"):
        extractor.extract_text_with_spans_strict("药品", ["实体"])


def test_strict_preserves_empty_slot_alignment_and_successful_no_hits():
    def predict(texts, labels, **kwargs):
        assert texts == ["药品", "未知"]
        return [[{"start": 0, "end": 2, "text": "药品", "label": "实体", "score": 0.9}], []]

    result = extractor_with(predict).extract_batch_with_spans_strict(
        ["药品", "", "未知"], ["实体"]
    )
    assert result[0][0]["text"] == "药品"
    assert result[1:] == [[], []]


def test_strict_preserves_original_ner_score_without_changing_legacy_rounding():
    span = {"start": 0, "end": 2, "text": "药品", "label": "实体", "score": 0.9123456}
    extractor = extractor_with(lambda *args, **kwargs: [[span]])
    assert extractor.extract_text_with_spans_strict("药品", ["实体"])[0]["score"] == 0.9123456
    assert extractor.extract_batch_with_spans(["药品"], ["实体"])[0][0]["score"] == 0.9123


def test_strict_rejects_word_truncation_without_running_model():
    def predict(*args, **kwargs):
        pytest.fail("Truncated text must not reach inference")

    extractor = extractor_with(predict, max_len=2)
    with pytest.raises(GlinerExtractionError, match="input_word_limit_exceeded"):
        extractor.extract_text_with_spans_strict("长试验名称", ["实体"])


def test_strict_requires_the_cjk_splitter_that_supports_source_offsets():
    extractor = extractor_with(lambda *args, **kwargs: [[]])
    extractor._model.data_processor.words_splitter.splitter = object()
    with pytest.raises(GlinerExtractionError, match="cjk_splitter_unavailable"):
        extractor.prepare_strict()
