"""Model tool results share repetition without losing evidence or recall coverage."""

import json
from dataclasses import replace

from app.services.extraction.evidence_identity import canonical_json
from app.services.extraction.ontology_guided import tool_runtime as runtime
from app.services.extraction.ontology_guided.model_reference_projection import (
    ModelReferenceProjection,
)
from app.services.extraction.ontology_guided.tool_contracts import (
    MentionCoverage,
    MentionData,
    MentionLimits,
    MentionSuggestion,
    ToolIssue,
    ToolResult,
)

from .test_tool_runtime import call
from .test_tool_runtime import tool_context as _tool_context_fixture
from .test_tool_runtime_recall import FakeGliner25, ner_context

tool_context = _tool_context_fixture


def _mention_result():
    text = "否定：A–B，条件 x > 0"
    mentions = [
        MentionSuggestion(
            mention_ref="mention-" + "a" * 64,
            evidence_id="evidence-" + "b" * 64,
            start=10,
            end=10 + len(text),
            text=text,
            class_iris=["urn:Entity"] if role == "entity" else [],
            predicate_iris=[] if role == "entity" else ["urn:field"],
            role=role,
            score=score,
        )
        for role, score in (("entity", 0.92), ("field_label", 0.83), ("field_value", 0.77))
    ]
    mentions.extend([
        mentions[0].model_copy(update={
            "mention_ref": "second-location", "start": 50, "end": 50 + len(text),
        }),
        mentions[0].model_copy(update={
            "mention_ref": "other-source", "evidence_id": "other-evidence",
        }),
    ])
    return ToolResult[MentionData](
        status="ok",
        data=MentionData(
            mentions=mentions,
            coverage=MentionCoverage(
                requested_units=[mentions[0].evidence_id, "other-evidence", "unprocessed"],
                processed_units=[mentions[0].evidence_id, "other-evidence"],
                unprocessed_units=["unprocessed"],
            ),
            limits=MentionLimits(
                labels_per_batch=8, window_chars=500, overlap_chars=100,
                word_limit=160, encoder_token_limit=512, candidate_pool_limit=64,
                max_returned_mentions=64,
            ),
            omissions=[ToolIssue(
                code="mention_windows_incomplete", field_path=None,
                message="未完成的原文不能作否定判断。", evidence_ids=["unprocessed"],
            )],
        ),
        evidence_refs=[mentions[0].evidence_id, "other-evidence"],
        issues=[],
    )


def _expand_mentions(projected):
    return [
        {
            "evidence_id": unit["evidence_id"],
            **{key: value for key, value in mention.items() if key != "roles"},
            **role,
        }
        for unit in projected["data"]["units"]
        for mention in unit["mentions"]
        for role in mention["roles"]
    ]


def test_grouped_mentions_preserve_roles_scores_text_positions_and_coverage():
    result = _mention_result()
    original = result.model_dump(mode="json")
    projected = runtime.project_tool_result(result)
    assert sorted(map(canonical_json, _expand_mentions(projected))) == sorted(
        map(canonical_json, original["data"]["mentions"])
    )
    assert len(projected["data"]["units"]) == 2
    assert len(projected["data"]["units"][0]["mentions"]) == 2
    roles = projected["data"]["units"][0]["mentions"][0]["roles"]
    assert [role["score"] for role in roles] == [0.92, 0.83, 0.77]
    for key in ("coverage", "limits", "omissions"):
        assert projected["data"][key] == original["data"][key]
    assert projected["evidence_refs"] == original["evidence_refs"]
    assert result.model_dump(mode="json") == original
    assert len(canonical_json(projected)) < len(canonical_json(original))


def test_saved_result_projection_is_idempotent_and_does_not_mutate_stored_data():
    original = _mention_result().model_dump(mode="json")
    saved = canonical_json(original)
    projected = runtime.project_tool_result(original)
    assert canonical_json(original) == saved
    assert runtime.project_tool_result(projected) == projected
    output = runtime.to_function_call_output("call-original", original)
    assert output["call_id"] == "call-original"
    assert json.loads(output["output"]) == projected


