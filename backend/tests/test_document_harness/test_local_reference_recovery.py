"""Exact citation choices and source-local recovery without losing valid discoveries."""

import json
from copy import deepcopy

import jsonschema
import pytest
from test_controller import inputs as inputs  # noqa: F401
from test_discovery_optimization import answer, ir_for, reader

from app.services.document_harness.calls import MemoryCalls
from app.services.document_harness.continuation import (
    remaining_reading_window,
    restore_window_plan,
    serialize_window_plan,
)
from app.services.document_harness.protocols import Discovery, stage_schema
from app.services.document_harness.reading import decode_local_discovery
from app.services.document_harness.source import QUOTE_FRAGMENT_BYTES, build_windows, make_window


def decode(ir, window, result):
    return decode_local_discovery(
        ir, window, Discovery.model_validate(result), {},
        {"id": "document", "field_ids": []}, class_iris=["urn:test:Thing"],
    )


def entities(delta):
    return [e for key, e in delta["changes"]["entities"].items() if key != "document"]


def test_fragment_choices_preserve_exact_positions_context_and_restore(tmp_path):
    text = "对象甲😀用于第一操作；对象甲😀未用于第二操作。"
    ir = ir_for(tmp_path, ["其他来源", text])
    window = build_windows(ir)[0]
    fragments = window.discovery_payload()["quote_fragments"]
    assert len(fragments) == 2
    refs = [window.resolve(ir, {"source_id": f["source_id"], "text": "对象甲😀"})
            for f in fragments]
    assert refs[0]["start"] != refs[1]["start"]
    assert all(ref["text"] == "对象甲😀" for ref in refs)
    assert window.sources[-1]["text"] == text
    for fragment in fragments:
        ref = window.resolve_anchor(ir, {"source_id": fragment["source_id"]})
        assert ref["text"] == text[fragment["start"]:fragment["end"]]
        assert window.owns(ref)
    restored = restore_window_plan(ir, serialize_window_plan(window))
    assert restored.discovery_payload() == window.discovery_payload()
    single = make_window(ir, [(refs[0]["source_id"], 0, len(text))], [refs[0]["source_id"]])
    # Short source aliases change with the window; physical fragment IDs do not.
    assert set(single.quote_fragments) == set(window.quote_fragments)
    quote = {"source_id": fragments[1]["source_id"], "text": "对象甲😀"}
    assert single.resolve(ir, quote) == refs[1]
    with pytest.raises(ValueError, match="source_outside_reading_window"):
        single.resolve_anchor(ir, {"source_id": "Qforeign"})


def test_fragment_menu_is_bounded_without_truncating_original_sources(tmp_path):
    text = "".join(f"第{i}处对象甲用于该操作；" for i in range(100))
    ir = ir_for(tmp_path, [text])
    window = build_windows(ir)[0]
    payload = window.discovery_payload()
    assert len(json.dumps(payload["quote_fragments"], ensure_ascii=False).encode()) <= (
        QUOTE_FRAGMENT_BYTES)
    assert 0 < len(payload["quote_fragments"]) < 100
    assert payload["sources"][0]["text"] == text
    ref = window.resolve(ir, {"source_id": "S1", "text": "对象甲", "occurrence": 99})
    assert ref["start"] == text.rindex("对象甲")


