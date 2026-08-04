"""Tests for R-RA1~5 risk-matrix placeholder templating:
context resolution + deterministic substitution into the assessment rows."""

from __future__ import annotations

from app.models.ontology_meta import OntologyDecisionRule
from app.services.reporting.risk_report_generator import RiskReportGenerator

_DEV = "https://ontology.pharma-gmp.cn/slpra/drug-development/"
_DRUG = "https://ontology.pharma-gmp.cn/slpra/drug/"
_EQUIP = "https://ontology.pharma-gmp.cn/slpra/equipment/"


def _drug_edge(code: str = "HRS-1597", extra_props: list[dict] | None = None) -> dict:
    return {
        "subject_class_iri": _DEV + "CMCReport",
        "predicate_iri": _DEV + "describes",
        "object_class_iri": _DRUG + "DrugProduct",
        "object_text": code,
        "object_data_properties": extra_props or [],
        "source_ref": "§ 产品信息",
    }


def _equipment_edge(code: str, source_ref: str = "642车间 设备需求") -> dict:
    return {
        "subject_class_iri": _DEV + "CMCReport",
        "predicate_iri": _DEV + "usesEquipment",
        "object_class_iri": _EQUIP + "Equipment",
        "object_text": code,
        "object_data_properties": [{"iri": None, "label": "设备名称", "value": f"设备-{code}"}],
        "source_ref": source_ref,
    }


def _shared_line_edge() -> dict:
    return {
        "subject_class_iri": _DEV + "CMCReport",
        "predicate_iri": _DEV + "hasSharedLineData",
        "object_class_iri": _DEV + "SharedLineAssessmentData",
        "object_text": "共线评估",
        "object_data_properties": [],
        "source_ref": "§ 共线评估",
    }


class TestBuildTemplateContext:
    def test_drug_code_from_object_text(self, db):
        ctx = RiskReportGenerator(db)._build_template_context([_drug_edge("HRS-1597")])
        assert ctx["药物名称/代号"] == "HRS-1597"

    def test_drug_name_property_preferred_over_code(self, db):
        edge = _drug_edge("HRS-1597", extra_props=[{"label": "药物名称", "value": "某某单抗"}])
        ctx = RiskReportGenerator(db)._build_template_context([edge])
        assert ctx["药物名称/代号"] == "某某单抗"

    def test_drug_missing_is_none(self, db):
        ctx = RiskReportGenerator(db)._build_template_context([_equipment_edge("RE001")])
        assert ctx["药物名称/代号"] is None

    def test_workshops_joined_sorted(self, db):
        edges = [
            _equipment_edge("RE001", source_ref="646车间 设备需求"),
            _equipment_edge("RE002", source_ref="642车间 设备需求"),
        ]
        ctx = RiskReportGenerator(db)._build_template_context(edges)
        assert ctx["车间"] == "642车间、646车间"

    def test_numeric_data_property_value_does_not_crash(self, db):
        """回归：DrugProduct 边携带数值型（float/int）数据属性时不得抛
        ``'float' object has no attribute 'strip'``；非名称属性被跳过，回退到 object_text 代号。"""
        edge = _drug_edge(
            "HRS-1597",
            extra_props=[
                {"label": "分子量", "value": 456.7},   # float
                {"label": "批次数", "value": 3},        # int
                {"label": "是否无菌", "value": False},   # bool
            ],
        )
        ctx = RiskReportGenerator(db)._build_template_context([edge])
        assert ctx["药物名称/代号"] == "HRS-1597"

    def test_numeric_name_property_rejected_falls_back_to_code(self, db):
        """名称标签携带数值（异常抽取）时**不**被当作合法药名——既不崩溃，也不覆盖可信代号，
        而是回退到 object_text 程序代号（Codex 审查 finding 2：数值/布尔绝非合法药名）。"""
        edge = _drug_edge("HRS-1597", extra_props=[{"label": "药物名称", "value": 123}])
        ctx = RiskReportGenerator(db)._build_template_context([edge])
        assert ctx["药物名称/代号"] == "HRS-1597"

    def test_context_keys_are_only_the_two_whitelisted_placeholders(self, db):
        """白名单只有 {{药物名称/代号}} 与 {{车间}}——context 恰好这两个键，不含 剂型/生产阶段。"""
        ctx = RiskReportGenerator(db)._build_template_context([_drug_edge("HRS-1597")])
        assert set(ctx) == {"药物名称/代号", "车间"}

    def test_edge_order_does_not_change_result(self, db):
        edges = [
            _drug_edge("HRS-1597"),
            _equipment_edge("RE001", source_ref="642车间 设备需求"),
            _equipment_edge("RE002", source_ref="646车间 设备需求"),
        ]
        ctx_a = RiskReportGenerator(db)._build_template_context(edges)
        ctx_b = RiskReportGenerator(db)._build_template_context(list(reversed(edges)))
        assert ctx_a == ctx_b


