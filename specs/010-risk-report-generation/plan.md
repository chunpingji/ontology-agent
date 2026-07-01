# Implementation Plan: Risk Assessment Report Generation

**Branch**: `010-risk-report-generation` | **Date**: 2026-06-29 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/010-risk-report-generation/spec.md`

## Summary

Generate QS-A-020F05 structured risk assessment reports from document extraction results. Bridge extracted relationship edges to the existing reasoning engine's `Facts` format, evaluate `DecisionRule`s for pre/post-control risk levels across 5 HazID dimensions, render the result as a persisted `.docx` file via python-docx, and expose a download button in `ExtractionDrawer` scoped to CMCReport-classified documents. Includes audit logging and server-side report persistence for compliance retrieval.

## Technical Context

**Language/Version**: Python 3.11 (backend), TypeScript / Next.js 14 (frontend)

**Primary Dependencies**: FastAPI, SQLAlchemy 2.0, python-docx (already in project), React Query, shadcn/ui

**Storage**: PostgreSQL (existing `extraction_jobs`, `audit_log` tables; new `generated_reports` table)

**Testing**: pytest (`uv run pytest`), manual E2E via browser

**Target Platform**: Linux server (air-gap / offline deployment)

**Project Type**: Web application (FastAPI backend + Next.js frontend)

**Performance Goals**: Report generation < 10 seconds end-to-end (SC-001)

**Constraints**: Fully offline (Constitution Principle VI), no new external dependencies, python-docx already available

**Scale/Scope**: Single-document operation, ~5-10 DecisionRules, ~30 edges per document

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Status | Evidence |
|-----------|--------|----------|
| I. 规范驱动开发 | PASS | Followed specify → clarify → plan flow; spec is the single truth source |
| II. 本体权威性与保真 | PASS | Feature reads from T-Box (DecisionRule patterns) but does not write to TTL; no A-Box individuals created (bridge layer is in-memory only) |
| III. 可追溯与审计 | PASS | FR-012: audit.append() on each generation; FR-009: source_ref propagated to traceability column; FR-013: .docx persisted server-side |
| IV. 测试纪律与契约优先 | PASS | Contract defined before implementation; quickstart.md provides E2E validation; pytest coverage planned for bridge + generator |
| V. 最小复杂度与复用 | PASS | Reuses existing evaluate(), Facts, DecisionRule, AuditLog, python-docx; no new dependencies; ~810 lines incremental |
| VI. 离线优先与优雅降级 | PASS | FR-010: zero network dependencies; python-docx renders locally; no cloud LLM calls |

## Project Structure

### Documentation (this feature)

```text
specs/010-risk-report-generation/
├── plan.md              # This file
├── research.md          # Phase 0 output
├── data-model.md        # Phase 1 output
├── quickstart.md        # Phase 1 output
├── contracts/           # Phase 1 output
│   └── risk-report-api.md
└── tasks.md             # Phase 2 output (/speckit-tasks)
```

### Source Code (repository root)

```text
backend/
├── app/
│   ├── api/
│   │   └── extraction.py           # Add POST/GET risk-report endpoints
│   ├── models/
│   │   └── extraction.py           # Add GeneratedReport model
│   ├── schemas/
│   │   └── extraction.py           # Add report response schemas
│   └── services/
│       ├── reasoning/
│       │   └── fact_bridge.py      # NEW: edges → Facts bridge
│       └── reporting/
│           ├── risk_report.py      # EXISTING: PDF report (untouched)
│           ├── risk_report_generator.py  # NEW: report template layer
│           └── docx_renderer.py    # NEW: .docx rendering
├── alembic/
│   └── versions/
│       └── xxx_add_generated_reports.py  # Migration
└── tests/
    └── test_reporting/
        ├── test_fact_bridge.py     # Unit tests for edges→Facts
        └── test_risk_report_generator.py  # Unit tests for report generation

frontend/
└── src/
    ├── components/
    │   └── extraction/
    │       └── extraction-drawer.tsx  # Add report button + download handler
    └── lib/
        └── api.ts                    # Add generateRiskReport() API call
```

**Structure Decision**: Extends existing `backend/app/services/reporting/` module (where `risk_report.py` already lives) with two new files. Bridge layer goes under `reasoning/` since it transforms data for the reasoning engine. API endpoints added to existing `extraction.py` router since they operate on extraction jobs. Frontend changes are minimal (button + API call in existing components).

## Complexity Tracking

No constitution violations to justify.
