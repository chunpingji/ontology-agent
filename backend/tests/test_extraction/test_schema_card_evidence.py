"""Source checks reject cross-row evidence without requiring model calls."""

from copy import deepcopy

import pytest

from app.evaluation.schema_card_evidence import (
    build_sources,
    marker_state,
    ownership_issue,
    quote_issue,
)


@pytest.fixture
def table_case():
    table = {
        "table_path": ["table:a"], "header_row_count": 1,
        "grid": [["h0", "h1", "h2"], ["api", "trial1", "v1"],
                 ["api", "trial2", "v2"]],
        "source_cells": [],
    }
    units = []
    for identity, row, column, text, span in [
        ("h0", 0, 0, "产品", 1), ("h1", 0, 1, "试验", 1), ("h2", 0, 2, "值", 1),
        ("api", 1, 0, "产品甲", 2), ("trial1", 1, 1, "试验甲", 1),
        ("v1", 1, 2, "12", 1), ("trial2", 2, 1, "试验乙", 1), ("v2", 2, 2, "34", 1),
    ]:
        table["source_cells"].append({
            "cell_id": identity, "row_index": row, "column_index": column,
            "row_span": span, "column_span": 1,
            "blocks": [{"kind": "paragraph", "text": text}],
        })
        units.append({
            "evidence_id": identity, "text": text, "table_path": ["table:a"],
            "row_index": row, "column_index": column, "source_cell_id": identity,
            "paragraph_index": 0,
        })
    units.append({"evidence_id": "paragraph", "text": "本文涉及产品甲。", "table_path": None})
    return {"source_units": units}, {"tables": [table]}


def citation(sources, evidence_id):
    ref, source = next((ref, unit) for ref, unit in sources.items()
                       if unit["evidence_id"] == evidence_id)
    return {"ref": ref, "quote": source["text"]}


def test_source_refs_preserve_units_and_expose_only_local_header_candidates(table_case):
    case, ir = table_case
    saved = deepcopy((case, ir))
    sources = build_sources(case, ir)
    assert list(sources) == [f"u{i}" for i in range(len(case["source_units"]))]
    assert (case, ir) == saved
    assert sources["u3"]["logical_rows"] == [1, 2]
    assert sources["u3"]["row_span"] == 2
    assert sources["u7"]["column_header_refs"] == ["u2"]
    assert sources["u2"]["is_header"]
    assert sources["u2"]["header_status"] == "heuristic_candidate"
    assert sources["u8"]["header_status"] == "not_a_table"


@pytest.mark.parametrize("owner,value,error", [
    ("api", "v1", None), ("api", "v2", None), ("trial1", "v1", None),
    ("trial2", "v2", None), ("trial1", "v2", "owner_row_mismatch"),
    ("trial2", "v1", "owner_row_mismatch"),
    ("h1", "v1", "owner_is_header_candidate"),
    ("trial1", "h2", "value_is_header_candidate"),
    ("paragraph", "v2", None),
])
def test_physical_owner_check(table_case, owner, value, error):
    sources = build_sources(*table_case)
    assert ownership_issue(sources, citation(sources, owner), citation(sources, value)) == error


def test_span_cannot_authorize_a_row_omitted_from_grid(table_case):
    case, ir = table_case
    ir["tables"][0]["grid"][2][0] = None
    sources = build_sources(case, ir)
    assert sources["u3"]["logical_rows"] == [1]
    assert ownership_issue(sources, citation(sources, "api"), citation(sources, "v2")) == (
        "owner_row_mismatch"
    )


def test_grid_cannot_authorize_a_row_outside_recorded_span(table_case):
    case, ir = table_case
    ir["tables"][0]["source_cells"][3]["row_span"] = 1
    sources = build_sources(case, ir)
    assert sources["u3"]["grid_rows"] == [1, 2]
    assert sources["u3"]["logical_rows"] == [1]


