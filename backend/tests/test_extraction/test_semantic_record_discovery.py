"""Discovery spends recognition calls on relevant source/card pairs only."""

from copy import deepcopy

import pytest

from app.schemas.document_analysis import RunProgress
from app.schemas.retrieval_diagnostics import diagnostic_payload
from app.services.document_analysis import application, current_state
from app.services.extraction.ontology_guided.claim_protocol import ExtractionProfile
from app.services.extraction.ontology_guided.contracts import OntologySnapshot
from app.services.extraction.ontology_guided.record_discovery import (
    RecordDiscoveryPolicy,
    compile_record_schema_card,
)
from app.services.extraction.ontology_guided.record_search import RecordSearch
from app.services.extraction.ontology_guided.records import RecordIndex
from app.services.extraction.ontology_guided.semantic_reranker import (
    RankingPaused,
    RankingPolicy,
    RankingService,
)
from tests.test_extraction.test_contextual_record_executor import setup_contextual
from tests.test_extraction.test_layered_recognition import arguments
from tests.test_extraction.test_ontology_guided_core import _definition

pytest_plugins = ["tests.test_extraction.test_tool_engine_resume"]


class Embeddings:
    identity = {"model": "controlled-semantic-discovery"}

    def __init__(self):
        self.calls = []

    def count_tokens_batch(self, texts):
        return [len(text) for text in texts]

    def count_tokens(self, text):
        return len(text)

    def embed(self, texts):
        self.calls.append(list(texts))
        return [[float("装置" in text or "机械机组" in text), float("部件" in text),
                 float("报告" in text or "无关类型" in text),
                 float(not any(term in text for term in (
                     "装置", "机械机组", "部件", "报告", "无关类型",
                 )))]
                for text in texts]

    def score_pairs(self, pairs):
        return [1.0] * len(pairs)


def service(model=None, **kwargs):
    return RankingService(RankingPolicy(mode="semantic", enable_reranker=False,
                                       **kwargs), model or Embeddings())


def search(tmp_path, *, texts, extra_types=0, limit=1):
    args = arguments(tmp_path, texts)
    index = RecordIndex(args["ir"])
    classes = {"urn:device": _definition("urn:device", "装置"),
               "urn:part": _definition("urn:part", "部件")}
    classes.update({f"urn:irrelevant:{i}": _definition(f"urn:irrelevant:{i}", f"无关类型{i}")
                    for i in range(extra_types)})
    ontology = OntologySnapshot(snapshot_id="semantic-cards", ontology_hash="f" * 64,
                                classes=classes, created_from="frozen_fixture")
    cards = {}
    for iri in classes:
        card = compile_record_schema_card(ontology, class_iris=[iri], analysis_scope_ref="scope",
                                          profile=ExtractionProfile())
        cards[card.schema_card_id] = card
    slots = [([record.record_id], [], None) for record in index.records]
    frontier = RecordSearch(slots, cards=cards, ordinary_cards=list(cards), ontology=ontology,
                            index=index, policy=RecordDiscoveryPolicy(
                                candidate_cards_per_record=limit, minimum_similarity=0.5,
                            ))
    return frontier, index


def test_semantic_synonym_outranks_cover_and_queue_shrinks_without_global_type_removal(tmp_path):
    frontier, index = search(tmp_path, texts=["时间：2026年02月", "机械机组甲。", "装置乙。"])
    ranking = service()
    frontier.prepare(ranking, lambda: None)
    assert frontier.pending == 2
    assert frontier.unselected == 4
    one = frontier.take()
    assert index.by_id[one[0][0]].text == "机械机组甲。"
    assert frontier.pending == 1
    two = frontier.take()
    assert index.by_id[two[0][0]].text == "装置乙。"
    assert one[1] == two[1]
    assert frontier.pending == 0 and frontier.take() is None
    before = deepcopy(ranking.model.calls)
    frontier.prepare(ranking, lambda: None)
    assert frontier.take() is None and ranking.model.calls == before


@pytest.mark.parametrize("extra_types", [0, 137])
def test_added_ontology_types_do_not_expand_recognition_work(tmp_path, extra_types):
    frontier, _ = search(tmp_path, texts=["机械机组甲。", "装置乙。"], extra_types=extra_types)
    ranking = service()
    frontier.prepare(ranking, lambda: None)
    assert frontier.pending == 2
    assert sum(len(batch) for batch in ranking.model.calls) == 4 + extra_types
    assert all(len(row["remaining"]) <= 1 for row in frontier.rows.values())


