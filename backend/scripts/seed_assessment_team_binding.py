"""Seed the 评估小组 (assessment team) 016 fact-source binding into an AST template.

The frontend template editor can only author ``ontology_relation`` coverage bindings,
so this one-off, **idempotent** script wires the mock ``external.assessment_team`` fact
source (backed by :mod:`app.services.extraction.assessment_team_source`, reusing the 5
GxP roles) into a template's ``schema_json``:

  * a new dedicated Section "评估小组 Assessment Team" whose ONLY coverage is a
    ``fact_source`` binding on ``external.assessment_team`` — a dedicated section keeps
    the team facts out of the other sections' ``coverage_refs: []`` (project-all) slots;
  * one ``semantic`` slot in that section, ``coverage_refs`` pinned to the fact source's
    :func:`coverage_key`, prompted to emit a member roster as a Markdown table (the docx
    renderer turns that into a real Word table).

Re-running is safe: it detects the marker section id and does nothing.

Usage (uv-managed env; point DATABASE_URL at the running DB, e.g. docker on 55432)::

    DATABASE_URL=postgresql://slpra:slpra_dev@localhost:55432/slpra \
        uv run python scripts/seed_assessment_team_binding.py \
        99b6a873-1267-408c-9ef8-73162ad435e5
"""

from __future__ import annotations

import os
import sys
import uuid

# Allow ``uv run python scripts/seed_assessment_team_binding.py`` — the script's own
# directory shadows the backend root on sys.path, so add the backend root explicitly.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy.orm.attributes import flag_modified

from app.db import SessionLocal
from app.models.extraction import AstTemplate
from app.services.reporting.ast_template import (
    FactSourceBinding,
    ReportTemplate,
    coverage_key,
)

_SECTION_ID = "sec_assessment_team"
_GROUP_ID = "grp_assessment_team"
_SLOT_ID = "grp_assessment_team.members"
_SOURCE = "external.assessment_team"
_LABEL = "评估小组"

_SLOT_PROMPT = (
    "列出本报告的风险评估小组成员会签表。严格依据「关联本体（评估小组主数据事实源）」"
    "给出的每一位成员，逐一列为一行，输出为 Markdown 表格，列依次为：姓名、角色、部门、"
    "签名（签名列留空）。事实源中形如「姓名（部门）」的取值请拆分到姓名列与部门列。"
    "不要编造未提供的成员，不要输出表格以外的多余说明文字。"
)


def _build_section() -> dict:
    """The 评估小组 section dict (validated below as part of the whole template)."""
    fact_binding = FactSourceBinding(kind="fact_source", source=_SOURCE, label=_LABEL)
    ref_key = coverage_key(fact_binding)  # coverage.fact_source.external.assessment_team
    return {
        "section_id": _SECTION_ID,
        "title": "评估小组 Assessment Team",
        "coverage": [
            {"kind": "fact_source", "source": _SOURCE, "label": _LABEL},
        ],
        "groups": [
            {
                "group_id": _GROUP_ID,
                "title": "评估小组成员 Assessment Team Members",
                "kind": "fields",
                "slots": [
                    {
                        "slot_id": _SLOT_ID,
                        "label": "评估小组成员",
                        "source": {
                            "kind": "semantic",
                            "prompt": _SLOT_PROMPT,
                            "coverage_refs": [ref_key],
                        },
                        "required": False,
                        "on_missing": "leave_blank",
                    }
                ],
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

        # Insert the assessment-team section at the front (it conventionally leads a
        # risk assessment report).
        sections.insert(0, _build_section())
        schema["sections"] = sections

        # Validate the whole template before persisting so we never write a broken schema.
        ReportTemplate.model_validate(schema)

        tpl_row.schema_json = schema
        flag_modified(tpl_row, "schema_json")
        db.commit()
        print(
            f"[seed] template {template_id}: added section {_SECTION_ID!r} "
            f"with fact_source binding {_SOURCE!r} + semantic slot {_SLOT_ID!r}"
        )
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    tid = sys.argv[1] if len(sys.argv) > 1 else "99b6a873-1267-408c-9ef8-73162ad435e5"
    raise SystemExit(seed(tid))
