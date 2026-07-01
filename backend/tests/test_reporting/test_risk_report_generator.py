"""Unit tests for the risk report generator (010, quickstart scenario 5)."""

from __future__ import annotations

import uuid

import pytest

from app.services.reporting.risk_report_generator import (
    EquipmentEntry,
    RiskReport,
    RiskReportGenerator,
    RiskRow,
)


def _equipment_edge(code: str, source_ref: str = "表 设备需求", spec: str = "搅拌釜 500L") -> dict:
    return {
        "subject_class_iri": "https://ontology.pharma-gmp.cn/slpra/drug-development/CMCReport",
        "predicate_iri": "https://ontology.pharma-gmp.cn/slpra/drug-development/usesEquipment",
        "object_class_iri": "https://ontology.pharma-gmp.cn/slpra/equipment/Equipment",
        "object_text": code,
        "object_data_properties": [
            {"iri": None, "label": "设备名称", "value": f"设备-{code}"},
            {"iri": None, "label": "设备规格", "value": spec},
            {"iri": None, "label": "材质", "value": "316L"},
        ],
        "source_ref": source_ref,
    }


def _drug_product_edge() -> dict:
    return {
        "subject_class_iri": "https://ontology.pharma-gmp.cn/slpra/drug-development/CMCReport",
        "predicate_iri": "https://ontology.pharma-gmp.cn/slpra/drug-development/describes",
        "object_class_iri": "https://ontology.pharma-gmp.cn/slpra/drug/DrugProduct",
        "object_text": "HRS-1234",
        "object_data_properties": [
            {"iri": "https://ontology.pharma-gmp.cn/slpra/drug/pde_mg_per_day",
             "label": "PDE", "value": "1.80"},
            {"iri": None, "label": "分类", "value": "化学药品"},
        ],
        "source_ref": "§ 产品信息",
    }


def _shared_line_edge() -> dict:
    return {
        "subject_class_iri": "https://ontology.pharma-gmp.cn/slpra/drug-development/CMCReport",
        "predicate_iri": "https://ontology.pharma-gmp.cn/slpra/drug-development/hasSharedLineData",
        "object_class_iri": "https://ontology.pharma-gmp.cn/slpra/drug-development/SharedLineAssessmentData",
        "object_text": "共线评估",
        "object_data_properties": [
            {"iri": "https://ontology.pharma-gmp.cn/slpra/drug/pde_mg_per_day",
             "label": "PDE", "value": "1.80"},
        ],
        "source_ref": "§ 共线评估",
    }


def _make_risk_rule(
    db,
    *,
    key: str,
    category: str,
    risk_level: str = "HighRisk",
    antecedent: dict | None = None,
    postconditions: dict | None = None,
) -> None:
    """Insert a risk_assessment DecisionRule directly into the test DB."""
    from app.models.ontology_meta import OntologyDecisionRule

    rule = OntologyDecisionRule(
        slpra_iri=f"https://ontology.pharma-gmp.cn/slpra/rules/{key}",
        label=f"Rule {key}",
        rule_key=key,
        rule_group="risk_assessment",
        antecedent=antecedent or {
            "op": "some_values_from",
            "property": "hasSharedLineData",
            "filler_class": "SharedLineAssessmentData",
        },
        consequent={
            "risk_level": risk_level,
            "category": category,
            "description": f"风险因素：{category}",
            "control_measure": f"控制措施：{category}",
            "traceability_docs": f"追溯文件：{category}",
            "postconditions": postconditions or {},
        },
        priority=100,
        status="published",
    )
    db.add(rule)
    db.commit()


