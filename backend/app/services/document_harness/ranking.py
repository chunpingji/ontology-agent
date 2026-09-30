"""Source-related card retrieval with ontology ancestry and marginal coverage.

All scores are retrieval signals, never source support or type judgments. This
module uses the shared local model transport, not an extraction ranking protocol.
"""

from __future__ import annotations

import json
import math
import re
import unicodedata
from collections import deque
from time import monotonic

from .ontology import SchemaCatalog, identity_guidance

PROTOCOL = "harness-card-ranking-v1"
GUIDANCE_RULE = (
    "这些本体卡仅用于理解可能的对象、字段归属和关系；排名或属性匹配不证明类型成立。"
    "仍只从原文发现对象，不为每张卡造实体；否定分类、缺失值和身份边界按原文处理。"
    "未提供的类型不代表被否定，后续类型对齐仍检查其余目录。"
)


def default_policy():
    return {
        "protocol": PROTOCOL, "seed_limit": 24, "max_cards": 8,
        "ancestor_decay": 0.8,
        "semantic": {"enabled": False, "mode": "deterministic"},
    }


def freeze_ranking_policy(settings):
    policy = default_policy()
    enabled = settings.semantic_ranking_enabled and settings.semantic_ranking_mode == "semantic"
    policy["semantic"] = {
        "enabled": enabled, "mode": "rerank" if enabled else "deterministic",
        **{
            key: getattr(settings, "semantic_ranking_" + key)
            for key in (
                "device", "dtype", "cuda_version", "embedding_path", "embedding_manifest_path",
                "reranker_path", "reranker_manifest_path", "batch_size", "max_tokens_per_pair",
                "timeout_seconds",
            )
        },
    }
    return policy


def _terms(text):
    value = unicodedata.normalize("NFKC", text).casefold()
    result = set()
    for part in re.findall(r"[\u3400-\u9fff]+|[a-z0-9]+", value):
        if "\u3400" <= part[0] <= "\u9fff":
            result.update(part[i:i + 2] for i in range(max(1, len(part) - 1)))
        else:
            result.add(part)
    return result


def _similarity(left, right):
    return len(left & right) / math.sqrt(len(left) * len(right)) if left and right else 0.0


def semantic_text(card):
    # Shared/inherited properties are represented once, and do not masquerade
    # as a child's distinguishing definition.
    return "\n".join(dict.fromkeys(filter(None, [
        card.label, *card.aliases, card.description,
        *(prop.label for prop in card.properties if prop.constraint_status == "resolved"),
    ])))


def reading_card(card, *, annotation_contracts=()):
    return {
        "iri": card.iri, "label": card.label, "aliases": list(card.aliases),
        "description": card.description,
        "definition": [item.model_dump(mode="json", exclude_none=True) for item in card.definition],
        "property_labels": sorted({
            prop.label for prop in card.properties if prop.constraint_status == "resolved"
        }),
        **identity_guidance(card, annotation_contracts=annotation_contracts),
        "relations": [
            {"iri": rel.iri, "label": rel.label, "description": rel.description,
             "range_class_iris": list(rel.range_class_iris), "direction": rel.direction}
            for rel in card.relations if rel.constraint_status == "resolved"
        ],
    }


def _ancestors(catalog, iri, allowed):
    """Shortest named parent paths; cycles never make a class its own parent."""
    distances = {}
    pending = deque([(iri, 0)])
    seen = {iri}
    while pending:
        current, distance = pending.popleft()
        for parent in sorted(catalog.classes[current].parent_iris):
            if parent not in allowed or parent in seen:
                continue
            seen.add(parent)
            distances[parent] = distance + 1
            pending.append((parent, distance + 1))
    return distances


def _field_weights(catalog, allowed, fields):
    queries = [_terms(field["label"]) for field in fields if field.get("label")]
    properties = {
        prop.iri: prop for iri in sorted(allowed) for prop in catalog.classes[iri].properties
        if prop.constraint_status == "resolved"
    }
    # One aspect per property IRI, even when hundreds of subclasses inherit it.
    return {
        iri: max((max(
            _similarity(query, _terms(label)) for label in (prop.label, *prop.aliases)
        ) for query in queries), default=0.0)
        for iri, prop in properties.items()
    }