def test_cross_table_rejected_even_with_same_row_number(table_case):
    case, ir = table_case
    second = deepcopy(ir["tables"][0])
    second["table_path"] = ["table:b"]
    ir["tables"].append(second)
    extra = deepcopy(case["source_units"][5])
    extra.update(evidence_id="other_value", table_path=["table:b"])
    case["source_units"].append(extra)
    sources = build_sources(case, ir)
    assert ownership_issue(sources, citation(sources, "trial1"),
                           citation(sources, "other_value")) == "owner_table_mismatch"


def test_narrative_first_row_remains_eligible_data(table_case):
    case, ir = table_case
    narrative = "步骤一：在设备中加入物料。保持搅拌后出料。"
    case["source_units"][0]["text"] = "投料"
    case["source_units"][1]["text"] = narrative
    ir["tables"][0]["source_cells"][0]["blocks"][0]["text"] = "投料"
    ir["tables"][0]["source_cells"][1]["blocks"][0]["text"] = narrative
    sources = build_sources(case, ir)
    assert sources["u0"]["parser_header_candidate"]
    assert not sources["u0"]["is_header"]
    assert sources["u0"]["header_status"] == "unconfirmed"
    assert sources["u4"]["column_header_refs"] == []
    assert ownership_issue(sources, citation(sources, "h0"), citation(sources, "h1")) is None


def test_missing_cell_and_changed_coordinates_stay_unresolved(table_case):
    case, ir = table_case
    case["source_units"][4]["row_index"] = 2
    sources = build_sources(case, ir)
    assert ownership_issue(sources, citation(sources, "trial1"),
                           citation(sources, "v2")) == "owner_cell_structure_missing"
    sources = build_sources(case, {"tables": []})
    assert ownership_issue(sources, citation(sources, "api"),
                           citation(sources, "v1")) == "owner_cell_structure_missing"


def test_nested_table_and_merged_columns_have_local_headers(table_case):
    case, ir = table_case
    table = ir["tables"][0]
    table["source_cells"][1]["column_span"] = 2
    table["grid"][0][2] = "h1"
    table["source_cells"] = [c for c in table["source_cells"] if c["cell_id"] != "h2"]
    case["source_units"] = [u for u in case["source_units"] if u["evidence_id"] != "h2"]
    nested_ir = {"tables": [{"table_path": ["outer"], "header_row_count": 0,
                            "grid": [], "source_cells": [{"cell_id": "container",
                            "blocks": [{"kind": "table", "table": table}]}]}]}
    sources = build_sources(case, nested_ir)
    value = sources[citation(sources, "v1")["ref"]]
    assert value["column_header_refs"] == [citation(sources, "h1")["ref"]]


@pytest.mark.parametrize("reference,error", [
    ({"ref": [], "quote": "ab"}, "citation_ref_missing"),
    ({"ref": "missing", "quote": "ab"}, "citation_ref_missing"),
    ({"ref": "u0", "quote": " "}, "citation_quote_empty"),
    ({"ref": "u0", "quote": "changed"}, "citation_quote_not_in_source"),
    ({"ref": "u0", "quote": "aba"}, "citation_quote_ambiguous"),
    ({"ref": "u0", "quote": "ababa"}, None),
])
def test_quote_check_requires_unique_verbatim_position(reference, error):
    assert quote_issue({"u0": {"text": "ababa"}}, reference) == error


@pytest.mark.parametrize("marker,legend,expected", [
    (" N/A ", "", "not_applicable"), ("n/a", "", "not_applicable"),
    ("N/A 说明", "", None), ("—", "", None), ("×", "", None),
    ("—", "—是区间分隔符。", None),
    ("—", "备注：“—”代表相关的研究数据不充分。", "unknown"),
    ("×", "备注：“×”代表没有相应的毒性。", "negated"),
    ("√", "备注：“√”代表有相应毒性。", "affirmed"),
    ("×", "×不代表没有相应毒性。", None),
    ("×", "×代表没有证据表明有相应毒性。", None),
    ("×", "×代表并非没有相应毒性。", None),
    ("—", "—代表并非研究数据不充分。", None),
    ("×", "×代表没有相应毒性；×代表有相应毒性。", None),
])
def test_markers_require_explicit_nonconflicting_legend(marker, legend, expected):
    assert marker_state(marker, legend) == expected