def _make_templated_rule(db) -> None:
    db.add(OntologyDecisionRule(
        slpra_iri="https://ontology.pharma-gmp.cn/slpra/rules/R-RA-TPL",
        label="R-RA-TPL",
        rule_key="R-RA-TPL",
        rule_group="risk_assessment",
        antecedent={
            "op": "some_values_from",
            "property": "hasSharedLineData",
            "filler_class": "SharedLineAssessmentData",
        },
        consequent={
            "risk_level": "MediumRisk",
            "category": "人员",
            "description": "无法进行{{药物名称/代号}}在{{车间}}的临床批生产操作",
            "control_measure": "{{药物名称/代号}}岗位培训与考核",
            "traceability_docs": "培训记录",  # no placeholder — must stay verbatim
        },
        priority=100,
        status="published",
    ))
    db.commit()


class TestSubstitutionIntoRows:
    def test_placeholders_filled_in_report_rows(self, db):
        _make_templated_rule(db)
        edges = [_drug_edge("HRS-1597"), _shared_line_edge(),
                 _equipment_edge("RE001", source_ref="642车间 设备需求")]
        report = RiskReportGenerator(db).generate(edges)
        row = next(r for r in report.assessment_rows if r.hazid == "人员")
        assert row.contributing_factors == "无法进行HRS-1597在642车间的临床批生产操作"
        assert row.control_measures == "HRS-1597岗位培训与考核"
        assert row.traceability == "培训记录"
        assert "{{" not in row.contributing_factors

    def test_missing_value_renders_sentinel(self, db):
        _make_templated_rule(db)
        edges = [_shared_line_edge()]  # no drug, no equipment
        report = RiskReportGenerator(db).generate(edges)
        row = next(r for r in report.assessment_rows if r.hazid == "人员")
        assert "【待补充：药物名称/代号】" in row.contributing_factors
        assert "【待补充：车间】" in row.contributing_factors

    def test_full_report_survives_numeric_object_text(self, db):
        """回归（Codex finding 1）：数值型 object_text 经**公开报告入口** generate() 不得崩溃——
        覆盖 _detect_workshop / _build_subject_description / coverage / narrative 等相邻链路，
        而非仅私有 _build_template_context。数值代号非法 → 车间/药名回退为哨兵，但流程完整产出。"""
        _make_templated_rule(db)
        drug = _drug_edge("HRS-1597")
        drug["object_text"] = 12345          # 数值型药物代号（异常抽取）
        equip = _equipment_edge("RE001")
        equip["object_text"] = 646.0         # 数值型设备编号（异常抽取）
        report = RiskReportGenerator(db).generate([drug, equip, _shared_line_edge()])
        row = next(r for r in report.assessment_rows if r.hazid == "人员")
        assert "{{" not in row.contributing_factors  # 占位符已被替换或渲染为哨兵

    def test_assess_deterministic_matches_generate_text(self, db):
        """Preview (assess_deterministic) and the real report must produce
        byte-identical matrix text, not just identical risk levels."""
        _make_templated_rule(db)
        edges = [_drug_edge("HRS-1597"), _shared_line_edge(),
                 _equipment_edge("RE001", source_ref="642车间 设备需求")]
        report, _ = RiskReportGenerator(db).generate_with_coverage(edges)
        rows, _, _ = RiskReportGenerator(db).assess_deterministic(edges)
        assert [
            (r.hazid, r.contributing_factors, r.control_measures, r.traceability)
            for r in rows
        ] == [
            (r.hazid, r.contributing_factors, r.control_measures, r.traceability)
            for r in report.assessment_rows
        ]
