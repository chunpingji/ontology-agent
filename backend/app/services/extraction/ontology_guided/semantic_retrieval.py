"""Distinct sparse, metadata, dense and protected candidate channels."""

from __future__ import annotations

import math
import re

from app.services.extraction.ontology_guided.retrieval_query import SubjectSlotQuery
from app.services.extraction.ontology_guided.retrieval_views import RetrievalView


def query_terms(query: SubjectSlotQuery) -> list[str]:
    import json

    payload = json.loads(query.model_text)
    predicate = payload["predicate"]
    values = [predicate["label"], predicate["iri"].rsplit("#", 1)[-1].rsplit("/", 1)[-1]]
    values.extend(payload["subject_mentions"])
    values.extend(item.get("label", "") for item in payload["allowed_object_types"])
    return list(dict.fromkeys(re.sub(r"\s+", "", value).casefold() for value in values if value))


def sparse_scores(texts: dict[str, str], terms: list[str]) -> dict[str, float]:
    result = {}
    for rid, text in texts.items():
        compact = re.sub(r"\s+", "", text).casefold()
        value = float(sum(bool(term) and term in compact for term in terms))
        if value > 0:
            result[rid] = value
    return result


def cosine_scores(query: list[float], records: dict[str, list[float]]) -> dict[str, float]:
    def norm(vector):
        if not vector or not all(math.isfinite(value) for value in vector):
            raise ValueError("embedding must contain finite values")
        value = math.sqrt(sum(item * item for item in vector))
        if not value or not math.isfinite(value):
            raise ValueError("embedding must have nonzero norm")
        return value

    query_norm = norm(query)
    result = {}
    for rid, vector in records.items():
        if len(vector) != len(query):
            raise ValueError("embedding dimensions differ")
        value = sum(left * right for left, right in zip(query, vector, strict=True))
        result[rid] = value / (query_norm * norm(vector))
    return result


def channel_orders(
    query: SubjectSlotQuery,
    views: dict[str, RetrievalView],
    record_ids: list[str],
    *,
    dense_scores: dict[str, float] | None = None,
) -> tuple[dict[str, list[str]], dict[str, dict[str, float]]]:
    terms = query_terms(query)
    signals = {
        "structure": sparse_scores({rid: views[rid].structure_text for rid in record_ids}, terms),
        "metadata": sparse_scores({rid: views[rid].metadata_text for rid in record_ids}, terms),
        "dense": dense_scores or {},
    }
    positions = {rid: index for index, rid in enumerate(record_ids)}
    return {
        name: sorted(values, key=lambda rid: (-values[rid], positions[rid], rid))
        for name, values in signals.items()
    }, signals


def select_candidate_pool(
    record_ids: list[str],
    channels: dict[str, list[str]],
    *,
    pool_size: int,
    protected_ids: list[str],
    exploration_ids: list[str],
    quotas: dict[str, int],
    fill_pool: bool = True,
) -> tuple[list[str], dict[str, list[str]]]:
    if pool_size < 1 or len(record_ids) != len(set(record_ids)):
        raise ValueError("candidate pool requires a positive limit and unique records")
    allowed = set(record_ids)
    selected: list[str] = []
    reasons: dict[str, list[str]] = {}
    sequences = {"protected": protected_ids, "exploration": exploration_ids, **channels}
    cursors = {name: 0 for name in sequences}

    def take(name, limit):
        taken = 0
        sequence = sequences[name]
        while cursors[name] < len(sequence) and taken < limit and len(selected) < pool_size:
            rid = sequence[cursors[name]]
            cursors[name] += 1
            if rid not in allowed:
                continue
            reasons.setdefault(rid, [])
            if name not in reasons[rid]:
                reasons[rid].append(name)
            if rid not in selected:
                selected.append(rid)
                taken += 1
        return taken

    for name in ("protected", "exploration", *channels):
        take(name, quotas.get(name, 0))
    while fill_pool and len(selected) < min(pool_size, len(record_ids)):
        previous = len(selected)
        for name in channels:
            take(name, 1)
        if len(selected) == previous:
            # Empty channels yield their allocation to complete-index exploration.
            sequences["tail"] = record_ids
            cursors.setdefault("tail", 0)
            take("tail", pool_size)
            break
    return selected, {rid: reasons.get(rid, []) for rid in selected}
