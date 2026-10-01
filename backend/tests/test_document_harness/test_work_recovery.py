"""Paid requests survive interruption independently of applying their current work."""

from copy import deepcopy

import pytest
from docx import Document
from test_work_evidence import (
    add_alignment,
    add_interpretation,
    add_review,
    create_engine,
    proposal,
    relation,
    review_answer,
    seed,
)
from test_work_evidence import evidence_case as evidence_case

from app.services.document_harness.controller import Engine, Paused
from app.services.document_harness.ontology import PropertyCard
from app.services.document_harness.source import reference
from app.services.document_harness.work import digest, make_work
from app.services.extraction.document_ir import build_document_ir
from app.services.extraction.docx_structure import parse_docx_structure


class MemoryRepository:
    """Exercise the same prepare/request/invoke/save ports without touching a live run."""

    def __init__(self, state, respond, *, pause_after=None):
        self.state = deepcopy(state)
        self.respond = respond
        self.pause_after = pause_after
        self.requests, self.answers = {}, {}
        self.paid, self.reads = [], []

    def save(self, changes):
        for domain, rows in changes.items():
            current = self.state.setdefault(domain, {})
            for key, value in rows.items():
                if value is None:
                    current.pop(key, None)
                else:
                    current[key] = deepcopy(value)

    def prepare(self, stage, payload, schema, target):
        assert not self.state["cursor"]["main"].get("active_batches")
        key = digest({"stage": stage, "payload": payload, "schema": schema})
        batch = {"call_key": key, "stage": stage, **deepcopy(target)}
        batch["batch_id"] = digest(batch)
        self.requests[key] = deepcopy({"payload": payload, "schema": schema})
        for row in target["targets"]:
            if row["domain"] == "work":
                saved = self.state["work"][row["id"]]
                assert saved["dependency_hash"] == row["dependency_hash"]
                saved["last_call_key"] = key
        self.state["cursor"]["main"]["active_batches"] = {batch["batch_id"]: deepcopy(batch)}
        return batch

    def request(self, batch):
        self.reads.append(batch["call_key"])
        return deepcopy(self.requests[batch["call_key"]])

    def invoke(self, batch):
        assert self.state["cursor"]["main"]["active_batches"][batch["batch_id"]] == batch
        key = batch["call_key"]
        if key not in self.answers:
            request = self.request(batch)
            answer = self.respond(batch["stage"], request["payload"], request["schema"])
            self.answers[key] = deepcopy(answer)
            self.paid.append((batch["stage"], deepcopy(request["payload"])))
            if self.pause_after == batch["stage"]:
                self.pause_after = None
                raise Paused()
        return deepcopy(self.answers[key])


def initial(case):
    engine = create_engine(case, lambda *args: pytest.fail("unexpected direct model call"))
    engine.state["windows"] = {
        window.id: {**Engine.window_row(window, [i]), "entity_phase": "done",
                    "reading_state": "complete"}
        for i, window in enumerate(engine.windows)
    }
    return engine


def resume(case, repo, *, budget=None):
    engine = Engine(
        ir=case[0], catalog=case[1], state=repo.state,
        invoke=lambda *args: pytest.fail("prepared path bypassed"), save=repo.save,
        should_stop=lambda: False, max_request_bytes=budget,
        prepare_batch=repo.prepare, invoke_prepared=repo.invoke, batch_request=repo.request,
    )
    engine.run()
    assert engine.state == repo.state
    return engine


def respond(stage, payload, schema):
    if stage == "relation_alignment":
        return proposal(payload)
    assert stage == "evidence_review"
    return review_answer(payload)


def test_paid_relation_alignment_resumes_exact_request_without_second_payment(evidence_case):
    engine = initial(evidence_case)
    key = add_alignment(engine, seed(engine))
    repo = MemoryRepository(engine.state, respond, pause_after="relation_alignment")
    paused = resume(evidence_case, repo)
    batch = deepcopy(next(iter(repo.state["cursor"]["main"]["active_batches"].values())))
    request = deepcopy(repo.requests[batch["call_key"]])
    assert not paused.state.get("relations")
    assert repo.state["work"][key]["status"] == "ready"
    assert batch["source_bindings"]
    finished = resume(evidence_case, repo)
    assert repo.requests[batch["call_key"]] == request
    assert batch["call_key"] in repo.reads
    assert [stage for stage, _ in repo.paid] == ["relation_alignment", "evidence_review"]
    assert not finished.state["cursor"]["main"]["active_batches"]
    assert len(finished.state["relations"]) == 1
    assert next(iter(finished.state["relations"].values()))["state"] == "accepted"
    assert finished.state["work"][key]["status"] == "done"


