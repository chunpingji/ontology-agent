"""Risk assessment report generator (010, FR-002/FR-003/FR-004).

Orchestrates:  edges → Facts bridging → DecisionRule evaluation (pre/post control)
→ RiskReport dataclass ready for docx rendering.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.orm import Session

from app.models.ontology_meta import OntologyDecisionRule
from app.services.reasoning.fact_bridge import apply_postconditions, edges_to_facts
from app.services.reasoning.interpreter import FALSE, TRUE, evaluate
from app.services.reporting.ast_template import ReportTemplate, load_default_template
from app.services.reporting.coverage_validator import CoverageManifest, validate_coverage

logger = logging.getLogger(__name__)

HAZID_DIMENSIONS = ("人员", "生产设备", "物料管理", "文件", "三废处理")

RISK_LEVEL_MAP = {
    "HighRisk": "高",
    "MediumRisk": "中",
    "LowRisk": "低",
}

# G1 three-state correction (AST-4): an UNKNOWN evaluation means the underlying
# fact is missing — it must NOT silently collapse to "低". These sentinels keep
# "确认低风险" distinct from "无数据". The level text matches the template's
# default ``missing_placeholder`` so coverage and rendering stay consistent.
PENDING_LEVEL = "⚠ 待评估（数据缺失）"
PENDING_STATUS = "待评估"


@dataclass
class EquipmentEntry:
    seq: int
    equipment_id: str
    name: str
    spec: str
    material: str


@dataclass
class RiskRow:
    hazid: str
    contributing_factors: str
    pre_control_level: str
    post_control_level: str
    control_measures: str
    traceability: str
    status: str


@dataclass
class RiskReport:
    doc_no: str = "QS-A-020F05"
    revision: str = "00"
    effective_date: str = ""
    subject_description: str = ""
    equipment_tables: dict[str, list[EquipmentEntry]] = field(default_factory=dict)
    equipment_notes: list[str] = field(default_factory=list)
    team_members: list[dict] = field(default_factory=list)
    assessment_rows: list[RiskRow] = field(default_factory=list)
    qa_comments: str = ""
    approvers: list[dict] = field(default_factory=list)
    risk_review: str = ""
    conclusion: str = ""
    # 013: LLM-sourced content tracking for DOCX annotation
    llm_supplements: dict[str, str] = field(default_factory=dict)
    llm_generated_fields: set[str] = field(default_factory=set)
    # 015: per-section 行文 narrative prose (list of {section_id, title, text}).
    # Kept structured (not in llm_supplements) so it renders under its own section
    # headings and is persisted for the web report reading pane.
    section_narratives: list[dict] = field(default_factory=list)
    # 016+: per-semantic-slot LLM-synthesized text (list of {slot_id, section_id, text}).
    # A semantic slot projects its Section's coverage + prompt; the LLM fuses prompt +
    # associated ontology + fact sources + deterministic rule results (read-only, FR-009).
    semantic_slots: list[dict] = field(default_factory=list)


def _template_has_section_prompt(template: Any) -> bool:
    """True if any section carries a non-empty 行文 ``prompt`` (015).

    Such templates drive report prose the section-level way: one ``{content}`` LLM
    call per prompted section (:func:`generate_section_narratives`). Templates
    without any section prompt fall back to the legacy 013 whole-report narrative
    pass (:func:`generate_narratives`) — see ``_try_narrative_generation``."""
    for sec in getattr(template, "sections", None) or []:
        prompt = getattr(sec, "prompt", None)
        if prompt and str(prompt).strip():
            return True
    return False


class RiskReportGenerator:
    """Generate a RiskReport from extraction edges and declarative rules."""

    def __init__(self, db: Session, template: ReportTemplate | None = None):
        self._db = db
        self._template = template or load_default_template()
        self._last_manifest: CoverageManifest | None = None

    def generate(self, edges: list[dict], source_filename: str = "") -> RiskReport:
        """Build a RiskReport (backward-compatible; manifest available via ``coverage``)."""
        report, _ = self.generate_with_coverage(edges, source_filename)
        return report

    def generate_with_coverage(
        self,
        edges: list[dict],
        source_filename: str = "",
        dismissed_slot_ids: set[str] | None = None,
        document_path: str | None = None,
    ) -> tuple[RiskReport, CoverageManifest]:
        """Build the report AND the material-coverage manifest in one pass (AST-3).

        Report prose is produced the section-level way (逐节生成后组装): each section
        with a 行文 ``prompt`` yields its narrative via ONE ``{content}`` LLM call
        (:func:`generate_section_narratives`); deterministic slots (tables / values /
        rosters) fill without the LLM. ``document_path`` is accepted for call-site
        compatibility but no longer used (the source-document gap-fill pass was
        removed). LLM prose never alters deterministic evaluation (FR-009).
        """
        # Ontology-aware fact building (014 US3): pass the loaded engine so
        # hierarchy membership / domain gating / alignments / vocab apply. Falls
        # back to legacy string matching when the ontology is not loaded.
        from app.services.ontology_engine import get_loaded_engine

        engine = get_loaded_engine()
        # Gap B (016+): materialize the PRODUCT report's own declared relations
        # (评估小组 / 审批人小组 …) as edges from master data, so they flow through the
        # single fact spine exactly like source-doc edges. No-op unless the template
        # declares the predicate as ontology_relation coverage (zero blast radius).
        edges = self._enriched_edges(edges, engine)
        facts = edges_to_facts(edges, engine)
        rules = self._load_rules()
        pre_rows = self._evaluate_rules(rules, facts)
        post_rows = self._evaluate_post_control(rules, facts, pre_rows)

        subject = self._build_subject_description(edges, source_filename)
        equipment_tables = self._build_equipment_tables(edges)
        equipment_notes: list[str] = []
        if "未分组" in equipment_tables:
            equipment_notes.append("以下设备未能自动识别所属车间，请人工确认归属。")

        report = RiskReport(
            doc_no=self._template.doc_no,
            revision=self._template.revision,
            subject_description=subject,
            equipment_tables=equipment_tables,
            equipment_notes=equipment_notes,
            assessment_rows=post_rows,
        )

        manifest = validate_coverage(
            self._template, edges, rules, facts,
            dismissed_slot_ids=dismissed_slot_ids,
            engine=engine,  # 016: expand section.coverage into ontology positions
        )
        self._last_manifest = manifest

        self._try_narrative_generation(report, edges, facts=facts, engine=engine)

        return report, manifest

    def assess_deterministic(
        self,
        edges: list[dict],
        *,
        dismissed_slot_ids: set[str] | None = None,
    ) -> tuple[list[RiskRow], CoverageManifest, list[dict]]:
        """Deterministic-only core (NO LLM): extracted facts → post-control risk
        rows + coverage manifest + the enriched edge list.

        Mirrors the deterministic head of :meth:`generate_with_coverage` so a
        design-time 行文 Prompt *preview* can reuse the exact risk levels and
        coverage status the real report would quote (§5.4 read-only context)
        WITHOUT firing the narrative / value-merge LLM passes (which would issue
        one LLM call per section).

        Returns the Gap-B-enriched ``edges`` (source-doc + declared product-report
        relations) as the third element so the preview endpoint can feed the SAME
        list to :func:`preview_section_narrative` — no re-enrichment, no double edge.
        """
        from app.services.ontology_engine import get_loaded_engine

        engine = get_loaded_engine()
        edges = self._enriched_edges(edges, engine)
        facts = edges_to_facts(edges, engine)
        rules = self._load_rules()
        pre_rows = self._evaluate_rules(rules, facts)
        post_rows = self._evaluate_post_control(rules, facts, pre_rows)

        manifest = validate_coverage(
            self._template, edges, rules, facts,
            dismissed_slot_ids=dismissed_slot_ids,
            engine=engine,
        )
        self._last_manifest = manifest
        return post_rows, manifest, edges

    def _enriched_edges(self, edges: list[dict], engine: Any) -> list[dict]:
        """Source-doc edges + the product report's own declared relations (Gap B).

        A new list is returned (inputs untouched). Empty add — hence byte-identical
        output — unless ``self._template`` declares a product-report predicate with a
        registered finder (:func:`product_report_edges_for_template`)."""
        from app.services.reporting.product_report_edges import (
            product_report_edges_for_template,
        )

        product_edges = product_report_edges_for_template(engine, self._template)
        return edges + product_edges if product_edges else edges

    def _try_narrative_generation(
        self,
        report: RiskReport,
        edges: list[dict],
        *,
        facts: Any | None = None,
        engine: Any | None = None,
    ) -> None:
        """Generate narrative prose via LLM if the feature flag is on (013 US3).

        Section-level model (逐节生成后组装): a template that carries section 行文
        prompts produces its prose one ``{content}`` LLM call per prompted section
        (:func:`generate_section_narratives`); semantic-slot text is absorbed into
        that section narrative (``report.semantic_slots`` stays empty). Templates
        authored without any section prompt fall back to the legacy whole-report
        pass (:func:`generate_narratives`).

        ``facts``/``engine`` (already computed by ``generate_with_coverage``) are
        threaded through so section coverage can scope the *associated ontology*
        facts by range type. All LLM output here is additive; it never re-enters
        the deterministic evaluation (FR-009)."""
        from app.config import settings

        if not settings.llm_report_narrative_enabled:
            return

        from app.services.llm.local_client import get_local_llm

        client = get_local_llm()
        if client is None:
            return

        if _template_has_section_prompt(self._template):
            # 015/016: per-section 行文 narrative — one {content} call per prompted
            # section. §5.4: inject the deterministic rule results + coverage manifest
            # read-only. A section covering several relations feeds each roster under
            # its own ### label heading (see generate_section_narratives). Semantic
            # slots are absorbed here, so no separate per-slot synthesis pass runs.
            from app.services.reporting.narrative_generator import (
                generate_section_narratives,
            )

            report.section_narratives = generate_section_narratives(
                edges,
                self._template,
                client,
                assessment_rows=report.assessment_rows,
                manifest=self._last_manifest,
                engine=engine,
                skip_semantic_sections=False,
            )
            report.semantic_slots = []  # absorbed into the section narratives
            return

        # Legacy fallback: templates authored without any section prompt keep the
        # 013 whole-report narrative (subject_description / conclusion / dimensions).
        from app.services.reporting.narrative_generator import generate_narratives

        narratives = generate_narratives(edges, self._template, client)
        for field_name, text in narratives.items():
            if field_name == "subject_description" and text:
                report.subject_description = text
                report.llm_generated_fields.add("subject_description")
            elif field_name == "conclusion" and text:
                report.conclusion = text
                report.llm_generated_fields.add("conclusion")
            elif field_name.startswith("narrative.") and text:
                report.llm_supplements[field_name] = text
                report.llm_generated_fields.add(field_name)

    @property
    def rules_fired_count(self) -> int:
        return self._last_fired_count

    @property
    def coverage(self) -> CoverageManifest | None:
        """Coverage manifest from the most recent generate call (AST-2/AST-6)."""
        return self._last_manifest

    def _load_rules(self) -> list[OntologyDecisionRule]:
        return (
            self._db.query(OntologyDecisionRule)
            .filter(
                OntologyDecisionRule.rule_group == "risk_assessment",
                OntologyDecisionRule.is_disabled == False,  # noqa: E712
            )
            .order_by(OntologyDecisionRule.priority)
            .all()
        )

    def _evaluate_rules(
        self, rules: list[OntologyDecisionRule], facts: Any
    ) -> list[RiskRow]:
        rows: list[RiskRow] = []
        fired = 0
        for rule in rules:
            result = evaluate(rule.antecedent, facts)
            consequent = rule.consequent or {}
            risk_level = consequent.get("risk_level", "LowRisk")
            level_zh = RISK_LEVEL_MAP.get(risk_level, "低")

            # G1 three-state: TRUE → declared level; FALSE → 确认低风险;
            # UNKNOWN → 数据缺失，显式待评估（不静默兜底为 低）。
            if result is TRUE:
                fired += 1
                pre_control_level = level_zh
            elif result is FALSE:
                pre_control_level = "低"
            else:  # UNKNOWN
                pre_control_level = PENDING_LEVEL

            rows.append(RiskRow(
                hazid=consequent.get("category", ""),
                contributing_factors=consequent.get("description", ""),
                pre_control_level=pre_control_level,
                post_control_level="",
                control_measures=consequent.get("control_measure", ""),
                traceability=consequent.get("traceability_docs", ""),
                status="",
            ))
        self._last_fired_count = fired
        return rows

    def _evaluate_post_control(
        self,
        rules: list[OntologyDecisionRule],
        facts: Any,
        pre_rows: list[RiskRow],
    ) -> list[RiskRow]:
        for i, rule in enumerate(rules):
            row = pre_rows[i]

            # G1: a dimension whose pre-control level is undeterminable (missing
            # data) cannot be claimed acceptable by applying postconditions —
            # carry the pending state through to status.
            if row.pre_control_level == PENDING_LEVEL:
                row.post_control_level = PENDING_LEVEL
                row.status = PENDING_STATUS
                continue

            postconditions = (rule.consequent or {}).get("postconditions", {})
            if postconditions:
                row.post_control_level = "低"
            else:
                row.post_control_level = row.pre_control_level

            row.status = "可以接受" if row.post_control_level == "低" else "不可接受"
        return pre_rows

    def _build_subject_description(
        self, edges: list[dict], source_filename: str
    ) -> str:
        drug_info: list[str] = []
        for edge in edges:
            obj_class = edge.get("object_class_iri", "")
            if "DrugProduct" in obj_class:
                text = edge.get("object_text") or edge.get("subject_text", "")
                if text and text not in drug_info:
                    drug_info.append(text)
                for dp in edge.get("object_data_properties") or []:
                    lbl = dp.get("label", "")
                    val = dp.get("value", "")
                    if lbl and val:
                        entry = f"{lbl}：{val}"
                        if entry not in drug_info:
                            drug_info.append(entry)
        if drug_info:
            return "；".join(drug_info)
        return source_filename or ""

    def _build_equipment_tables(
        self, edges: list[dict]
    ) -> dict[str, list[EquipmentEntry]]:
        tables: dict[str, list[EquipmentEntry]] = {}
        seq_counter: dict[str, int] = {}

        for edge in edges:
            obj_class = edge.get("object_class_iri", "")
            if "Equipment" not in obj_class:
                continue

            equipment_id = edge.get("object_text", "")
            props = {
                dp.get("label", ""): dp.get("value", "")
                for dp in (edge.get("object_data_properties") or [])
            }

            workshop = self._detect_workshop(edge, props)
            seq_counter.setdefault(workshop, 0)
            seq_counter[workshop] += 1

            entry = EquipmentEntry(
                seq=seq_counter[workshop],
                equipment_id=equipment_id,
                name=props.get("设备名称", equipment_id),
                spec=props.get("设备规格", props.get("规格型号", "")),
                material=props.get("材质", ""),
            )
            tables.setdefault(workshop, []).append(entry)
        return tables

    def _detect_workshop(self, edge: dict, props: dict) -> str:
        source_ref = edge.get("source_ref", "")
        for ws in ("642", "646", "644"):
            if ws in source_ref:
                return f"{ws}车间"
        for ws in ("642", "646", "644"):
            if ws in props.get("设备规格", ""):
                return f"{ws}车间"
        return "未分组"
