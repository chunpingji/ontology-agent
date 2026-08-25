"""016+: materialize a PRODUCT report type's *own* relations as fact edges.

An AST template is **dual-root**: its ontology types come both from the SOURCE
document report (e.g. ``CMCReport``, addressed by ``iri_pattern``) **and** from the
PRODUCT report the template produces (``RiskAssessmentReport``) — the latter's own
object relations (``含评估小组`` / ``hasAssessmentTeam`` → ``AssessmentTeam``,
``风险评分`` …) are facts too, but they are NOT in the source document; they are
resolved from master data / other A-Box sources.

This module closes **Gap B** (data resolution): it turns each *declared* product-report
relation into one edge in the **same** ``edges`` list the source-doc extractor produces,
so it flows uniformly through the single fact spine — ``edges_to_facts`` → decision rules,
``validate_coverage`` / ``coverage_scoped_edges`` (presence + property expansion), and
``narrative_generator._format_facts`` (LLM synthesis) — with **no special-casing** in any
of them. Report provenance is materialized by :func:`source_document_edge` independently
of template coverage: every generated report must retain its source-document edge even
when an authored template does not display that relation as a coverage slot. The emitted
edge dict mirrors ``relation_extractor._make_edge`` key-for-key.

Design (deliberately NOT a render-time fact_source stopgap):

* An edge is materialized **iff the template declares coverage for that predicate**
  (:func:`product_report_edges_for_template`). Undeclared → nothing is added, so
  templates that don't use a relation are byte-for-byte unchanged (zero blast radius),
  and the coverage-omission check stays meaningful: if the master-data source is
  unavailable/empty the finder yields no edge → the declared position reads BLANK.
* Finders are keyed by ``predicate_iri`` and rooted on the **product** report class
  (``domain = RiskAssessmentReport``), the complement of the source-doc endpoint finders
  in ``relation_extractor`` (rooted on the source doc class).
* Offline-graceful (Principle VI): the master-data source returning ``[]`` yields no edge.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

logger = logging.getLogger(__name__)

# ── Risk / personnel IRIs (byte-verified against ontology/slpra/slpra-risk.ttl) ──
_RISK = "https://ontology.pharma-gmp.cn/slpra/risk/"
_DOCUMENT = "https://ontology.pharma-gmp.cn/slpra/document/"
_DRUG_DEVELOPMENT = "https://ontology.pharma-gmp.cn/slpra/drug-development/"

RISK_ASSESSMENT_REPORT_IRI = _RISK + "RiskAssessmentReport"
BASED_ON_SOURCE_DOCUMENT_IRI = _RISK + "basedOnSourceDocument"
HAS_ASSESSMENT_TEAM_IRI = _RISK + "hasAssessmentTeam"
ASSESSMENT_TEAM_IRI = _RISK + "AssessmentTeam"
HAS_APPROVER_TEAM_IRI = _RISK + "hasApproverTeam"
APPROVER_TEAM_IRI = _RISK + "ApproverTeam"
REGULATORY_DOCUMENT_IRI = _DOCUMENT + "RegulatoryDocument"
DOCUMENT_NAME_IRI = _DOCUMENT + "documentName"
CMC_REPORT_IRI = _DRUG_DEVELOPMENT + "CMCReport"


def _class_label(engine: Any, iri: str, fallback: str) -> str:
    """Read-only class label from the engine (Principle II), with a stable fallback
    when the engine is unavailable/unloaded (tests, air-gap)."""
    try:
        if engine is not None:
            label = engine.get_class_label(iri)
            if label:
                return label
    except Exception:  # pragma: no cover - defensive; label lookup must never crash a report
        logger.debug("class label lookup failed for %s", iri, exc_info=True)
    return fallback


def source_document_edge(
    engine: Any,
    source_filename: str,
    *,
    source_class_iri: str = CMC_REPORT_IRI,
    source_ref: str | None = None,
) -> dict | None:
    """Materialize ``RiskAssessmentReport --basedOnSourceDocument--> document``.

    The uploaded filename is a deterministic job fact. It is carried both as the edge's
    ``object_text`` and as the typed ``documentName`` data property, allowing renderers,
    coverage validation, and audit snapshots to retrieve the analyzed CMCReport name
    from the same edge without asking an LLM. Blank names yield no edge rather than a
    fabricated placeholder.
    """
    document_name = str(source_filename or "").replace("\\", "/").rsplit("/", 1)[-1].strip()
    if not document_name:
        return None

    fallback_class_label = source_class_iri.rstrip("/#").rsplit("/", 1)[-1].rsplit("#", 1)[-1]
    return {
        "subject_class_iri": RISK_ASSESSMENT_REPORT_IRI,
        "subject_class_label": _class_label(
            engine, RISK_ASSESSMENT_REPORT_IRI, "风险评估报告",
        ),
        "subject_text": "本风险评估报告",
        "predicate_iri": BASED_ON_SOURCE_DOCUMENT_IRI,
        "predicate_label": "依据源文档",
        "object_class_iri": source_class_iri,
        "object_class_label": _class_label(
            engine, source_class_iri, fallback_class_label or "法规文档",
        ),
        "object_text": document_name,
        "object_source": "report-source-document",
        "object_data_properties": [{
            "iri": DOCUMENT_NAME_IRI,
            "label": "文档名称",
            "value": document_name,
        }],
        "sub_relationships": [],
        "source_ref": source_ref or document_name,
    }


def _assessment_team_edge(engine: Any) -> dict | None:
    """``RiskAssessmentReport --hasAssessmentTeam--> AssessmentTeam`` from master data.

    Each evaluation-team member becomes one ``object_data_properties`` entry
    (``label = 角色``, ``value = 姓名（部门）``) — the same projection the deprecated
    ``external.assessment_team`` fact_source produced, so the rendered roster is identical
    whichever path an older template used. ``iri = None`` on each member keeps them out of
    ``edges_to_facts`` data-value assertions (they are roster rows, not ontology data
    properties of ``AssessmentTeam``). Source empty/unavailable → ``None`` (graceful)."""
    from app.services.extraction.assessment_team_source import get_assessment_team_source

    members = get_assessment_team_source().list_members()
    if not members:
        return None

    data_properties: list[dict] = []
    for m in members:
        dept = f"（{m.department}）" if m.department else ""
        data_properties.append({
            "iri": None,
            "label": m.role_label,
            "value": f"{m.name}{dept}",
        })

    return {
        "subject_class_iri": RISK_ASSESSMENT_REPORT_IRI,
        "subject_class_label": _class_label(engine, RISK_ASSESSMENT_REPORT_IRI, "风险评估报告"),
        "subject_text": "本报告",
        "predicate_iri": HAS_ASSESSMENT_TEAM_IRI,
        "predicate_label": "含评估小组",
        "object_class_iri": ASSESSMENT_TEAM_IRI,
        "object_class_label": _class_label(engine, ASSESSMENT_TEAM_IRI, "评估小组"),
        "object_text": "风险评估小组",
        "object_source": "external-master-data",
        "object_data_properties": data_properties,
        "sub_relationships": [],
        "source_ref": "external.assessment_team",
    }


def _approver_team_edge(engine: Any) -> dict | None:
    """``RiskAssessmentReport --hasApproverTeam--> ApproverTeam`` from master data.

    Symmetric to :func:`_assessment_team_edge`: each approver becomes one
    ``object_data_properties`` entry (``label = 角色``, ``value = 姓名（部门）``), rooted on
    the product report class. Source empty/unavailable → ``None`` (graceful)."""
    from app.services.extraction.approver_team_source import get_approver_team_source

    members = get_approver_team_source().list_members()
    if not members:
        return None

    data_properties: list[dict] = []
    for m in members:
        dept = f"（{m.department}）" if m.department else ""
        data_properties.append({
            "iri": None,
            "label": m.role_label,
            "value": f"{m.name}{dept}",
        })

    return {
        "subject_class_iri": RISK_ASSESSMENT_REPORT_IRI,
        "subject_class_label": _class_label(engine, RISK_ASSESSMENT_REPORT_IRI, "风险评估报告"),
        "subject_text": "本报告",
        "predicate_iri": HAS_APPROVER_TEAM_IRI,
        "predicate_label": "含审批人小组",
        "object_class_iri": APPROVER_TEAM_IRI,
        "object_class_label": _class_label(engine, APPROVER_TEAM_IRI, "审批人小组"),
        "object_text": "审批人小组",
        "object_source": "external-master-data",
        "object_data_properties": data_properties,
        "sub_relationships": [],
        "source_ref": "external.approver_team",
    }


# Finders keyed by the product-report relation's ``predicate_iri``. Each maps
# ``engine → edge dict | None`` (None = no fact available, graceful).
_PRODUCT_FINDERS_BY_PREDICATE: dict[str, Callable[[Any], dict | None]] = {
    HAS_ASSESSMENT_TEAM_IRI: _assessment_team_edge,
    HAS_APPROVER_TEAM_IRI: _approver_team_edge,
}


def _declared_predicates(template: Any) -> set[str]:
    """Predicate IRIs the template declares as ``ontology_relation`` coverage (any section)."""
    preds: set[str] = set()
    for sec in getattr(template, "sections", None) or []:
        for binding in getattr(sec, "coverage", None) or []:
            if getattr(binding, "kind", None) == "ontology_relation":
                pred = getattr(binding, "predicate_iri", None)
                if pred:
                    preds.add(pred)
    return preds


def product_report_edges_for_template(engine: Any, template: Any) -> list[dict]:
    """Edges for the product report's own relations that THIS template declares.

    Only predicates the template covers (``ontology_relation``) with a registered finder
    are materialized; everything else is left untouched. Returns ``[]`` when nothing is
    declared or no master data is available — the caller simply appends to ``edges``."""
    declared = _declared_predicates(template)
    if not declared:
        return []
    out: list[dict] = []
    for pred, finder in _PRODUCT_FINDERS_BY_PREDICATE.items():
        if pred not in declared:
            continue
        try:
            edge = finder(engine)
        except Exception:  # pragma: no cover - defensive; a bad finder must never crash a report
            logger.warning("product-report finder for %s failed", pred, exc_info=True)
            continue
        if edge:
            out.append(edge)
    return out
