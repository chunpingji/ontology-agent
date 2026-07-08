"""Unit tests for 016+ fact-source providers (semantic-slot 事实源).

Verifies the provider registry keyed on ``FactSourceBinding.source``:
  - ``extraction`` projects edges (optional ``selector`` narrows by object class),
  - ``rule_results`` projects the deterministic ``RiskRow[]`` READ-ONLY (FR-009),
  - ``abox.*`` / ``external.*`` are registered stubs returning ``[]`` (deferred),
  - an unknown source resolves to ``[]`` (today's non-counting behaviour, no regression),
  - a provider that raises degrades to ``[]`` (a bad source never crashes a report).
"""

from __future__ import annotations

from types import SimpleNamespace

from app.services.reporting.ast_template import FactSourceBinding
from app.services.reporting.fact_sources import (
    FactContext,
    _REGISTRY,
    register_fact_source,
    resolve_fact_source,
)


def _drug_edge() -> dict:
    return {
        "object_class_iri": "https://ontology.pharma-gmp.cn/slpra/drug/DrugProduct",
        "object_text": "HRS-1234",
        "object_data_properties": [
            {"iri": None, "label": "PDE", "value": "1.80"},
            {"iri": None, "label": "分类", "value": "化学药品"},
        ],
        "source_ref": "§ 产品信息",
    }


def _equipment_edge() -> dict:
    return {
        "object_class_iri": "https://ontology.pharma-gmp.cn/slpra/equipment/Equipment",
        "object_text": "RE001",
        "object_data_properties": [{"iri": None, "label": "材质", "value": "316L"}],
        "source_ref": "642车间",
    }


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


class TestExtractionProvider:
    def test_projects_edges_as_rows(self):
        ctx = FactContext(edges=[_drug_edge()])
        binding = FactSourceBinding(source="extraction")
        rows = resolve_fact_source(binding, ctx)
        # object_text + the two data properties surface as read-only rows
        values = {r["value"] for r in rows}
        assert "HRS-1234" in values
        assert "1.80" in values
        assert "化学药品" in values

    def test_selector_narrows_by_object_class(self):
        ctx = FactContext(edges=[_drug_edge(), _equipment_edge()])
        binding = FactSourceBinding(source="extraction", selector="Equipment")
        rows = resolve_fact_source(binding, ctx)
        values = {r["value"] for r in rows}
        assert "316L" in values
        assert "HRS-1234" not in values  # drug edge filtered out by selector

    def test_empty_edges_yield_no_rows(self):
        rows = resolve_fact_source(FactSourceBinding(source="extraction"), FactContext())
        assert rows == []


class TestRuleResultsProvider:
    def test_projects_assessment_rows_readonly(self):
        ctx = FactContext(assessment_rows=[_risk_row("H-01"), _risk_row("H-02")])
        rows = resolve_fact_source(FactSourceBinding(source="rule_results"), ctx)
        assert len(rows) == 2
        assert rows[0]["label"] == "H-01"
        # the deterministic decision is quoted verbatim, not recomputed
        assert "初始风险 高" in rows[0]["value"]
        assert "残余风险 低" in rows[0]["value"]
        assert "可以接受" in rows[0]["value"]

    def test_no_rows_when_no_assessment(self):
        rows = resolve_fact_source(FactSourceBinding(source="rule_results"), FactContext())
        assert rows == []


class TestAssessmentTeamProvider:
    """016+: ``external.assessment_team`` projects the mock evaluation-team roster
    (reusing the 5 GxP roles). Exact-key registration wins over the ``external.*`` stub."""

    def test_projects_team_members_as_rows(self):
        rows = resolve_fact_source(
            FactSourceBinding(source="external.assessment_team", label="评估小组"),
            FactContext(),
        )
        assert len(rows) == 5  # one member per GxP role
        labels = {r["label"] for r in rows}
        assert {"QA", "仓储管理", "EHS评估", "生产工艺评估", "设备评估"} == labels
        # value is 姓名（部门）, and every row is attributed to the external fact source
        assert all("（" in r["value"] and r["value"].endswith("）") for r in rows)
        assert all(r["source_ref"] == "外部事实源：评估小组主数据" for r in rows)

    def test_exact_key_wins_over_external_star_stub(self):
        # the exact provider returns rows; a sibling external.* source stays empty
        assert resolve_fact_source(
            FactSourceBinding(source="external.assessment_team"), FactContext()
        )
        assert (
            resolve_fact_source(
                FactSourceBinding(source="external.something_else"), FactContext()
            )
            == []
        )


class TestRegistryResolution:
    def test_unknown_source_returns_empty(self):
        rows = resolve_fact_source(FactSourceBinding(source="does.not.exist"), FactContext())
        assert rows == []

    def test_abox_and_external_stubs_registered_and_empty(self):
        assert "abox.*" in _REGISTRY
        assert "external.*" in _REGISTRY
        ctx = FactContext(edges=[_drug_edge()])
        assert resolve_fact_source(FactSourceBinding(source="abox.personnel"), ctx) == []
        assert resolve_fact_source(FactSourceBinding(source="external.gmp_db"), ctx) == []

    def test_namespace_wildcard_falls_back_to_star_key(self):
        """A ``ns.<x>`` source with no exact match resolves to the ``ns.*`` provider."""
        rows = resolve_fact_source(FactSourceBinding(source="abox.anything_new"), FactContext())
        assert rows == []  # matched the abox.* stub, not an error

    def test_provider_error_degrades_to_empty(self):
        @register_fact_source("test.boom")
        def _boom(binding, ctx):  # pragma: no cover - body raises before returning
            raise RuntimeError("provider blew up")

        try:
            rows = resolve_fact_source(FactSourceBinding(source="test.boom"), FactContext())
            assert rows == []  # exception swallowed, report never crashes
        finally:
            _REGISTRY.pop("test.boom", None)

    def test_registered_providers_snapshot(self):
        # the four providers wired now (extraction/rule_results + two deferred stubs)
        assert {"extraction", "rule_results", "abox.*", "external.*"} <= set(_REGISTRY)
