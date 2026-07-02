# Implementation Plan: LLM Template Design Assist + Report Enhancement

**Branch**: `013-llm-template-report-enhance` | **Date**: 2026-07-02 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/013-llm-template-report-enhance/spec.md`

## Summary

Add two LLM-assisted capabilities on top of the 012 AST template + gap-filling foundation, both **default-off** and **advisory-only** (never touching deterministic evaluation):

1. **Template design assist** — a synchronous `POST /api/ast-templates/suggest-slots` endpoint runs two-round LLM prompting (document structure analysis → slot mapping) over an uploaded document or an existing extraction job, cross-references each suggestion against the published Owlready2 ontology to bind class/property IRIs and pick a `source_kind`, deduplicates against the current template (LLM-driven, round-2), and returns confidence-scored suggestions with evidence spans. A left/right linked drawer in the template editor lets a senior analyst preview suggestions against the source document and selectively adopt them.

2. **Report enhancement** — when enabled, report generation (a) merges 012's `fill_coverage_gaps` LLM values into the `RiskReport` so gap-filled slots render their value instead of "待评估", and (b) generates style-consistent narrative prose (subject description, per-dimension risk narrative, conclusion) from extracted facts using template prose as few-shot examples. All LLM-sourced content is visually annotated (gray italic + ⓘ + end disclaimer) for GMP audit. Because enhancement invokes the LLM, enhanced generation runs as an **asynchronous background job** (status poll + DOCX download); the existing synchronous path is unchanged when all flags are off.

The deterministic core (rule evaluation, coverage validation, risk-level mapping, G1 three-state) stays purely rule-driven; LLM output is data/prose only.

## Technical Context

**Language/Version**: Python 3.11+ (backend), TypeScript / Next.js (frontend)

**Primary Dependencies**: FastAPI + `APIRouter`/`Depends` + `BackgroundTasks`, SQLAlchemy 2.0 `Mapped`/`mapped_column`, Pydantic 2.x, Alembic, OpenAI-compatible client (local LLM), python-docx; React, shadcn/ui (Radix), TanStack React Query (frontend)

**Storage**: PostgreSQL (existing). Reuses `generated_reports` + `extraction_jobs`; adds report-generation async status tracking (see data-model). No new table strictly required for suggest-slots (stateless request/response); report-job status persisted on `GeneratedReport` (or an equivalent status row) — decided in research R4.

**Testing**: pytest via `uv run pytest` (backend, mock LLM client); manual UI verification (frontend)

**Target Platform**: Linux server, air-gap / offline-first deployment

**Project Type**: Web application (FastAPI backend + Next.js frontend)

**Performance Goals**: suggest-slots synchronous response within a bounded server-side timeout (target ≤ 30s, hard cap configurable); enhanced report job completes < 60s on target hardware; zero added latency on the non-LLM report path

**Constraints**: Offline-first (Constitution VI) — all three flags default off; LLM never participates in deterministic evaluation (FR-009); endpoints role-gated to `senior_analyst`; LLM keys via env only, never DB/VCS; ontology read-only

**Scale/Scope**: Single-tenant, < 10 concurrent users; ≤ 50 suggested slots per request (FR-004); typical GMP document ≤ 12k chars fed to LLM (existing `_MAX_DOC_CHARS`)

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Status | Notes |
|-----------|--------|-------|
| I. Spec-Driven Development | ✅ PASS | Full speckit flow: specify → clarify (5 Q) → plan → tasks → implement |
| II. Ontology Authority & Fidelity | ✅ PASS | suggest-slots **reads** the Owlready2 World (search_one / property-by-domain lookups) to bind IRIs; never writes TTL or the World. Ontology stays read-only authority |
| III. Traceability & Auditability | ✅ PASS | Every LLM value/narrative is source-tagged (`source="llm"`) and visually annotated in the DOCX (FR-006, SC-004); suggest-slot adoption and enhanced report generation emit audit entries; confidence + evidence span carried on each suggestion |
| IV. Test Discipline & Contract-First | ✅ PASS | Contracts (suggest-slots, report-enhancement) defined before implementation; pytest with mocked LLM for slot suggester, narrative generator, value merge; quickstart E2E |
| V. Minimal Complexity & Reuse | ⚠️ NOTED | Async report path + linked-drawer UI add complexity. Justified: async is forced by LLM latency during generation (user-clarified); reuses `get_local_llm`, `fill_coverage_gaps`, `validate_coverage`, `docx_renderer` annotation style, React Query. See Complexity Tracking |
| VI. Offline-First & Graceful Degradation | ✅ PASS | Three flags (`llm_suggest_slots_enabled`, `llm_report_merge_values`, `llm_report_narrative_enabled`) default `False`; LLM unreachable → suggest-slots returns retriable error, report generation skips enhancement and renders baseline (no `degraded` flag); flags off = byte-identical to 012 (SC-005) |

No gate violations. Principle V noted items are latency/UX-justified, not violations.

## Project Structure

### Documentation (this feature)

```text
specs/013-llm-template-report-enhance/
├── plan.md              # This file
├── research.md          # Phase 0 output
├── data-model.md        # Phase 1 output
├── quickstart.md        # Phase 1 output
├── contracts/           # Phase 1 output
│   ├── suggest-slots-api.md
│   └── report-enhancement.md
├── checklists/
│   └── requirements.md  # (from /speckit-specify)
└── tasks.md             # Phase 2 output (/speckit-tasks — NOT created here)
```

### Source Code (repository root)

```text
backend/
├── app/
│   ├── api/
│   │   ├── ast_templates.py         # Extend: POST /suggest-slots (role-gated, sync)
│   │   └── extraction.py            # Extend: async report-generation job (poll + download)
│   ├── schemas/
│   │   └── extraction.py            # Extend: SuggestSlotsRequest/Response, SuggestedSlot DTOs
│   ├── services/
│   │   ├── reporting/
│   │   │   ├── risk_report_generator.py  # Extend: RiskReport.llm_supplements / llm_generated_fields; merge gap-fill values; narrative hook (advisory only)
│   │   │   ├── docx_renderer.py          # Extend: LLM value gray-italic+ⓘ style, narrative disclaimer, end-of-report disclaimer section
│   │   │   └── narrative_generator.py    # NEW: style-consistent prose from facts + template few-shot
│   │   ├── extraction/
│   │   │   └── slot_suggester.py         # NEW: two-round LLM slot suggestion + ontology IRI binding + dedup
│   │   └── llm/
│   │       └── local_client.py           # Extend: chat_with_schema() helper (json_schema + prompt fallback)
│   └── config.py                    # Extend: llm_suggest_slots_enabled, llm_report_merge_values, llm_report_narrative_enabled, suggest_slots_timeout_s, suggest_slots_max
└── tests/
    ├── test_reporting/
    │   ├── test_narrative_generator.py   # NEW: mock LLM, facts-only, style few-shot
    │   ├── test_report_llm_merge.py      # NEW: gap-fill values → RiskReport → DOCX annotation
    │   └── test_docx_llm_annotation.py   # NEW: gray-italic + disclaimer rendering
    └── test_extraction/
        └── test_slot_suggester.py        # NEW: two-round flow, IRI binding, dedup, cap (mock LLM)

