"""Field-specific citation domains and exact ID-only source replay."""

import json
from copy import deepcopy

import pytest
from docx import Document

from app.evaluation.citation_protocol import CitationProtocol
from app.schemas.evidence import ExtractionTask
from app.services.extraction.extraction_tasks import (
    SYSTEM,
    AssertionResponse,
    BindingDecision,
    EntityResponse,
    ReferenceDecision,
)
from app.services.extraction.hierarchical_context import ContextEnvelope
from app.services.extraction.word_analysis import analyze_word_core
from tests.test_extraction.test_evaluation_citation_protocol import (
    SCHEMA,
    candidate,
    envelope,
    quote,
    source_id,
    subject_id,
    unit,
)
from tests.test_extraction.test_evaluation_citation_protocol import (
    source as citation_source,
)

source = citation_source


def restricted(ir, *, kind="property", source_text="5 mg", intervals=None):
    original = envelope(ir, kind=kind)
    payload = json.loads(original.serialized_input)
    for fragment in payload["fragments"]:
        fragment["fact_eligible"] = False
        fragment["purpose"] = "subject_evidence" if fragment["text"] == "A" else "source_context"
    identity = unit(ir, source_text).evidence_id
    intervals = [(0, len(source_text))] if intervals is None else intervals
    facts = [ir.anchor(identity, start, end) for start, end in intervals]
    payload["fragments"].extend(
        {
            "anchor": anchor.model_dump(mode="json"),
            "text": ir.resolve(anchor),
            "purpose": "target",
            "fact_eligible": True,
        }
        for anchor in facts
    )
    original.allowed_fact_regions = facts
    original.serialized_input = json.dumps(payload)
    return original


@pytest.mark.parametrize("compact", [False, True])
@pytest.mark.parametrize(
    "kind,response_type",
    [
        ("entity", EntityResponse),
        ("property", AssertionResponse),
        ("relationship", AssertionResponse),
    ],
)
def test_fact_fields_have_closed_fact_only_schema_and_reject_binding_only_background(
    source, compact, kind, response_type
):
    protocol = CitationProtocol(
        SYSTEM,
        restricted(source, kind=kind),
        response_type,
        ir=source,
        stage="recall",
        compact_identifiers=compact,
    )
    definitions = protocol.schema["$defs"]
    fact_ids = definitions["FactSpan"]["properties"]["evidence_id"]["enum"]
    binding_ids = definitions["BindingSpan"]["properties"]["evidence_id"]["enum"]
    assert fact_ids == [source_id(protocol, source, "5 mg")]
    assert (
        source_id(protocol, source, "A") in binding_ids
        and source_id(protocol, source, "A") not in fact_ids
    )
    assert "SpanProposal" not in definitions
    assert definitions["FactSpan"]["required"] == ["evidence_id"]
    assert definitions["BindingSpan"]["required"] == ["evidence_id"]
    for span in ("FactSpan", "BindingSpan"):
        assert set(definitions[span]["properties"]) == {"evidence_id", "text"}
        assert definitions[span]["additionalProperties"] is False
    background = {"evidence_id": source_id(protocol, source, "A")}
    if kind == "entity":
        fields = definitions["EntityProposal"]["properties"]
        assert fields["mention"]["$ref"] == "#/$defs/FactSpan"
        assert fields["assertion_spans"]["items"]["$ref"] == "#/$defs/BindingSpan"
        assert (
            definitions["IdentifierProposal"]["properties"]["value"]["$ref"]
            == "#/$defs/BindingSpan"
        )
        class_id = next(iter(protocol.references["class"])) if compact else "urn:Product"
        raw = {
            "entities": [
                {
                    "class_iri": class_id,
                    "mention": background,
                    "supported": True,
                    "reason": "Background is not a fact target",
                }
            ]
        }
    else:
        fields = definitions["AssertionProposal"]["properties"]
        assert fields["conditions"]["items"]["$ref"] == "#/$defs/BindingSpan"
        if kind == "property":
            assert fields["value"]["$ref"] == "#/$defs/FactSpan"
            assert fields["assertion_spans"]["items"]["$ref"] == "#/$defs/BindingSpan"
            assertion = {"value": background}
        else:
            assert fields["assertion_spans"]["items"]["$ref"] == "#/$defs/FactSpan"
            assert fields["value"]["type"] == "null"
            assertion = {
                "assertion_spans": [background],
                "object_candidate_id": subject_id(protocol, "C"),
            }
        raw = {"assertions": [{**assertion, "assertion_status": "affirmed"}]}
    with pytest.raises(ValueError, match="unknown_or_disallowed_citation_source"):
        protocol.decode(raw)


