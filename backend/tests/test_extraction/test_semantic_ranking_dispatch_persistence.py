"""The durable owner cannot forget a claimed reservation or its cumulative charge."""

from copy import deepcopy

import pytest

from app.services.document_analysis import execution as execution_service
from app.services.extraction.ontology_guided.semantic_reranker import RankingService
from tests.test_extraction.test_document_analysis_execution_recovery import (
    _claim,
    _create_pending_run,
)
from tests.test_extraction.test_semantic_ranking import RankingModel
from tests.test_extraction.test_semantic_ranking_dispatch_receipts import (
    assert_same_gross_charges,
    paused_before_last_dispatch,
)


@pytest.fixture
def boundary(client, db, analyst_headers, tmp_path, monkeypatch):
    run_id = _create_pending_run(
        client, analyst_headers, tmp_path, monkeypatch, key="dispatch-receipt-persistence",
    )
    store, token = _claim(db, run_id)
    arguments, policy, saved, clock = paused_before_last_dispatch(tmp_path, monkeypatch)
    clock[0] = 0.0
    fingerprint = arguments["run_fingerprint"]
    run = store.get_owned(run_id, "analyst")
    store.update_stage(
        run_id, "analyst", token, expected_revision=run.revision,
        stage="recognition", run_fingerprint=fingerprint,
    )
    db.commit()

    def persist(service):
        execution_service._persist_ranking_state(
            db, store, store.get_owned(run_id, "analyst"), token,
            final_fingerprint=fingerprint,
            state={
                "recognition_run_id": run_id, "run_fingerprint": fingerprint,
                "service": deepcopy(service),
            },
        )

    def restore():
        return execution_service._restore_ranking_state(
            db, store, store.get_owned(run_id, "analyst"), final_fingerprint=fingerprint,
        )["service"]

    return arguments, policy, saved, persist, restore


def test_durable_unused_reservation_claims_once_and_keeps_all_charges(boundary):
    arguments, policy, saved, persist, restore = boundary
    persist(saved)
    assert restore() == saved
    model = RankingModel()
    service = RankingService(policy, model, state=restore(), before_model_hook=persist)
    ready = service.prepare_next_epoch(**arguments)
    assert ready.status == "ready"
    assert model.calls == [("score", 1)]
    claimed = restore()
    assert [item["status"] for item in claimed["dispatch_receipts"]] == [
        "not_dispatched", "dispatch_claimed",
    ]
    assert_same_gross_charges(saved, claimed)

    # The durable claim precedes the actual dispatch. Without its result being
    # committed, recovery treats dispatch as ambiguous and cannot claim again.
    replay_model = RankingModel()
    replay = RankingService(policy, replay_model, state=claimed)
    assert replay.prepare_next_epoch(**arguments).reason == "ranking_call_budget_exhausted"
    assert replay_model.calls == []
    assert_same_gross_charges(saved, replay.snapshot())

    service.commit_epoch(ready)
    persist(service.snapshot())
    committed = restore()
    persist(committed)
    assert restore() == committed
    assert_same_gross_charges(saved, committed)


@pytest.mark.parametrize("mutation", ["drop_claim", "omit_history", "rewrite_receipt"])
def test_persistence_rejects_receipt_history_regression_even_with_higher_costs(
    boundary, mutation,
):
    _arguments, _policy, saved, persist, restore = boundary
    persist(saved)
    claimed = deepcopy(saved)
    claimed["dispatch_receipts"].append({
        **claimed["dispatch_receipts"][0],
        "sequence": 2, "status": "dispatch_claimed", "reason": None,
    })
    persist(claimed)
    changed = deepcopy(claimed)
    changed["costs"]["model_calls"] += 1
    if mutation == "drop_claim":
        changed["dispatch_receipts"].pop()
    elif mutation == "omit_history":
        changed.pop("dispatch_receipts")
    else:
        changed["dispatch_receipts"][0]["reservation_identity"] = "different-request"
    with pytest.raises(execution_service.CheckpointMismatch, match="dispatch receipts"):
        persist(changed)
    assert restore() == claimed


@pytest.mark.parametrize("ledger", ["request_attempts", "record_call_counts", "slot_costs"])
@pytest.mark.parametrize("mutation", ["decrease", "omit_ledger"])
def test_persistence_rejects_individual_budget_ledger_regression(boundary, ledger, mutation):
    _arguments, _policy, saved, persist, restore = boundary
    persist(saved)
    changed = deepcopy(saved)
    assert changed[ledger]
    if mutation == "decrease":
        key = next(iter(changed[ledger]))
        changed[ledger][key] -= 1
    else:
        changed.pop(ledger)
    with pytest.raises(execution_service.CheckpointMismatch, match="cost regressed"):
        persist(changed)
    assert restore() == saved


@pytest.mark.parametrize("missing", [
    ["dispatch_receipts"],
    ["dispatch_receipts", "request_attempts", "record_call_counts", "slot_costs"],
])
def test_legacy_empty_state_accepts_new_receipts_without_retroactive_credits(boundary, missing):
    _arguments, policy, saved, persist, restore = boundary
    legacy = RankingService(policy, RankingModel()).snapshot()
    for key in missing:
        legacy.pop(key)
    persist(legacy)
    assert restore() == legacy
    persist(saved)
    assert restore() == saved
    assert saved["costs"]["model_calls"] == 4