class TestRiskReportGenerator:
    def test_pre_control_level_high_when_rule_fires(self, db):
        _make_risk_rule(db, key="R-RA-EQ", category="生产设备", risk_level="HighRisk")
        edges = [_shared_line_edge(), _equipment_edge("RE001")]
        gen = RiskReportGenerator(db)
        report = gen.generate(edges, source_filename="HRS-1234.docx")
        eq_row = next((r for r in report.assessment_rows if r.hazid == "生产设备"), None)
        assert eq_row is not None
        assert eq_row.pre_control_level == "高"

    def test_post_control_level_drops_with_postconditions(self, db):
        _make_risk_rule(
            db, key="R-RA-EQ2", category="生产设备", risk_level="HighRisk",
            postconditions={"equipment_qualified": True, "shared_line_assessed": True},
        )
        edges = [_shared_line_edge(), _equipment_edge("RE001")]
        gen = RiskReportGenerator(db)
        report = gen.generate(edges, source_filename="HRS-1234.docx")
        eq_row = next((r for r in report.assessment_rows if r.hazid == "生产设备"), None)
        assert eq_row is not None
        assert eq_row.pre_control_level == "高"
        assert eq_row.post_control_level == "低"
        assert eq_row.status == "可以接受"

    def test_equipment_grouping_by_workshop(self, db):
        edges = [
            _equipment_edge("RE001", source_ref="642车间 设备需求"),
            _equipment_edge("RE002", source_ref="646车间 设备需求"),
        ]
        gen = RiskReportGenerator(db)
        report = gen.generate(edges)
        assert "642车间" in report.equipment_tables
        assert "646车间" in report.equipment_tables

    def test_subject_description_from_drug_product(self, db):
        edges = [_drug_product_edge()]
        gen = RiskReportGenerator(db)
        report = gen.generate(edges, source_filename="HRS-1234.docx")
        assert "HRS-1234" in report.subject_description

    def test_unknown_evaluation_flags_pending_not_low(self, db):
        """G1 (AST-4): missing data must surface as 待评估, not silent 低."""
        from app.services.reporting.risk_report_generator import (
            PENDING_LEVEL,
            PENDING_STATUS,
        )

        _make_risk_rule(
            db, key="R-RA-MISS", category="人员",
            antecedent={"op": "literal_eq", "key": "nonexistent_property", "value": "yes"},
        )
        edges = [_equipment_edge("RE001")]
        gen = RiskReportGenerator(db)
        report = gen.generate(edges)
        row = next((r for r in report.assessment_rows if r.hazid == "人员"), None)
        assert row is not None
        assert row.pre_control_level == PENDING_LEVEL
        assert row.post_control_level == PENDING_LEVEL
        assert row.status == PENDING_STATUS

    def test_empty_rules_produce_empty_assessment(self, db):
        edges = [_equipment_edge("RE001")]
        gen = RiskReportGenerator(db)
        report = gen.generate(edges)
        assert report.assessment_rows == []

    def test_ungrouped_equipment_gets_note(self, db):
        edges = [_equipment_edge("RE999", source_ref="某工序", spec="反应釜 100L")]
        gen = RiskReportGenerator(db)
        report = gen.generate(edges)
        assert "未分组" in report.equipment_tables
        assert len(report.equipment_notes) == 1
        assert "人工确认" in report.equipment_notes[0]

    def test_grouped_equipment_no_note(self, db):
        edges = [_equipment_edge("RE001", source_ref="642车间 设备需求")]
        gen = RiskReportGenerator(db)
        report = gen.generate(edges)
        assert "未分组" not in report.equipment_tables
        assert report.equipment_notes == []


class TestGenerateWithCoverage:
    def test_returns_report_and_manifest(self, db):
        _make_risk_rule(db, key="R-RA-EQ", category="生产设备", risk_level="HighRisk")
        edges = [_drug_product_edge(), _shared_line_edge(), _equipment_edge("RE001")]
        gen = RiskReportGenerator(db)
        report, manifest = gen.generate_with_coverage(edges, source_filename="HRS-1234.docx")
        assert report.assessment_rows  # report still built
        assert manifest.template_id == "QS-A-020F05@v1"
        # all prerequisites present → no omissions
        assert not manifest.has_omissions
        # generator exposes the same manifest via property
        assert gen.coverage is manifest

    def test_missing_shared_line_surfaces_in_manifest(self, db):
        _make_risk_rule(db, key="R-RA-EQ", category="生产设备", risk_level="HighRisk")
        edges = [_drug_product_edge(), _equipment_edge("RE001")]  # no shared-line
        gen = RiskReportGenerator(db)
        report, manifest = gen.generate_with_coverage(edges)
        assert manifest.has_omissions
        missing = {s.slot_id for s in manifest.missing_required_slots}
        assert "prereq.shared_line" in missing
        # report still generates (backward compatible)
        assert report.doc_no == "QS-A-020F05"

    def test_generate_backward_compatible(self, db):
        edges = [_equipment_edge("RE001", source_ref="642车间 设备需求")]
        gen = RiskReportGenerator(db)
        report = gen.generate(edges)  # legacy single-return path
        assert isinstance(report, RiskReport)
        assert gen.coverage is not None  # manifest still computed


class TestDocxRenderer:
    def test_render_produces_valid_docx_bytes(self):
        from app.services.reporting.docx_renderer import render_risk_report

        report = RiskReport(
            subject_description="Test subject",
            equipment_tables={
                "642车间": [
                    EquipmentEntry(seq=1, equipment_id="RE001", name="搅拌釜",
                                   spec="500L", material="316L"),
                ],
            },
            assessment_rows=[
                RiskRow(hazid="人员", contributing_factors="factor",
                        pre_control_level="高", post_control_level="低",
                        control_measures="measure", traceability="doc",
                        status="可以接受"),
            ],
        )
        result = render_risk_report(report)
        assert isinstance(result, bytes)
        assert len(result) > 0
        assert result[:2] == b"PK"  # .docx is a ZIP archive

    def test_render_with_manifest_embeds_coverage(self, db):
        """AST-5: a manifest with omissions surfaces 待补充 text in the docx."""
        from docx import Document as _Doc
        import io as _io

        from app.services.reporting.coverage_validator import validate_coverage
        from app.services.reporting.docx_renderer import render_risk_report
        from app.services.reporting.ast_template import load_default_template

        _make_risk_rule(db, key="R-RA-EQ", category="生产设备", risk_level="HighRisk")
        edges = [_drug_product_edge(), _equipment_edge("RE001")]  # no shared-line
        from app.models.ontology_meta import OntologyDecisionRule
        rules = db.query(OntologyDecisionRule).all()
        manifest = validate_coverage(load_default_template(), edges, rules)
        assert manifest.has_omissions

        report = RiskReport(subject_description="HRS-1234")
        out = render_risk_report(report, manifest)
        text = "\n".join(
            p.text for p in _Doc(_io.BytesIO(out)).paragraphs
        )
        assert "待补充" in text
        assert "Outstanding Materials" in text