def test_property_menu_shard_is_replayed_then_remaining_menu_runs_once(evidence_case, monkeypatch):
    catalog = evidence_case[1]
    card = catalog.classes["urn:gate:Thing"]
    card = card.model_copy(update={"properties": tuple(PropertyCard(
        iri=f"urn:gate:p{i}", label=f"Property {i}", description="Literal value",
        domain_class_iris=(card.iri,), datatype_iris=("http://www.w3.org/2001/XMLSchema#string",),
    ) for i in range(4))})
    catalog = catalog.model_copy(update={"classes": {**catalog.classes, card.iri: card}})
    case = (evidence_case[0], catalog, evidence_case[2])
    engine = initial(case)
    source = engine.ir.evidence_units[1]
    ref = reference(engine.ir, source.evidence_id, 0, len(source.text))
    field = {"id": "field", "label": "Literal", "value": source.text, "missing": False,
             "evidence": [ref], "value_evidence": [ref], "alias": "", "source_aliases": []}
    engine.commit({"entities": {"a": {**engine.state["entities"]["a"], "field_ids": ["field"]}},
                   "fields": {"field": field}})
    work = make_work("property_alignment", {"subject_id": "a", "field_id": "field"},
                     engine.state, engine.catalog, engine.execution_policy)
    engine.commit({"work": {work["id"]: work}})

    def request_size(stage, payload, schema):
        return 100 * len(payload["card"]["properties"]) if stage == "property_alignment" else 10

    monkeypatch.setattr("app.services.document_harness.controller.request_size", request_size)

    def model(stage, payload, schema):
        if stage == "property_alignment":
            return {"properties": {key: {
                "mappings": [{"predicate_iri": p["iri"], "value_component": "whole",
                              "value_quote": None, "confidence": 0.99}
                             for p in payload["card"]["properties"]],
                "reason": "Literal properties",
            } for key in payload["property_field_ids"]}}
        assert stage == "evidence_review"
        return review_answer(payload)

    repo = MemoryRepository(engine.state, model, pause_after="property_alignment")
    resume(case, repo, budget=250)
    batch = deepcopy(next(iter(repo.state["cursor"]["main"]["active_batches"].values())))
    paid_menu = repo.requests[batch["call_key"]]["payload"]["card"]["properties"]
    assert len(paid_menu) == 2 and not repo.state.get("properties")
    finished = resume(case, repo, budget=250)
    menus = [[p["iri"] for p in payload["card"]["properties"]]
             for stage, payload in repo.paid if stage == "property_alignment"]
    assert len(menus) == 2
    assert sorted(iri for menu in menus for iri in menu) == [f"urn:gate:p{i}" for i in range(4)]
    assert len(finished.state["properties"]) == 4
    assert all(row["state"] == "accepted" for row in finished.state["properties"].values())
    assert finished.state["work"][work["id"]]["status"] == "done"


def test_paid_group_interpretation_resumes_before_independent_review(evidence_case):
    engine = initial(evidence_case)
    key = add_interpretation(engine, relation(engine, group=True,
                                             participation="unknown", timing="unspecified"))

    def model(stage, payload, schema):
        if stage == "group_interpretation":
            return {"verdict": "supported", "participation": "all", "selection": "unspecified",
                    "timing": "parallel", "reason": "Explicit participation",
                    "evidence": [source["source_id"] for source in payload["sources"]]}
        assert stage == "evidence_review"
        return review_answer(payload)

    repo = MemoryRepository(engine.state, model, pause_after="group_interpretation")
    resume(evidence_case, repo)
    assert repo.state["relation_groups"]["r"]["participation"] == "unknown"
    assert repo.state["work"][key]["expansions_used"] == 1
    finished = resume(evidence_case, repo)
    assert [stage for stage, _ in repo.paid] == ["group_interpretation", "evidence_review"]
    assert len(finished.state["relation_groups"]) == 1
    group = next(iter(finished.state["relation_groups"].values()))
    work = next(work for work in finished.state["work"].values()
                if work["kind"] == "group_interpretation")
    assert group["id"] == work["input"]["group_id"]
    assert work["output_ids"] == [group["id"]] and work["status"] == "done"
    assert group["state"] == "accepted" and group["timing_state"] == "accepted"


def test_paid_answer_survives_apply_failure_and_reuses_current_batch_on_continue(evidence_case):
    engine = initial(evidence_case)
    key = add_alignment(engine, seed(engine))
    repo = MemoryRepository(engine.state, respond)
    original_save = repo.save
    fail = True

    def save(changes):
        nonlocal fail
        if fail and changes.get("relations"):
            fail = False
            raise RuntimeError("simulated application transaction failure")
        original_save(changes)

    repo.save = save
    with pytest.raises(RuntimeError, match="application transaction"):
        resume(evidence_case, repo)
    assert repo.state["work"][key]["status"] == "failed"
    assert repo.state["cursor"]["main"]["active_batches"]
    assert not repo.state.get("relations")
    finished = resume(evidence_case, repo)
    assert finished.state["work"][key]["status"] == "done"
    assert [stage for stage, _ in repo.paid] == ["relation_alignment", "evidence_review"]


