"""GLiNER2.5 strict adapter contracts without weights or HTTP calls."""

import json
import re
import sys
from types import ModuleType, SimpleNamespace

import pytest
import torch

from app.services.extraction import gliner2_extractor as adapter
from app.services.extraction.gliner_extractor import GlinerExtractionError
from app.services.extraction.tool_validation.mentions import propose_mentions


class CharLevelSplitter:
    def __call__(self, text, lower=True):
        for match in re.finditer(r"[A-Za-z0-9@._\-+]+|\S", text):
            yield match[0].lower() if lower else match[0], match.start(), match.end()


class FakeProcessor:
    def __init__(self):
        self.word_splitter = CharLevelSplitter()
        self.tokenizer = SimpleNamespace(model_max_length=512)
        self.mutate = lambda record: record
        self.mutate_metadata = lambda batch: batch
        self.padded_records = []
        self.boundary_metadata_calls = []

    def change_mode(self, *, is_training):
        assert is_training is False

    def transform_and_format(self, text, schema):
        words = list(self.word_splitter(text))
        prefix = sum(len(key) + len(value) for key, value in schema["entities"].items()) + 3
        record = SimpleNamespace(
            text=text,
            schema=schema,
            input_ids=list(range(prefix + len(words))),
            text_tokens=[word[0] for word in words],
            start_token_idx=[word[1] for word in words],
            end_token_idx=[word[2] for word in words],
            text_word_first_positions=list(range(prefix, prefix + len(words))),
            mapped_indices=[("schema",)] * prefix + [("text",)] * len(words),
        )
        return self.mutate(record)

    def _pad_batch(self, records):
        self.padded_records = records
        return SimpleNamespace(original_texts=[record.text for record in records],
                               original_schemas=[record.schema for record in records])

    def _add_boundary_metadata(self, batch, architecture, **kwargs):
        self.boundary_metadata_calls.append((architecture, kwargs))
        batch.query_layouts = [SimpleNamespace(queries=tuple(
            SimpleNamespace(query_id=index, task_index=0, task_type="entities",
                            task_name="entities", role_index=index, role_name=label,
                            extractive=True)
            for index, label in enumerate(schema["entities"])
        )) for schema in batch.original_schemas]
        batch.targets = None
        return self.mutate_metadata(batch)


class FakeSchema:
    def entities(self, types):
        self.types = types
        return self

    def build(self):
        return {"entities": self.types}


class FakeBoundaryHead(torch.nn.Module):
    def __init__(self, settings):
        super().__init__()
        self.settings = settings
        self.shared_pool_builder = SimpleNamespace(
            pool_boundary_top_k=32, pool_size=192, min_pool_per_query=8,
        )
        self.output = None

    def forward(self, text_states, text_mask, query_states, query_mask):
        if self.output is not None:
            return self.output
        b, q = query_mask.shape
        return SimpleNamespace(
            candidates=SimpleNamespace(
                indices=torch.tensor([0, 1]).expand(b, q, 1, 2),
                pair_logits=torch.zeros(b, q, 1), valid_mask=torch.ones(b, q, 1).bool(),
                query_mask=query_mask,
            ),
            null_logits=torch.zeros(b, q), count_log_rates=torch.zeros(b, q),
        )


class FakeModel:
    def __init__(self):
        self.processor = FakeProcessor()
        self.architecture = "boundary"
        self.config = SimpleNamespace(architecture="boundary")
        self.boundary_settings = SimpleNamespace(
            candidate_pool="shared", pool_boundary_top_k=32, pool_size=192, min_pool_per_query=8,
            enable_abstention=True, adaptive_threshold=False,
        )
        self.boundary_head = FakeBoundaryHead(self.boundary_settings)
        self.encoder = SimpleNamespace(config=SimpleNamespace(max_position_embeddings=512))
        self.calls = []
        self.results = None
        self.error = None

    def set_word_splitter(self, name):
        assert name == "char"
        self.processor.word_splitter = CharLevelSplitter()

    def eval(self):
        return self

    def create_schema(self):
        return FakeSchema()

    def batch_extract_entities(self, texts, types, **kwargs):
        self.calls.append((texts, types, kwargs))
        if self.error:
            raise self.error
        # Match the official public runtime's collator selection. A non-None
        # max_len bypasses the instance hook, reviving its source mutation.
        if kwargs.get("max_len") is not None:
            raise AssertionError("public runtime bypassed source-preserving collator")
        batch = self._inference_collator([(text, {"entities": types}) for text in texts])
        assert batch.original_texts == texts
        lengths = [len(record.text_tokens) for record in self.processor.padded_records]
        width = max(lengths)
        text_mask = torch.arange(width)[None, :] < torch.tensor(lengths)[:, None]
        self.boundary_head(torch.zeros(len(texts), width, 1), text_mask,
                           torch.zeros(len(texts), len(types), 1),
                           torch.ones(len(texts), len(types)).bool())
        if self.results is not None:
            return self.results
        return [{"entities": {
            label: ([{"text": "HRS-1597", "start": text.index("HRS-1597"),
                      "end": text.index("HRS-1597") + 8, "confidence": 0.9}]
                    if "HRS-1597" in text else []) for label in types
        }} for text in texts]