@pytest.mark.parametrize("mode", [None, "draft", "refine"])
def test_fragment_ids_are_scoped_quote_choices_never_reading_or_evidence_ids(tmp_path, mode):
    ir = ir_for(tmp_path, ["对象甲；对象乙。", "辅助甲；辅助乙。"])
    window = build_windows(ir)[0]
    window.primary_ids = [window.sources[0]["evidence_id"]]
    fragments = window.citation_choices()
    primary = next(key for key, parent in fragments.items() if parent == "S1")
    auxiliary = next(key for key, parent in fragments.items() if parent == "S2")
    schema = stage_schema("discover", source_ids=["S1", "S2"], primary_source_ids=["S1"],
                          class_iris=["urn:test:Thing"], quote_fragments=fragments,
                          discovery_mode=mode)
    result = answer(window.payload())
    result["entities"][0]["anchor"] = {"source_id": primary}
    result["entities"][0]["name"] = {"source_id": primary, "text": "对象甲"}

    def wrapped(value):
        if mode == "draft":
            return {**value, "lookup_requests": [], "lookup_refinement_required": False}
        if mode == "refine":
            return {"replacement": value, "source_suggestions": []}
        return value

    jsonschema.validate(wrapped(result), schema)
    for mutation in ["foreign", "auxiliary_anchor", "evidence", "cursor"]:
        bad = deepcopy(result)
        if mutation in {"foreign", "auxiliary_anchor"}:
            bad["entities"][0]["anchor"]["source_id"] = (
                "Qforeign" if mutation == "foreign" else auxiliary)
        elif mutation == "evidence":
            bad["entities"][0]["evidence"] = [primary]
        else:
            bad.update(complete=False, read_through_source_id=primary)
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(wrapped(bad), schema)


def test_name_disambiguates_only_within_exact_anchor_without_changing_explicit_position(tmp_path):
    text = "对象甲用于第一操作；对象甲用于第二操作。"
    ir = ir_for(tmp_path, [text])
    window = build_windows(ir)[0]
    result = answer(window.payload())
    result["entities"][0].update(
        anchor={"source_id": "S1", "text": "对象甲用于第二操作"},
        name={"source_id": "S1", "text": "对象甲"},
    )
    delta = decode(ir, window, result)
    assert delta["complete"]
    assert entities(delta)[0]["name"]["start"] == text.rindex("对象甲")
    anchor = entities(delta)[0]["referent"]
    with pytest.raises(ValueError, match="source_occurrence_invalid"):
        window.resolve(ir, {"source_id": "S1", "text": "对象甲", "occurrence": 8}, within=anchor)
    with pytest.raises(ValueError, match="source_quote_ambiguous"):
        window.resolve(ir, {"source_id": "S1", "text": "对象甲"},
                       within=window.resolve_anchor(ir, {"source_id": "S1"}))


def test_invalid_name_keeps_independent_anchor_and_relation_but_withholds_source_coverage(tmp_path):
    ir = ir_for(tmp_path, ["对象甲在这里", "对象乙在另一处"])
    window = build_windows(ir)[0]
    result = answer(window.payload())
    result["entities"][0]["name"] = {"source_id": "S1", "text": "不在原文"}
    result["relation_hints"] = [{"subject_id": "S1", "object_id": "S2", "label": "关联",
                                 "evidence": ["S1", "S2"], "polarity": "negative",
                                 "conditions": ["条件一"]}]
    delta = decode(ir, window, result)
    assert len(entities(delta)) == 2
    first = next(e for e in entities(delta) if e["referent"]["text"] == "对象甲在这里")
    assert first["name"] is None and first["state"] == "candidate"
    hint = next(iter(delta["changes"]["hints"].values()))
    assert hint["subject_id"] == first["id"]
    assert hint["polarity"] == "negative" and hint["conditions"] == ["条件一"]
    assert not delta["complete"]
    assert [r["evidence_id"] for r in delta["completed_ranges"]] == [
        ir.evidence_units[1].evidence_id,
    ]


def test_invalid_field_does_not_erase_valid_anchor_other_fields_or_good_prefix(tmp_path):
    ir = ir_for(tmp_path, ["对象甲数值7", "对象乙数值8", "尚未读取"])
    window = build_windows(ir)[0]
    result = answer(window.payload(), through="S2")
    result["entities"][0]["source_fields"] = [
        {"label": None, "value": {"source_id": "S1", "text": "不存在"}},
        {"label": None, "value": {"source_id": "S1", "text": "7"}},
    ]
    delta = decode(ir, window, result)
    assert len(entities(delta)) == 2
    assert any(f["value"] == "7" for f in delta["changes"]["fields"].values())
    assert not any(f["value"] == "不存在" for f in delta["changes"]["fields"].values())
    assert [r["evidence_id"] for r in delta["completed_ranges"]] == [
        ir.evidence_units[1].evidence_id,
    ]
    child = remaining_reading_window(ir, window, delta["completed_ranges"])
    assert [ir.unit(r["evidence_id"]).text for r in child.primary()] == ["对象甲数值7", "尚未读取"]


