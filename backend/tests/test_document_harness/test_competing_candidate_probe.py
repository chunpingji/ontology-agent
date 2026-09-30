"""Semantic regression boundaries for the isolated grouped-candidate experiment."""

from copy import deepcopy

import jsonschema
import pytest
from docx import Document

from app.evaluation.harness_competing_candidate_probe import (
    RELATION,
    Alignment,
    Mentions,
    Review,
    exact_queries,
    project_result,
    review_candidates,
    schema_for,
    score,
    validate_alignment,
    validate_mentions,
)
from app.services.document_harness.source import build_windows
from app.services.extraction.document_ir import build_document_ir
from app.services.extraction.docx_structure import parse_docx_structure

TEXT = "计划于A1/A2区域生产。"


def q(text):
    return {"source_id": "S1", "text": text, "occurrence": None}


@pytest.fixture
def sample(tmp_path):
    path = tmp_path / "source.docx"
    document = Document()
    document.add_paragraph(TEXT)
    document.save(path)
    ir = build_document_ir(path, parse_docx_structure(path))
    window = build_windows(ir)[0]
    mentions = Mentions.model_validate({
        "area_anchor": q("A1/A2区域"),
        "expressions": [{"id": "whole", "quote": q("A1/A2")},
                        {"id": "a", "quote": q("A1")}, {"id": "b", "quote": q("A2")}],
        "partitions": [{"id": "one", "members": [{"id": "M0", "expression_ids": ["whole"]}],
                        "reason": "whole expression"},
                       {"id": "two", "members": [{"id": "M1", "expression_ids": ["a"]},
                                                  {"id": "M2", "expression_ids": ["b"]}],
                        "reason": "member reading"}],
        "plan_anchor": q("计划"),
    })
    return ir, window, mentions


def align(mode="options", timing="unspecified"):
    return Alignment.model_validate({
        "selected_partition_id": "two", "identity_evidence": [q(TEXT)],
        "identity_reason": "member reading",
        "relation": {"partition_id": "two", "predicate_iri": RELATION,
                     "member_ids": ["M1", "M2"], "participation": mode,
                     "selection": "exactly_one" if mode == "options" else "unspecified",
                     "timing": timing, "polarity": "positive", "evidence": [q(TEXT)],
                     "reason": "Candidate only; test projection independently of wording"},
    })


def judged(candidates):
    return Review.model_validate({"judgments": {
        key: {"verdict": "accepted", "evidence": [q(TEXT)], "reason": "Fixture verdict"}
        for key in candidates
    }})


def project(sample, alignment=None, change=None):
    ir, window, mentions = sample
    alignment = alignment or align()
    candidates = review_candidates(mentions, alignment, {"results": []})
    review = judged(candidates)
    if change:
        change(review)
    return project_result(mentions, alignment, candidates, review, window, ir)


def test_overlapping_whole_and_member_interpretations_are_candidates_not_three_entities(sample):
    ir, window, mentions = sample
    validate_mentions(mentions, window, ir)
    output = project(sample)
    assert len(mentions.expressions) == 3
    assert len(output["members"]) == 2
    assert output["accepted_identifier_groups"] == [["A1"], ["A2"]]


def test_same_partition_cannot_include_whole_and_overlapping_members(sample):
    ir, window, mentions = sample
    mentions.partitions[0].members.extend(deepcopy(mentions.partitions[1].members))
    with pytest.raises(ValueError, match="overlapping_members"):
        validate_mentions(mentions, window, ir)


def test_same_object_may_have_multiple_identifiers(sample):
    ir, window, mentions = sample
    mentions.partitions[1].members[0].expression_ids = ["a", "b"]
    mentions.partitions[1].members = mentions.partitions[1].members[:1]
    validate_mentions(mentions, window, ir)
    alignment = align("single")
    alignment.relation.member_ids = ["M1"]
    validate_alignment(alignment, mentions, window, ir)
    assert project(sample, alignment)["accepted_identifier_groups"] == [["A1", "A2"]]


def test_query_uses_all_model_quotes_exactly_without_delimiter_splitting(sample):
    _, _, mentions = sample
    queries = exact_queries(mentions, [{"mapping_id": "mapping"}])
    assert [q["property_filters"][0]["value"] for q in queries] == ["A1/A2", "A1", "A2"]
    mentions.expressions = mentions.expressions[:1]
    assert len(exact_queries(mentions, [{"mapping_id": "mapping"}])) == 1


def test_source_absence_does_not_drop_document_identifiers(sample):
    result = project(sample)
    assert result["accepted_identifier_groups"] == [["A1"], ["A2"]]
    assert result["identity_status"] == "not_checked"
    assert all(i["source_matches"] is None for m in result["members"] for i in m["identifiers"])


def test_exclusive_options_remain_one_group_without_unconditional_edges(sample):
    output = project(sample)
    assert output["relation_group"]["participation"] == "options"
    assert output["relation_group"]["selection"] == "exactly_one"
    assert output["edges"] == []


