"""Cross-window candidate pairing and source authorization regressions."""

from copy import deepcopy

import pytest
from docx import Document
from rdflib import Graph, Namespace

from app.services.document_harness.ontology import catalog_from_graph
from app.services.document_harness.planning import (
    admit_relation_work,
    alignment_jobs,
    build_source_index,
    collect_relation_seeds,
    context_window,
)
from app.services.document_harness.source import make_window, quote_for_reference, reference
from app.services.document_harness.work import DEFAULT_POLICY
from app.services.extraction.document_ir import build_document_ir
from app.services.extraction.docx_structure import parse_docx_structure

EX = Namespace("https://example.test/schema/")


def catalog():
    source = Graph().parse(data="""
        @prefix : <https://example.test/schema/> .
        @prefix owl: <http://www.w3.org/2002/07/owl#> .
        @prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
        :Root a owl:Class . :A a owl:Class . :B a owl:Class . :Leaf a owl:Class .
        :describes a owl:ObjectProperty ; rdfs:domain :Root ;
            rdfs:range [owl:unionOf (:A :B :Leaf)] .
        :uses a owl:ObjectProperty ; rdfs:domain :A ; rdfs:range :B .
        :usedBy a owl:ObjectProperty ; rdfs:domain :B ; rdfs:range :A .
    """, format="turtle")
    return catalog_from_graph(source, str(EX.Root))


def entity(key, class_iri, *, fields=(), role="object", state="candidate"):
    return {"id": key, "label": "same spelling", "class_iri": str(class_iri), "role": role,
            "state": state, "field_ids": list(fields), "evidence": []}


def test_explicit_clue_can_propose_a_cross_window_pair_before_acceptance(tmp_path):
    ir = document(tmp_path, ["Historical A", "Current B"])
    left, right = [reference(ir, unit.evidence_id, 0, len(unit.text))
                   for unit in ir.evidence_units]
    values = {
        "root": entity("root", EX.Root, role="document_root", state="accepted"),
        "old-a": {**entity("old-a", EX.A, state="unresolved"), "referent": left},
        "new-b": {**entity("new-b", EX.B), "referent": right},
    }
    state = {"entities": values, "hints": {}, "fields": {}}
    index = build_source_index(ir, state)
    assert collect_relation_seeds(ir, catalog(), state, {"entities": values},
                                  index, DEFAULT_POLICY) == []
    hint = {"id": "hint", "subject_id": "old-a", "object_id": "new-b", "label": "uses",
            "evidence": [left, right]}
    state["hints"][hint["id"]] = hint
    seeds = collect_relation_seeds(ir, catalog(), state, {"hints": state["hints"]},
                                   index, DEFAULT_POLICY)
    assert [(s["subject_id"], s["object_ids"]) for s in seeds] == [("old-a", ["new-b"])]
    assert seeds[0]["endpoint_hypothesis"]
    assert set(ref["source_id"] for ref in seeds[0]["clue_refs"]) == {
        left["source_id"], right["source_id"],
    }


def test_clue_work_batches_preserve_distinct_same_name_mentions_and_fields(tmp_path):
    ir = document(tmp_path, ["原字段：完整原值", *("same spelling" for _ in range(9))])
    first = window_for(ir, [ir.evidence_units[0]])
    field = first.fields[0]
    left = reference(ir, ir.evidence_units[0].evidence_id, 0, len(ir.evidence_units[0].text))
    values = {"new-a": {**entity("new-a", EX.A, fields=[field["id"]]), "referent": left}}
    hints = {}
    for i, unit in enumerate(ir.evidence_units[1:]):
        ref = reference(ir, unit.evidence_id, 0, len(unit.text))
        values[f"b{i}"] = {**entity(f"b{i}", EX.B), "referent": ref}
        hints[str(i)] = {"id": str(i), "subject_id": "new-a", "object_id": f"b{i}",
                         "label": "uses", "evidence": [left, ref]}
    state = {"entities": values, "hints": hints, "fields": {field["id"]: field}}
    before = deepcopy(state)
    cards = catalog()
    seeds = collect_relation_seeds(ir, cards, state, {"hints": hints},
                                   build_source_index(ir, state), DEFAULT_POLICY)
    rows = admit_relation_work(seeds, state, cards, DEFAULT_POLICY)
    jobs = list(alignment_jobs(state, rows, DEFAULT_POLICY))
    assert sum(len(batch) for batch in jobs) == 9
    assert all(len(batch) <= DEFAULT_POLICY["relation_items_per_call"] for batch in jobs)
    assert {row["input"]["object_ids"][0] for batch in jobs for row in batch} == {
        f"b{i}" for i in range(9)
    }
    assert len({row["id"] for row in rows}) == 9
    assert state == before
    assert state["fields"][field["id"]]["value"] == "完整原值"