@pytest.mark.parametrize(
    "stage,response_type",
    [("verify_binding", BindingDecision), ("verify_reference", ReferenceDecision)],
)
def test_verification_spans_use_only_binding_domain(source, stage, response_type):
    original = restricted(source)
    header_id = unit(source, "Approval").evidence_id
    original.allowed_binding_regions = [
        a for a in original.allowed_binding_regions if a.evidence_id != header_id
    ]
    protocol = CitationProtocol(
        SYSTEM, original, response_type, ir=source, stage=stage, candidate=candidate(source)
    )
    assert (
        protocol.schema["properties"]["assertion_spans"]["items"]["$ref"] == "#/$defs/BindingSpan"
    )
    binding_ids = protocol.schema["$defs"]["BindingSpan"]["properties"]["evidence_id"]["enum"]
    assert source_id(protocol, source, "A") in binding_ids
    assert source_id(protocol, source, "Approval") not in binding_ids
    if stage == "verify_binding":
        for name in ("source_unit", "boolean_legend"):
            assert {"$ref": "#/$defs/BindingSpan"} in protocol.schema["properties"][name]["anyOf"]


@pytest.mark.parametrize("compact", [False, True])
@pytest.mark.parametrize("text_fields", [{}, {"text": None}])
def test_id_only_replays_only_fact_interval_while_binding_can_replay_full_cell(
    source, compact, text_fields
):
    original = restricted(source, intervals=[(0, 1)])
    protocol = CitationProtocol(
        SYSTEM, original, AssertionResponse, ir=source, stage="recall", compact_identifiers=compact
    )
    reference = {"evidence_id": source_id(protocol, source, "5 mg"), **text_fields}
    raw = {
        "assertions": [
            {"value": reference, "assertion_spans": [reference], "assertion_status": "conditional"}
        ]
    }
    before = deepcopy(raw)
    assertion = protocol.decode(raw)["assertions"][0]
    assert assertion["value"] == {
        "evidence_id": unit(source, "5 mg").evidence_id,
        "start": 0,
        "end": 1,
        "text": "5",
    }
    assert assertion["assertion_spans"][0]["text"] == "5 mg"
    assert assertion["assertion_spans"][0]["end"] == 4
    assert assertion["assertion_status"] == "conditional"
    assert raw == before


@pytest.mark.parametrize("text_fields", [{}, {"text": None}])
def test_discontinuous_regions_require_unique_quote_and_never_model_coordinates(
    source, text_fields
):
    text = "Repeated 5 mg; 5 mg."
    first, second = text.index("5 mg"), text.rindex("5 mg")
    original = restricted(
        source, source_text=text, intervals=[(first, first + 4), (second, second + 5)]
    )
    protocol = CitationProtocol(SYSTEM, original, AssertionResponse, ir=source, stage="recall")
    reference = {"evidence_id": source_id(protocol, source, text), **text_fields}
    raw = {"assertions": [{"value": reference, "assertion_status": "affirmed"}]}
    with pytest.raises(ValueError, match="ambiguous_allowed_source_fragments"):
        protocol.decode(raw)
    reference.update(text="5 mg.")
    decoded = protocol.decode(raw)["assertions"][0]["value"]
    assert decoded["text"] == "5 mg." and decoded["start"] == second
    assert decoded["end"] == second + 5
    reference.update(text="5 mg")
    with pytest.raises(ValueError, match="ambiguous_source_quote"):
        protocol.decode(raw)
    reference.update(start=first, end=second + 4)
    with pytest.raises(ValueError, match="non_atomic_source_citation"):
        protocol.decode(raw)


@pytest.mark.parametrize(
    "forbidden_fields",
    [
        {"start": 0, "end": 4},
        {"start": None, "end": None},
        {"start": 0, "end": None},
        {"start": 0},
        {"end": 4},
        {"start": True, "end": 1},
        {"start": 0, "end": 99},
        {"start": 2, "end": 1},
        {"context": None},
        {"context": "5 mg"},
    ],
)
@pytest.mark.parametrize("text_fields", [{}, {"text": "5 mg"}])
@pytest.mark.parametrize("compact", [False, True])
def test_model_coordinates_and_context_always_reject_even_if_correct_or_null(
    source, forbidden_fields, text_fields, compact
):
    protocol = CitationProtocol(
        SYSTEM,
        restricted(source),
        AssertionResponse,
        ir=source,
        stage="recall",
        compact_identifiers=compact,
    )
    with pytest.raises(ValueError, match="non_atomic_source_citation"):
        protocol.decode(
            {
                "assertions": [
                    {
                        "value": {
                            "evidence_id": source_id(protocol, source, "5 mg"),
                            **text_fields,
                            **forbidden_fields,
                        },
                        "assertion_status": "affirmed",
                    }
                ]
            }
        )