def rank_cards(catalog, payload, card_budget, *, policy=None, dense_scores=None, score_pairs=None):
    """Rank and greedily select cards, optionally within remaining request bytes.

    R = .75 * direct relevance + .25 * distinct property coverage.
    H = max(.8 ** shortest_parent_distance * R(descendant seed)).
    Selection gain = .50 R + .25 H + .25 new field coverage + .40 new ancestor coverage.
    An exact ancestor card covers its level fully; descendants only contribute a
    discounted partial cover. This encodes the requested preference for retaining
    useful broader alternatives, without adding a global bonus to broad classes.
    """
    policy = policy or default_policy()
    if policy["protocol"] != PROTOCOL:
        raise ValueError("harness_ranking_policy_mismatch")
    if not 0 < policy["ancestor_decay"] < 1 or not 1 <= policy["max_cards"] <= 32:
        raise ValueError("harness_ranking_policy_invalid")
    if not 1 <= policy["seed_limit"] <= 1024 or (card_budget is not None and card_budget < 0):
        raise ValueError("harness_ranking_budget_invalid")
    allowed = set(catalog.reachable_class_iris) - {catalog.root_class_iri}
    query = "\n".join(source["text"] for source in payload["sources"])
    query_terms = _terms(query)
    weights = _field_weights(catalog, allowed, payload.get("fields", []))
    field_total = sum(weights.values()) or 1.0
    field_sets = {
        iri: {prop.iri for prop in catalog.classes[iri].properties if weights.get(prop.iri, 0) > 0}
        for iri in allowed
    }
    field_scores = {
        iri: sum(weights[key] for key in field_sets[iri]) / field_total for iri in allowed
    }
    lexical = {
        iri: _similarity(query_terms, _terms(semantic_text(catalog.classes[iri])))
        for iri in allowed
    }
    if dense_scores is not None:
        if set(dense_scores) != allowed or any(
            isinstance(value, bool) or not math.isfinite(value) for value in dense_scores.values()
        ):
            raise ValueError("harness_ranking_invalid_dense_scores")
    initial = {
        iri: 0.75 * max(0.0, min(1.0, dense_scores[iri])) + 0.25 * field_scores[iri]
        if dense_scores is not None else 0.75 * lexical[iri] + 0.25 * field_scores[iri]
        for iri in allowed
    }
    seeds = sorted(
        (iri for iri in allowed if initial[iri] > 0), key=lambda iri: (-initial[iri], iri),
    )[:policy["seed_limit"]]
    ancestry = {iri: _ancestors(catalog, iri, allowed) for iri in allowed}
    pool = sorted(set(seeds) | {parent for seed in seeds for parent in ancestry[seed]})
    raw_scores = {}
    direct = {iri: lexical[iri] for iri in pool}
    if score_pairs and pool:
        scores = score_pairs([(query, semantic_text(catalog.classes[iri])) for iri in pool])
        if len(scores) != len(pool) or any(
            isinstance(value, bool) or not isinstance(value, (int, float))
            or not math.isfinite(value) for value in scores
        ):
            raise ValueError("harness_ranking_invalid_pair_scores")
        raw_scores = dict(zip(pool, scores, strict=True))
        # Bounded monotone transform of raw logits, NOT calibrated type probability.
        direct = {iri: 1 / (1 + math.exp(-max(-60, min(60, score))))
                  for iri, score in raw_scores.items()}
    relevance = {iri: 0.75 * direct[iri] + 0.25 * field_scores[iri] for iri in pool}
    decay = policy["ancestor_decay"]
    support = {iri: [] for iri in pool}
    for seed in seeds:
        for parent, distance in ancestry[seed].items():
            support[parent].append({
                "seed_iri": seed, "distance": distance,
                "score": relevance[seed] * decay ** distance,
            })
    hierarchy = {iri: max((row["score"] for row in support[iri]), default=0.0) for iri in pool}
    ancestor_weights = {iri: score for iri, score in hierarchy.items() if score > 0}
    ancestor_total = sum(ancestor_weights.values()) or 1.0
    cover = {
        iri: {parent: (1.0 if iri == parent else 0.2 * decay ** ancestry[iri][parent])
              for parent in ancestor_weights if iri == parent or parent in ancestry[iri]}
        for iri in pool
    }
    costs = {
        iri: len(json.dumps(reading_card(
            catalog.classes[iri], annotation_contracts=catalog.annotation_contracts,
        ), ensure_ascii=False).encode()) + 2
        for iri in pool
    }
    selected, covered_fields, covered_ancestors = [], set(), {}
    remaining = card_budget
    bytes_used = 0
    gains = {}
    while len(selected) < policy["max_cards"]:
        options = []
        for iri in pool:
            if iri in selected or (remaining is not None and costs[iri] > remaining):
                continue
            field_gain = sum(weights[key] for key in field_sets[iri] - covered_fields) / field_total
            ancestor_gain = sum(
                ancestor_weights[parent] * max(0, amount - covered_ancestors.get(parent, 0))
                for parent, amount in cover[iri].items()
            ) / ancestor_total
            gain = (0.50 * relevance[iri] + 0.25 * hierarchy[iri]
                    + 0.25 * field_gain + 0.40 * ancestor_gain)
            options.append((gain, iri, field_gain, ancestor_gain))
        if not options:
            break
        gain, iri, field_gain, ancestor_gain = min(options, key=lambda row: (-row[0], row[1]))
        selected.append(iri)
        bytes_used += costs[iri]
        if remaining is not None:
            remaining -= costs[iri]
        gains[iri] = {"gain": gain, "new_field_coverage": field_gain,
                      "new_ancestor_coverage": ancestor_gain}
        covered_fields.update(field_sets[iri])
        for parent, amount in cover[iri].items():
            covered_ancestors[parent] = max(covered_ancestors.get(parent, 0), amount)
    return {
        "protocol": PROTOCOL, "snapshot_id": catalog.snapshot_id,
        "score_semantics": "retrieval_only_not_source_support",
        "mode": "semantic" if score_pairs else "deterministic",
        "selected_iris": selected, "seed_iris": seeds,
        "card_budget_bytes": card_budget, "card_bytes_used": bytes_used,
        "candidates": [{
            "iri": iri, "direct_score": direct[iri], "raw_pair_score": raw_scores.get(iri),
            "field_score": field_scores[iri], "relevance": relevance[iri],
            "hierarchy_support": hierarchy[iri], "parent_sources": support[iri],
            "selection": gains.get(iri), "card_bytes": costs[iri],
            "omission_reason": None if iri in selected else (
                "card_exceeds_remaining_budget"
                if remaining is not None and costs[iri] > remaining else "card_limit"
            ),
        } for iri in pool],
        "outside_pool_iris": sorted(allowed - set(pool)),
    }


