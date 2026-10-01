"""Experimental post-type correction cannot silently keep the aggregate owner."""

from copy import deepcopy
from types import SimpleNamespace

import jsonschema
import pytest

from app.evaluation.harness_key_realign_probe import (
    FAC,
    REVIEW_INSTRUCTIONS,
    REVIEW_STAGE,
    KeyRealignment,
    ProbeEngine,
    final_score,
    key_context,
    replace_partition,
)
from app.models.mock_data import MockProductionArea
from app.services.document_harness.protocols import INSTRUCTIONS
from tests.test_document_harness.test_lookup import (
    NS,
    discovery,
    quote,
    runner,
)
from tests.test_document_harness.test_lookup import (
    lookup_fixture as source_lookup_fixture,
)


@pytest.fixture
def lookup_fixture(db, tmp_path):
    return source_lookup_fixture.__wrapped__(db, tmp_path)


def prefix(fixture):
    engine, _ = runner(fixture, lambda *_: {
        **discovery(["A1/A2"]), "lookup_requests": [],
    })
    engine.run()
    for key, entity in engine.state["entities"].items():
        if key != "document":
            entity.update(class_iri=NS + "Object", class_label="Object", state="candidate")
    engine.state["cursor"]["main"]["stage"] = "entity_review"
    return engine


def answer(names=("A1", "A2")):
    return {**discovery(names), "source_suggestions": [], "interpretations": {"E1": {
        "identifier_values": [quote(n) for n in names],
        "grouping": "multiple_objects" if len(names) > 1 else "single_object",
        "reason": "Shared source expression supports these identifier readings",
    }}}


def probe(fixture, monkeypatch, invoke, condition="B"):
    original = prefix(fixture)
    monkeypatch.setitem(INSTRUCTIONS, REVIEW_STAGE, REVIEW_INSTRUCTIONS)
    return ProbeEngine(
        ir=original.ir, catalog=original.catalog, state=original.state,
        invoke=invoke, save=lambda _: None, should_stop=lambda: False,
        key_lookup=fixture[2], condition=condition, trace=lambda *_: None,
    )


def test_actual_mapped_keys_replace_aggregate_and_require_fresh_type_review(
    lookup_fixture, monkeypatch,
):
    def invoke(stage, payload, schema):
        assert stage == REVIEW_STAGE
        assert sorted(k["value"] for c in payload["key_candidates"]
                      for k in c["key_occurrences"]) == ["A1", "A2"]
        result = answer()
        jsonschema.validate(result, schema)
        return result
    engine = probe(lookup_fixture, monkeypatch, invoke)
    old = set(engine.state["entities"]) - {"document"}
    engine.entity_review(engine.windows[0])
    assert not old.intersection(engine.state["entities"])
    entities = [e for k, e in engine.state["entities"].items() if k != "document"]
    assert [e["label"] for e in entities] == ["A1", "A2"]
    assert all(e["class_iri"] is None and e["state"] == "candidate" for e in entities)
    assert engine.state["cursor"]["main"]["stage"] == "type_alignment"
    assert engine.state["cursor"]["main"]["reading_windows"]["saved"] == 1
    orphan = next(f for f in engine.state["fields"].values() if f["value"] == "A1/A2")
    assert all(orphan["id"] not in e["field_ids"] for e in entities)
    assert not engine.state.get("properties") and not engine.state.get("relations")


def test_source_free_control_is_not_reported_as_no_match(lookup_fixture, monkeypatch):
    def invoke(stage, payload, schema):
        assert payload["key_candidates"] == []
        assert payload["source_status"] == {"status": "not_provided_control"}
        return answer()
    engine = probe(lookup_fixture, monkeypatch, invoke, condition="C")
    before = len(lookup_fixture[3])
    engine.entity_review(engine.windows[0])
    assert [x[0] for x in lookup_fixture[3][before:]] == ["capabilities"]


def test_literal_recall_preserves_whole_and_member_competitors(
    lookup_fixture, db, monkeypatch,
):
    db.add(MockProductionArea(code="A1/A2", label="Composite", iri="urn:source:whole"))
    db.commit()
    def invoke(stage, payload, schema):
        assert sorted(k["value"] for c in payload["key_candidates"]
                      for k in c["key_occurrences"]) == ["A1", "A1/A2", "A2"]
        return answer(["A1/A2"])
    engine = probe(lookup_fixture, monkeypatch, invoke)
    engine.entity_review(engine.windows[0])
    assert len(engine.state["entities"]) == 2


def test_source_substring_is_only_an_unverified_hint(lookup_fixture):
    _, catalog, lookup, *_ = lookup_fixture
    caps = lookup("capabilities", catalog, [NS + "Object"])["capabilities"]
    response = lookup("query", catalog, {"capabilities": caps, "queries": [{
        "query_id": "KQ1", "class_iri": NS + "Object", "mapping_ids": [caps[0]["mapping_id"]],
    }]})
    window = SimpleNamespace(sources=[{"source_id": "S1", "text": "XA1; A1/A2"}])
    candidates, _ = key_context(window, caps, response)
    occurrences = [q for c in candidates for q in c["key_occurrences"]]
    assert len(occurrences) == 3
    assert all(q["boundary_status"] == "not_checked" for q in occurrences)


def test_fake_source_reference_cannot_be_applied(lookup_fixture):
    engine = prefix(lookup_fixture)
    result = answer()
    result["source_suggestions"] = [{
        "local_id": "A1", "candidate_ids": ["invented"], "reason": "no actual source",
    }]
    with pytest.raises(ValueError, match="invalid_candidate_reference"):
        replace_partition(engine, engine.windows[0], KeyRealignment.model_validate(result), {})


def test_existing_assertions_cannot_be_silently_deleted(lookup_fixture):
    engine = prefix(lookup_fixture)
    old = next(k for k in engine.state["entities"] if k != "document")
    engine.state["properties"] = {"p": {"subject_id": old}}
    before = deepcopy(engine.state)
    with pytest.raises(ValueError, match="after_assertions_forbidden"):
        replace_partition(engine, engine.windows[0], KeyRealignment.model_validate(answer()), {})
    assert engine.state == before


def test_final_score_rejects_swapped_accepted_property_ownership():
    cls = FAC + "ProductionArea"
    catalog = SimpleNamespace(classes={cls: SimpleNamespace(parent_iris=())})
    state = {"entities": {}, "fields": {}, "properties": {}}
    refs = [{"source_id": "S1", "text": value, "start": i * 4, "end": i * 4 + 3}
            for i, value in enumerate(("644", "642"))]
    for i, ref in enumerate(refs):
        key = f"e{i}"
        state["entities"][key] = {"id": key, "label": ref["text"], "role": "object",
                                  "class_iri": cls, "state": "accepted", "referent": ref,
                                  "field_ids": []}
        wrong = refs[1 - i]
        state["properties"][key] = {
            "subject_id": key, "predicate_iri": FAC + "areaIdentifier", "state": "accepted",
            "value": wrong["text"], "value_evidence": [wrong],
        }
    result = final_score(state, catalog, [["644"], ["642"]])
    assert result["accepted_entity_count"] == 2
    assert not result["accepted_identifiers_ok"]
