"""Seed the 评估小组 + 审批人小组 ``ontology_relation`` coverage into an AST template.

Closes **Layer 2** of docs/fix-assessment-team-preview-plan.md. The product report
(``RiskAssessmentReport``) owns two relations that are NOT in the source document —
``hasAssessmentTeam → AssessmentTeam`` (评估小组) and ``hasApproverTeam → ApproverTeam``
(审批人小组) — resolved from master data (mock 评估/审批人小组事实源). For
:func:`product_report_edges_for_template` to materialize those edges into the fact spine
(so 行文 Prompt 预览 / 语义插槽 / 真实报告都能看到成员名册), the template must **declare**
each predicate as ``ontology_relation`` coverage.

Why a *dedicated* section (not the existing embedded 评估小组 slot's section): every
semantic slot with ``coverage_refs: []`` projects ALL of its section's coverage. Attaching
the team bindings to a section full of project-all drug/risk slots would hijack those slots
(they'd select the team rosters and lose their own facts). A dedicated section isolates the
declarations; the two pinned roster slots render proper Markdown 会签表; sections elsewhere
are byte-unchanged. (The template's original embedded 评估小组 slot still picks the roster up
via the ``facts_text_all`` fallback once the edge is materialized.)

Re-running is safe: it detects the marker section id and does nothing.

Usage (uv-managed env; point DATABASE_URL at the running DB, e.g. docker on 55432)::

    DATABASE_URL=postgresql://slpra:slpra_dev@localhost:55432/slpra \
        uv run python scripts/seed_report_team_coverage.py \
        7fa1236c-5906-47c9-b89c-69347921eb43
"""

from __future__ import annotations

import os
import sys
import uuid

# Allow ``uv run python scripts/seed_report_team_coverage.py`` — the script's own
# directory shadows the backend root on sys.path, so add the backend root explicitly.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy.orm.attributes import flag_modified

from app.db import SessionLocal
from app.models.extraction import AstTemplate
from app.services.reporting.ast_template import (
    OntologyRelationBinding,
    ReportTemplate,
    coverage_key,
)

_RISK = "https://ontology.pharma-gmp.cn/slpra/risk/"
_DOC_IRI = _RISK + "RiskAssessmentReport"

_SECTION_ID = "sec_report_teams"
_GROUP_ID = "grp_report_teams"

# (predicate_iri, range_iri, label, slot_id, 会签角色词)
_TEAMS = (
    (
        _RISK + "hasAssessmentTeam",
        _RISK + "AssessmentTeam",
        "评估小组",
        "grp_report_teams.assessment",
        "评估小组成员",
    ),
    (
        _RISK + "hasApproverTeam",
        _RISK + "ApproverTeam",
        "审批人小组",
        "grp_report_teams.approver",
        "审批人",
    ),
)


def _slot_prompt(label: str, member_word: str) -> str:
    return (
        f"列出本报告的{label}会签表。严格依据「关联本体（{label}主数据事实源）」给出的"
        f"每一位{member_word}，逐一列为一行，输出为 Markdown 表格，列依次为：姓名、角色、"
        "部门、签名（签名列留空）。事实源中形如「姓名（部门）」的取值请拆分到姓名列与部门列。"
        "不要编造未提供的成员，不要输出表格以外的多余说明文字。"
    )


def _build_section() -> dict:
    """The dedicated 报告会签 section dict (validated below as part of the template)."""
    coverage: list[dict] = []
    slots: list[dict] = []
    for pred, rng, label, slot_id, member_word in _TEAMS:
        binding = OntologyRelationBinding(
            kind="ontology_relation",
            doc_class_iri=_DOC_IRI,
            predicate_iri=pred,
            range_class_iri=rng,
            label=label,
        )
        coverage.append({
            "kind": "ontology_relation",
            "doc_class_iri": _DOC_IRI,
            "predicate_iri": pred,
            "range_class_iri": rng,
            "label": label,
        })
        slots.append({
            "slot_id": slot_id,
            "label": f"{label}会签表",
            "source": {
                "kind": "semantic",
                "prompt": _slot_prompt(label, member_word),
                "coverage_refs": [coverage_key(binding)],
            },
            "required": False,
            "on_missing": "leave_blank",
        })
    return {
        "section_id": _SECTION_ID,
        "title": "报告会签 Report Sign-off",
        "coverage": coverage,
        "groups": [
            {
                "group_id": _GROUP_ID,
                "title": "评估小组与审批人小组 Assessment & Approver Teams",
                "kind": "fields",
                "slots": slots,
            }
        ],
    }


def seed(template_id: str) -> int:
    db = SessionLocal()
    try:
        tpl_row = db.get(AstTemplate, uuid.UUID(template_id))
        if tpl_row is None:
            print(f"[seed] template {template_id} NOT FOUND")
            return 1

        schema = dict(tpl_row.schema_json or {})
        sections = list(schema.get("sections") or [])

        if any(s.get("section_id") == _SECTION_ID for s in sections):
            print(f"[seed] template {template_id} already has section {_SECTION_ID!r} — no-op")
            return 0

        sections.append(_build_section())
        schema["sections"] = sections

        # Validate the whole template before persisting so we never write a broken schema.
        ReportTemplate.model_validate(schema)

        tpl_row.schema_json = schema
        flag_modified(tpl_row, "schema_json")
        db.commit()
        preds = ", ".join(t[0].rsplit("/", 1)[-1] for t in _TEAMS)
        print(
            f"[seed] template {template_id}: added section {_SECTION_ID!r} "
            f"with ontology_relation coverage [{preds}] + {len(_TEAMS)} pinned roster slots"
        )
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    tid = sys.argv[1] if len(sys.argv) > 1 else "7fa1236c-5906-47c9-b89c-69347921eb43"
    raise SystemExit(seed(tid))