frontend/
├── src/
│   ├── components/extraction/
│   │   ├── template-slot-editor.tsx      # Extend: "AI 分析" entry + adopt-selected merge
│   │   ├── slot-suggestion-drawer.tsx    # NEW: left doc / right suggestion tree, linked scroll+highlight
│   │   └── report-generate-button.tsx    # Extend: async job start + status poll + download (when LLM enabled)
│   └── lib/
│       └── api.ts                        # Extend: suggestSlots(), async report job start/status/download
```

**Structure Decision**: Follows the existing web-application layout (`backend/` + `frontend/`). Two new backend services (`slot_suggester.py`, `narrative_generator.py`) and one new frontend component (`slot-suggestion-drawer.tsx`); everything else extends 012 modules. `chat_with_schema()` is added to the existing `local_client.py` rather than a new module.

## Complexity Tracking

| Noted Item | Why Accepted | Simpler Alternative Rejected Because |
|------------|-------------|-------------------------------------|
| Async report-generation path (Principle V) | LLM merge + narrative generation exceed a safe synchronous HTTP budget; user clarified async job with status poll (session 2026-07-02) | Synchronous generation — rejected: risks gateway timeout and blocks the UI during multi-second LLM calls |
| Linked-drawer suggestion UI (Principle V) | Bidirectional doc↔slot highlighting is the core value of P1 (evidence traceability); a flat list gives no provenance and undercuts SC-002 | Plain suggestion list — rejected: analyst cannot verify evidence spans, lowering adoption trust |

## Post-Design Constitution Re-Check

| Principle | Status | Delta from Pre-Check |
|-----------|--------|---------------------|
| I | ✅ PASS | No change |
| II | ✅ PASS | No change — `slot_suggester.py` reads the World via `search_one` / `get_*_properties_by_domain`; no writes |
| III | ✅ PASS | No change — contracts define source tags, confidence, evidence spans; audit on adopt/generate |
| IV | ✅ PASS | No change — contracts + quickstart authored; all LLM paths unit-tested with mocks |
| V | ⚠️ NOTED | No change — async + drawer justified above |
| VI | ✅ PASS | No change — three flags default off; graceful skip on LLM unreachable; SC-005 backward-compat |
