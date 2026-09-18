"""Cold hydration preserves obligations without a historical task prefix."""

import json
from copy import deepcopy
from time import perf_counter

import pytest

from app.services.extraction.ontology_guided.current_work import SlotParts, WorkMap
from tests.test_extraction.test_layered_recognition import (
    HAS_CHILD,
    Adapter,
    arguments,
    ontology,
    run_executor,
)


class Crash(BaseException):
    pass


def merge(rows, changes):
    for domain, values in changes.items():
        for key, value in values.items():
            if value is None:
                rows.setdefault(domain, {}).pop(key, None)
            else:
                rows.setdefault(domain, {})[key] = deepcopy(value)


def test_business_change_does_not_encode_completed_history():
    encoded = []
    rows = WorkMap({str(i): i for i in range(10000)}, encode=lambda v: encoded.append(v) or v)
    rows.drain()
    encoded.clear()
    rows["17"] = 23
    rows.drain()
    assert encoded == [23]


def test_reinserted_business_key_keeps_cold_dictionary_order():
    values = WorkMap({"a": 1, "b": 2})
    values.pop("a")
    values["a"] = 3
    assert list(WorkMap.load(values.drain())) == ["b", "a"]


def test_new_proof_generation_removes_only_superseded_slot_parts():
    slots = SlotParts()
    rows = {}
    for slot in (("subject-a", 1, "predicate"), ("subject-b", 1, "predicate")):
        merge(
            rows,
            {
                "parts": slots.changes(
                    slot,
                    "generation-0",
                    {
                        "records": WorkMap({"first": 1, "obsolete": 2}).drain(),
                    },
                )
            },
        )
    cold = SlotParts(
        rows["parts"],
        {
            ("subject-a", 1, "predicate"): "generation-0",
            ("subject-b", 1, "predicate"): "generation-0",
        },
    )
    merge(
        rows,
        {
            "parts": cold.changes(
                ("subject-a", 1, "predicate"),
                "generation-1",
                {
                    "records": WorkMap({"first": 3}).drain(),
                },
            )
        },
    )
    assert len(rows["parts"]) == 3
    assert [
        row["value"]["value"] for row in rows["parts"].values() if row["slot"][0] == "subject-a"
    ] == [3]


@pytest.mark.parametrize("history", [20, 100, 400])
def test_executor_fixed_change_does_not_serialize_completed_history(tmp_path, history):
    from tests.test_extraction.test_semantic_ranking_execution import NoFactsAdapter

    args = arguments(tmp_path, [f"第一属性相关背景 {i}" for i in range(history)])
    sizes, timings, rows, counts = [], [], {}, []

    def commit(batch):
        start = perf_counter()
        sizes.append(len(json.dumps(batch.work_changes, ensure_ascii=False).encode()))
        timings.append(perf_counter() - start)
        counts.append(sum(len(values) for values in batch.work_changes.values()))
        assert batch.task_outcomes == []
        merge(rows, batch.work_changes)

    result = run_executor(
        ontology(branches=False), NoFactsAdapter(), current_state=True, max_tasks=history + 1
    ).run(
        **args,
        batch_hook=commit,
        work_hook=lambda changes: merge(rows, changes),
    )
    assert result.graph.progress.records_examined == history
    # Same unsupported business update after a longer completed prefix. Initial
    # admissions and terminal closure have different work, so compare middle tasks.
    assert max(sizes[5:-2]) <= sizes[5] * 1.25 + 1024
    control = rows["control"]["current"]
    adapter = NoFactsAdapter()
    start = perf_counter()
    resumed = run_executor(
        ontology(branches=False), adapter, current_state=True, max_tasks=history + 1
    ).run(
        **args,
        resume_state={
            "work_state": rows,
            "frontier": control["frontier_policy"],
            "diagnostics": control["diagnostics"],
        },
    )
    cold_ms = (perf_counter() - start) * 1000
    assert not adapter.calls and resumed.graph == result.graph
    print(
        json.dumps(
            {
                "completed_tasks": history,
                "steady_bytes": sizes[-3],
                "steady_changed_rows": counts[-3],
                "encode_ms": round(timings[-3] * 1000, 3),
                "cold_load_ms": round(cold_ms, 3),
            }
        )
    )


