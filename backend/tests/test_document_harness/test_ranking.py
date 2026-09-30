"""Broader alternatives survive sibling crowding without changing fact authority."""

import json

import pytest
from rdflib import Graph

from app.services.document_harness.ontology import catalog_from_graph
from app.services.document_harness.ranking import (
    CardRanker,
    default_policy,
    rank_cards,
    reading_card,
)

PREFIX = """
@prefix : <urn:ranking:> .
@prefix owl: <http://www.w3.org/2002/07/owl#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .
"""


def catalog(siblings=5):
    body = """
    :Report a owl:Class .
    :Container a owl:Class ; rdfs:label "Container" .
    :Substance a owl:Class ; rdfs:label "Substance" .
    :SpecificSubstance a owl:Class ; rdfs:subClassOf :Substance ;
        rdfs:label "Specific substance" .
    :Unrelated a owl:Class ; rdfs:label "Unrelated" .
    :describes a owl:ObjectProperty ; rdfs:domain :Report ;
        rdfs:range [owl:unionOf (:Container :Substance)] .
    :size a owl:DatatypeProperty ; rdfs:domain :Container ; rdfs:range xsd:string ;
        rdfs:label "Size" .
    :phase a owl:DatatypeProperty ; rdfs:domain :Substance ; rdfs:range xsd:string ;
        rdfs:label "Phase" .
    """
    for index in range(siblings):
        body += f':C{index} a owl:Class ; rdfs:subClassOf :Container ; '
        body += f'rdfs:label "Specialised container {index}" .\n'
    return catalog_from_graph(
        Graph().parse(data=PREFIX + body, format="turtle"), "urn:ranking:Report",
    )


def payload():
    return {
        "sources": [{"source_id": "S1", "text": "Size: 3; Phase: powder; specialised: no"}],
        "fields": [{"label": "Size"}, {"label": "Phase"}],
    }


def ranked(siblings=5, *, max_cards=2, budget=100000):
    cards = catalog(siblings)
    dense = {
        iri: 0.1 if iri.endswith((":Container", ":Substance")) else 0.9
        for iri in cards.reachable_class_iris if iri != cards.root_class_iri
    }
    pairs_seen = []

    def scores(pairs):
        pairs_seen.extend(pairs)
        return [-3 if text.splitlines()[0] in {"Container", "Substance"} else 2
                for _, text in pairs]

    policy = {**default_policy(), "seed_limit": siblings + 1, "max_cards": max_cards}
    result = rank_cards(
        cards, payload(), budget, policy=policy, dense_scores=dense, score_pairs=scores,
    )
    return cards, result, pairs_seen


def test_low_direct_score_parents_are_expanded_reranked_and_selected_ahead_of_siblings():
    _, result, seen = ranked()
    parents = {"urn:ranking:Container", "urn:ranking:Substance"}
    assert not parents.intersection(result["seed_iris"])
    assert set(result["selected_iris"]) == parents
    assert {text.splitlines()[0] for _, text in seen} >= {"Container", "Substance"}
    assert all("specialised: no" in query for query, _ in seen)
    assert "urn:ranking:Unrelated" not in {row["iri"] for row in result["candidates"]}
    assert result["score_semantics"] == "retrieval_only_not_source_support"
    assert not any("supported" in row or "class_accepted" in row for row in result["candidates"])


def test_more_siblings_do_not_accumulate_parent_support_or_duplicate_property_weight():
    _, first, _ = ranked(5)
    _, second, _ = ranked(15)
    for iri in ("urn:ranking:Container", "urn:ranking:Substance"):
        a = next(row for row in first["candidates"] if row["iri"] == iri)
        b = next(row for row in second["candidates"] if row["iri"] == iri)
        assert a["hierarchy_support"] == b["hierarchy_support"]
        assert a["field_score"] == b["field_score"]
    assert set(first["selected_iris"]) == set(second["selected_iris"])


def test_specific_types_still_enter_after_broad_coverage_and_selection_is_deterministic():
    _, first, _ = ranked(max_cards=3)
    _, second, _ = ranked(max_cards=3)
    assert first == second
    assert set(first["selected_iris"][:2]) == {"urn:ranking:Container", "urn:ranking:Substance"}
    assert first["selected_iris"][2] in first["seed_iris"]