def document(tmp_path, paragraphs):
    doc = Document()
    for text in paragraphs:
        doc.add_paragraph(text)
    path = tmp_path / "planning.docx"
    doc.save(path)
    return build_document_ir(path, parse_docx_structure(path))


def window_for(ir, units):
    return make_window(ir, [(u.evidence_id, 0, len(u.text)) for u in units],
                       [u.evidence_id for u in units])


def test_context_includes_historical_referent_type_and_field_sources_without_new_fields(tmp_path):
    ir = document(tmp_path, ["名称：Alpha", "质量：12 kg", "记录类别：容器", "后文：Beta"])
    name, mass, type_unit, later = ir.evidence_units
    first = window_for(ir, [name, mass])
    base = window_for(ir, [later])
    old = entity("old", EX.A, fields=[first.fields[1]["id"]])
    old["referent"] = reference(ir, name.evidence_id, 3, len(name.text))
    old["type_evidence"] = [reference(ir, type_unit.evidence_id, 0, len(type_unit.text))]
    before = deepcopy(base)
    fields = {field["id"]: field for field in first.fields}
    expanded = context_window(ir, base, [old], fields)
    assert expanded.id == base.id
    assert expanded.primary_ids == base.primary_ids
    assert base == before
    assert expanded.entity_ids == ["old"]
    # Adding the name/type sources does not invent additional original fields.
    assert {field["id"] for field in expanded.fields} == {
        base.fields[0]["id"], first.fields[1]["id"],
    }
    for ref in [old["referent"], *old["type_evidence"], *first.fields[1]["evidence"]]:
        quote = quote_for_reference(expanded, ref)
        assert quote is not None
        assert expanded.resolve(ir, quote)["text"] == ref["text"]
    assert next(f for f in expanded.fields if f["id"] == first.fields[1]["id"])["value"] == "12 kg"
    assert len({source["source_id"] for source in expanded.sources}) == len(expanded.sources)


def test_long_source_is_added_as_exact_spans_and_extra_review_evidence_is_available(tmp_path):
    prefix = "背景" * 1500
    ir = document(tmp_path, [prefix + "真实主体Alpha" + "末尾" * 1500, "当前记录"])
    old_unit, current = ir.evidence_units
    base = window_for(ir, [current])
    ref = reference(ir, old_unit.evidence_id, len(prefix), len(prefix) + len("真实主体Alpha"))
    root = entity("root", EX.Root, role="document_root", state="accepted")
    expanded = context_window(ir, base, [root], {}, extra_refs=[ref])
    assert expanded.entity_ids == []
    assert [s["text"] for s in expanded.sources] == ["当前记录", "真实主体Alpha"]
    quote = quote_for_reference(expanded, ref)
    assert expanded.resolve(ir, quote) == ref


def test_context_does_not_truncate_an_original_value_even_if_it_is_large(tmp_path):
    value = "字" * 2500
    ir = document(tmp_path, ["说明：" + value, "当前记录"])
    old_unit, current = ir.evidence_units
    old_window = window_for(ir, [old_unit])
    base = window_for(ir, [current])
    field = old_window.fields[0]
    old = entity("old", EX.A, fields=[field["id"]])
    expanded = context_window(ir, base, [old], {field["id"]: field})
    assert next(f for f in expanded.fields if f["id"] == field["id"])["value"] == value
    for ref in field["evidence"]:
        assert quote_for_reference(expanded, ref) is not None
    assert any(source["text"] == value for source in expanded.sources)


def test_context_rejects_wrong_references_and_missing_fields_instead_of_silently_dropping(tmp_path):
    ir = document(tmp_path, ["原文", "当前"])
    base = window_for(ir, [ir.evidence_units[1]])
    bad = reference(ir, ir.evidence_units[0].evidence_id, 0, 2)
    bad["text"] = "篡改"
    with pytest.raises(ValueError, match="context_reference_mismatch"):
        context_window(ir, base, [], {}, extra_refs=[bad])
    with pytest.raises(ValueError, match="context_field_not_saved"):
        context_window(ir, base, [entity("old", EX.A, fields=["not-saved"])], {})