@pytest.fixture
def prepared(tmp_path, monkeypatch):
    checkpoint = tmp_path / "model"
    (checkpoint / "encoder_config").mkdir(parents=True)
    for name in ("config.json", "encoder_config/config.json", "tokenizer_config.json",
                 "tokenizer.json"):
        config = {"architecture": "boundary"} if name == "config.json" else {}
        (checkpoint / name).write_text(json.dumps(config))
    (checkpoint / "model.safetensors").write_bytes(b"test placeholder; loader is mocked")
    model = FakeModel()
    loads = []

    class Loader:
        @staticmethod
        def from_pretrained(path, **kwargs):
            loads.append((path, kwargs))
            return model

    monkeypatch.setattr(adapter, "_offline_model_class", lambda: Loader)
    extractor = adapter.Gliner2Extractor(
        checkpoint, descriptions={"产品": "报告中药品名称", "温度": "温度数值及单位"},
        device="cuda:0",
    )
    return extractor, model, loads


def test_boundary_loader_records_active_shared_pool_without_fixed_width(prepared):
    extractor, _, loads = prepared
    limits = extractor.prepare_strict()
    assert loads == [(str(extractor.model_path), {
        "local_files_only": True, "map_location": "cuda:0", "word_splitter": "char",
        "use_flashdeberta": False, "quantize": False, "compile": False,
        "architecture": "boundary",
    })]
    assert limits["max_width"] is None and limits["max_len"] == 160
    assert limits["architecture"] == "boundary" and limits["backend"] == "gliner2.5"
    assert limits["candidate_pool"] == "shared"
    assert limits["shared_candidate_budget"] == {
        "pool_boundary_top_k": 32, "pool_size": 192, "min_pool_per_query": 8,
    }
    assert limits["checkpoint_default_splitter"] == "whitespace"
    assert limits["splitter_quality_verified"] is False
    assert limits["encoder_input_limit"] == 512
    assert limits["source_augmentation"] == "disabled_default_terminal_period"
    assert limits["preprocessing_error_policy"] == "raise"
    assert extractor.prepare_strict() == limits and len(loads) == 1


def test_description_mapping_confidence_coordinates_and_blank_alignment(prepared):
    extractor, model, _ = prepared
    texts = ["产品HRS-1597", "", "无匹配"]
    result = extractor.extract_batch_with_spans_strict(texts, ["产品"], threshold=0.7)
    assert result == [[{"start": 2, "end": 10, "text": "HRS-1597", "label": "产品", "score": 0.9}],
                      [], []]
    payload, types, kwargs = model.calls[0]
    assert payload == [texts[0], texts[2]]
    assert types == {"产品": "报告中药品名称"}
    assert kwargs == {"batch_size": 2, "threshold": 0.7, "format_results": True,
                      "include_confidence": True, "include_spans": True,
                      "max_len": None, "overlap_policy": "allow"}
    assert len(extractor.last_batch_lengths) == 3
    assert extractor.last_batch_lengths[1] == 0
    assert all(type(value) is int for value in extractor.last_batch_lengths)
    assert model.processor.boundary_metadata_calls == [("boundary", {
        "is_training": False, "build_targets": False,
        "on_capacity_exceeded": "raise", "ignore_missing_entities": False,
    })]