def test_discovery_capacity_splits_before_parent_expensive_work_and_reuses_entities(
    tmp_path, evidence_case,
):
    doc = Document()
    for i in range(12):
        doc.add_paragraph(f"Object E{i:02d} has value V{i:02d}.")
    path = tmp_path / "capacity.docx"
    doc.save(path)
    ir = build_document_ir(path, parse_docx_structure(path))
    catalog = evidence_case[1]
    card = catalog.classes["urn:gate:Thing"]
    card = card.model_copy(update={"properties": (PropertyCard(
        iri="urn:gate:value", label="value", description="Literal observation",
        domain_class_iris=(card.iri,), datatype_iris=("http://www.w3.org/2001/XMLSchema#string",),
    ),)})
    catalog = catalog.model_copy(update={"classes": {**catalog.classes, card.iri: card}})
    calls = []

    def invoke(stage, payload, schema):
        current = engine.state["cursor"]["main"]["entity_window_id"]
        calls.append((stage, current, deepcopy(payload)))
        if stage == "discover":
            entities = []
            primary = {r["source_id"] for r in payload["reading_scope"]}
            for source in payload["sources"]:
                if source["source_id"] not in primary:
                    continue
                if not source["text"].startswith("Object E"):
                    continue
                name = source["text"].split()[1]
                value = source["text"].split()[-1].rstrip(".")

                def quote(text):
                    return {"source_id": source["source_id"], "text": text, "occurrence": 0}

                entities.append({
                    "local_id": name, "name": quote(name), "anchor": quote(name),
                    "role": "object", "evidence": [source["source_id"]], "field_ids": [],
                    "source_fields": [{"label": quote("value"), "value": quote(value)}],
                })
            return {"entities": entities, "document_field_ids": [], "document_source_fields": [],
                    "unowned_fields": [], "relation_hints": [], "complete": True}
        if stage == "type_alignment":
            return {"entities": {row["entity_id"]: {
                "class_iri": card.iri, "confidence": 0.99, "evidence": row["evidence"],
                "reason": "Explicit object",
            } for row in payload["entities"]}}
        if stage == "property_alignment":
            return {"properties": {key: {"mappings": [{
                "predicate_iri": "urn:gate:value", "value_component": "whole",
                "value_quote": None, "confidence": 0.99,
            }], "reason": "Exact value"} for key in payload["property_field_ids"]}}
        assert stage in {"entity_review", "evidence_review"}
        answer = review_answer(payload)
        if stage == "entity_review":
            answer.pop("type_concerns")
            for candidate in payload["candidates"]:
                answer["judgments"][candidate["id"]]["evidence"] = candidate["evidence"]
        return answer

    engine = Engine(ir=ir, catalog=catalog, state={}, invoke=invoke, save=lambda changes: None,
                    should_stop=lambda: False)
    parent = engine.windows[0].id
    engine.run()
    row = engine.state["windows"][parent]
    assert len(row["children"]) == 2 and row["entity_phase"] == "done"
    assert [stage for stage, _, _ in calls[:3]] == ["discover"] * 3
    # Parent discoveries retain ownership; children do not retype them.
    assert row["reading_state"] == "split"
    assert len(engine.state["entities"]) == 13
    assert len(engine.state["fields"]) == 12
    assert len(engine.state["properties"]) == 12
    typed = [entity["anchor"]["text"] for stage, _, payload in calls if stage == "type_alignment"
             for entity in payload["entities"]]
    assert sorted(typed) == [f"E{i:02d}" for i in range(12)]
    assert engine.state["cursor"]["main"]["reading"] == {
        "total_characters": sum(len(unit.text) for unit in ir.evidence_units),
        "processed_characters": sum(len(unit.text) for unit in ir.evidence_units),
        "complete_characters": sum(len(unit.text) for unit in ir.evidence_units),
        "complete": True,
    }


def test_failed_work_without_active_batch_becomes_eligible_on_explicit_continue(evidence_case):
    engine = initial(evidence_case)
    key = add_review(engine, relation(engine))
    engine.state["work"][key].update(status="failed", reason_code="technical_failure")
    repo = MemoryRepository(engine.state, respond)
    finished = resume(evidence_case, repo)
    assert finished.state["work"][key]["status"] == "done"
    assert finished.state["relations"]["r"]["state"] == "accepted"
    assert [stage for stage, _ in repo.paid] == ["evidence_review"]


@pytest.mark.parametrize("mutation", ["delete", "change"])
def test_obsolete_prepared_target_does_not_apply_its_paid_answer(evidence_case, mutation):
    engine = initial(evidence_case)
    key = add_alignment(engine, seed(engine))
    repo = MemoryRepository(engine.state, respond, pause_after="relation_alignment")
    resume(evidence_case, repo)
    old_key = next(iter(repo.state["cursor"]["main"]["active_batches"].values()))["call_key"]
    if mutation == "delete":
        del repo.state["work"][key]
    else:
        work = repo.state["work"][key]
        work["input"]["condition_hints"] = ["Only under C"]
        work["dependency_hash"] = "new-dependency"
        repo.respond = lambda stage, payload, schema: proposal(payload, verdict="no_relation")
    finished = resume(evidence_case, repo)
    assert not finished.state.get("relations")
    assert not finished.state["cursor"]["main"]["active_batches"]
    assert len([row for row in repo.paid if row[0] == "relation_alignment"]) == (
        1 if mutation == "delete" else 2
    )
    assert old_key in repo.answers
