"""Unfinished later endpoint pages preserve supported facts and record semantics."""

from copy import deepcopy

from app.services.document_analysis import current_state
from app.services.extraction.ontology_guided.records import RecordIndex
from tests.test_extraction.test_record_executor import EDGE, TEXT, A, B, record_setup

pytest_plugins = ["tests.test_extraction.test_tool_engine_resume"]


def test_supported_relation_page_survives_empty_sibling_pages_in_two_call_batch(
    tmp_path, monkeypatch, current_run,
):
    first_text = "部件丙与部件丁也是部件。"
    second_text = TEXT + "另列部件丙与部件丁。"
    relation_turns = []

    def transform(view, payload, *, record):
        records = RecordIndex(args["ir"]).records
        first, second = records

        def model_ref(value):
            return f"@r:{value[:12]}"

        def quote(text, source):
            return dict(evidence_id=model_ref(source.source_units[0].evidence_id),
                        text=text, context_text=None)

        if view["stage"] == "verification":
            source = (second if model_ref(second.source_units[0].evidence_id)
                      in str(view["verification_input"]) else first)
            for target in payload["verifications"]:
                for facet in target["facets"]:
                    facet["support"] = [quote(source.text, source)]
            return payload
        if record:
            if view["record_id"] == model_ref(first.record_id):
                template = next(e for e in payload["entities"] if e["class_iri"] == B)
                payload["entities"] = []
                payload["properties"] = []
                for identity, name in (("c", "部件丙"), ("d", "部件丁")):
                    entity = deepcopy(template)
                    entity.update(local_id=identity, mentions=[quote(name, first)])
                    payload["entities"].append(entity)
            else:
                payload["entities"] = [dict(
                    local_id=identity, class_iri=class_iri, representation="mention",
                    mentions=[quote(name, second)], record_components=[], identifier_claims=[],
                ) for identity, class_iri, name in (("a", A, "装置甲"), ("b", B, "部件乙"))]
                payload["properties"] = []
        elif view["predicate_iri"] == EDGE:
            if not any(ref["evidence_id"] == model_ref(second.source_units[0].evidence_id)
                       and ref["fact_eligible"] for ref in view["evidence_refs"]):
                payload["relations"] = []
            else:
                relation_turns.append(deepcopy(view))
                if len(relation_turns) > 1:
                    payload["relations"] = []
                else:
                    local_part = next(
                        item["entity_ref"]["id"]
                        for item in view["registered_entities"]
                        if item["class_iri"] == B and any(
                            ref["evidence_id"] == model_ref(
                                second.source_units[0].evidence_id
                            )
                            for ref in item["source_refs"]
                        )
                    )
                    relation = payload["relations"][0]
                    relation.update(object_ids=[local_part], bridge_support=[quote(TEXT, second)])
                    relation["source_assertion"].update(
                        subject_support=[quote("装置甲", second)],
                        object_support=[dict(object_id=local_part,
                                             support=[quote("部件乙", second)])],
                        predicate_support=[quote(TEXT, second)],
                    )
        return payload

    args, executor, _requests, hooks = record_setup(
        tmp_path, monkeypatch, current_run, texts=[first_text, second_text],
        transform=transform, page_size=2,
    )
    result = executor(max_model_calls_per_record=6).run(**args, **hooks)
    store, run, _token = current_run
    rows = current_state.restore_work(store, run, run.run_fingerprint).work_state
    # Each source-local endpoint group is visited once after the owner has a
    # proved incoming relation.  Unbound cross-scope entities do not create an
    # additional relationship page.
    assert len(relation_turns) == 2, result.diagnostics
    assert any(edge.predicate_iri == EDGE for edge in result.graph.edges), result.diagnostics
    second_record = RecordIndex(args["ir"]).records[1].record_id
    entries = [row["value"]["value"] for row in rows["plan_parts"].values()
               if row["name"] == "ledger"
               and row["slot"][-1] == EDGE
               and row["value"]["value"]["record_id"] == second_record]
    supported = [entry for entry in entries if "supported" in entry["semantic_outcomes"]]
    assert len(supported) == 1, entries
    assert supported[0]["coverage_state"] == "examined"
    assert len(result.graph.properties) == 2
    assert result.graph.progress.supported == 2 + len(result.graph.properties)
    assert result.graph.progress.completion == "incomplete"


def test_cold_relation_page_resume_keeps_paid_context_and_visits_remaining_endpoints(
    tmp_path, monkeypatch, current_run,
):
    pages = []

    def transform(view, payload, *, record):
        if view["stage"] != "discovery":
            return payload
        second = RecordIndex(args["ir"]).records[1]
        model_record_id = f"@r:{second.record_id[:12]}"
        model_evidence_id = f"@r:{second.source_units[0].evidence_id[:12]}"
        if record:
            if view["record_id"] == model_record_id:
                return {key: [] for key in payload}
            template = next(entity for entity in payload["entities"] if entity["class_iri"] == B)
            for identity, name in (("c", "部件丙"), ("d", "部件丁")):
                entity = deepcopy(template)
                entity.update(local_id=identity)
                entity["mentions"][0].update(text=name, context_text=None)
                payload["entities"].append(entity)
        elif view["predicate_iri"] == EDGE:
            payload["relations"] = []
            if any(
                ref["evidence_id"] == model_evidence_id
                and ref["fact_eligible"] for ref in view["evidence_refs"]
            ):
                pages.append(deepcopy(view))
        return payload

    args, executor, requests, hooks = record_setup(
        tmp_path, monkeypatch, current_run, transform=transform, page_size=2,
        texts=[TEXT + "部件丙与部件丁也是部件。", "装置甲连接部件乙、部件丙、部件丁。"],
    )
    paused = executor(progress_hook=lambda stage: not (stage == "after_model" and len(pages) == 1))
    first = paused.run(**args, **hooks)
    assert "execution_pause_requested" in first.diagnostics
    assert len(pages) == 1
    store, run, _token = current_run
    resumed = executor().run(
        **args, **hooks,
        resume_state=vars(current_state.restore_work(store, run, run.run_fingerprint)),
        model_call_state=current_state.restore_calls(store, run, run.run_fingerprint),
    )
    assert len(pages) == 2, resumed.diagnostics
    assert len({page["task_id"] for page in pages}) == 2
    endpoints = {entity["entity_ref"]["id"] for page in pages
                 for entity in page["registered_entities"] if entity["class_iri"] == B}
    assert endpoints == {
        f"@r:{node.entity_id[:12]}" for node in resumed.graph.nodes if node.class_iri == B
    }
    calls = current_state.restore_calls(store, run, run.run_fingerprint)
    task_ids = {page["task_id"] for page in pages}
    attempts = [(member["task_id"], view["stage"]) for view in requests
                for member in view.get("members", []) if member["task_id"] in task_ids]
    # Empty discoveries finalize without a verification model call.
    assert len(attempts) == len(set(attempts)) == 2
    lineages = {member["claim_lineage_id"] for protocol in calls["protocols"].values()
                for member in protocol.get("work_unit", {}).get("members", [])
                if f"@r:{member['task_id'][:12]}" in task_ids}
    assert len(lineages) == 1
    assert calls["lineage_calls"][next(iter(lineages))] == 2
    rows = current_state.restore_work(store, run, run.run_fingerprint).work_state
    row = next(row["value"] for row in rows["record_relation_inputs"].values()
               if row["key"] in lineages)
    assert row["status"] == "examined" and not row["remaining_pages"]
    assert resumed.graph.progress.model_calls == len(requests)
