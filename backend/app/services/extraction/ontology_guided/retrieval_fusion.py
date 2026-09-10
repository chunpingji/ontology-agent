"""Finite, pool-scoped ranks and reciprocal-rank fusion."""

from __future__ import annotations

import math


def rank_scores(scores: dict[str, float], record_ids: list[str]) -> dict[str, int]:
    if set(scores) != set(record_ids) or len(record_ids) != len(set(record_ids)):
        raise ValueError("scores must cover the exact unique ranking pool")
    if not all(math.isfinite(value) for value in scores.values()):
        raise ValueError("ranking scores must be finite")
    positions = {record_id: index for index, record_id in enumerate(record_ids)}
    ordered = sorted(record_ids, key=lambda rid: (-scores[rid], positions[rid], rid))
    return {rid: rank for rank, rid in enumerate(ordered, 1)}


def reciprocal_rank_fusion(
    ranks: dict[str, dict[str, int]],
    record_ids: list[str],
    *,
    weights: dict[str, float] | None = None,
    smoothing: float = 60.0,
    require_complete: bool = False,
) -> tuple[list[str], dict[str, float]]:
    if smoothing <= 0 or not math.isfinite(smoothing):
        raise ValueError("RRF smoothing must be positive and finite")
    weights = weights or {name: 1.0 / len(ranks) for name in ranks}
    if (
        set(weights) != set(ranks)
        or not all(math.isfinite(value) and value >= 0 for value in weights.values())
        or not any(weights.values())
    ):
        raise ValueError("RRF weights must match channels and contain positive mass")
    universe = set(record_ids)
    if len(universe) != len(record_ids):
        raise ValueError("ranking pool contains duplicate records")
    for values in ranks.values():
        if not set(values).issubset(universe) or (require_complete and set(values) != universe):
            raise ValueError("channel rank scope does not match the pool")
        if any(rank < 1 for rank in values.values()):
            raise ValueError("ranks start at one")
    scores = {
        rid: sum(
            weights[name] / (smoothing + values[rid])
            for name, values in ranks.items()
            if rid in values
        )
        for rid in record_ids
    }
    positions = {rid: index for index, rid in enumerate(record_ids)}
    ordered = sorted(record_ids, key=lambda rid: (-scores[rid], positions[rid], rid))
    return ordered, scores
