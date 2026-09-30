"""A correct identifier multiset cannot hide swapped ownership or a merged referent."""

from copy import deepcopy

from app.evaluation.harness_lookup_probe import score


def example():
    state = {"entities": {}, "fields": {}}
    for index, value in enumerate(("644", "642")):
        ref = {"source_id": "S1", "text": value, "start": index * 4, "end": index * 4 + 3}
        state["entities"][value] = {"id": value, "label": value, "state": "candidate",
                                    "referent": ref, "field_ids": [value]}
        state["fields"][value] = {"value": value, "value_evidence": [ref]}
    return state


def test_correct_independent_members_are_scored_without_identity_or_fact_claim():
    result = score(example(), [["644"], ["642"]])
    assert result["count_ok"] and result["identifier_ownership_ok"]
    assert result["nonoverlapping_anchors"] and result["accepted_facts"] == 0


def test_correct_multiset_with_swapped_ownership_fails():
    state = deepcopy(example())
    state["entities"]["644"]["field_ids"] = ["642"]
    state["entities"]["642"]["field_ids"] = ["644"]
    result = score(state, [["644"], ["642"]])
    assert result["count_ok"] and not result["identifier_ownership_ok"]


def test_merged_referent_does_not_count_as_two_members():
    state = example()
    state["entities"].pop("642")
    state["entities"]["644"].update(field_ids=["644", "642"], referent={
        "source_id": "S1", "text": "644/642", "start": 0, "end": 7,
    })
    result = score(state, [["644"], ["642"]])
    assert not result["count_ok"] and not result["identifier_ownership_ok"]


def test_single_explicit_object_allows_a_separate_identifier_field():
    state = {"entities": {"e1": {
        "id": "e1", "label": "一个车间", "state": "candidate", "field_ids": ["f1"],
        "referent": {"source_id": "S1", "text": "一个车间", "start": 6, "end": 10},
    }}, "fields": {"f1": {
        "value": "644/642", "value_evidence": [
            {"source_id": "S1", "text": "644/642", "start": 17, "end": 24},
        ],
    }}}
    result = score(state, [["644/642"]])
    assert result["count_ok"] and result["identifier_ownership_ok"]