def test_boundary_span_longer_than_legacy_width_is_preserved(prepared):
    extractor, model, _ = prepared
    text = "工艺条件：在氮气保护下缓慢加入乙醇并搅拌至完全溶解"
    model.results = [{"entities": {"产品": [
        {"text": text[5:], "start": 5, "end": len(text), "confidence": 0.9},
    ]}}]
    result = extractor.extract_batch_with_spans_strict([text], ["产品"])
    assert result[0][0]["text"] == text[5:]
    assert result[0][0]["end"] - result[0][0]["start"] > 8


@pytest.mark.parametrize("fault", ["missing", "dropped_label", "wrong_label", "query_id", "gold"])
def test_boundary_metadata_cannot_omit_or_misroute_queries(prepared, fault):
    extractor, model, _ = prepared

    def break_layout(batch):
        if fault == "missing":
            batch.query_layouts = None
        elif fault == "dropped_label":
            batch.query_layouts[0].queries = batch.query_layouts[0].queries[:1]
        elif fault == "wrong_label":
            batch.query_layouts[0].queries[0].role_name = "其他标签"
        elif fault == "query_id":
            batch.query_layouts[0].queries[0].query_id = 2
        elif fault == "gold":
            batch.targets = object()
        return batch

    model.processor.mutate_metadata = break_layout
    with pytest.raises(GlinerExtractionError, match="boundary_query_layout_invalid"):
        extractor.extract_batch_with_spans_strict(["HRS-1597"], ["产品", "温度"])


def test_encoder_gate_stays_conservatively_512_when_model_declares_more(prepared):
    extractor, model, _ = prepared
    model.encoder.config.max_position_embeddings = 4096
    model.processor.tokenizer.model_max_length = 4096
    extractor.descriptions["产品"] = "超长描述" * 140
    assert extractor.prepare_strict()["encoder_input_limit"] == 512
    with pytest.raises(GlinerExtractionError, match="encoder_input_limit_exceeded"):
        extractor.extract_batch_with_spans_strict(["HRS-1597"], ["产品"])
    assert model.calls == []


def test_span_checkpoint_rejected_before_loading(prepared):
    extractor, _, loads = prepared
    (extractor.model_path / "config.json").write_text(json.dumps({"architecture": "span"}))
    with pytest.raises(GlinerExtractionError, match="model_architecture_mismatch"):
        extractor.prepare_strict()
    assert loads == []


@pytest.mark.parametrize("field", ["architecture", "config"])
def test_loaded_model_must_match_boundary_checkpoint(prepared, field):
    extractor, model, _ = prepared
    if field == "config":
        model.config.architecture = "span"
    else:
        model.architecture = "span"
    with pytest.raises(GlinerExtractionError, match="model_architecture_mismatch"):
        extractor.prepare_strict()


def test_inactive_per_query_pool_is_not_reported_as_shared_budget(prepared):
    extractor, model, _ = prepared
    model.boundary_settings.candidate_pool = "per_query"
    with pytest.raises(GlinerExtractionError, match="candidate_pool_unsupported"):
        extractor.prepare_strict()


@pytest.mark.parametrize("value", [0, True, None, 999])
def test_invalid_or_divergent_actual_candidate_budget_fails(prepared, value):
    extractor, model, _ = prepared
    model.boundary_head.shared_pool_builder.pool_size = value
    with pytest.raises(GlinerExtractionError, match="candidate_budget_invalid"):
        extractor.prepare_strict()


def _candidate_output():
    # Two sources/queries with both candidate and query padding. Padding is
    # deliberately nonfinite/out-of-range and must never be judged as a hit.
    query_mask = torch.tensor([[True, False], [True, True]])
    return SimpleNamespace(
        candidates=SimpleNamespace(
            indices=torch.tensor([[[[0, 2], [-9, 99]], [[-9, 99], [-9, 99]]],
                                  [[[0, 1], [-9, 99]], [[0, 1], [-9, 99]]]]),
            valid_mask=torch.tensor([[[True, False], [True, False]],
                                     [[True, False], [True, False]]]),
            pair_logits=torch.tensor([[[0., float("nan")], [float("nan"), float("nan")]],
                                      [[0., float("nan")], [0., float("nan")]]]),
            query_mask=query_mask,
        ),
        null_logits=torch.tensor([[0., float("nan")], [0., 0.]]),
        count_log_rates=torch.tensor([[0., float("nan")], [0., 0.]]),
    )


