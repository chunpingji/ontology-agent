"""Post-reading planning keeps bounded source clues and idempotent current work."""

from copy import deepcopy

from test_coreference import case as case
from test_coreference import runner
from test_grouped_alignment import engine_fixture
from test_lookup import lookup_fixture as lookup_fixture
from test_lookup import responses
from test_lookup import runner as lookup_runner

from app.services.document_harness.referents import run_referent_alignment
from app.services.document_harness.source import Window, reference
from app.services.document_harness.work import dependency_hash
from app.services.document_harness.work_execution import plan_coreference_work, plan_graph_work


def test_two_lookup_chains_share_paid_requests_but_keep_local_continuations(lookup_fixture):
    checkpoints = []
    engine, _ = lookup_runner(
        lookup_fixture, responses, save=lambda c: checkpoints.append(deepcopy(c)),
    )
    first = engine.windows[0]
    engine.windows.append(Window("overlap", deepcopy(first.sources), deepcopy(first.fields),
                                 list(first.primary_ids)))
    engine.run()
    lookup_states = [row["lookup_work"] for change in checkpoints
                     for row in change.get("windows", {}).values() if row.get("lookup_work")]
    assert len(lookup_states) >= 4
    assert all(set(work) <= {"draft_call_key", "feedback"} for work in lookup_states)
    assert len({work["draft_call_key"] for work in lookup_states}) == 1
    assert len(engine._memory_calls.answers) == 2  # draft + refine, each shared by two targets.
    assert all(row["reading_state"] == "complete" for row in engine.state["windows"].values())
    assert all(not row.get("lookup_work") for row in engine.state["windows"].values())
    assert engine.state["window_entities"][first.id] == engine.state["window_entities"]["overlap"]
    assert len(engine.state["entities"]) == 3  # document + two physical mentions, not four.


def test_symmetric_cues_merge_before_review_and_replanning_preserves_paid_work(case):
    engine = runner(case, lambda *_: None)
    engine.state["work"] = {}
    entities = engine.state["entities"]
    engine.state["reference_cues"] = {
        key: {"id": key, "subject_id": left, "reference": entities[right]["referent"],
              "relation_label": None, "kind": "explicit_reference", "direction": "outgoing",
              "evidence": [entities[left]["referent"]], "polarity": "positive", "conditions": []}
        for key, left, right in [("forward", "0", "1"), ("backward", "1", "0")]
    }
    plan_coreference_work(engine)
    assert len(engine.state["work"]) == 1
    work = next(iter(engine.state["work"].values()))
    assert len(work["input"]["clue_refs"]) == 2
    work.update(status="done", output_ids=["decision"], last_call_key="paid",
                applied_dependency_hash=work["dependency_hash"])
    before = deepcopy(work)
    plan_coreference_work(engine)
    assert engine.state["work"][work["id"]] == before


def test_same_names_alone_never_create_coreference_work(case):
    engine = runner(case, lambda *_: None)
    engine.state["work"] = {}
    for entity in engine.state["entities"].values():
        entity["label"] = "same"
    plan_coreference_work(engine)
    assert not engine.state["work"]


def test_graph_replanning_keeps_property_menu_progress_and_changed_labels_invalidate(case):
    engine = runner(case, lambda *_: None)
    ref = engine.state["entities"]["0"]["referent"]
    engine.state["fields"] = {"f": {"id": "f", "label": "编号", "value": ref["text"],
        "missing": False, "evidence": [ref], "value_evidence": [ref]}}
    engine.state["entities"]["0"]["field_ids"] = ["f"]
    plan_graph_work(engine)
    work = next(w for w in engine.state["work"].values()
                if w["kind"] == "property_alignment")
    work.update(processed_predicate_iris=["urn:seen"], output_ids=["property"],
                last_call_key="paid")
    before = deepcopy(work)
    plan_graph_work(engine)
    assert engine.state["work"][work["id"]] == before
    changed = deepcopy(engine.state)
    changed["fields"]["f"]["label"] = "批号"
    assert dependency_hash(work["kind"], work["input"], changed, engine.catalog,
                           engine.execution_policy) != work["dependency_hash"]
    changed = deepcopy(engine.state)
    changed["entities"]["0"]["state"] = "unresolved"
    assert dependency_hash(work["kind"], work["input"], changed, engine.catalog,
                           engine.execution_policy) == work["dependency_hash"]


def test_member_refinement_rebinds_all_window_memberships_and_observations(lookup_fixture):
    engine, window, _ = engine_fixture(lookup_fixture)
    old = engine.state["window_entities"][window.id]["ids"][0]
    engine.state["window_entities"]["overlap"] = {"ids": [old]}
    engine.state["observations"] = {"shared": {"id": "shared", "label": "共同观察",
        "reason": "归属待定", "kind": "entity", "candidate_subject_ids": [old], "evidence": []}}
    run_referent_alignment(engine, window)
    members = engine.state["window_entities"][window.id]["ids"]
    assert len(members) == 2 and set(engine.state["entities"][old]["refined_member_ids"]) == set(members)
    assert engine.state["window_entities"]["overlap"]["ids"] == members
    assert engine.state["observations"]["shared"]["candidate_subject_ids"] == members


def test_scoped_identifier_candidates_require_local_scope_proof(lookup_fixture):
    engine, window, _ = engine_fixture(lookup_fixture)
    original = next(e for e in engine.state["entities"].values() if e["id"] != "document")
    iri = original["class_iri"]
    capability = {"class_iri": iri, "identifier_namespace": "urn:namespace",
                  "lookup_key_groups": [{"property_iris": [iri.replace("Object", "code")],
                                          "scope_property_iris": ["urn:scope"]}]}
    unit = engine.ir.evidence_units[0]
    first = reference(engine.ir, unit.evidence_id, unit.text.index("A1"), unit.text.index("A1") + 2)
    scope = reference(engine.ir, unit.evidence_id, unit.text.index("trial"),
                      unit.text.index("trial") + 5)
    identities = [{"property_iri": iri.replace("Object", "code"), "value": "A1", "quote": first},
                  {"property_iri": "urn:scope", "value": "trial", "quote": scope}]
    engine.state["entities"] = {"document": engine.state["entities"]["document"], **{
        key: {**deepcopy(original), "id": key, "state": "accepted", "identity_binding": {
            "group_id": "bound", "identifiers": deepcopy(identities),
        }} for key in ("left", "right")
    }}
    engine.state["referent_work"] = {"bound": {"key_context": {
        "lookup_capabilities": [capability],
    }}}
    plan_coreference_work(engine)
    assert len(engine.state["work"]) == 1
    assert len(next(iter(engine.state["work"].values()))["input"]["clue_refs"]) == 2
    del engine.state["work"]
    engine.state["entities"]["right"]["identity_binding"]["identifiers"].pop()
    plan_coreference_work(engine)
    assert not engine.state["work"]