def test_unbudgeted_ranking_selects_cards_by_relevance_and_card_limit():
    _, result, _ = ranked(budget=None)
    assert len(result["selected_iris"]) == 2
    assert result["card_budget_bytes"] is None
    assert result["card_bytes_used"] == sum(
        row["card_bytes"] for row in result["candidates"]
        if row["iri"] in result["selected_iris"]
    )
    assert all(row["omission_reason"] != "card_exceeds_remaining_budget"
               for row in result["candidates"])


def test_budget_skips_whole_cards_and_reports_omissions_without_truncating_definitions():
    cards, _, _ = ranked()
    cost = len(json.dumps(reading_card(cards.classes["urn:ranking:Container"]),
                          ensure_ascii=False).encode()) + 2
    _, result, _ = ranked(budget=cost)
    selected = [reading_card(cards.classes[iri]) for iri in result["selected_iris"]]
    assert len(selected) == 1
    assert sum(len(json.dumps(card, ensure_ascii=False).encode()) + 2 for card in selected) <= cost
    assert result["card_bytes_used"] <= cost
    assert any(row["omission_reason"] == "card_exceeds_remaining_budget"
               for row in result["candidates"])
    _, empty, _ = ranked(budget=0)
    assert empty["selected_iris"] == []
    assert empty["candidates"]  # Unselected is not absent or disproved.


def test_cycles_multiple_parents_and_shortest_ancestor_distance_are_bounded():
    source = Graph().parse(data=PREFIX + """
        :Report a owl:Class . :A a owl:Class ; rdfs:label "target" ; rdfs:subClassOf :B, :C .
        :B a owl:Class ; rdfs:subClassOf :A, :C . :C a owl:Class .
        :describes a owl:ObjectProperty ; rdfs:domain :Report ; rdfs:range :C .
    """, format="turtle")
    cards = catalog_from_graph(source, "urn:ranking:Report")
    result = rank_cards(cards, {"sources": [{"text": "target"}], "fields": []}, 100000,
                        policy={**default_policy(), "seed_limit": 1})
    assert set(result["selected_iris"]) == {"urn:ranking:A", "urn:ranking:B", "urn:ranking:C"}
    for row in result["candidates"]:
        assert all(item["seed_iri"] != row["iri"] for item in row["parent_sources"])
        if row["iri"] == "urn:ranking:C":
            assert row["parent_sources"][0]["distance"] == 1


@pytest.mark.parametrize("value", [float("nan"), float("inf"), True])
def test_invalid_model_scores_are_failures_not_silent_deterministic_fallback(value):
    cards = catalog()
    with pytest.raises(ValueError, match="invalid_pair_scores"):
        rank_cards(cards, payload(), 100000, score_pairs=lambda pairs: [value] * len(pairs))


def test_no_related_source_does_not_receive_a_global_broad_class_bonus():
    result = rank_cards(catalog(), {"sources": [{"text": "zzzz"}], "fields": []}, 100000)
    assert result["selected_iris"] == []
    assert result["outside_pool_iris"]


def test_semantic_adapter_batches_reuses_vectors_and_propagates_failure(monkeypatch):
    from app.services.llm import semantic_ranking

    class Semantic:
        identity = {"model": "isolated-test"}

        def __init__(self):
            self.observations = []
            self.embedded = []
            self.fail = False

        def embed(self, texts):
            assert len(texts) <= 2
            self.embedded.extend(texts)
            self.observations.append({"operation": "embed"})
            return [[1.0, 0.0] for _ in texts]

        def score_pairs(self, pairs):
            assert len(pairs) <= 2
            if self.fail:
                raise RuntimeError("model_unavailable")
            self.observations.append({"operation": "score_pairs"})
            return [0.5 for _ in pairs]

        def close(self):
            pass

    semantic = Semantic()
    monkeypatch.setattr(semantic_ranking, "configured_semantic_ranking", lambda _: semantic)
    ranker = CardRanker({**default_policy(), "semantic": {"enabled": True, "batch_size": 2}})
    first = ranker.rank(catalog(), payload(), 100000)
    previous = len(semantic.embedded)
    second = ranker.rank(catalog(), payload(), 100000)
    assert first["selected_iris"] == second["selected_iris"]
    assert len(semantic.embedded) == previous + 1  # Only the new source query.
    assert second["operations"] == semantic.observations[len(first["operations"]):]
    semantic.fail = True
    with pytest.raises(RuntimeError, match="model_unavailable"):
        ranker.rank(catalog(), payload(), 100000)
    ranker.close()