def test_joint_participation_does_not_imply_parallel_timing(sample):
    output = project(sample, align("all"))
    assert len(output["edges"]) == 2
    assert output["relation_group"]["timing"] == "unspecified"
    assert all(edge["scope"] == "plan_statement" for edge in output["edges"])


def test_unresolved_production_relation_does_not_invalidate_identifiers(sample):
    output = project(sample, align("unknown"),
                     lambda r: setattr(r.judgments["R"], "verdict", "unresolved"))
    assert output["accepted_identifier_groups"] == [["A1"], ["A2"]]
    assert output["relation_group"] is None
    assert output["edges"] == []


def test_parallel_needs_its_own_evidence_acceptance(sample):
    output = project(sample, align("all", "parallel"),
                     lambda r: setattr(r.judgments["T"], "verdict", "unresolved"))
    assert len(output["edges"]) == 2
    assert output["relation_group"]["timing"] == "unspecified"


@pytest.mark.parametrize("key", ["I", "P"])
def test_rejected_endpoint_blocks_relation_and_only_identity_blocks_own_properties(sample, key):
    output = project(sample, change=lambda r: setattr(r.judgments[key], "verdict", "rejected"))
    assert output["relation_group"] is None
    assert bool(output["members"]) == (key == "P")


def test_relation_cannot_reference_competing_partition(sample):
    ir, window, mentions = sample
    alignment = align()
    alignment.relation.partition_id = "one"
    with pytest.raises(ValueError, match="partition_conflict"):
        validate_alignment(alignment, mentions, window, ir)


def test_relation_cannot_invent_missing_plan(sample):
    ir, window, mentions = sample
    mentions.plan_anchor = None
    with pytest.raises(ValueError, match="grounded_endpoints"):
        validate_alignment(align(), mentions, window, ir)


def test_exclusive_options_cannot_also_assert_parallel(sample):
    ir, window, mentions = sample
    with pytest.raises(ValueError, match="timing_without_joint"):
        validate_alignment(align(timing="parallel"), mentions, window, ir)


def test_acceptance_evidence_cannot_belong_only_to_other_member(sample):
    def change(review):
        review.judgments["A1_1"].evidence = [type(review.judgments["A1_1"].evidence[0])(**q("A2"))]
    with pytest.raises(ValueError, match="evidence_ownership_conflict"):
        project(sample, change=change)


def test_final_review_must_answer_all_candidates_and_cannot_rewrite_values(sample):
    _, window, mentions = sample
    candidates = review_candidates(mentions, align(), {"results": []})
    schema = schema_for(Review, window, candidate_ids=list(candidates))
    answer = judged(candidates).model_dump()
    jsonschema.validate(answer, schema)
    missing = deepcopy(answer)
    del missing["judgments"]["A1_1"]
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(missing, schema)
    answer["judgments"]["A1_1"]["value"] = "A2"
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(answer, schema)


def test_production_options_cannot_be_misrepresented_as_partial_referent_partitions(sample):
    ir, window, mentions = sample
    mentions.partitions[1].members = mentions.partitions[1].members[:1]
    with pytest.raises(ValueError, match="partition_omits_group_mention"):
        validate_mentions(mentions, window, ir)


def test_model_sees_explicit_member_references_and_cannot_invent_an_index(sample):
    _, window, _ = sample
    schema = schema_for(Alignment, window, partitions=["one", "two"], member_ids=["M0", "M1", "M2"])
    answer = align().model_dump()
    jsonschema.validate(answer, schema)
    answer["relation"]["member_ids"] = ["0", "1"]
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(answer, schema)


@pytest.mark.parametrize("verdict,expected", [("accepted", False), ("unresolved", True)])
def test_relation_scoring_uses_final_review_not_intermediate_guess(sample, verdict, expected):
    _, _, mentions = sample
    alignment = align()
    result = project(sample, alignment,
                     lambda r: setattr(r.judgments["R"], "verdict", verdict))
    reference = {"ids": [["A1"], ["A2"]], "participation": "unknown",
                 "selection": "unspecified", "timing": "unspecified",
                 "plan_present": True, "require_competition": False}
    scored = score(mentions, alignment, result, reference)
    assert scored["relation_semantics_ok"] is expected
    assert scored["strict_pass"] is expected


def test_no_plan_schema_keeps_identity_choice_but_has_no_relation_endpoints(sample):
    _, window, _ = sample
    schema = schema_for(Alignment, window, partitions=["one", "two"],
                        member_ids=["M0", "M1", "M2"], has_plan=False)
    answer = align().model_dump()
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(answer, schema)
    answer["relation"].update(participation="none", member_ids=[], selection="unspecified")
    jsonschema.validate(answer, schema)
    assert answer["selected_partition_id"] == "two"