def test_invalid_anchor_and_dependent_relation_are_not_published(tmp_path):
    ir = ir_for(tmp_path, ["对象甲", "对象乙", "对象丙"])
    window = build_windows(ir)[0]
    result = answer(window.payload())
    result["entities"][0]["anchor"]["text"] = "伪造锚点"
    result["relation_hints"] = [{"subject_id": "document", "object_id": "S1", "label": "描述",
                                 "evidence": ["S1"], "polarity": "positive", "conditions": []}]
    delta = decode(ir, window, result)
    assert len(entities(delta)) == 2 and not delta["changes"].get("hints")
    assert len(delta["completed_ranges"]) == 2 and not delta["complete"]


@pytest.mark.parametrize("corrected", [True, False])
def test_pause_resume_rereads_only_error_source_and_never_claims_unresolved_coverage(
    tmp_path, inputs, corrected,
):
    ir = ir_for(tmp_path, ["对象甲", "对象乙", "对象丙"])
    paid = []

    def invoke(stage, payload, schema):
        assert stage == "discover"
        paid.append(deepcopy(payload))
        result = answer(payload)
        if len(paid) == 1 or not corrected:
            sid = next(s["source_id"] for s in payload["sources"] if s["text"] == "对象乙")
            target = next(m for m in result["entities"] if m["local_id"] == sid)
            target["name"] = {"source_id": sid, "text": "错误名称"}
        return result

    calls = MemoryCalls(invoke)
    engine = reader(ir, inputs[1], invoke, calls=calls)
    engine.should_stop = lambda: any(w.get("completed_ranges")
                                   for w in engine.state.get("windows", {}).values())
    engine.run()
    assert len(paid) == 1 and len(engine.state["entities"]) == 4
    assert engine.state["cursor"]["main"]["reading"]["complete_characters"] == 6
    resumed = reader(ir, inputs[1], invoke, state=engine.state, calls=calls)
    resumed.run()
    assert len(paid) == 2 and len(resumed.state["entities"]) == 4
    last = paid[-1]
    ids = {r["source_id"] for r in last["reading_scope"]}
    assert [s["text"] for s in last["sources"] if s["source_id"] in ids] == ["对象乙"]
    progress = resumed.state["cursor"]["main"]["reading"]
    assert progress["complete"] is corrected
    assert progress["complete_characters"] == (9 if corrected else 6)


def test_interval_subtraction_preserves_unread_gaps_and_original_coordinates(tmp_path):
    ir = ir_for(tmp_path, ["ABCDEFGHIJKL"])
    window = build_windows(ir)[0]
    eid = window.sources[0]["evidence_id"]
    completed = [{"evidence_id": eid, "start": a, "end": b} for a, b in [(2, 4), (6, 9)]]
    child = remaining_reading_window(ir, window, completed)
    assert [(r["start"], r["end"]) for r in child.primary()] == [(0, 2), (4, 6), (9, 12)]
    assert restore_window_plan(ir, serialize_window_plan(child)).primary() == child.primary()


def test_unknown_source_without_attribution_cannot_credit_coverage(tmp_path):
    ir = ir_for(tmp_path, ["对象甲", "对象乙"])
    window = build_windows(ir)[0]
    result = answer(window.payload())
    result["unowned_fields"] = [{"label": None, "value": {
        "source_id": "foreign", "text": "未知来源",
    }}]
    delta = decode(ir, window, result)
    assert not delta["complete"] and delta["completed_ranges"] == []


def test_invalid_optional_name_does_not_bypass_attribute_entity_boundary(tmp_path):
    ir = ir_for(tmp_path, ["是否存在细胞毒性：否"])
    window = build_windows(ir)[0]
    result = answer(window.payload())
    result["entities"][0]["name"] = {"source_id": "S1", "text": "某个虚构对象"}
    delta = decode(ir, window, result)
    assert not entities(delta)
    assert any(o["reason"] == "attribute_is_not_entity"
               for o in delta["changes"]["observations"].values())