def _guarded_head(output, *, adaptive=False):
    head = FakeBoundaryHead(SimpleNamespace(enable_abstention=True, adaptive_threshold=adaptive))
    head.output = output
    head.register_forward_hook(adapter._validate_boundary_output)
    return head(torch.zeros(2, 2, 1), torch.tensor([[True, True], [True, False]]),
                torch.zeros(2, 2, 1), torch.tensor([[True, False], [True, True]]))


def test_valid_boundary_candidates_ignore_candidate_and_query_padding():
    output = _candidate_output()
    assert _guarded_head(output, adaptive=True) is output


@pytest.mark.parametrize("pair", [(-1, 1), (1, 1), (1, 0), (0, 2)])
def test_valid_boundary_candidate_uses_its_own_source_length(pair):
    output = _candidate_output()
    output.candidates.indices[1, 0, 0] = torch.tensor(pair)
    with pytest.raises(GlinerExtractionError, match="boundary_candidate_span_invalid"):
        _guarded_head(output)


@pytest.mark.parametrize("field,code", [
    ("pair_logits", "boundary_candidate_score_nonfinite"),
    ("null_logits", "boundary_null_score_nonfinite"),
    ("count_log_rates", "boundary_count_score_nonfinite"),
])
@pytest.mark.parametrize("bad", [float("nan"), float("inf"), -float("inf")])
def test_nonfinite_active_decision_scores_are_not_silently_filtered(field, code, bad):
    output = _candidate_output()
    if field == "pair_logits":
        output.candidates.pair_logits[0, 0, 0] = bad
    else:
        getattr(output, field)[0, 0] = bad
    with pytest.raises(GlinerExtractionError, match=code):
        _guarded_head(output, adaptive=True)


def test_unused_count_head_does_not_change_normal_threshold_behavior():
    output = _candidate_output()
    output.count_log_rates[:] = float("nan")
    assert _guarded_head(output) is output


@pytest.mark.parametrize("fault", ["batch", "query", "mask", "float_indices", "null_shape"])
def test_candidate_batch_query_and_score_shapes_are_checked(fault):
    output = _candidate_output()
    if fault == "batch":
        output.candidates.indices = output.candidates.indices[:1]
    elif fault == "query":
        output.candidates.indices = output.candidates.indices[:, :1]
    elif fault == "mask":
        output.candidates.query_mask[0, 1] = True
    elif fault == "float_indices":
        output.candidates.indices = output.candidates.indices.float()
    elif fault == "null_shape":
        output.null_logits = torch.zeros(1, 2)
    with pytest.raises(GlinerExtractionError, match="boundary_.*layout_invalid"):
        _guarded_head(output)


def test_installed_hook_prevents_empty_success_from_invalid_candidate(prepared):
    extractor, model, _ = prepared
    model.results = [{"entities": {}}]
    model.boundary_head.output = SimpleNamespace(
        candidates=SimpleNamespace(
            indices=torch.tensor([[[[0, 1]]]]), valid_mask=torch.tensor([[[True]]]),
            pair_logits=torch.tensor([[[float("nan")]]]), query_mask=torch.tensor([[True]]),
        ),
        null_logits=torch.zeros(1, 1), count_log_rates=torch.zeros(1, 1),
    )
    with pytest.raises(GlinerExtractionError, match="boundary_candidate_score_nonfinite"):
        extractor.extract_batch_with_spans_strict(["HRS-1597"], ["产品"])


def test_empty_output_is_a_success_only_after_real_inference(prepared):
    extractor, model, _ = prepared
    assert extractor.extract_batch_with_spans_strict(["没有命中"], ["产品"]) == [[]]
    assert len(model.calls) == 1
    model.error = RuntimeError("inference failure")
    with pytest.raises(GlinerExtractionError, match="inference_failed"):
        extractor.extract_batch_with_spans_strict(["没有命中"], ["产品"])


@pytest.mark.parametrize("entities", [{}, {"产品": []}, {"温度": []}])
def test_official_sparse_empty_labels_are_valid_after_inference(prepared, entities):
    extractor, model, _ = prepared
    model.results = [{"entities": entities}]
    assert extractor.extract_batch_with_spans_strict(["无命中"], ["产品", "温度"]) == [[]]
    assert len(model.calls) == 1