@pytest.mark.parametrize("single", [False, True])
def test_cold_resume_directly_hydrates_child_frontier(tmp_path, single):
    args = arguments(
        tmp_path,
        [
            "第一属性：1",
            "具有子项：子项",
            "第二属性：1",
            "具有末级：末级",
            "第三属性：1",
            "独立背景",
        ],
    )
    snapshot = ontology(single=single)
    expected = run_executor(snapshot, Adapter(), current_state=True).run(**args)
    adapter, rows, ranking, calls = Adapter(), {}, {}, {}

    def commit(batch):
        assert batch.task_outcomes == []
        merge(rows, batch.work_changes)
        if batch.task.predicate_iri == HAS_CHILD and batch.outcome.edges:
            raise Crash()

    with pytest.raises(Crash):
        run_executor(snapshot, adapter, current_state=True).run(
            **args,
            batch_hook=commit,
            work_hook=lambda c: merge(rows, c),
            ranking_hook=lambda s: ranking.update(deepcopy(s)),
            model_call_hook=lambda s: calls.update(deepcopy(s)),
        )
    control = rows["control"]["current"]
    replay = Adapter()
    restored = run_executor(snapshot, replay, current_state=True).run(
        **args,
        resume_state={
            "work_state": rows,
            "frontier": control["frontier_policy"],
            "diagnostics": control["diagnostics"],
        },
        ranking_state=ranking or None,
        model_call_state=calls or None,
    )
    assert restored.graph.model_dump(mode="json") == expected.graph.model_dump(mode="json")
    assert not {t.task_id for t in adapter.tasks}.intersection(t.task_id for t in replay.tasks)


def test_current_review_repair_and_second_cold_resume(tmp_path):
    args = arguments(tmp_path, ["第一属性：1", "独立背景"])
    snapshot, rows, batches = ontology(single=True, branches=False), {}, []

    def commit(batch):
        batches.append(batch)
        merge(rows, batch.work_changes)

    original_result = run_executor(snapshot, Adapter(), current_state=True).run(
        **args, batch_hook=commit, work_hook=lambda c: merge(rows, c)
    )
    original = original_result.graph.properties[0]
    original_task = next(b.task for b in batches if b.outcome.properties)
    control = rows["control"]["current"]
    review = dict(
        review_id="current-review",
        candidate_id=original.candidate_id,
        candidate_revision=original.revision,
        review_revision=1,
        decision="rejected",
        reason="重新核对数值",
        reason_code="incorrect_value",
        after_outcomes=control["completed_tasks"],
        subject_ref=original.subject_ref.model_dump(mode="json"),
        predicate_iri=original.predicate_iri,
        original_task=original_task.model_dump(mode="json"),
        record_ids=[original_task.record_id],
    )
    operation = dict(
        operation_id="current-repair",
        review_id=review["review_id"],
        target=review,
        status="queued",
        after_outcomes=review["after_outcomes"],
        max_tasks=16,
        max_model_calls=32,
    )

    class Corrected(Adapter):
        def inspect(self, task, context, predicate, menu):
            assert context.expert_feedback["reason"] == review["reason"]
            outcome = super().inspect(task, context, predicate, menu)
            outcome.properties[0].raw_value = "2"
            outcome.properties[0].candidate_id = "current-corrected"
            return outcome

    def resume():
        control = rows["control"]["current"]
        return {
            "work_state": deepcopy(rows),
            "frontier": control["frontier_policy"],
            "diagnostics": control["diagnostics"],
        }

    repaired = run_executor(snapshot, Corrected(), current_state=True).run(
        **args,
        resume_state=resume(),
        batch_hook=commit,
        work_hook=lambda c: merge(rows, c),
        property_reviews=[review],
        property_repairs=[operation],
        repair_only=True,
    )
    assert repaired.graph.progress.stop_reason == "expert_repair_completed"
    assert (
        next(
            p for p in repaired.graph.properties if p.candidate_id == original.candidate_id
        ).independent_review
        == "rejected"
    )
    adapter = Corrected()
    restored = run_executor(snapshot, adapter, current_state=True).run(
        **args,
        resume_state=resume(),
        property_reviews=[review],
        property_repairs=[operation],
        repair_only=True,
    )
    assert adapter.tasks == []
    assert restored.graph == repaired.graph