def test_id_only_preserves_tabs_newlines_and_unicode_from_real_docx(tmp_path):
    document = Document()
    text = "Claim\tDE64603\n24盘（计划）"
    document.add_paragraph(text)
    path = tmp_path / "raw-source.docx"
    document.save(path)
    ir = analyze_word_core(path).ir
    source_unit = next(u for u in ir.evidence_units if u.text == text)
    anchor = ir.anchor(source_unit.evidence_id)
    task = ExtractionTask(
        task_id="literal-source",
        task_kind="entity",
        target_class_iris=["urn:Product"],
        predicate_definition={"classes": SCHEMA},
        target_evidence_ids=[anchor.evidence_id],
    )
    fragments = [
        {
            "anchor": anchor.model_dump(mode="json"),
            "text": text,
            "purpose": "target",
            "fact_eligible": True,
        }
    ]
    original = ContextEnvelope(
        lookup_key="raw",
        context_hash="raw",
        completion="complete",
        fragments=fragments,
        allowed_fact_regions=[anchor],
        allowed_binding_regions=[anchor],
        serialized_input=json.dumps(
            {
                "task": task.model_dump(mode="json"),
                "subjects": {},
                "fragments": fragments,
                "effective_class": "urn:Product",
            }
        ),
    )
    protocol = CitationProtocol(SYSTEM, original, EntityResponse, ir=ir, stage="recall")
    result = protocol.decode(
        {
            "entities": [
                {
                    "class_iri": "t0",
                    "mention": {"evidence_id": "e0"},
                    "supported": False,
                    "reason": "Only test exact transport; not entity support",
                }
            ]
        }
    )
    assert result["entities"][0]["mention"]["text"] == text
    assert result["entities"][0]["supported"] is False
    assert "身份键" in protocol.system and "不静默规范化" in protocol.system


def test_provided_quote_remains_strict_and_never_becomes_id_only_fallback(source):
    protocol = CitationProtocol(
        SYSTEM, restricted(source), AssertionResponse, ir=source, stage="recall"
    )
    for text in ("", "5mg", "invented", "5 mg\n"):
        with pytest.raises(ValueError):
            protocol.decode(
                {
                    "assertions": [
                        {
                            "value": quote(protocol, source, "5 mg", text),
                            "assertion_status": "affirmed",
                        }
                    ]
                }
            )


@pytest.mark.parametrize("compact", [False, True])
def test_quote_does_not_search_another_source_id(source, compact):
    protocol = CitationProtocol(
        SYSTEM,
        restricted(source),
        AssertionResponse,
        ir=source,
        stage="recall",
        compact_identifiers=compact,
    )
    with pytest.raises(ValueError, match="source_excerpt_mismatch"):
        protocol.decode(
            {
                "assertions": [
                    {
                        "value": quote(protocol, source, "5 mg", "9 mg"),
                        "assertion_status": "affirmed",
                    }
                ]
            }
        )


@pytest.mark.parametrize("compact", [False, True])
def test_program_computes_absolute_bounds_inside_nonzero_authorized_window(source, compact):
    text = "Repeated 5 mg; 5 mg."
    start = text.rindex("5 mg")
    original = restricted(source, source_text=text, intervals=[(start, start + 4)])
    protocol = CitationProtocol(
        SYSTEM,
        original,
        AssertionResponse,
        ir=source,
        stage="recall",
        compact_identifiers=compact,
    )
    raw = {
        "assertions": [
            {
                "value": quote(protocol, source, text, "5 mg"),
                "assertion_status": "affirmed",
            }
        ]
    }
    assert set(raw["assertions"][0]["value"]) == {"evidence_id", "text"}
    span = protocol.decode(raw)["assertions"][0]["value"]
    assert span == {
        "evidence_id": unit(source, text).evidence_id,
        "start": start,
        "end": start + 4,
        "text": "5 mg",
    }