@pytest.mark.parametrize("text", ["项目名称：HRS-1597", "末尾中文。", "值 ", "字" * 160])
def test_actual_collator_preserves_source_without_synthetic_terminal_period(prepared, text):
    extractor, model, _ = prepared
    result = extractor.extract_batch_with_spans_strict([text], ["产品"])
    assert model.processor.padded_records[0].text == text
    assert model.processor.padded_records[0].end_token_idx[-1] <= len(text)
    assert model.strict_extraction is True
    assert all(span["text"] == text[span["start"]:span["end"]] for span in result[0])


def test_synthetic_terminal_period_prediction_is_rejected_not_clipped(prepared):
    extractor, model, _ = prepared
    model.results = [{"entities": {"产品": [
        {"text": "HRS-1597.", "start": 5, "end": 14, "confidence": 0.66},
    ]}}]
    with pytest.raises(GlinerExtractionError, match="span_invalid"):
        extractor.extract_batch_with_spans_strict(["项目名称：HRS-1597"], ["产品"])


def test_actual_collator_error_is_not_converted_to_fallback_empty_result(prepared):
    extractor, model, _ = prepared
    calls = 0

    def fail_during_batch(record):
        nonlocal calls
        calls += 1
        if calls > 1:
            raise RuntimeError("batch preprocessing failed")
        return record

    model.processor.mutate = fail_during_batch
    with pytest.raises(GlinerExtractionError, match="inference_failed"):
        extractor.extract_batch_with_spans_strict(["HRS-1597"], ["产品"])
    assert model.processor.padded_records == []


def test_padding_cannot_change_source_after_preflight(prepared, monkeypatch):
    extractor, model, _ = prepared
    monkeypatch.setattr(model.processor, "_pad_batch", lambda _: SimpleNamespace(
        original_texts=["HRS-1597."],
    ))
    with pytest.raises(GlinerExtractionError, match="source_preprocessing_alignment_invalid"):
        extractor.extract_batch_with_spans_strict(["HRS-1597"], ["产品"])


def test_long_descriptions_cannot_overflow_declared_encoder_budget(prepared):
    extractor, model, _ = prepared
    extractor.descriptions["产品"] = "超长描述" * 140
    with pytest.raises(GlinerExtractionError, match="encoder_input_limit_exceeded"):
        extractor.extract_batch_with_spans_strict(["HRS-1597"], ["产品"])
    assert extractor.last_batch_lengths[0] > 512
    assert model.calls == []


def test_word_limit_is_rejected_before_source_truncation(prepared):
    extractor, model, _ = prepared
    with pytest.raises(GlinerExtractionError, match="input_word_limit_exceeded"):
        extractor.extract_batch_with_spans_strict(["字" * 161], ["产品"])
    assert model.calls == []


@pytest.mark.parametrize("field,value", [
    ("start_token_idx", []), ("end_token_idx", []), ("text_tokens", []),
    ("text_word_first_positions", []), ("text_word_first_positions", [999]),
    ("mapped_indices", [("schema",)] * 100),
    ("text", "HRS-1597."),
])
def test_missing_source_encoding_is_explicit_failure(prepared, field, value):
    extractor, model, _ = prepared

    def changed(record):
        setattr(record, field, value)
        return record

    model.processor.mutate = changed
    with pytest.raises(GlinerExtractionError, match="source_preprocessing_alignment_invalid"):
        extractor.extract_batch_with_spans_strict(["HRS-1597"], ["产品"])
    assert model.calls == []


@pytest.mark.parametrize("changes", [
    {"start": True}, {"start": -1}, {"end": 99}, {"text": "HRS1597"},
    {"confidence": float("nan")}, {"confidence": True}, {"confidence": 1.1},
])
def test_invalid_span_cannot_be_repaired_by_search_or_score_coercion(prepared, changes):
    extractor, model, _ = prepared
    model.results = [{"entities": {"产品": [{"text": "HRS-1597", "start": 0, "end": 8,
                                            "confidence": 0.9, **changes}]}}]
    with pytest.raises(GlinerExtractionError, match="span_invalid"):
        extractor.extract_batch_with_spans_strict(["HRS-1597"], ["产品"])