def test_dispatch_budget_counts_exact_grouped_function_output(tool_context):
    ctx, card = ner_context(tool_context, extractor=FakeGliner25())
    owner = next(unit for unit in ctx.index.ir.evidence_units if unit.text == "A")
    request = call(
        "propose_mentions", evidence_ids=[owner.evidence_id], schema_card_id=card.schema_card_id,
    )
    original = runtime.dispatch_tool(request, ctx, caller="controller")
    canonical_item = runtime.to_function_call_output(request.call_id, original)
    output = ModelReferenceProjection({"input": [canonical_item]}).request["input"][0]["output"]
    measured = []

    def measure(text):
        measured.append(text)
        return len(text)

    ctx = replace(
        ctx, measure_result_tokens=measure,
        limits=replace(ctx.limits, max_result_tokens=len(output)),
    )
    assert len(canonical_json(original.model_dump(mode="json"))) > ctx.limits.max_result_tokens
    assert len(canonical_item["output"]) > ctx.limits.max_result_tokens
    accepted = runtime.dispatch_tool(request, ctx)
    assert accepted.status == "ok"
    assert len(accepted.data.mentions) == 3
    assert measured == [output]
    accepted_item = runtime.to_function_call_output(request.call_id, accepted)
    accepted_wire = ModelReferenceProjection({"input": [accepted_item]}).request["input"][0]
    assert accepted_wire["output"] == measured[0]
    assert accepted.evidence_refs == [owner.evidence_id]

    rejected = runtime.dispatch_tool(
        request, replace(ctx, limits=replace(ctx.limits, max_result_tokens=len(output) - 1)),
    )
    assert rejected.status == "blocked"
    assert rejected.issues[0].code == "result_budget_exceeded"
    assert rejected.data is None and rejected.evidence_refs == []
    assert ctx.registered_mentions == {}


def test_no_match_projection_keeps_complete_empty_recall(tool_context):
    ctx, card = ner_context(tool_context, extractor=FakeGliner25(hits=False))
    evidence_id = ctx.context.fragments[0].anchor.evidence_id
    result = runtime.dispatch_tool(call(
        "propose_mentions", evidence_ids=[evidence_id], schema_card_id=card.schema_card_id,
    ), ctx)
    projected = runtime.project_tool_result(result)
    assert projected["status"] == "no_match"
    assert projected["data"]["units"] == []
    assert projected["data"]["coverage"] == {
        "requested_units": [evidence_id], "processed_units": [evidence_id],
        "unprocessed_units": [],
    }


def test_complete_prompt_hides_redundant_read_but_preserves_mention_tool(tool_context):
    ctx, _card = ner_context(tool_context, extractor=FakeGliner25())
    before = {item["name"] for item in runtime.build_tool_definitions(ctx, ctx.stage)}
    in_prompt = replace(ctx, evidence_text_in_prompt=True)
    after = {item["name"] for item in runtime.build_tool_definitions(in_prompt, ctx.stage)}
    assert before - after == {"inspect_evidence"}
    assert {"propose_mentions", "resolve_source_anchor"}.issubset(after)
    result = runtime.dispatch_tool(call(
        "inspect_evidence", evidence_ids=[ctx.context.fragments[0].anchor.evidence_id],
    ), in_prompt, caller="controller")
    assert result.status == "ok"


def test_record_pipeline_hides_and_rejects_boundary_mention_tool(tool_context):
    ctx, card = ner_context(tool_context, extractor=FakeGliner25())
    record_ctx = replace(ctx, allow_mention_discovery=False)
    offered = {item["name"] for item in runtime.build_tool_definitions(
        record_ctx, record_ctx.stage,
    )}
    assert "propose_mentions" not in offered

    blocked = runtime.dispatch_tool(call(
        "propose_mentions",
        evidence_ids=[ctx.context.fragments[0].anchor.evidence_id],
        schema_card_id=card.schema_card_id,
    ), record_ctx)
    assert blocked.status == "blocked"
    assert blocked.issues[0].code == "tool_not_allowed"


def test_batch_still_offers_read_to_member_missing_prompt_text(tool_context):
    first = replace(tool_context, evidence_text_in_prompt=True)
    contexts = {"included": first, "not-included": tool_context}
    definitions = runtime.build_member_tool_definitions(contexts, tool_context.stage)
    inspect = next(item for item in definitions if item["name"] == "inspect_evidence")
    assert inspect["parameters"]["properties"]["member_task_id"]["enum"] == ["not-included"]
    evidence_id = tool_context.context.fragments[0].anchor.evidence_id
    blocked = runtime.dispatch_member_tool(call(
        "inspect_evidence", member_task_id="included", evidence_ids=[evidence_id],
    ), contexts)
    assert blocked.status == "blocked" and blocked.issues[0].code == "tool_not_offered"
