"""Risk assessment report generator (010, FR-002/FR-003/FR-004).

Orchestrates:  edges → Facts bridging → DecisionRule evaluation (pre/post control)
→ RiskReport dataclass ready for docx rendering.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.orm import Session

from app.models.ontology_meta import OntologyDecisionRule
from app.services.reasoning.fact_bridge import apply_postconditions, edges_to_facts
from app.services.reasoning.interpreter import FALSE, TRUE, evaluate
from app.services.reporting.ast_template import ReportTemplate, load_default_template
from app.services.reporting.coverage_validator import CoverageManifest, validate_coverage
from app.services.reporting.placeholder_util import substitute
from app.services.reporting.source_ref import format_source_ref

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
    # Deterministic provenance projected from
    # RiskAssessmentReport --basedOnSourceDocument--> CMCReport.  The edge is the
    # source of truth; the two display fields are derived from it for renderers.
    source_document_edge: dict[str, Any] | None = None
    source_document_name: str = ""
    source_document_type: str = ""
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
    # PDE/OEB「原文 vs 推导」冲突及生成时的人工裁决快照。报告必须同时保留双方值，
    # 不覆盖抽取事实；``effective`` 仅表示本次报告采用哪一侧。
    pde_conflicts: list[dict] = field(default_factory=list)


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
        # 设备富化「文档 vs 档案」冲突说明（_enriched_edges 填充，设备表注记消费）。
        self._equipment_conflicts: list[str] = []

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
        assessment_progress_fn: Callable[[list[dict[str, Any]]], None] | None = None,
        narrative_progress_fn: Callable[[dict, int, int], None] | None = None,
        pde_conflict_decision: dict[str, Any] | None = None,
        source_document_ref: str | None = None,
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

        assessment_steps: list[dict[str, Any]] = [
            {"key": "facts", "label": "构建评估事实", "status": "pending", "result": None},
            {"key": "rules", "label": "执行风险规则", "status": "pending", "result": None},
            {"key": "coverage", "label": "校验信息完整性", "status": "pending", "result": None},
            {"key": "narratives", "label": "生成章节行文", "status": "pending", "result": None},
        ]

        def emit_assessment_step(
            key: str,
            status: str,
            result: str | None = None,
            items: list[dict[str, str | None]] | None = None,
        ) -> None:
            step = next(item for item in assessment_steps if item["key"] == key)
            step["status"] = status
            step["result"] = result
            if items is not None:
                step["items"] = items
            if assessment_progress_fn:
                # 回调接收完整快照，轮询即使错过某次快速更新也能看到此前步骤的结果。
                assessment_progress_fn([dict(item) for item in assessment_steps])

        if assessment_progress_fn:
            assessment_progress_fn([dict(item) for item in assessment_steps])
        emit_assessment_step("facts", "running")
        engine = get_loaded_engine()
        # Materialize deterministic report-side relations into the same fact spine:
        # provenance is always present when a source filename exists; optional product
        # relations (评估小组 / 审批人小组 …) remain template-coverage-driven.
        edges = self._enriched_edges(
            edges,
            engine,
            document_path=document_path,
            source_filename=source_filename,
            source_document_ref=source_document_ref,
        )
        facts = edges_to_facts(edges, engine)
        fact_items = [
            {
                "label": str(edge.get("predicate_label") or edge.get("predicate_iri") or "关系事实"),
                "detail": " → ".join(filter(None, [
                    str(edge.get("subject_text") or edge.get("subject_class_label") or ""),
                    str(edge.get("object_text") or edge.get("object_class_label") or ""),
                ])) or None,
            }
            for edge in edges
        ]
        emit_assessment_step(
            "facts", "completed", f"{len(edges)} 条关系事实", fact_items,
        )

        emit_assessment_step("rules", "running")
        rules = self._load_rules()
        context = self._build_template_context(edges)
        pre_rows = self._evaluate_rules(rules, facts, context)
        post_rows = self._evaluate_post_control(rules, facts, pre_rows)

        pde_conflicts = self._build_pde_conflict_snapshots(
            edges, pde_conflict_decision,
        )
        post_rows.extend(
            self._build_pde_conflict_row(conflict) for conflict in pde_conflicts
        )
        # Count only after deterministic PDE/OEB conflict rows have joined the matrix;
        # otherwise Drawer progress could say 「结果可接受」 while the final report is pending.
        pending_count = sum(row.status == PENDING_STATUS for row in post_rows)
        unacceptable_count = sum(row.status == "不可接受" for row in post_rows)
        if pending_count:
            rules_result = f"{len(post_rows)} 项 · {pending_count} 项待评估"
        elif unacceptable_count:
            rules_result = f"{len(post_rows)} 项 · {unacceptable_count} 项不可接受"
        else:
            rules_result = f"{len(post_rows)} 项 · 结果可接受"
        rule_items = [
            {
                "label": row.hazid,
                "detail": f"控制前 {row.pre_control_level} · 控制后 {row.post_control_level}",
            }
            for row in post_rows
        ]
        emit_assessment_step("rules", "completed", rules_result, rule_items)

        subject = self._build_subject_description(edges, source_filename)
        source_edge, source_name, source_type = self._source_document_metadata(
            edges, source_filename,
        )
        equipment_tables = self._build_equipment_tables(edges)
        equipment_notes: list[str] = []
        if "未分组" in equipment_tables:
            equipment_notes.append("以下设备未能自动识别所属车间，请人工确认归属。")
        equipment_notes.extend(
            str(edge.get("object_text"))
            for edge in edges
            if edge.get("predicate_label") == "APS排期告警"
            and edge.get("object_text")
        )
        # 冲突说明由富化阶段（_enriched_edges → enrich_equipment_facts）统一产出。
        equipment_notes.extend(self._equipment_conflicts)

        report = RiskReport(
            doc_no=self._template.doc_no,
            revision=self._template.revision,
            subject_description=subject,
            source_document_edge=source_edge,
            source_document_name=source_name,
            source_document_type=source_type,
            equipment_tables=equipment_tables,
            equipment_notes=equipment_notes,
            assessment_rows=post_rows,
            pde_conflicts=pde_conflicts,
        )

        emit_assessment_step("coverage", "running")
        manifest = validate_coverage(
            self._template, edges, rules, facts,
            dismissed_slot_ids=dismissed_slot_ids,
            engine=engine,  # 016: expand section.coverage into ontology positions
        )
        self._last_manifest = manifest
        coverage_result = (
            f"{manifest.total_slots} 项 · {manifest.missing_required} 项缺失"
            if manifest.missing_required
            else f"{manifest.total_slots} 项 · 信息完整"
        )
        coverage_items = [
            {
                "label": slot.label,
                "detail": f"{slot.status} · {slot.value or slot.note or '无值'}",
            }
            for slot in manifest.slots
        ]
        emit_assessment_step(
            "coverage", "completed", coverage_result, coverage_items,
        )

        emit_assessment_step("narratives", "running")
        self._try_narrative_generation(
            report,
            edges,
            facts=facts,
            engine=engine,
            progress_fn=narrative_progress_fn,
        )
        self._apply_pde_conflict_conclusion(report)
        narrative_count = len(report.section_narratives)
        narrative_result = (
            f"{narrative_count} 个章节"
            if narrative_count
            else "无待生成章节"
        )
        narrative_items = [
            {
                "label": str(section.get("title") or "章节行文"),
                "detail": str(section.get("text") or ""),
            }
            for section in report.section_narratives
        ]
        emit_assessment_step(
            "narratives", "completed", narrative_result, narrative_items,
        )

        return report, manifest

    @staticmethod
    def _build_pde_conflict_snapshots(
        edges: list[dict],
        decision: dict[str, Any] | None,
    ) -> list[dict]:
        """Capture PDE conflicts plus the decision effective at generation time.

        The extraction edge remains untouched.  A report snapshot preserves both
        asserted and derived values, while ``effective`` identifies the selected
        side only after an explicit human decision.
        """
        decision_payload = {
            "chosen": "pending",
            "note": "",
            "actor": "",
            "version": 0,
            "decided_at": None,
            **(decision or {}),
        }
        if decision_payload.get("chosen") not in {"derived", "asserted", "pending"}:
            decision_payload["chosen"] = "pending"
        snapshots: list[dict] = []
        seen_keys: set[str] = set()
        for edge in edges:
            raw = edge.get("conflict")
            if not isinstance(raw, dict) or raw.get("conflict_key") != "shared_line_pde":
                continue

            conflict = deepcopy(raw)
            conflict_key = str(conflict.get("conflict_key") or "")
            if conflict_key in seen_keys:
                continue
            seen_keys.add(conflict_key)
            asserted = conflict.get("asserted") or {}
            derived = conflict.get("derived") or {}
            chosen = decision_payload.get("chosen")
            effective: dict[str, Any] | None = None
            if chosen == "asserted":
                effective = {
                    "source": "asserted",
                    "source_label": "原文",
                    "pde_mg_day": asserted.get("pde_mg_day"),
                    "pde_ug_day": asserted.get("pde_ug_day"),
                    "band": asserted.get("band"),
                }
            elif chosen == "derived":
                derived_ug = derived.get("pde_ug_day")
                try:
                    derived_mg = round(float(derived_ug) / 1000.0, 6)
                except (TypeError, ValueError):
                    derived_mg = None
                effective = {
                    "source": "derived",
                    "source_label": "推导",
                    "pde_mg_day": derived_mg,
                    "pde_ug_day": derived_ug,
                    "band": derived.get("band"),
                }

            snapshots.append({
                "conflict_key": conflict_key,
                "summary": conflict.get("summary") or "PDE/OEB 潜能等级存在冲突",
                "delta_bands": conflict.get("delta_bands"),
                "pde_ratio": conflict.get("pde_ratio"),
                "asserted": asserted,
                "derived": derived,
                "decision": deepcopy(decision_payload),
                "effective": effective,
            })
        return snapshots

    @staticmethod
    def _build_pde_conflict_row(conflict: dict) -> RiskRow:
        asserted = conflict.get("asserted") or {}
        derived = conflict.get("derived") or {}
        decision = conflict.get("decision") or {}
        effective = conflict.get("effective")
        before = (
            f"原文 OEB band {asserted.get('band', '—')} / "
            f"推导 OEB band {derived.get('band', '—')}"
        )
        trace_parts = ["PDE 推导依据及人工裁决记录"]
        if decision.get("actor"):
            trace_parts.append(f"裁决人：{decision['actor']}")
        if decision.get("decided_at"):
            trace_parts.append(f"裁决时间：{decision['decided_at']}")
        if decision.get("note"):
            trace_parts.append(f"备注：{decision['note']}")

        if not effective:
            return RiskRow(
                hazid="PDE/OEB 潜能等级冲突",
                contributing_factors=conflict.get("summary") or "PDE/OEB 数据存在冲突",
                pre_control_level=before,
                post_control_level=PENDING_LEVEL,
                control_measures="完成人工裁决，明确采用推导值或原文值后重新生成报告。",
                traceability="；".join(trace_parts),
                status=PENDING_STATUS,
            )

        source_label = effective.get("source_label") or "已选值"
        return RiskRow(
            hazid="PDE/OEB 潜能等级冲突",
            contributing_factors=conflict.get("summary") or "PDE/OEB 数据存在冲突",
            pre_control_level=before,
            post_control_level=f"采用{source_label} OEB band {effective.get('band', '—')}",
            control_measures=f"人工裁决采纳{source_label}数据；双方原始值均保留在报告中。",
            traceability="；".join(trace_parts),
            status=f"已裁决（采纳{source_label}）",
        )

    @staticmethod
    def _apply_pde_conflict_conclusion(report: RiskReport) -> None:
        if not report.pde_conflicts:
            return
        pending = [item for item in report.pde_conflicts if not item.get("effective")]
        if pending:
            report.conclusion = (
                "⚠ PDE/OEB 潜能等级冲突尚未完成裁决，本报告总体风险结论为待评估；"
                "在明确采用推导值或原文值并重新生成报告前，不得认定全部风险可以接受。"
            )
        else:
            def display(value: Any) -> Any:
                return value if value is not None and value != "" else "—"

            selected = "；".join(
                f"采用{item['effective'].get('source_label')}数据："
                f"PDE {display(item['effective'].get('pde_mg_day'))} mg/日，"
                f"OEB band {display(item['effective'].get('band'))}"
                for item in report.pde_conflicts
            )
            report.conclusion = (
                f"PDE/OEB 潜能等级冲突已完成人工裁决（{selected}）。"
                "双方原始数据及裁决记录已保留，其他风险维度详见风险评估矩阵。"
            )
        # 冲突结论是确定性合规结论，不得继续标记成 LLM 生成内容。
        report.llm_generated_fields.discard("conclusion")

    def assess_deterministic(
        self,
        edges: list[dict],
        *,
        dismissed_slot_ids: set[str] | None = None,
        source_filename: str = "",
        source_document_ref: str | None = None,
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
        edges = self._enriched_edges(
            edges,
            engine,
            source_filename=source_filename,
            source_document_ref=source_document_ref,
        )
        facts = edges_to_facts(edges, engine)
        rules = self._load_rules()
        context = self._build_template_context(edges)
        pre_rows = self._evaluate_rules(rules, facts, context)
        post_rows = self._evaluate_post_control(rules, facts, pre_rows)

        manifest = validate_coverage(
            self._template, edges, rules, facts,
            dismissed_slot_ids=dismissed_slot_ids,
            engine=engine,
        )
        self._last_manifest = manifest
        return post_rows, manifest, edges

    def _enriched_edges(
        self,
        edges: list[dict],
        engine: Any,
        document_path: str | None = None,
        source_filename: str = "",
        source_document_ref: str | None = None,
    ) -> list[dict]:
        """Source facts + deterministic provenance + declared product relations.

        A new list is returned (inputs untouched). Source provenance is added whenever
        ``source_filename`` is available; other product-report relations remain gated by
        registered template coverage (:func:`product_report_edges_for_template`).
        """
        from app.services.extraction.equipment_source import enrich_equipment_facts
        from app.services.reporting.aps_equipment_resolver import (
            resolve_aps_equipment_candidates,
        )
        from app.services.reporting.product_report_edges import (
            BASED_ON_SOURCE_DOCUMENT_IRI,
            CMC_REPORT_IRI,
            RISK_ASSESSMENT_REPORT_IRI,
            product_report_edges_for_template,
            source_document_edge,
        )

        combined = list(edges)
        if source_filename and not any(
            edge.get("predicate_iri") == BASED_ON_SOURCE_DOCUMENT_IRI
            for edge in combined
        ):
            source_class_iri = next((
                str(edge.get("subject_class_iri"))
                for edge in combined
                if edge.get("subject_class_iri")
                and edge.get("subject_class_iri") != RISK_ASSESSMENT_REPORT_IRI
            ), CMC_REPORT_IRI)
            provenance_edge = source_document_edge(
                engine,
                source_filename,
                source_class_iri=source_class_iri,
                source_ref=source_document_ref,
            )
            if provenance_edge:
                combined.append(provenance_edge)

        product_edges = product_report_edges_for_template(engine, self._template)
        if product_edges:
            combined.extend(product_edges)
        # 设备需求中的 A/B、A或B 是候选集合；风险事实只能消费 APS 在计划月份确认的实际设备。
        # 返回报告专用投影，不改写原始抽取图谱。
        combined = resolve_aps_equipment_candidates(
            combined, self._db, document_path=document_path
        )
        # 报告期设备富化（幂等）：标注即便是旧代码所存、缺外部设备属性，报告仍就地补齐
        # 设备名称/规格/材质（写入文档规范标签，模板槽位/覆盖/设备表零改动即可取到），
        # 并收集「文档 vs 档案」冲突用于设备表注记。抽取期已富化过的 edge 会被幂等跳过。
        try:
            self._equipment_conflicts = enrich_equipment_facts(combined)
        except Exception:
            logger.debug("报告期设备档案富化整体跳过", exc_info=True)
            self._equipment_conflicts = []
        return combined

    @staticmethod
    def _source_document_metadata(
        edges: list[dict], fallback_filename: str = "",
    ) -> tuple[dict[str, Any] | None, str, str]:
        """Read the analyzed document identity from the provenance edge.

        ``fallback_filename`` is retained only for legacy callers that supply a name
        but bypass enrichment. Normal report generation always reaches this method with
        a materialized ``basedOnSourceDocument`` edge.
        """
        from app.services.reporting.product_report_edges import (
            BASED_ON_SOURCE_DOCUMENT_IRI,
            CMC_REPORT_IRI,
            DOCUMENT_NAME_IRI,
        )

        edge = next((
            item for item in edges
            if item.get("predicate_iri") == BASED_ON_SOURCE_DOCUMENT_IRI
        ), None)
        if edge is None:
            return None, fallback_filename, "CMCReport" if fallback_filename else ""

        data_properties = edge.get("object_data_properties") or []
        document_name = next((
            str(prop.get("value"))
            for prop in data_properties
            if prop.get("iri") == DOCUMENT_NAME_IRI and prop.get("value")
        ), str(edge.get("object_text") or fallback_filename))
        class_iri = str(edge.get("object_class_iri") or "")
        document_type = (
            "CMCReport"
            if class_iri == CMC_REPORT_IRI or class_iri.endswith("/CMCReport")
            else str(edge.get("object_class_label") or class_iri.rsplit("/", 1)[-1])
        )
        return edge, document_name, document_type

    def _try_narrative_generation(
        self,
        report: RiskReport,
        edges: list[dict],
        *,
        facts: Any | None = None,
        engine: Any | None = None,
        progress_fn: Callable[[dict, int, int], None] | None = None,
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
                progress_fn=progress_fn,
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
        self,
        rules: list[OntologyDecisionRule],
        facts: Any,
        context: dict[str, str | None] | None = None,
    ) -> list[RiskRow]:
        # R-RA1~5 文本模版化：consequent 的三列文本（描述/控制措施/可追溯性）可含
        # ``{{药物名称/代号}}`` 等占位符，用抽取事实确定性填充（``context``）。替换只作用于
        # 展示文本，绝不参与 antecedent / risk_level / status 计算，也不改 ORM 原值（FR-009）。
        ctx = context or {}

        def _fill(text: str) -> str:
            return substitute(text, ctx, missing=lambda label: f"【待补充：{label}】")

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
                contributing_factors=_fill(consequent.get("description", "")),
                pre_control_level=pre_control_level,
                post_control_level="",
                control_measures=_fill(consequent.get("control_measure", "")),
                traceability=_fill(consequent.get("traceability_docs", "")),
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
                text = self._as_text(edge.get("object_text") or edge.get("subject_text"))
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

    def _build_template_context(self, edges: list[dict]) -> dict[str, str | None]:
        """Resolve the whitelisted risk-matrix placeholders from extracted facts.

        R-RA1~5 consequent 文本的白名单占位符为 ``{{药物名称/代号}}`` 与 ``{{车间}}``（仅此两个）。
        这里做**确定性**解析（无 LLM、无网络）：命中返回展示串，缺失/无法解析返回 ``None`` ——
        由 :func:`placeholder_util.substitute` 渲染成 ``【待补充：…】`` 哨兵，让数据缺失在受控矩阵上
        显式可见，绝不用模糊通用词兜底。模板中出现的任何其他占位符同样渲染为哨兵。两个确定性入口
        （``generate_with_coverage`` / ``assess_deterministic``）都用**同一份富化后 edges**
        构建，保证预览与正式报告三列文本逐字相同。"""
        return {
            "药物名称/代号": self._resolve_drug_label(edges),
            "车间": self._resolve_workshops(edges),
        }

    @staticmethod
    def _as_text(raw: object) -> str:
        """把抽取值安全归一为可 strip 的字符串：``None`` → ``""``；数值/布尔等非字符串
        （如数据属性值可能是 ``float``/``int``）先 ``str()`` 再去空白，避免 ``'float' object
        has no attribute 'strip'`` 之类运行期错误。"""
        return "" if raw is None else str(raw).strip()

    def _resolve_drug_label(self, edges: list[dict]) -> str | None:
        """主产品「药物名称/代号」：``DrugProduct`` 边优先药物名称属性，兜底 ``object_text``
        （程序代号，如 HRS-1597）。与 ``_build_subject_description`` 同口径识别 DrugProduct；
        去重后取首个稳定值（本域文档单主产品；出现顺序无关结果）。"""
        _NAME_LABELS = ("药物名称", "药品名称", "药物名称/代号", "通用名", "名称")
        codes: list[str] = []
        for edge in edges:
            if "DrugProduct" not in (edge.get("object_class_iri") or ""):
                continue
            for dp in edge.get("object_data_properties") or []:
                label = self._as_text(dp.get("label"))
                raw = dp.get("value")
                value = self._as_text(raw)
                # 仅接受**文本型**药物名称：数值/布尔等（如误抽的分子量、批次数）不是合法药名，
                # 视为异常抽取并跳过，回退到可信的程序代号 object_text（Codex 审查 finding 2）。
                if value and isinstance(raw, str) and label in _NAME_LABELS:
                    return value  # 药物名称优先
            code = self._as_text(edge.get("object_text") or edge.get("subject_text"))
            if code and code not in codes:
                codes.append(code)
        return codes[0] if codes else None

    def _resolve_workshops(self, edges: list[dict]) -> str | None:
        """全部设备边解析出的车间集合，去重排序后用 ``、`` 连接。解析口径与
        ``_detect_workshop`` / narrative ``_extract_entity_context`` **完全一致**（共享解析器）：
        档案富化权威标签 → 设备编号锚定 → 业务文本兜底扫描。"""
        from app.services.extraction.equipment_source import (
            iter_equipment_edges,
            workshop_from_enrichment,
            workshop_from_loose_scan,
        )

        workshops: set[str] = set()
        for eq in iter_equipment_edges(edges):
            raw_ref = eq.get("source_ref")
            ws = workshop_from_enrichment(format_source_ref(raw_ref))
            if not ws:
                code = self._as_text(eq.get("object_text"))
                m = re.fullmatch(r"[A-Za-z]{1,3}(642|646|644)\d{2,}", code)
                if m:
                    ws = f"{m.group(1)}车间"
            if not ws:
                ws = workshop_from_loose_scan(raw_ref)
            if ws:
                workshops.add(ws)
        return "、".join(sorted(workshops)) if workshops else None

    def _build_equipment_tables(
        self, edges: list[dict]
    ) -> dict[str, list[EquipmentEntry]]:
        """递归收集全部设备（顶层 + ``sub_relationships``），**按设备编号聚合去重**，按车间
        分组，产出 5 列一览表（序号/编号/名称/规格/材质）。

        设备经两条路径进图谱：``extractionProfile`` 顶层 ``usesEquipment`` 边、合成路线→步骤
        下的嵌套「使用设备」子关系；同一编号可能两处各带一部分属性，故须**跨全部出现处聚合
        属性**（非空先到先得），仅按首条 edge 归组——若只取首条会吞掉后续属性。识别与遍历
        复用 :func:`iter_equipment_edges`（谓词权威判定，见 equipment_source）。列值按「档案
        规范标签优先、文档标签兜底」读取；富化阶段已把档案值写入文档规范标签，故文档缺失的
        规格/材质在此自然取到外部值。冲突说明由富化阶段统一产出，不在此重复检测。"""
        from app.services.extraction.equipment_source import iter_equipment_edges

        # code → {"props": {label: value}, "edge": 首条 edge（用于归组）}
        agg: dict[str, dict] = {}
        order: list[str] = []
        for edge in iter_equipment_edges(edges):
            equipment_id = self._as_text(edge.get("object_text"))
            if not equipment_id:
                continue
            rec = agg.get(equipment_id)
            if rec is None:
                rec = {"props": {}, "edge": edge}
                agg[equipment_id] = rec
                order.append(equipment_id)
            merged = rec["props"]
            for dp in edge.get("object_data_properties") or []:
                label = dp.get("label", "")
                value = dp.get("value", "")
                if label and value and not merged.get(label):
                    merged[label] = value  # 非空先到先得：合并顶层×嵌套两处属性

        def _prop(props: dict, *labels: str) -> str:
            for label in labels:
                val = props.get(label)
                if val:
                    return val
            return ""

        tables: dict[str, list[EquipmentEntry]] = {}
        seq_counter: dict[str, int] = {}
        for equipment_id in order:
            rec = agg[equipment_id]
            props = rec["props"]
            workshop = self._detect_workshop(rec["edge"], props)
            seq_counter.setdefault(workshop, 0)
            seq_counter[workshop] += 1
            tables.setdefault(workshop, []).append(EquipmentEntry(
                seq=seq_counter[workshop],
                equipment_id=equipment_id,
                name=_prop(props, "设备名称", "equipmentName") or equipment_id,
                spec=_prop(props, "规格型号", "modelSpecification", "设备规格"),
                material=_prop(props, "主体材质", "constructedOf", "材质"),
            ))
        return tables

    def _detect_workshop(self, edge: dict, props: dict) -> str:
        # 车间归组优先级（Codex 审查）：档案富化的**权威标签**（外部设备档案（…workshop=<码>车间））>
        # 设备编号锚定 > **业务文本**的模糊数字扫描。权威解析读 format_source_ref 展示串（标签文本），
        # 而兜底扫描必须读**原始** source_ref——否则 017 结构坐标「表 642」（table=641 的 1-based
        # 渲染）会被数字扫描误当车间号，把无档案的设备误归 642 并吞掉「人工确认」提示（Codex round-3 #1）。
        from app.services.extraction.equipment_source import (
            workshop_from_enrichment,
            workshop_from_loose_scan,
        )

        raw_ref = edge.get("source_ref")
        ws = workshop_from_enrichment(format_source_ref(raw_ref))
        if ws:
            return ws
        # 从设备编号推断：1–3 字母 + 车间号(642/646/644) + ≥2 位序号，整串锚定避免误分
        # （如 AB6429… 因尾部仅 1 位数字不匹配 → 归「未分组」而非误判 642）。
        equipment_id = self._as_text(edge.get("object_text"))
        m = re.fullmatch(r"[A-Za-z]{1,3}(642|646|644)\d{2,}", equipment_id)
        if m:
            return f"{m.group(1)}车间"
        # 无权威标签/编号 → legacy 兜底：共享扫描器（与 narrative 同口径）。传**原始** source_ref
        # （只扫业务文本键、不扫坐标）+ 设备规格串（本就是纯文本）。
        return (
            workshop_from_loose_scan(raw_ref)
            or workshop_from_loose_scan(props.get("设备规格", ""))
            or "未分组"
        )
