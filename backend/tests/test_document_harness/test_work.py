"""Stable work identity, bounded enrichment and dependency-driven activation."""

from copy import deepcopy
from types import SimpleNamespace

from app.services.document_harness.work import DEFAULT_POLICY, WorkIndex, make_work
from app.services.document_harness.work_execution import invalidate_changes


def ref(source="s1", text="Alpha"):
    return {"source_id": source, "start": 0, "end": len(text), "text": text}


def fixture_state():
    return {"entities": {
        key: {"id": key, "referent": ref(key, key), "class_iri": "urn:Thing",
              "state": "accepted", "type_evidence": [ref(key, key)]}
        for key in ("a", "b", "unrelated")
    }, "work": {}}


def seed():
    return {
        "subject_id": "a", "object_ids": ["b"], "predicate_iri": "urn:uses",
        "region_id": "region:one", "clue_refs": [ref()], "required_context_refs": [],
        "priority": "structural", "origin_ids": ["hint:one"],
        "polarity_hint": "positive", "condition_hints": [], "endpoint_hypothesis": False,
    }


def engine(state):
    return SimpleNamespace(
        state=state, catalog=SimpleNamespace(ontology_hash="ontology"),
        execution_policy=deepcopy(DEFAULT_POLICY), work_index=WorkIndex(state),
    )


def work(state, data=None, kind="relation_alignment"):
    value = engine(state)
    return make_work(kind, seed() if data is None else data, state,
                     value.catalog, value.execution_policy)


def test_parent_child_windows_and_source_origins_reuse_the_same_work():
    state = fixture_state()
    original = work(state, {**seed(), "window_id": "parent"})
    original.update(status="done", output_ids=["fact"], last_call_key="paid")
    state["work"][original["id"]] = original
    child = work(state, {**seed(), "window_id": "child", "origin_ids": ["hint:child"]})
    assert child["id"] == original["id"]
    assert child["dependency_hash"] == original["dependency_hash"]
    assert child["status"] == "done"
    assert child["last_call_key"] == "paid"
    assert child["output_ids"] == ["fact"]


def test_confirmation_is_a_gate_but_type_evidence_changes_the_semantic_dependency():
    state = fixture_state()
    state["entities"]["a"]["state"] = "candidate"
    original = work(state)
    state["entities"]["a"]["state"] = "accepted"
    assert work(state)["dependency_hash"] == original["dependency_hash"]
    state["entities"]["a"]["type_evidence"] = [ref("later", "new type proof")]
    changed = work(state)
    assert changed["id"] == original["id"]
    assert changed["dependency_hash"] != original["dependency_hash"]


def test_unrelated_entity_change_does_not_reactivate_completed_work():
    state = fixture_state()
    original = work(state)
    original["status"] = "done"
    state["work"][original["id"]] = original
    changes = {"entities": {"unrelated": {**state["entities"]["unrelated"], "state": "unresolved"}}}
    invalidate_changes(engine(state), changes)
    assert "work" not in changes


def test_confirmed_endpoints_release_stored_proof_without_new_work():
    state = fixture_state()
    state["entities"]["a"]["state"] = "candidate"
    original = work(state)
    original.update(status="done", output_ids=["fact"])
    state["work"][original["id"]] = original
    proof = {"method": "llm", "semantic_verdict": "accepted", "dependency_hash": "semantic"}
    state["relations"] = {"fact": {
        "id": "fact", "subject_id": "a", "object_id": "b", "state": "unresolved",
        "verification": proof,
    }}
    changes = {"entities": {"a": {**state["entities"]["a"], "state": "accepted"}}}
    invalidate_changes(engine(state), changes)
    assert changes["relations"]["fact"]["state"] == "accepted"
    assert changes["relations"]["fact"]["verification"] == proof
    assert "work" not in changes


def test_waiting_coreference_activates_when_endpoints_become_eligible():
    state = fixture_state()
    state["entities"]["a"]["state"] = "candidate"
    original = work(state, {"left_mention_id": "a", "right_mention_id": "b",
                            "clue_refs": [ref()]}, "coreference_review")
    original.update(status="waiting", reason_code="type_or_constraint_unresolved")
    state["work"][original["id"]] = original
    changes = {"entities": {"a": {**state["entities"]["a"], "state": "accepted"}}}
    invalidate_changes(engine(state), changes)
    assert changes.get("work", {}).get(original["id"], original)["status"] == "ready"


def test_replanning_original_seed_preserves_one_used_context_expansion():
    state = fixture_state()
    original = work(state)
    expanded = work(state, {**seed(), "required_context_refs": [ref("s2", "context")]})
    expanded.update(expansion_basis_hash=original["dependency_hash"], expansions_used=1,
                    status="waiting", reason_code="insufficient_context")
    state["work"][expanded["id"]] = expanded
    resumed = work(state)
    assert resumed["id"] == original["id"]
    assert resumed["expansion_basis_hash"] == original["dependency_hash"]
    assert resumed["expansions_used"] == 1
    assert resumed["input"]["required_context_refs"] == expanded["input"]["required_context_refs"]
    assert resumed["status"] == "waiting"


def test_new_real_clue_invalidates_prior_proof_and_renews_the_expansion_basis():
    state = fixture_state()
    original = work(state)
    original.update(status="done", expansions_used=1, output_ids=["fact"])
    state["work"][original["id"]] = original
    state["relations"] = {"fact": {
        "id": "fact", "subject_id": "a", "object_id": "b", "state": "accepted",
        "verification": {"method": "llm", "semantic_verdict": "accepted"},
    }}
    changes = {"hints": {"negative": {
        "id": "negative", "subject_id": "a", "object_id": "b",
        "polarity": "negative", "conditions": [], "evidence": [ref("new", "not uses")],
    }}}
    invalidate_changes(engine(state), changes)
    changed = changes["work"][original["id"]]
    assert changed["status"] == "ready"
    assert changed["expansions_used"] == 0
    assert changed["dependency_hash"] != original["dependency_hash"]
    assert changes["relations"]["fact"]["state"] == "unresolved"
    assert changes["relations"]["fact"]["verification"] is None
