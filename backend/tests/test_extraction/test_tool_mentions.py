"""Source and execution boundaries of the recall-only NER tool, without weights."""

import pytest

from app.services.extraction.gliner_extractor import GlinerExtractionError
from app.services.extraction.tool_validation.mentions import propose_mentions


class StubExtractor:
    def __init__(self, *, needle="HRS-1597", failure=None):
        self.needle = needle
        self.failure = failure
        self.calls = []

    def prepare_strict(self):
        if self.failure == "load":
            raise GlinerExtractionError("model_unavailable", stage="load")
        return {"max_len": 384, "max_width": 12, "encoder_truncation": "unknown"}

    def extract_batch_with_spans_strict(self, texts, labels, threshold=None):
        self.calls.append((texts, labels, threshold))
        if self.failure == "inference":
            raise GlinerExtractionError("inference_failed", stage="inference")
        result = []
        for text in texts:
            start = text.find(self.needle)
            result.append([] if start < 0 else [{
                "start": start, "end": start + len(self.needle), "text": self.needle,
                "label": labels[0], "score": 0.9,
            }])
        return result


def test_windows_recover_boundary_mentions_and_remap_exact_source_offsets():
    # First occurrence straddles the first window end; second one is in the tail.
    text = "甲" * 156 + "HRS-1597" + "乙" * 175 + "HRS-1597"
    extractor = StubExtractor()
    sources = {"r1": {"text": text}, "r2": {"text": "HRS-1597"}}
    result = propose_mentions(sources, groups={"entities": ["产品"]}, extractor=extractor)
    assert result["execution_status"] == "completed"
    assert result["coverage"]["covered_refs"] == ["r1", "r2"]
    assert {(span["ref"], span["start"]) for span in result["spans"]} == {
        ("r1", 156), ("r1", 339), ("r2", 0),
    }
    assert all(
        sources[span["ref"]]["text"][span["start"]:span["end"]] == span["text"]
        for span in result["spans"]
    )
    assert all(len(text) <= 160 for texts, _, _ in extractor.calls for text in texts)
    assert result["limits"]["source_truncated"] is False
    assert result["limits"]["encoder_truncation"] == "unknown"
    assert all(chunk["truncation"]["word_limit_exceeded"] is False for chunk in result["chunks"])


def test_overlap_deduplicates_same_span_but_groups_remain_independent():
    result = propose_mentions(
        {"r": {"text": "甲" * 140 + "HRS-1597" + "乙" * 40}},
        groups={"entities": ["产品"], "values": ["产品名称"]}, extractor=StubExtractor(),
    )
    assert len(result["spans"]) == 2
    assert len(result["groups"]["entities"]) == 1
    assert len(result["groups"]["values"]) == 1
    assert len({span["id"] for span in result["spans"]}) == 2
    assert result["fact_eligible"] is False
    assert result["semantic_status"] == "not_checked"


def test_technical_failure_is_not_an_empty_success():
    result = propose_mentions(
        {"r": {"text": "HRS-1597"}}, groups={"entities": ["产品"]},
        extractor=StubExtractor(failure="inference"),
    )
    assert result["execution_status"] == "failed"
    assert result["coverage"]["attempted_refs"] == ["r"]
    assert result["coverage"]["covered_refs"] == []
    assert result["coverage"]["failed_refs"] == ["r"]
    assert result["issues"][0]["code"] == "inference_failed"
    assert result["spans"] == []
    success = propose_mentions(
        {"r": {"text": "没有命中"}}, groups={"entities": ["产品"]}, extractor=StubExtractor()
    )
    assert success["execution_status"] == "completed"
    assert success["coverage"]["covered_refs"] == ["r"]
    assert success["spans"] == []


def test_load_failure_leaves_sources_unattempted():
    result = propose_mentions(
        {"r": {"text": "HRS-1597"}}, groups={"entities": ["产品"]},
        extractor=StubExtractor(failure="load"),
    )
    assert result["execution_status"] == "unavailable"
    assert result["coverage"]["unattempted_refs"] == ["r"]
    assert result["coverage"]["attempted_refs"] == []
    assert result["timing"]["inference_seconds"] == 0


def test_unavailable_source_is_visible_without_hiding_completed_sources():
    result = propose_mentions(
        {"bad": {}, "empty": {"text": "  "}, "good": {"text": "HRS-1597"}},
        groups={"entities": ["产品"]}, extractor=StubExtractor(),
    )
    assert result["execution_status"] == "partial"
    assert result["coverage"]["unattempted_refs"] == ["bad", "empty"]
    assert result["coverage"]["covered_refs"] == ["good"]


@pytest.mark.parametrize("change", [
    {"text": "invented"}, {"end": 500}, {"label": "unrequested"}, {"score": float("nan")},
])
def test_adapter_independently_rejects_bad_coordinates_and_unrequested_labels(change):
    extractor = StubExtractor()
    extractor.extract_batch_with_spans_strict = lambda *args, **kwargs: [[{
        "start": 0, "end": 8, "text": "HRS-1597", "label": "产品", "score": 0.8,
    } | change]]
    result = propose_mentions(
        {"r": {"text": "HRS-1597"}}, groups={"entities": ["产品"]}, extractor=extractor,
    )
    assert result["execution_status"] == "failed"
    assert result["spans"] == []
    assert result["rejected_spans"][0]["code"] == "span_invalid"
    assert result["coverage"]["covered_refs"] == []


def test_label_batches_cover_all_requested_labels_and_do_not_mix_groups():
    extractor = StubExtractor()
    labels = [f"属性{i}" for i in range(19)]
    result = propose_mentions(
        {"r": {"text": "HRS-1597"}}, groups={"values": labels, "units": ["单位"]},
        extractor=extractor,
    )
    assert result["execution_status"] == "completed"
    assert [label for _, batch, _ in extractor.calls for label in batch] == [*labels, "单位"]
    assert all(len(batch) <= 8 for _, batch, _ in extractor.calls)


def test_described_label_batch_budget_covers_all_labels_and_records_encoding_length():
    extractor = StubExtractor()
    extractor.last_batch_lengths = [140]
    labels = ["产品名称", "试验名称", "每日允许暴露量数值", "包装描述", "设备编号"]
    result = propose_mentions(
        {"r": {"text": "HRS-1597"}}, groups={"values": labels},
        extractor=extractor, labels_per_batch=2,
    )
    assert result["execution_status"] == "completed"
    assert [label for _, batch, _ in extractor.calls for label in batch] == labels
    assert [len(batch) for _, batch, _ in extractor.calls] == [2, 2, 1]
    assert result["limits"]["labels_per_batch"] == 2
    assert all(chunk["encoder_input_tokens"] == 140 for chunk in result["chunks"])


@pytest.mark.parametrize("budget", [0, 9, True, 2.5, None])
def test_invalid_label_batch_budget_does_not_invoke_model(budget):
    extractor = StubExtractor()
    result = propose_mentions(
        {"r": {"text": "HRS-1597"}}, groups={"entities": ["产品名称"]},
        extractor=extractor, labels_per_batch=budget,
    )
    assert result["execution_status"] == "failed"
    assert extractor.calls == []
