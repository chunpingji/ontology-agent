# Implementation Plan: AST 提取前端 UI

**Branch**: `011-ast-extraction-ui` | **Date**: 2026-07-01 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/011-ast-extraction-ui/spec.md`

## Summary

Provide a dedicated frontend page (`/entities/extraction/[jobId]/ast`) for visualizing AST (Assessment Semantic Template) coverage, managing slot dismissals, and accessing report history. The backend already delivers `CoverageManifest` via 010; this feature adds three lightweight API endpoints (coverage preview, report history listing, slot dismissal CRUD), one new DB model (`SlotDismissal`), and a full-page React UI with AST tree view, coverage dashboard, slot detail panel, and report history list — all built on the existing shadcn/ui + React Query stack with zero external dependencies.

## Technical Context

**Language/Version**: Python 3.11 (backend), TypeScript / Next.js 14 App Router (frontend)

**Primary Dependencies**: FastAPI, SQLAlchemy 2.0, React Query, shadcn/ui, python-docx (existing)

**Storage**: PostgreSQL (existing `extraction_jobs`, `generated_reports`, `audit_log` tables; new `slot_dismissals` table)

**Testing**: pytest (`uv run pytest`), manual E2E via browser

**Target Platform**: Linux server (air-gap / offline deployment)

**Project Type**: Web application (FastAPI backend + Next.js frontend)

**Performance Goals**: AST page load < 2 seconds including coverage API round-trip (SC-001)

**Constraints**: Fully offline (Constitution Principle VI), no external CDN/fonts/API, shadcn/ui only

**Scale/Scope**: Single-job view, ~18 slots per template, ~30 edges per document, ~5-10 historical reports per job

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Status | Evidence |
|-----------|--------|----------|
| I. 规范驱动开发 | PASS | Followed specify → clarify → plan flow; spec is the single truth source; 5 clarifications integrated before plan |
| II. 本体权威性与保真 | PASS | Feature is read-only w.r.t. ontology — reads AST template + coverage; no T-Box writes; SlotDismissal is application-layer state |
| III. 可追溯与审计 | PASS | FR-API-004/005: dismiss/undismiss both log via `audit.append()`; historical reports preserve coverage snapshots in `rules_summary` |
| IV. 测试纪律与契约优先 | PASS | Contracts defined for 3 new API endpoints + dismiss model; quickstart.md provides E2E validation; pytest coverage planned for new endpoints |
| V. 最小复杂度与复用 | PASS | Reuses existing CoverageManifest, validate_coverage(), WordViewer highlightRef, lib/api.ts + React Query; shadcn/ui components only; no new dependencies |
| VI. 离线优先与优雅降级 | PASS | All UI uses shadcn/ui (local); no CDN/font/external API; coverage computation is local; LLM seam reserved internally but not exposed |

## Project Structure

### Documentation (this feature)

```text
specs/011-ast-extraction-ui/
├── plan.md              # This file
├── research.md          # Phase 0 output
├── data-model.md        # Phase 1 output
├── quickstart.md        # Phase 1 output
├── contracts/           # Phase 1 output
│   ├── ast-coverage-api.md
│   └── slot-dismissal-api.md
└── tasks.md             # Phase 2 output (/speckit-tasks)
```

### Source Code (repository root)

```text
backend/
├── app/
│   ├── api/
│   │   └── extraction.py           # Add GET /ast-coverage, GET /reports,
│   │                               # POST dismiss, DELETE dismiss endpoints
│   ├── models/
│   │   └── extraction.py           # Add SlotDismissal model
│   ├── schemas/
│   │   └── extraction.py           # Add coverage + dismissal response schemas
│   └── services/
│       └── reporting/
│           └── coverage_validator.py  # Extend to accept dismissed slot_ids
├── alembic/
│   └── versions/
│       └── 0008_add_slot_dismissals.py  # Migration
└── tests/
    └── test_reporting/
        └── test_ast_coverage_api.py    # Unit tests for new endpoints

frontend/
└── src/
    ├── app/
    │   └── (dashboard)/
    │       └── entities/
    │           └── extraction/
    │               └── [jobId]/
    │                   └── ast/
    │                       └── page.tsx        # AST page (new route)
    ├── components/
    │   └── extraction/
    │       ├── ast-tree-view.tsx               # Section → Group → Slot tree
    │       ├── coverage-summary-card.tsx       # Dashboard card with counts + progress bar
    │       ├── slot-detail-panel.tsx           # Slot source info + source_ref jump
    │       ├── slot-action-bar.tsx             # Dismiss/undismiss + extensibility seam
    │       └── report-history-list.tsx         # Historical reports table
    └── lib/
        └── api.ts                             # Add getAstCoverage(), getReports(),
                                               # dismissSlot(), undismissSlot() API calls
```

**Structure Decision**: Frontend adds a new dynamic route under the existing `entities/extraction/` path, with 5 new components in the existing `extraction/` component directory. Backend extends the existing `extraction.py` router (consistent with 010's pattern) with 4 new endpoints and 1 new model. No new top-level directories.

## Complexity Tracking

No constitution violations to justify.