class CardRanker:
    """One worker-local semantic session; vectors are reused within this run."""

    def __init__(self, policy=None, *, should_stop=lambda: False):
        self.policy = policy or default_policy()
        self.should_stop = should_stop
        self.semantic = None
        self.vectors = {}

    def _batch(self, operation, items):
        values = []
        size = self.policy["semantic"]["batch_size"]
        for start in range(0, len(items), size):
            if self.should_stop():
                from app.services.llm.model_runtime import ModelCancelled

                raise ModelCancelled()
            values.extend(getattr(self.semantic, operation)(items[start:start + size]))
        return values

    def rank(self, catalog, payload, card_budget):
        catalog = SchemaCatalog.model_validate(catalog)
        started = monotonic()
        config = self.policy["semantic"]
        if config["enabled"] and self.semantic is None:
            from app.services.llm.semantic_ranking import configured_semantic_ranking

            self.semantic = configured_semantic_ranking(config)
            if self.semantic is None:
                raise ValueError("harness_semantic_ranking_unavailable")
        operation_start = len(self.semantic.observations) if self.semantic else 0
        dense = None
        if self.semantic:
            cards = [catalog.classes[iri] for iri in sorted(catalog.reachable_class_iris)
                     if iri != catalog.root_class_iri]
            unseen = [card for card in cards if (catalog.snapshot_id, card.iri) not in self.vectors]
            vectors = self._batch("embed", [semantic_text(card) for card in unseen])
            self.vectors.update({(catalog.snapshot_id, card.iri): vector
                                 for card, vector in zip(unseen, vectors, strict=True)})
            query = "\n".join(source["text"] for source in payload["sources"])
            query_vector = self._batch("embed", [query])[0]
            dense = {
                card.iri: sum(a * b for a, b in zip(
                    query_vector, self.vectors[(catalog.snapshot_id, card.iri)], strict=True,
                )) for card in cards
            }
        result = rank_cards(
            catalog, payload, card_budget, policy=self.policy, dense_scores=dense,
            score_pairs=(
                (lambda pairs: self._batch("score_pairs", pairs)) if self.semantic else None
            ),
        )
        return {
            **result, "seconds": monotonic() - started,
            "model_identity": self.semantic.identity if self.semantic else None,
            "operations": (
                list(self.semantic.observations[operation_start:]) if self.semantic else []
            ),
        }

    def close(self):
        if self.semantic:
            self.semantic.close()