def test_vector_results_survive_cold_restore_and_low_scores_are_not_examined(tmp_path):
    frontier, _ = search(tmp_path, texts=["时间：2026年02月", "装置甲。"])
    ranking = service()
    persisted = []
    frontier.prepare(ranking, lambda: persisted.append(deepcopy(ranking.snapshot())))
    assert persisted and persisted[-1]["cache"]
    frontier.take()
    rows = frontier.rows.drain()
    restored, _ = search(tmp_path, texts=["时间：2026年02月", "装置甲。"])
    restored.restore(rows)
    restored.prepare(service(), lambda: pytest.fail("completed ranking repeated"))
    assert restored.pending == 0
    diagnostic = restored.diagnostics("semantic")
    assert diagnostic["unselected_groups"] == 1 and diagnostic["admitted_pairs"] == 1
    uncached_work, _ = search(tmp_path, texts=["时间：2026年02月", "装置甲。"])
    model = Embeddings()
    recovered_ranking = RankingService(ranking.policy, model, state=persisted[-1])
    uncached_work.prepare(recovered_ranking, lambda: None)
    assert not model.calls and uncached_work.pending == 1


def test_model_unavailable_and_token_budget_pause_without_exhaustive_fallback(tmp_path):
    frontier, _ = search(tmp_path, texts=["装置甲。"])
    for ranking, reason in [
        (RankingService(RankingPolicy(mode="semantic")), "semantic_ranking_model_unavailable"),
        (service(max_ranking_tokens_per_run=1), "ranking_token_budget_exhausted"),
    ]:
        with pytest.raises(RankingPaused, match=reason):
            frontier.prepare(ranking, lambda: None)
        assert all(not row["ranked"] and not row["remaining"] for row in frontier.rows.values())


def test_explicit_gap_adds_only_one_relevant_pair_and_cannot_repeat(tmp_path):
    frontier, index = search(tmp_path, texts=["装置甲和部件乙。"], limit=1)
    frontier.prepare(service(), lambda: None)
    selected = frontier.take()
    selected_class = frontier.cards[selected[1]].class_iris[0]
    missing = {"urn:part", "urn:device"} - {selected_class}
    rid = index.records[0].record_id
    frontier.admit_feedback(rid, missing, {selected[1]})
    assert frontier.pending == 1
    extra = frontier.take()
    assert frontier.cards[extra[1]].class_iris[0] in missing
    frontier.admit_feedback(rid, missing, {selected[1], extra[1]})
    assert frontier.pending == 0


def test_endpoint_priority_uses_existing_pair_without_expanding_candidates(tmp_path):
    frontier, index = search(tmp_path, texts=["装置甲。", "部件乙。", "装置丙。"])
    frontier.prepare(service(), lambda: None)
    pending, unselected = frontier.pending, frontier.unselected
    assert frontier.prioritize(index.records[1].record_id, {"urn:part"})
    chosen = frontier.take()
    assert chosen[0] == [index.records[1].record_id]
    assert frontier.pending == pending - 1 and frontier.unselected == unselected
    assert not frontier.prioritize(index.records[1].record_id, {"urn:part"})


def test_discovery_rotates_type_opportunities_and_cold_restore_keeps_order(tmp_path):
    texts = ["装置甲。", "装置乙。", "部件丙。"]
    frontier, index = search(tmp_path, texts=texts)
    frontier.prepare(service(), lambda: None)
    first = frontier.take()
    assert first[0] == [index.records[0].record_id]
    snapshot = frontier.rows.drain()
    restored, _ = search(tmp_path, texts=texts)
    restored.restore(snapshot)
    next_source = [index.records[2].record_id]
    assert restored.take()[0] == frontier.take()[0] == next_source
    assert restored.take()[0] == frontier.take()[0] == [index.records[1].record_id]
    assert not restored.pending and not frontier.pending


