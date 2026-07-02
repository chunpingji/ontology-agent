# Implementation Plan: AST Template Management & LLM Pipeline Enhancement

**Branch**: `012-ast-template-llm-pipeline` | **Date**: 2026-07-01 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/012-ast-template-llm-pipeline/spec.md`

## Summary

Add a multi-template management layer for AST report evaluation (CRUD, versioning, document-type mapping, visual slot editor) and augment the extraction pipeline with LLM-based gap filling and ontology-driven dynamic slot expansion. The template layer (Phase 1) enables multi-format support; the LLM enhancement (Phases 2–3) improves extraction recall while preserving the deterministic evaluation engine. Both share the template data model as their foundation.

## Technical Context

**Language/Version**: Python 3.11+ (backend), TypeScript / Next.js (frontend)

**Primary Dependencies**: FastAPI + `APIRouter`/`Depends`, SQLAlchemy 2.0 `Mapped`/`mapped_column`, Pydantic 2.x, Alembic, OpenAI-compatible client (for local LLM); React, shadcn/ui (Radix), TanStack React Query, TanStack React Table (frontend)

**Storage**: PostgreSQL (existing), new tables `ast_templates` + `document_type_mappings`

**Testing**: pytest via `uv run pytest` (backend); manual UI verification (frontend)

**Target Platform**: Linux server, air-gap / offline-first deployment

**Project Type**: Web application (FastAPI backend + Next.js frontend)

**Performance Goals**: Template CRUD < 200ms; coverage recalculation with template switch < 3s; LLM gap filling < 10s per document on target hardware

**Constraints**: Offline-first (Constitution VI); LLM disabled by default; evaluation engine stays deterministic; no cloud dependencies at runtime

**Scale/Scope**: Single-tenant, small concurrent users (< 10); ~20–50 templates max; ~18–30 slots per template

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Status | Notes |
|-----------|--------|-------|
| I. Spec-Driven Development | ✅ PASS | Full speckit flow: specify → clarify → plan → tasks → implement |
| II. Ontology Authority & Fidelity | ✅ PASS | Feature reads T-Box (data properties) but never writes to TTL or Owlready2 World; ontology remains read-only authority |
| III. Traceability & Auditability | ✅ PASS | Template versioning (updates create new versions); extraction source tracking (`source: "rule"` vs `"llm"` + `source_span`); audit log entries for template CRUD |
| IV. Test Discipline & Contract-First | ✅ PASS | Contracts defined before implementation; pytest for CRUD, coverage, gap-filling; quickstart E2E scenario |
| V. Minimal Complexity & Reuse | ⚠️ NOTED | Visual slot editor adds frontend complexity beyond upload-only; justified by user's explicit clarification choice (session 2026-07-01). Reuses existing patterns: `APIRouter`/`Depends`, `Mapped`/`mapped_column`, React Query, shadcn/ui |
| VI. Offline-First & Graceful Degradation | ✅ PASS | Local LLM via OpenAI-compatible endpoint (e.g., Ollama); `local_llm_enabled` defaults to `False`; disabled = zero regression; model unavailable = warning + skip (no `degraded` flag) |

No gate violations. Principle V noted item is user-justified, not a violation.

## Project Structure

### Documentation (this feature)

```text
specs/012-ast-template-llm-pipeline/
├── plan.md              # This file
├── research.md          # Phase 0 output
├── data-model.md        # Phase 1 output
├── quickstart.md        # Phase 1 output
├── contracts/           # Phase 1 output
│   ├── ast-templates-api.md
│   └── llm-gap-filling.md
└── tasks.md             # Phase 2 output (/speckit-tasks)
```

### Source Code (repository root)

```text
backend/
├── app/
│   ├── api/
│   │   └── extraction.py          # Extend: template-aware coverage, new template CRUD routes
│   │   └── reports.py             # Extend: new AST template management endpoints
│   ├── models/
│   │   └── extraction.py          # Extend: AstTemplate, DocumentTypeMapping models
│   ├── schemas/
│   │   └── extraction.py          # Extend: template CRUD request/response schemas
│   ├── services/
│   │   ├── reporting/
│   │   │   ├── ast_template.py    # Extend: resolve_template(), LLMExtractionSource
│   │   │   ├── coverage_validator.py  # Extend: _resolve_llm_extraction()
│   │   │   └── template_expander.py   # NEW: ontology-driven slot expansion
│   │   └── extraction/
│   │       └── llm_gap_filler.py  # NEW: LLM gap-filling service
│   └── config.py                  # Extend: local_llm_* settings
├── alembic/versions/              # NEW: migration for ast_templates, document_type_mappings
└── tests/
    └── test_reporting/
        ├── test_ast_template.py       # Extend: template CRUD, resolve_template tests
        ├── test_coverage_validator.py  # Extend: LLM source coverage tests
        └── test_llm_gap_filler.py     # NEW: gap-filling unit tests (mock LLM)

frontend/
├── src/
│   ├── app/(dashboard)/
│   │   ├── entities/extraction/[jobId]/ast/page.tsx  # Extend: template selector
│   │   └── settings/ast-templates/page.tsx           # NEW: template management page
│   ├── components/extraction/
│   │   ├── slot-detail-panel.tsx       # Extend: source_span display, LLM badge
│   │   ├── ast-tree-view.tsx           # Extend: dynamic slot groups
│   │   └── template-slot-editor.tsx    # NEW: visual slot editor component
│   └── lib/
│       └── api.ts                     # Extend: template CRUD API calls
```

**Structure Decision**: Follows existing web application layout (`backend/` + `frontend/`). New files are minimal; most work extends existing modules. The visual slot editor (`template-slot-editor.tsx`) is the only significant new frontend component.

## Complexity Tracking

| Noted Item | Why Accepted | Simpler Alternative |
|------------|-------------|---------------------|
| Visual slot editor (Principle V) | User explicitly chose Option B during clarification; templates are a core management artifact and in-app editing reduces friction for domain experts who are not JSON-literate | Upload-only (Option A) — rejected by user |

## Post-Design Constitution Re-Check

| Principle | Status | Delta from Pre-Check |
|-----------|--------|---------------------|
| I | ✅ PASS | No change |
| II | ✅ PASS | No change — `template_expander.py` reads ontology via `get_data_properties_by_domain()`, no writes |
| III | ✅ PASS | No change — contracts define audit fields, versioning enforced at model level |
| IV | ✅ PASS | No change — contracts and quickstart defined |
| V | ⚠️ NOTED | No change — visual editor justified |
| VI | ✅ PASS | No change — local LLM client uses OpenAI-compatible API, defaults off |