@pytest.mark.parametrize("result,code", [
    ([], "batch_alignment_invalid"), ({}, "batch_alignment_invalid"),
    ([{}], "entities_invalid"), ([{"entities": {"额外类型": []}}], "entities_invalid"),
    ([{"entities": {"产品": "HRS-1597"}}], "entities_invalid"),
])
def test_bad_batch_or_label_alignment_is_not_an_empty_success(prepared, result, code):
    extractor, model, _ = prepared
    model.results = result
    with pytest.raises(GlinerExtractionError, match=code):
        extractor.extract_batch_with_spans_strict(["HRS-1597"], ["产品"])


def test_overlapping_valid_spans_remain_available_for_semantic_review(prepared):
    extractor, model, _ = prepared
    model.results = [{"entities": {"产品": [
        {"text": "HRS-1597", "start": 0, "end": 8, "confidence": 0.9},
        {"text": "HRS-1597粗品", "start": 0, "end": 10, "confidence": 0.8},
    ]}}]
    result = extractor.extract_batch_with_spans_strict(["HRS-1597粗品"], ["产品"])
    assert len(result[0]) == 2


def test_propose_mentions_uses_adapter_without_promoting_mentions_to_facts(prepared):
    extractor, _, _ = prepared
    result = propose_mentions({"u0": {"text": "产品HRS-1597"}},
                              groups={"entities": ["产品"]}, extractor=extractor)
    assert result["execution_status"] == "completed"
    assert result["spans"][0]["start"] == 2
    assert result["semantic_status"] == "not_checked"
    assert result["fact_eligible"] is False


def test_missing_description_fails_before_loading(prepared):
    extractor, _, loads = prepared
    with pytest.raises(GlinerExtractionError, match="label_description_missing"):
        extractor.extract_batch_with_spans_strict(["原文"], ["未配置标签"])
    assert loads == []


def test_missing_local_weights_never_resolve_a_repo_id(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(adapter, "_offline_model_class", lambda: calls.append("import"))
    extractor = adapter.Gliner2Extractor(tmp_path / "fastino" / "remote-repo",
                                         descriptions={"产品": "名称"})
    with pytest.raises(GlinerExtractionError, match="local_model_directory_missing"):
        extractor.prepare_strict()
    assert calls == []


def test_missing_checkpoint_component_fails_before_loader(prepared):
    extractor, _, loads = prepared
    (extractor.model_path / "tokenizer.json").unlink()
    with pytest.raises(GlinerExtractionError, match="local_model_files_missing"):
        extractor.prepare_strict()
    assert loads == []


def test_local_remote_code_configuration_is_not_loaded(prepared):
    extractor, _, loads = prepared
    (extractor.model_path / "encoder_config/config.json").write_text(
        json.dumps({"auto_map": {"AutoModel": "external.module"}}),
    )
    with pytest.raises(GlinerExtractionError, match="local_config_unsupported"):
        extractor.prepare_strict()
    assert loads == []


@pytest.mark.parametrize("env_active,cached_active,expected", [
    (False, False, "offline_mode_required"),
    (True, False, "offline_mode_not_active"),
    (True, True, None),
])
def test_offline_environment_must_match_import_time_library_state(
    monkeypatch, env_active, cached_active, expected,
):
    for key in ("HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE"):
        monkeypatch.setenv(key, "1" if env_active else "0")
    modules = {name: ModuleType(name) for name in (
        "gliner2", "transformers", "transformers.utils", "transformers.utils.hub",
        "huggingface_hub", "huggingface_hub.constants",
    )}
    modules["gliner2"].__version__ = "2.0.0"
    modules["gliner2"].AutoExtractor = FakeModel
    modules["transformers"].__version__ = "4.51.0"
    modules["huggingface_hub.constants"].HF_HUB_OFFLINE = cached_active
    modules["transformers.utils.hub"].is_offline_mode = lambda: cached_active
    for name, module in modules.items():
        monkeypatch.setitem(sys.modules, name, module)
    if expected:
        with pytest.raises(GlinerExtractionError, match=expected):
            adapter._offline_model_class()
    else:
        assert adapter._offline_model_class() is FakeModel
