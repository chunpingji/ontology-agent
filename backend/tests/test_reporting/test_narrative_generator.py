"""Tests for 013 narrative generation (T025).

Verifies:
  - Output uses only supplied facts (SC-006)
  - Template prose passed as few-shot context
  - LLM failure returns empty dict
  - Regeneration is stateless (no persistence)
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock

from app.services.reporting.ast_template import (
    Group,
    OntologyRelationBinding,
    ReportTemplate,
    Section,
    SemanticSource,
    Slot,
    coverage_key,
)
from app.services.reporting.narrative_generator import (
    generate_narratives,
    generate_semantic_slots,
    preview_section_narrative,
)

from tests.fixtures.ontology import (
    DRUG_PRODUCT,
    MANUFACTURED_BY,
    MANUFACTURER,
    MFR_NAME,
    build_drug_ontology,
)


def _make_client(response: dict):
    """Return a mock OpenAI client that returns a canned response."""
    client = MagicMock()
    resp = MagicMock()
    choice = MagicMock()
    choice.message.content = json.dumps(response, ensure_ascii=False)
    resp.choices = [choice]
    client.chat.completions.create.return_value = resp
    return client


_SAMPLE_EDGES = [
    {
        "object_class_iri": "http://slpra/ontology#DrugProduct",
        "object_text": "XX注射液",
        "subject_text": "测试药品",
        "object_data_properties": [
            {"label": "剂型", "value": "注射剂"},
        ],
    },
]

_SAMPLE_TEMPLATE = MagicMock()
_SAMPLE_TEMPLATE.sections = [
    MagicMock(title="评估对象", groups=[MagicMock(title="药品信息")]),
]

_LLM_RESPONSE = {
    "subject_description": "XX注射液是一种注射剂型药品。",
    "conclusion": "本次风险评估表明各维度风险可控。",
    "dimension_narratives": [
        {"dimension": "人员", "narrative": "人员培训到位，风险可控。"},
    ],
}


class TestGenerateNarratives:
    def test_successful_generation(self):
        client = _make_client(_LLM_RESPONSE)
        result = generate_narratives(_SAMPLE_EDGES, _SAMPLE_TEMPLATE, client)

        assert "subject_description" in result
        assert "conclusion" in result
        assert "narrative.人员" in result
        assert "XX注射液" in result["subject_description"]

    def test_llm_failure_returns_empty(self):
        client = MagicMock()
        client.chat.completions.create.side_effect = Exception("LLM down")
        result = generate_narratives(_SAMPLE_EDGES, _SAMPLE_TEMPLATE, client)
        assert result == {}

    def test_empty_edges_returns_empty(self):
        client = MagicMock()
        result = generate_narratives([], _SAMPLE_TEMPLATE, client)
        assert result == {}

    def test_stateless_no_persistence(self):
        client = _make_client(_LLM_RESPONSE)
        r1 = generate_narratives(_SAMPLE_EDGES, _SAMPLE_TEMPLATE, client)
        r2 = generate_narratives(_SAMPLE_EDGES, _SAMPLE_TEMPLATE, client)
        assert r1 == r2
        assert r1 is not r2

    def test_template_style_passed_to_prompt(self):
        client = _make_client(_LLM_RESPONSE)
        generate_narratives(_SAMPLE_EDGES, _SAMPLE_TEMPLATE, client)

        call_args = client.chat.completions.create.call_args
        messages = call_args.kwargs.get("messages", [])
        user_msg = next((m["content"] for m in messages if m["role"] == "user"), "")
        assert "评估对象" in user_msg or "参考模板" in user_msg


# --------------------------------------------------------------------------- #
# 016+: generate_semantic_slots — LLM synthesis projecting Section.coverage + prompt
# --------------------------------------------------------------------------- #

_SEMANTIC_RESPONSE = {"content": "本节综述：由 Acme 制药生产，风险经确定性评估为可控。"}


def _mfr_edge(name: str = "Acme 制药") -> dict:
    return {
        "predicate_iri": MANUFACTURED_BY,
        "object_class_iri": MANUFACTURER,
        "object_text": None,
        "object_data_properties": [{"iri": MFR_NAME, "label": "企业名称", "value": name}],
        "source_ref": "§ 生产信息",
    }


def _drug_edge() -> dict:
    return {
        "predicate_iri": "x:describes",
        "object_class_iri": DRUG_PRODUCT,
        "object_text": "HRS-1234",
        "object_data_properties": [],
        "source_ref": "§ 产品信息",
    }


def _rel(**kw) -> OntologyRelationBinding:
    return OntologyRelationBinding(
        doc_class_iri=DRUG_PRODUCT,
        predicate_iri=MANUFACTURED_BY,
        range_class_iri=MANUFACTURER,
        **kw,
    )


def _risk_row(hazid: str = "H-01") -> SimpleNamespace:
    return SimpleNamespace(
        hazid=hazid,
        contributing_factors="共线生产",
        pre_control_level="高",
        post_control_level="低",
        control_measures="清洁验证",
        traceability="SOP-001",
        status="可以接受",
    )


def _semantic_template(
    *, slot_source: SemanticSource | None = None, section_prompt: str | None = "本节行文指令",
    coverage: list | None = None,
) -> ReportTemplate:
    return ReportTemplate(
        template_id="t-sem",
        sections=[Section(
            section_id="s-sem",
            title="综合分析",
            prompt=section_prompt,
            coverage=coverage if coverage is not None else [_rel()],
            groups=[Group(
                group_id="g-sem", title="综述", kind="fields",
                slots=[Slot(
                    slot_id="analysis.overview", label="综合分析",
                    source=slot_source or SemanticSource(),
                )],
            )],
        )],
    )


def _user_prompt(client) -> str:
    """The user-role content of the most recent captured LLM call."""
    messages = client.chat.completions.create.call_args.kwargs.get("messages", [])
    return next((m["content"] for m in messages if m["role"] == "user"), "")


class TestGenerateSemanticSlots:
    def test_synthesizes_slot_text(self):
        client = _make_client(_SEMANTIC_RESPONSE)
        engine = build_drug_ontology()
        out = generate_semantic_slots(
            [_mfr_edge(), _drug_edge()], _semantic_template(), client, engine=engine,
        )
        assert len(out) == 1
        assert out[0]["slot_id"] == "analysis.overview"
        assert out[0]["section_id"] == "s-sem"
        assert out[0]["text"] == _SEMANTIC_RESPONSE["content"]

    def test_prompt_inherits_section_when_slot_prompt_none(self):
        client = _make_client(_SEMANTIC_RESPONSE)
        engine = build_drug_ontology()
        tpl = _semantic_template(section_prompt="节级行文指令ABC", slot_source=SemanticSource())
        generate_semantic_slots([_mfr_edge()], tpl, client, engine=engine)
        assert "节级行文指令ABC" in _user_prompt(client)

    def test_slot_prompt_overrides_section_prompt(self):
        client = _make_client(_SEMANTIC_RESPONSE)
        engine = build_drug_ontology()
        tpl = _semantic_template(
            section_prompt="节级指令", slot_source=SemanticSource(prompt="插槽自有指令XYZ"),
        )
        generate_semantic_slots([_mfr_edge()], tpl, client, engine=engine)
        user = _user_prompt(client)
        assert "插槽自有指令XYZ" in user
        assert "节级指令" not in user

    def test_associated_ontology_scoped_to_range_type(self):
        """The associated-ontology block carries the range-type facts (Manufacturer),
        NOT the unrelated drug edge — semantic slot narrates only its coverage slice."""
        client = _make_client(_SEMANTIC_RESPONSE)
        engine = build_drug_ontology()
        generate_semantic_slots(
            [_mfr_edge(name="Acme 制药"), _drug_edge()], _semantic_template(), client, engine=engine,
        )
        user = _user_prompt(client)
        assert "Acme 制药" in user  # in-scope manufacturer fact present
        assert "HRS-1234" not in user  # out-of-scope drug fact excluded

    def test_coverage_refs_filters_selected_bindings(self):
        """``coverage_refs`` selects WHICH section bindings feed the slot ([] ⇒ all)."""
        client = _make_client(_SEMANTIC_RESPONSE)
        engine = build_drug_ontology()
        distributed_by = "https://ontology.pharma-gmp.cn/slpra/drug/distributedBy"
        made_by, dist_by = _rel(), OntologyRelationBinding(
            doc_class_iri=DRUG_PRODUCT, predicate_iri=distributed_by, range_class_iri=MANUFACTURER,
        )
        # select only the manufacturedBy binding
        tpl = _semantic_template(
            coverage=[made_by, dist_by],
            slot_source=SemanticSource(coverage_refs=[coverage_key(made_by)]),
        )
        out = generate_semantic_slots([_mfr_edge()], tpl, client, engine=engine)
        assert len(out) == 1  # produced, using only the selected binding

    def test_assessment_rows_quoted_verbatim_for_fr009(self):
        """FR-009: the deterministic risk rows appear VERBATIM in the LLM user prompt as
        read-only context — the LLM quotes, never recomputes, the decided levels."""
        client = _make_client(_SEMANTIC_RESPONSE)
        engine = build_drug_ontology()
        rows = [_risk_row("HAZ-上下文标记")]
        generate_semantic_slots(
            [_mfr_edge()], _semantic_template(), client,
            assessment_rows=rows, engine=engine,
        )
        user = _user_prompt(client)
        assert "HAZ-上下文标记" in user
        assert "共线生产" in user
        assert "确定性结论" in user  # framed read-only, must-quote

    def test_engine_none_uses_local_name_substring_fallback(self):
        client = _make_client(_SEMANTIC_RESPONSE)
        out = generate_semantic_slots([_mfr_edge(name="离线企业")], _semantic_template(), client)
        assert len(out) == 1
        assert "离线企业" in _user_prompt(client)

    def test_llm_failure_skips_slot(self):
        client = MagicMock()
        client.chat.completions.create.side_effect = Exception("LLM down")
        out = generate_semantic_slots([_mfr_edge()], _semantic_template(), client,
                                      engine=build_drug_ontology())
        assert out == []

    def test_empty_llm_output_skips_slot(self):
        client = _make_client({"content": "   "})
        out = generate_semantic_slots([_mfr_edge()], _semantic_template(), client,
                                      engine=build_drug_ontology())
        assert out == []

    def test_slot_with_no_prompt_and_no_coverage_is_skipped(self):
        client = _make_client(_SEMANTIC_RESPONSE)
        tpl = _semantic_template(section_prompt=None, coverage=[])
        out = generate_semantic_slots([_mfr_edge()], tpl, client)
        assert out == []
        client.chat.completions.create.assert_not_called()

    def test_non_semantic_slots_ignored(self):
        """A template without any semantic slot yields no semantic-slot output."""
        from app.services.reporting.ast_template import ManualSource

        client = _make_client(_SEMANTIC_RESPONSE)
        tpl = ReportTemplate(
            template_id="t-legacy",
            sections=[Section(section_id="s0", title="节", coverage=[_rel()], groups=[Group(
                group_id="g0", title="t", kind="manual",
                slots=[Slot(slot_id="m0", label="人工", source=ManualSource())],
            )])],
        )
        out = generate_semantic_slots([_mfr_edge()], tpl, client, engine=build_drug_ontology())
        assert out == []
        client.chat.completions.create.assert_not_called()


# --------------------------------------------------------------------------- #
# 015+: preview_section_narrative — design-time 行文 Prompt preview (real facts)
# --------------------------------------------------------------------------- #


def _preview_section(prompt: str | None) -> Section:
    """A one-off ReportTemplate Section carrying the (unsaved) 行文 Prompt under test."""
    return Section(
        section_id="s-preview",
        title="评估对象",
        prompt=prompt,
        groups=[Group(
            group_id="g", title="药品信息", kind="fields",
            slots=[Slot(slot_id="drug.name", label="药品名称", source=SemanticSource())],
        )],
    )


_PREVIEW_EDGES = [
    {
        "object_class_iri": "http://slpra/ontology#DrugProduct",
        "object_text": "XX注射液",
        "subject_text": "",
        "object_data_properties": [{"label": "药品名称", "value": "XX注射液"}],
    },
]


class TestPreviewSectionNarrative:
    def test_returns_section_prose(self):
        client = _make_client({"content": "本品为 XX 注射液，属注射剂。"})
        text = preview_section_narrative(
            _PREVIEW_EDGES, _preview_section("描述 {{药品名称}}。"), client,
        )
        assert "XX 注射液" in text

    def test_empty_prompt_returns_empty_and_skips_llm(self):
        client = _make_client({"content": "不应被调用"})
        assert preview_section_narrative(_PREVIEW_EDGES, _preview_section(""), client) == ""
        client.chat.completions.create.assert_not_called()

    def test_none_prompt_returns_empty(self):
        client = _make_client({"content": "x"})
        assert preview_section_narrative(_PREVIEW_EDGES, _preview_section(None), client) == ""

    def test_empty_llm_output_returns_empty(self):
        client = _make_client({"content": "   "})
        assert preview_section_narrative(_PREVIEW_EDGES, _preview_section("描述"), client) == ""

    def test_assessment_rows_quoted_verbatim_fr009(self):
        """§5.4 fidelity: the deterministic risk rows the real report quotes appear
        VERBATIM in the preview's LLM prompt as read-only context (FR-009) — the preview
        reflects the same decided levels the rendered report would."""
        client = _make_client({"content": "正文"})
        preview_section_narrative(
            _PREVIEW_EDGES, _preview_section("描述"), client,
            assessment_rows=[_risk_row("HAZ-预览标记")],
        )
        user = _user_prompt(client)
        assert "HAZ-预览标记" in user
        assert "共线生产" in user
        assert "确定性结论" in user  # framed read-only, must-quote

    def test_manifest_coverage_injected(self):
        """The coverage status the rendered report quotes is injected read-only too, so
        preview prose can mirror the same 覆盖状态 (§5.4)."""
        client = _make_client({"content": "正文"})
        manifest = SimpleNamespace(slots=[
            SimpleNamespace(
                slot_id="s-preview.drug", label="覆盖标记LBL",
                status="filled", value="覆盖值VAL",
            ),
        ])
        preview_section_narrative(
            _PREVIEW_EDGES, _preview_section("描述"), client, manifest=manifest,
        )
        user = _user_prompt(client)
        assert "本章节覆盖状态" in user
        assert "覆盖标记LBL" in user
