"""Bounded, source-verified GLiNER suggestions; no fact or semantic decisions."""

from __future__ import annotations

import hashlib
import json
import math
from time import perf_counter

from app.services.extraction.annotation_execution import ExecutionLost
from app.services.extraction.gliner2_extractor import Gliner2Extractor
from app.services.extraction.gliner_extractor import (
    GlinerExtractionError,
    _valid_source_span,
    get_gliner_extractor,
)
from app.services.extraction.text_scanner import unicode_words
from app.services.llm.model_runtime import ModelCancelled, ModelWaitFailure

CHUNK_CHARS = 160
CHUNK_OVERLAP = 24
BATCH_SIZE = 8
LABEL_BATCH_SIZE = 8


def _windows(text: str):
    start = 0
    while start < len(text):
        end = min(start + CHUNK_CHARS, len(text))
        yield start, end
        if end == len(text):
            break
        start = end - CHUNK_OVERLAP


def _span_id(ref: str, group: str, span: dict) -> str:
    material = json.dumps(
        [ref, group, span["start"], span["end"], span["text"], span["label"]],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return "mention-" + hashlib.sha256(material.encode()).hexdigest()[:24]


def propose_mentions(
    sources: dict[str, dict],
    *,
    groups: dict[str, list[str]],
    extractor=None,
    threshold: float = 0.5,
    labels_per_batch: int = LABEL_BATCH_SIZE,
    required_backend: str | None = None,
) -> dict:
    """Suggest mentions for controller-selected sources and whitelisted labels.

    The controller derives ``groups`` from permitted schema cards, never a silver
    reference. Each source is independently windowed with overlap; entity/value/
    unit groups run separately so the legacy flat-span filter cannot suppress
    another group's candidates. Coverage records execution, not entity recall.
    Older GLiNER APIs do not report encoder truncation, which remains unknown.
    Long values always require the original source context, even after a hit.
    """
    result = {
        "tool": "propose_mentions",
        "execution_status": "not_attempted",
        "semantic_status": "not_checked",
        "fact_eligible": False,
        "spans": [],
        "groups": {},
        "coverage": {
            "requested_refs": list(sources),
            "attempted_refs": [],
            "covered_refs": [],
            "unattempted_refs": list(sources),
            "failed_refs": [],
            "meaning": "all requested group windows executed; not semantic or recall coverage",
        },
        "chunks": [],
        "rejected_spans": [],
        "issues": [],
        "timing": {"load_seconds": 0.0, "inference_seconds": 0.0},
        "limits": {
            "chunk_chars": CHUNK_CHARS,
            "overlap_chars": CHUNK_OVERLAP,
            "batch_size": BATCH_SIZE,
            "labels_per_batch": labels_per_batch,
            "source_truncated": False,
            "encoder_truncation": "unknown",
            "long_values_require_context": True,
        },
    }
    if (
        not groups
        or any(
            not isinstance(group, str)
            or not group
            or not isinstance(labels, list)
            or not labels
            or any(not isinstance(label, str) or not label.strip() for label in labels)
            for group, labels in groups.items()
        )
        or type(threshold) not in (int, float)
        or not math.isfinite(threshold)
        or not 0 <= threshold <= 1
        or type(labels_per_batch) is not int
        or not 1 <= labels_per_batch <= LABEL_BATCH_SIZE
    ):
        result["execution_status"] = "failed"
        result["issues"].append("invalid_tool_arguments")
        return result
    groups = {group: list(dict.fromkeys(labels)) for group, labels in groups.items()}
    result["groups"] = {group: [] for group in groups}
    windows = []
    for ref, source in sources.items():
        text = source.get("text") if isinstance(source, dict) else None
        if not isinstance(ref, str) or not isinstance(text, str) or not text.strip():
            result["issues"].append({"ref": ref, "code": "source_text_unavailable"})
            continue
        windows.extend(
            {"ref": ref, "start": start, "end": end, "text": text[start:end]}
            for start, end in _windows(text)
        )
    if not windows:
        result["issues"].append("no_usable_sources")
        return result

    load_started = perf_counter()
    try:
        extractor = extractor if extractor is not None else get_gliner_extractor()
        if extractor is None:
            raise GlinerExtractionError("model_disabled", stage="load")
        limits = extractor.prepare_strict()
        if not isinstance(limits, dict):
            raise GlinerExtractionError("model_limits_invalid", stage="load")
        if required_backend is not None and limits.get("backend") != required_backend:
            raise GlinerExtractionError("model_backend_mismatch", stage="load")
        result["limits"].update(limits)
    except (ModelCancelled, ExecutionLost, ModelWaitFailure):
        raise
    except Exception as exc:
        result["execution_status"] = "unavailable"
        result["issues"].append({
            "code": getattr(exc, "code", "model_load_failed"), "stage": "load"
        })
        return result
    finally:
        result["timing"]["load_seconds"] = round(perf_counter() - load_started, 6)

    mentions: dict[str, dict] = {}
    for group, labels in groups.items():
        for label_start in range(0, len(labels), labels_per_batch):
            label_batch = labels[label_start:label_start + labels_per_batch]
            for start in range(0, len(windows), BATCH_SIZE):
                batch = windows[start:start + BATCH_SIZE]
                chunk_rows = []
                for window in batch:
                    word_count = sum(1 for _ in unicode_words(window["text"]))
                    max_len = limits.get("max_len")
                    chunk = {
                        key: window[key] for key in ("ref", "start", "end")
                    } | {
                        "group": group,
                        "labels": label_batch,
                        "word_count": word_count,
                        "status": "failed",
                        "truncation": {
                            "source": False,
                            "word_limit_exceeded": (
                                word_count > max_len if isinstance(max_len, int) else None
                            ),
                            "encoder": limits.get("encoder_truncation", "unknown"),
                        },
                    }
                    chunk_rows.append(chunk)
                result["chunks"].extend(chunk_rows)
                started = perf_counter()
                try:
                    predictions = extractor.extract_batch_with_spans_strict(
                        [window["text"] for window in batch], label_batch, threshold=threshold
                    )
                    if not isinstance(predictions, list) or len(predictions) != len(batch):
                        raise GlinerExtractionError("batch_alignment_invalid", stage="output")
                    lengths = getattr(extractor, "last_batch_lengths", None)
                    if isinstance(lengths, list) and len(lengths) == len(chunk_rows):
                        for chunk, length in zip(chunk_rows, lengths):
                            if type(length) is int and length > 0:
                                chunk["encoder_input_tokens"] = length
                    for window, chunk, spans in zip(batch, chunk_rows, predictions):
                        if not isinstance(spans, list):
                            raise GlinerExtractionError("entities_invalid", stage="output")
                        invalid = False
                        for span in spans:
                            if not _valid_source_span(span, window["text"], set(label_batch)):
                                invalid = True
                                result["rejected_spans"].append({
                                    "ref": window["ref"], "group": group,
                                    "chunk_start": window["start"], "code": "span_invalid",
                                })
                                continue
                            mapped = dict(span) | {
                                "ref": window["ref"], "group": group,
                                "start": window["start"] + span["start"],
                                "end": window["start"] + span["end"],
                            }
                            # Check against the frozen source again after offset mapping.
                            if sources[window["ref"]]["text"][
                                mapped["start"]:mapped["end"]
                            ] != mapped["text"]:
                                raise GlinerExtractionError("span_mapping_invalid", stage="output")
                            mapped["id"] = _span_id(window["ref"], group, mapped)
                            prior = mentions.get(mapped["id"])
                            if prior is None or mapped["score"] > prior["score"]:
                                mentions[mapped["id"]] = mapped
                        chunk["status"] = "invalid_output" if invalid else "completed"
                except (ModelCancelled, ExecutionLost, ModelWaitFailure):
                    raise
                except Exception as exc:
                    result["issues"].append({
                        "code": getattr(exc, "code", "inference_failed"),
                        "stage": getattr(exc, "stage", "inference"),
                        "group": group,
                        "refs": list(dict.fromkeys(window["ref"] for window in batch)),
                    })
                finally:
                    result["timing"]["inference_seconds"] += perf_counter() - started

    result["timing"]["inference_seconds"] = round(result["timing"]["inference_seconds"], 6)
    result["spans"] = sorted(
        mentions.values(), key=lambda item: (item["ref"], item["start"], item["end"], item["group"])
    )
    for span in result["spans"]:
        result["groups"][span["group"]].append(span)
    attempted = {chunk["ref"] for chunk in result["chunks"]}
    failed = {chunk["ref"] for chunk in result["chunks"] if chunk["status"] != "completed"}
    coverage = result["coverage"]
    coverage.update({
        "attempted_refs": [ref for ref in sources if ref in attempted],
        "covered_refs": [ref for ref in sources if ref in attempted - failed],
        "unattempted_refs": [ref for ref in sources if ref not in attempted],
        "failed_refs": [ref for ref in sources if ref in failed],
    })
    incomplete = bool(failed or coverage["unattempted_refs"])
    any_completed = any(chunk["status"] == "completed" for chunk in result["chunks"])
    result["execution_status"] = (
        "partial" if incomplete and any_completed else "failed" if incomplete else "completed"
    )
    return result


def build_vocabulary_extractor(
    vocabulary: dict, *, model_path: str, device: str = "cpu", max_len: int = CHUNK_CHARS,
    manifest: dict | None = None,
) -> Gliner2Extractor:
    """Explicit local GLiNER2.5 construction; no application-default model lookup."""
    descriptions = {label: entry["description"] for label, entry in vocabulary["entries"].items()}
    return Gliner2Extractor(
        model_path, descriptions=descriptions, device=device, word_splitter="char", max_len=max_len,
        manifest=manifest,
    )


def propose_vocabulary_mentions(
    sources: dict[str, dict], *, vocabulary: dict, extractor,
    threshold: float = 0.5, labels_per_batch: int = 1,
) -> dict:
    """Use injected 2.5 and one full definition per default encoder request.

    Descriptions share the encoder window with source text. The label-only
    batch default can overflow even for a short source; keep all definitions
    in separate batches, with the same strict per-request length checks.
    """
    if extractor is None:
        raise ValueError("gliner25_extractor_required")
    groups = {role: labels for role, labels in vocabulary["groups"].items() if labels}
    if any(label not in vocabulary["entries"] for labels in groups.values() for label in labels):
        raise ValueError("vocabulary_label_missing")
    return propose_mentions(
        sources, groups=groups, extractor=extractor, threshold=threshold,
        labels_per_batch=labels_per_batch, required_backend="gliner2.5",
    )