def test_discovery_rotates_sections_before_repeated_high_score_rows(tmp_path):
    frontier, index = search(tmp_path, texts=["装置甲。", "装置乙。", "装置丙。"])
    # Keep the same type and scores: the first two records belong to one section,
    # the last to another. The second section must receive an early opportunity.
    first_section = index.records[0].section_node_id
    for record in index.records[:2]:
        object.__setattr__(record, "section_node_id", first_section)
    object.__setattr__(index.records[2], "section_node_id", "other-section")
    frontier.prepare(service(), lambda: None)
    assert frontier.take()[0] == [index.records[0].record_id]
    assert frontier.take()[0] == [index.records[2].record_id]
    assert frontier.take()[0] == [index.records[1].record_id]


def test_shared_top_card_does_not_delay_other_selected_type_for_a_full_round(tmp_path):
    texts = ["装置甲与部件乙。", "装置丙与部件丁。", "装置戊与部件己。"]
    frontier, index = search(tmp_path, texts=texts, limit=2)
    frontier.prepare(service(), lambda: None)
    choices = [row["remaining"] for row in frontier.rows.values()]
    assert all(len(items) == 2 for items in choices)
    assert len({items[0]["card_id"] for items in choices}) == 1
    first = frontier.take()
    assert first[0] == [index.records[0].record_id]
    snapshot = frontier.rows.drain()
    restored, _ = search(tmp_path, texts=texts, limit=2)
    restored.restore(snapshot)
    second = frontier.take()
    assert restored.take() == second
    assert second[0] == first[0] and second[1] != first[1]
    assert frontier.pending == restored.pending == 4
    assert frontier.unselected == restored.unselected == 0


def test_real_coordinator_discovers_body_before_cover_and_cold_resume_keeps_paid_results(
    tmp_path, monkeypatch, current_run,
):
    args, factory, requests, hooks = setup_contextual(
        tmp_path, monkeypatch, current_run, texts=["时间：2026年02月", "装置甲。", "部件乙。"],
    )
    store, run, token = current_run
    hooks["ranking_hook"] = lambda state: current_state.persist_ranking(
        store, run, token, run.run_fingerprint, state,
    )
    ranking = service()
    stopped = False
    persist = hooks["model_call_hook"]

    def calls(state):
        nonlocal stopped
        persist(state)
        if any(row["field"] == "model_turn" for row in state.get("result_changes", {}).values()):
            stopped = True

    runner = factory(ranking_service=ranking, progress_hook=lambda _: not stopped)
    first = runner.run(**args, **{**hooks, "model_call_hook": calls})
    first_sources = [unit["text"] for unit in requests[0]["evidence_units"]
                     if unit["fact_eligible"]]
    assert first_sources == ["装置甲。"]
    assert first.graph.progress.record_discovery.unselected_groups == 1
    saved_calls = current_state.restore_calls(store, run, run.run_fingerprint)
    saved_ranking = current_state.restore_ranking(store, run, run.run_fingerprint)
    assert saved_ranking["service"]["cache"]
    assert saved_ranking["service"]["costs"]["embedding_inputs"] > 0
    work = vars(current_state.restore_work(store, run, run.run_fingerprint))
    initial = deepcopy(requests[0])
    result = factory(ranking_service=service()).run(
        **args, **hooks, resume_state=work, model_call_state=saved_calls,
        ranking_state=saved_ranking,
    )
    assert requests.count(initial) == 1
    assert {node.label for node in result.graph.nodes if not node.root} == {"装置甲", "部件乙"}
    assert result.graph.progress.record_discovery.remaining_pairs == 0
    run.progress = result.graph.progress.model_dump(mode="json")
    public_progress = RunProgress.model_validate(application._progress(run))
    assert public_progress.record_discovery == result.graph.progress.record_discovery
    assert diagnostic_payload(result.graph.progress)["record_discovery"]["unselected_groups"] == 1


def test_discovery_timeout_stops_before_embedding_and_resets_shared_deadline(
    tmp_path, monkeypatch,
):
    from app.services.extraction.ontology_guided import semantic_reranker

    frontier, _ = search(tmp_path, texts=["装置甲。"])
    model = Embeddings()
    clock, deadlines = [0.0], []
    monkeypatch.setattr(semantic_reranker.time, "monotonic", lambda: clock[0])
    model.set_deadline = deadlines.append

    def slow_count(texts):
        clock[0] = 61.0
        return [len(text) for text in texts]

    model.count_tokens_batch = slow_count
    with pytest.raises(RankingPaused, match="ranking_timeout"):
        frontier.prepare(service(model), lambda: None)
    assert deadlines == [60.0, None] and not model.calls
    assert all(not row["ranked"] for row in frontier.rows.values())
