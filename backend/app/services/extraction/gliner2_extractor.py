"""Strict local GLiNER2.5 boundary adapter using the gliner2 2.0.0 package.

Start the process with HF_HUB_OFFLINE=1 and TRANSFORMERS_OFFLINE=1 before
importing the ML stack. Missing artifacts, truncated inputs and malformed
predictions raise explicit errors; they never become successful empty results.
This adapter is not registered as the application's default NER backend.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
from copy import deepcopy
from pathlib import Path, PurePosixPath

from app.services.extraction.gliner_extractor import GlinerExtractionError, _valid_source_span

REQUIRED_MODEL_FILES = frozenset({
    "config.json", "encoder_config/config.json", "tokenizer_config.json",
    "tokenizer.json", "model.safetensors",
})


def verify_local_checkpoint(model_path: str | Path, manifest: dict) -> dict:
    model_path = Path(model_path)
    if not isinstance(manifest, dict):
        raise ValueError("gliner2_model_manifest_invalid")
    if not all(isinstance(manifest.get(key), str) and manifest[key].strip()
               for key in ("repo", "revision")):
        raise ValueError("gliner2_model_manifest_identity_missing")
    entries = manifest.get("files")
    if not isinstance(entries, list) or not entries:
        raise ValueError("gliner2_model_manifest_files_empty")
    paths, resolved_paths = set(), set()
    for entry in entries:
        if (not isinstance(entry, dict) or not isinstance(entry.get("path"), str)
                or not entry["path"] or type(entry.get("bytes")) is not int
                or entry["bytes"] <= 0 or not isinstance(entry.get("sha256"), str)
                or re.fullmatch(r"[0-9a-f]{64}", entry["sha256"]) is None):
            raise ValueError("gliner2_model_manifest_entry_invalid")
        name = entry["path"]
        relative = PurePosixPath(name)
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("model_manifest_path_outside_directory")
        if relative.as_posix() != name or "\\" in name:
            raise ValueError("gliner2_model_manifest_path_not_canonical")
        path = model_path / name
        resolved = path.resolve()
        if not resolved.is_relative_to(model_path.resolve()):
            raise ValueError("model_manifest_path_outside_directory")
        if name in paths or resolved in resolved_paths:
            raise ValueError("gliner2_model_manifest_duplicate_path:" + name)
        paths.add(name)
        resolved_paths.add(resolved)
    missing = REQUIRED_MODEL_FILES - paths
    if missing:
        raise ValueError("gliner2_model_required_files_missing:" + ",".join(sorted(missing)))
    for entry in entries:
        path = model_path / entry["path"]
        if not path.is_file():
            raise ValueError("gliner2_model_file_missing:" + entry["path"])
        # The fixed F32 checkpoint is over 1 GB; hash without loading it all into RAM.
        with path.open("rb") as stream:
            actual_digest = hashlib.file_digest(stream, "sha256").hexdigest()
        if path.stat().st_size != entry["bytes"] or actual_digest != entry["sha256"]:
            raise ValueError("gliner2_model_digest_mismatch:" + entry["path"])
    config = json.loads((model_path / "config.json").read_text())
    if not isinstance(config, dict) or config.get("architecture") != "boundary":
        raise ValueError("gliner2_model_architecture_mismatch")
    return manifest



def _offline_model_class():
    # The 2.0.0 checkpoint loader forwards local_files_only to Hub resolution,
    # but its tokenizer loader does not. Require the actual process-wide flags
    # as well as local paths; setting environment variables after imports is
    # insufficient because both libraries cache offline state.
    if any(os.environ.get(key) != "1" for key in ("HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE")):
        raise GlinerExtractionError("offline_mode_required", stage="load")
    try:
        import gliner2
        import transformers
        from huggingface_hub.constants import HF_HUB_OFFLINE
        from transformers.utils.hub import is_offline_mode

        if not HF_HUB_OFFLINE or not is_offline_mode():
            raise GlinerExtractionError("offline_mode_not_active", stage="load")
        if gliner2.__version__ != "2.0.0" or not transformers.__version__.startswith("4."):
            raise GlinerExtractionError("gliner2_dependency_version_mismatch", stage="load")
        return gliner2.AutoExtractor
    except GlinerExtractionError:
        raise
    except Exception as exc:
        raise GlinerExtractionError("gliner2_dependency_unavailable", stage="load") from exc


def _positive_limit(value):
    # Hugging Face uses extremely large integers to represent no declared limit.
    return value if type(value) is int and 0 < value < 10**9 else None


def _validate_boundary_output(module, inputs, output):
    """Fail before thresholding can hide invalid active candidates as no hits."""
    import torch

    try:
        text_states, text_mask, query_states, query_mask = inputs[:4]
        candidates = output.candidates
        indices, valid = candidates.indices, candidates.valid_mask
        logits, output_queries = candidates.pair_logits, candidates.query_mask
        tensors = (text_states, text_mask, query_states, query_mask,
                   indices, valid, logits, output_queries)
        if (any(not isinstance(value, torch.Tensor) for value in tensors)
                or text_states.ndim != 3 or query_states.ndim != 3
                or text_mask.dtype != torch.bool or query_mask.dtype != torch.bool
                or text_mask.shape != text_states.shape[:2]
                or query_mask.shape != query_states.shape[:2]
                or text_mask.shape[0] != query_mask.shape[0]
                or indices.ndim != 4 or indices.shape[-1] != 2
                or indices.dtype != torch.int64 or valid.dtype != torch.bool
                or output_queries.dtype != torch.bool or not logits.is_floating_point()
                or indices.shape[:2] != query_mask.shape
                or valid.shape != indices.shape[:-1] or logits.shape != valid.shape
                or not torch.equal(output_queries, query_mask)):
            raise GlinerExtractionError("boundary_candidate_layout_invalid", stage="output")
        # Shared pools can mark candidates valid for padded queries too; those
        # queries and invalid candidate padding never participate in decoding.
        active = valid & query_mask.unsqueeze(-1)
        starts, ends = indices[..., 0], indices[..., 1]
        lengths = text_mask.sum(-1).view(-1, 1, 1)
        if bool((active & ((starts < 0) | (ends <= starts) | (ends > lengths))).any()):
            raise GlinerExtractionError("boundary_candidate_span_invalid", stage="output")
        if not bool(torch.isfinite(logits[active]).all()):
            raise GlinerExtractionError("boundary_candidate_score_nonfinite", stage="output")
        null_logits = output.null_logits
        count_rates = output.count_log_rates
        decision_scores = [("null", null_logits, module.settings.enable_abstention)]
        if module.settings.adaptive_threshold:
            decision_scores.append(("count", count_rates, True))
        for name, values, required in decision_scores:
            if values is None and not required:
                continue
            if (not isinstance(values, torch.Tensor) or values.shape != query_mask.shape
                    or not values.is_floating_point()):
                raise GlinerExtractionError("boundary_decision_layout_invalid", stage="output")
            if not bool(torch.isfinite(values[query_mask]).all()):
                raise GlinerExtractionError(f"boundary_{name}_score_nonfinite", stage="output")
    except GlinerExtractionError:
        raise
    except Exception as exc:
        raise GlinerExtractionError("boundary_output_invalid", stage="output") from exc


class Gliner2Extractor:
    """Description-guided GLiNER2 implementing the existing strict span protocol.

    ``max_len`` bounds word tokens, not encoder subwords or claim length.
    Boundary candidates have no fixed span width; their shared pool is bounded.
    Character splitting is an explicit runtime choice and may change quality
    relative to the default public model.
    """

    def __init__(
        self, model_path: str | Path, *, descriptions: dict[str, str],
        device: str = "cpu", word_splitter: str = "char", max_len: int = 160,
        manifest: dict | None = None,
    ) -> None:
        if (not isinstance(descriptions, dict) or not descriptions
                or any(not isinstance(key, str) or not key.strip()
                       or not isinstance(value, str) or not value.strip()
                       for key, value in descriptions.items())):
            raise GlinerExtractionError("label_descriptions_invalid", stage="input")
        if (not isinstance(device, str) or re.fullmatch(r"cpu|cuda(?::\d+)?", device) is None
                or word_splitter not in {"char", "whitespace"}
                or type(max_len) is not int or max_len <= 0):
            raise GlinerExtractionError("extractor_configuration_invalid", stage="input")
        self.model_path = Path(model_path).expanduser().resolve()
        self.descriptions = dict(descriptions)
        self.device = device
        self.word_splitter = word_splitter
        self.max_len = max_len
        self.manifest = deepcopy(manifest)
        self.last_batch_lengths: list[int] = []
        self._model = None
        self._limits = None

    def _check_checkpoint(self):
        if not self.model_path.is_dir():
            raise GlinerExtractionError("local_model_directory_missing", stage="load")
        required = ("config.json", "encoder_config/config.json", "tokenizer_config.json",
                    "tokenizer.json", "model.safetensors")
        if any(not (self.model_path / filename).is_file() for filename in required):
            raise GlinerExtractionError("local_model_files_missing", stage="load")
        # This experiment uses packaged built-in architectures/tokenizers only.
        for filename in required[:3]:
            try:
                config = json.loads((self.model_path / filename).read_text())
            except (OSError, ValueError) as exc:
                raise GlinerExtractionError("local_config_invalid", stage="load") from exc
            if not isinstance(config, dict) or config.get("auto_map"):
                raise GlinerExtractionError("local_config_unsupported", stage="load")
            if filename == "config.json" and config.get("architecture") != "boundary":
                raise GlinerExtractionError("model_architecture_mismatch", stage="load")
        try:
            manifest = self.manifest
            if manifest is None:
                manifest = json.loads((self.model_path / "DOWNLOAD-MANIFEST.json").read_text())
            verify_local_checkpoint(self.model_path, manifest)
        except (OSError, ValueError) as exc:
            raise GlinerExtractionError(
                "local_checkpoint_verification_failed", stage="load",
            ) from exc

    def prepare_strict(self) -> dict:
        if self._model is not None:
            return dict(self._limits)
        self._check_checkpoint()
        model_class = _offline_model_class()
        try:
            model = model_class.from_pretrained(
                str(self.model_path), architecture="boundary",
                local_files_only=True, map_location=self.device,
                word_splitter=self.word_splitter, use_flashdeberta=False,
                quantize=False, compile=False,
            )
            model.set_word_splitter(self.word_splitter)
            model.eval()
            if (getattr(model, "architecture", None) != "boundary"
                    or getattr(model.config, "architecture", None) != "boundary"):
                raise GlinerExtractionError("model_architecture_mismatch", stage="load")
            processor = model.processor
            expected_splitter = ("CharLevelSplitter" if self.word_splitter == "char"
                                 else "WhitespaceTokenSplitter")
            if type(processor.word_splitter).__name__ != expected_splitter:
                raise GlinerExtractionError("word_splitter_unavailable", stage="load")
            settings = model.boundary_settings
            head = model.boundary_head
            if settings.candidate_pool != "shared" or head.settings.candidate_pool != "shared":
                raise GlinerExtractionError("candidate_pool_unsupported", stage="load")
            # Report the active shared pool, not the unused per-query budget.
            pool = head.shared_pool_builder
            budget = {key: _positive_limit(getattr(pool, key, None)) for key in (
                "pool_boundary_top_k", "pool_size", "min_pool_per_query",
            )}
            if any(value is None or value != getattr(settings, key, None)
                   for key, value in budget.items()):
                raise GlinerExtractionError("candidate_budget_invalid", stage="load")
            encoder_limit = _positive_limit(model.encoder.config.max_position_embeddings)
            tokenizer_limit = _positive_limit(processor.tokenizer.model_max_length)
            limits = [value for value in (encoder_limit, tokenizer_limit) if value is not None]
            if not limits:
                raise GlinerExtractionError("encoder_input_limit_unavailable", stage="load")
            if any(not callable(getattr(processor, method, None)) for method in (
                "_pad_batch", "_add_boundary_metadata",
            )):
                raise GlinerExtractionError("source_collator_unavailable", stage="load")
            head.register_forward_hook(_validate_boundary_output)
        except GlinerExtractionError:
            raise
        except Exception as exc:
            raise GlinerExtractionError("gliner2_model_load_failed", stage="load") from exc
        self._model = model
        # GLiNER2 2.0.0's default batch collator appends "." to text that does
        # not end in .!?. This can extend an ASCII token and produce spans
        # outside the source. Its inference processor also defaults to fallback
        # records on preprocessing errors. Use the instance-level collator hook
        # with the same official, checked single-record transform as preflight.
        # Never trim model coordinates or search for replacement source spans.
        model._inference_collator = self._collate_exact_source
        model.strict_extraction = True
        self._limits = {
            "backend": "gliner2.5", "model_family": "GLiNER2.5", "architecture": "boundary",
            "package_version": "2.0.0", "device": self.device,
            "max_len": self.max_len, "max_width": None,
            "span_width_policy": "no_fixed_width_within_encoded_window",
            "candidate_pool": "shared", "shared_candidate_budget": budget,
            "encoder_max_positions": encoder_limit, "tokenizer_max_length": tokenizer_limit,
            "encoder_input_limit": min(512, *limits),
            "encoder_limit_policy": "conservative_512_and_declared_limit",
            "word_splitter": self.word_splitter, "checkpoint_default_splitter": "whitespace",
            "splitter_quality_verified": False,
            "word_truncation": "rejected_before_inference",
            "encoder_truncation": "preprocessor_checked_encoder_internal_not_instrumented",
            "source_collator": "checked_transform_pad_and_boundary_metadata",
            "source_augmentation": "disabled_default_terminal_period",
            "preprocessing_error_policy": "raise",
            "candidate_validation": "active_spans_and_decision_scores_before_decode",
            "overlap_policy": "allow", "long_values_require_context": True,
        }
        return dict(self._limits)

    def _checked_record(self, text, schema, limits, slot=None):
        processor = self._model.processor
        words = list(processor.word_splitter(text, lower=False))
        if len(words) > self.max_len:
            raise GlinerExtractionError("input_word_limit_exceeded", stage="input")
        record = processor.transform_and_format(text, schema)
        length = len(record.input_ids)
        if slot is not None:
            self.last_batch_lengths[slot] = length
        expected_starts = [item[1] for item in words]
        expected_ends = [item[2] for item in words]
        positions = record.text_word_first_positions
        if (record.text != text
                or record.start_token_idx != expected_starts
                or record.end_token_idx != expected_ends
                or len(record.text_tokens) != len(words) or len(positions) != len(words)
                or any(type(pos) is not int or not 0 <= pos < length for pos in positions)
                or any(left >= right for left, right in zip(positions, positions[1:]))
                or any(record.mapped_indices[pos][0] != "text" for pos in positions)):
            raise GlinerExtractionError("source_preprocessing_alignment_invalid", stage="input")
        if length > limits["encoder_input_limit"]:
            raise GlinerExtractionError("encoder_input_limit_exceeded", stage="input")
        return record

    def _collate_exact_source(self, batch):
        records = [self._checked_record(text, schema, self._limits) for text, schema in batch]
        processor = self._model.processor
        result = processor._pad_batch(records)
        if result.original_texts != [text for text, _ in batch]:
            raise GlinerExtractionError("source_preprocessing_alignment_invalid", stage="input")
        result = processor._add_boundary_metadata(
            result, "boundary", is_training=False, build_targets=False,
            on_capacity_exceeded="raise", ignore_missing_entities=False,
        )
        if result.original_texts != [text for text, _ in batch]:
            raise GlinerExtractionError("source_preprocessing_alignment_invalid", stage="input")
        layouts = getattr(result, "query_layouts", None)
        if (not isinstance(layouts, (list, tuple)) or len(layouts) != len(batch)
                or getattr(result, "targets", None) is not None):
            raise GlinerExtractionError("boundary_query_layout_invalid", stage="input")
        for layout, (_, schema) in zip(layouts, batch):
            queries = getattr(layout, "queries", ())
            labels = list(schema["entities"])
            if (len(queries) != len(labels)
                    or {query.role_name for query in queries} != set(labels)
                    or any(query.query_id != index or query.role_index != index
                           or query.task_type != "entities" or query.task_name != "entities"
                           or query.task_index != 0 or query.extractive is not True
                           for index, query in enumerate(queries))):
                raise GlinerExtractionError("boundary_query_layout_invalid", stage="input")
        return result

    def _check_inputs(self, texts, descriptions, limits):
        processor = self._model.processor
        schema = self._model.create_schema().entities(descriptions).build()
        processor.change_mode(is_training=False)
        for index, text in enumerate(texts):
            if not text:
                continue
            self._checked_record(text, schema, limits, slot=index)

    def extract_batch_with_spans_strict(
        self, texts: list[str], labels: list[str], threshold: float = 0.5,
    ) -> list[list[dict]]:
        self.last_batch_lengths = []
        if (not isinstance(texts, list) or any(not isinstance(text, str) for text in texts)
                or not isinstance(labels, list) or not labels
                or any(not isinstance(label, str) or not label.strip() for label in labels)
                or type(threshold) not in (int, float) or not math.isfinite(threshold)
                or not 0 <= threshold <= 1):
            raise GlinerExtractionError("extraction_arguments_invalid", stage="input")
        labels = list(dict.fromkeys(labels))
        if any(label not in self.descriptions for label in labels):
            raise GlinerExtractionError("label_description_missing", stage="input")
        descriptions = {label: self.descriptions[label] for label in labels}
        self.last_batch_lengths = [0] * len(texts)
        output = [[] for _ in texts]
        slots = [index for index, text in enumerate(texts) if text]
        if not slots:
            return output
        limits = self.prepare_strict()
        try:
            self._check_inputs(texts, descriptions, limits)
        except GlinerExtractionError:
            raise
        except Exception as exc:
            raise GlinerExtractionError("input_preprocessing_failed", stage="input") from exc
        try:
            rows = self._model.batch_extract_entities(
                [texts[index] for index in slots], descriptions,
                batch_size=len(slots), threshold=threshold, format_results=True,
                include_confidence=True, include_spans=True,
                # A non-None max_len replaces the instance collator with the
                # source-augmenting default. Our collator enforces max_len.
                max_len=None, overlap_policy="allow",
            )
        except GlinerExtractionError:
            raise
        except Exception as exc:
            raise GlinerExtractionError("inference_failed", stage="inference") from exc
        if not isinstance(rows, list) or len(rows) != len(slots):
            raise GlinerExtractionError("batch_alignment_invalid", stage="output")
        for slot, row in zip(slots, rows):
            if (not isinstance(row, dict) or not isinstance(row.get("entities"), dict)
                    or not set(row["entities"]).issubset(labels)):
                raise GlinerExtractionError("entities_invalid", stage="output")
            for label, predictions in row["entities"].items():
                if not isinstance(predictions, list):
                    raise GlinerExtractionError("entities_invalid", stage="output")
                for prediction in predictions:
                    if not isinstance(prediction, dict):
                        raise GlinerExtractionError("span_invalid", stage="output")
                    span = {key: prediction.get(key) for key in ("start", "end", "text")}
                    span.update(label=label, score=prediction.get("confidence"))
                    if not _valid_source_span(span, texts[slot], set(labels)):
                        raise GlinerExtractionError("span_invalid", stage="output")
                    output[slot].append(span)
            output[slot].sort(key=lambda span: (span["start"], span["end"], span["label"]))
        return output
