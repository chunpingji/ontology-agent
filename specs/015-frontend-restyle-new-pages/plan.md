# Implementation Plan: Frontend Restyle & New Operational Pages (design.pen)

**Branch**: `015-frontend-restyle-new-pages` | **Date**: 2026-07-03 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `/specs/015-frontend-restyle-new-pages/spec.md`

## Summary

Adopt the `design.pen` visual language as the platform's single UI source of truth — re-skinning the entire application shell and **every** existing dashboard page with **zero functional regression** — and build the five new operational pages `design.pen` introduces: **Connector (数据源接入)**, **Data Mapping (数据映射)**, **Report Center (报告中心)**, **Report Detail (报告详情)**, and **Approval (审批工作台)**.

This is a **frontend-only, presentation-layer** feature. The technical approach is **re-skin + compose, not rebuild**: the platform already ships a shadcn "new-york" design-token contract (feature 005 — `globals.css` + `tailwind.config.ts`), a typed API client (`lib/api.ts`) covering every capability the new pages surface, React Query, and per-page logic that must be preserved verbatim. The genuinely new build is: (1) an in-place **evolution of the existing token contract** to match `design.pen` (neutral base ramp, `--sidebar-*` token family, self-hosted fonts); (2) a **YAGNI-scoped delta of shared shadcn primitives** the in-scope pages need but the library lacks today (breadcrumb, dropdown-menu, tooltip, progress, switch, checkbox, avatar, accordion wrapper, pagination, sidebar shell, data-table); (3) the **five new pages**, each composed from those shared components over **existing** `lib/api.ts` calls; and (4) **superseding** the legacy `/integration` and `/approvals` routes with redirects, carrying all their functionality onto the new pages. **No backend contract, data model, business logic, or migration changes** (FR-027) — Report Center/Detail and Approval are composed client-side over endpoints that already exist.

## Technical Context

**Language/Version**: TypeScript 5 / React 19 (frontend only). No backend (Python) changes.

**Primary Dependencies**: Next.js 16 (App Router, `(dashboard)` route group), React 19, Tailwind CSS 3.4 + `tailwindcss-animate`, shadcn/ui (new-york, `components.json` cssVariables) built on Radix primitives, `class-variance-authority` + `clsx` + `tailwind-merge`, `@tanstack/react-query` 5, `@tanstack/react-table` 8, `@tiptap` 3 (existing rich-text/document renderer), `d3` 7, `zustand` 4, `lucide-react` (bundled icon set).
**New dependencies (build-time, bundled — Constitution V justification below)**: `@radix-ui/react-dropdown-menu`, `@radix-ui/react-tooltip`, `@radix-ui/react-progress`, `@radix-ui/react-switch`, `@radix-ui/react-checkbox`, `@radix-ui/react-avatar`. All are same-family Radix primitives (no parallel UI framework); each is required by an in-scope `design.pen` component and installed from the intranet npm mirror (no runtime fetch). `@radix-ui/react-accordion` is **already** a dependency — only a `ui/accordion.tsx` wrapper is added, no new package.

**Storage**: None added. The UI reads/writes exclusively through the existing FastAPI surface via `lib/api.ts`. No browser-persisted state beyond the existing identity `localStorage` key.

**Testing**: This feature introduces **no backend interface**, so no new pytest contracts are required (Constitution IV — see gate note). Verification is **contract-first UI contracts** (`contracts/`, page/component behavior + zero-regression matrix) plus an **executable `quickstart.md`** that walks every existing page's primary actions (regression proof, SC-001) and each new page's primary task end-to-end. Heavy E2E tooling (Playwright/Vitest) is **not** introduced — it would be a YAGNI framework add for a re-skin; the manual quickstart matrix is the executable judge, consistent with the project's current frontend (no test runner today, `lint` only).

**Target Platform**: Modern evergreen browsers on an internal, **air-gapped** (no public internet/cloud) network. All fonts and icons MUST be bundled/self-hosted; no CDN at runtime (Constitution VI, FR-028).

**Project Type**: Web application (Option 2) — existing `backend/` + `frontend/` trees. **All changes are confined to `frontend/`.**

**Performance Goals**: Standard interactive SPA responsiveness (internal, low-concurrency, long-lived platform). No high-throughput target. Lists (connectors, reports, class tree) render smoothly at the platform's realistic scale (single-digit→low-tens of connectors, ~200 ontology classes, tens–low-hundreds of reports/documents); tables paginate/virtualize where `design.pen` implies it.

**Constraints**: Zero functional regression on existing pages (FR-004, SC-001); role gating (senior_analyst / operator / qa) unchanged (FR-006); credentials never displayed or collected as plaintext — connectors reference **env-var names** only, carrying over 014's secret-handling contract; semantic tokens only, no raw-palette hardcoding (FR-003, SC-004); accessibility guarantees preserved (FR-008); primary language zh-CN (FR-029); air-gap-from-public-cloud is a **normal** state, never rendered as an error/`degraded` (FR-028, SC-009).

**Scale/Scope**: ~13 existing dashboard routes re-skinned; 5 new pages; 2 legacy routes superseded (redirected); ~10–12 new shared UI primitives + 1 app-shell (sidebar/top-bar) + 1 data-table composition; token-contract evolution in 2 files (`globals.css`, `tailwind.config.ts`); `lib/api.ts` gains **read-only composition helpers only** (no new endpoints).

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

Constitution **v1.1.0** — six principles evaluated. This is a **frontend-only presentation feature**; several gates are satisfied structurally (no backend/ontology surface is touched).

| Principle | Gate | Status | Notes |
|---|---|---|---|
| **I. Spec-Driven Development** | Feature originates from an approved, clarified spec; plan traces to FRs. | ✅ PASS | `spec.md` clarified (4 Qs, Session 2026-07-03); checklist 16/16. Every FR maps to a design element (research.md traceability). No scope decided in code — the two scope boundaries (unified report/document center; supersede legacy routes) were resolved in `clarify`. |
| **II. Ontology Authority & Fidelity** (NON-NEGOTIABLE) | T-Box stays authoritative; no unmanaged mutation; dual-storage consistency; triple-diff preview for TTL change. | ✅ PASS | Frontend-only; **no TTL write surface added**. Data Mapping edits flow through the **existing** 014 endpoints (`/api/ontology/.../mappings`, `/property-bindings`, `/validate`) which already enforce authority, dual-store consistency, optimistic concurrency, and diff preview. This feature adds **no** new mutation path. |
| **III. Traceability & Auditability** | Versioning + optimistic concurrency; audit log; provenance. | ✅ PASS | Reuses existing versioned endpoints; `lib/api.ts` already surfaces `VersionConflictError` (409) — the restyled binding editor keeps that flow. Approval decisions record through the **existing** compliance audit chain (`/api/compliance/*`); the timeline pane **reads** that chain. No new write/audit path. |
| **IV. Test Discipline & Contract-First** | Contracts before implementation; pytest on backend critical paths; executable quickstart; health gates. | ✅ PASS (scoped) | **No backend interface is added**, so no new pytest contract applies. Discipline is met by **contract-first UI contracts** in `contracts/` (authored before implementation) + an **executable `quickstart.md`** whose zero-regression matrix and per-page acceptance are runnable checks. Existing backend pytest is untouched and remains green. |
| **V. Minimal Complexity & Reuse** | Reuse existing stack; YAGNI; new deps minimized + justified; no parallel framework. | ✅ PASS | Reuses Tailwind+shadcn token contract, `lib/api.ts`, React Query, react-table, tiptap, existing page logic. New deps are **6 same-family Radix primitives**, each demanded by an in-scope `design.pen` component (FR-002 YAGNI): unused `design.pen` components are **not** built. No parallel UI framework, no state-mgmt change. Justification recorded here + in research R7; not a violation → Complexity Tracking stays empty. |
| **VI. Offline-First & Graceful Degradation** | Air-gap default; offline ≠ `degraded`; bundled assets; no runtime egress. | ✅ PASS | Fonts self-hosted (Inter + CJK via `next/font/local`, R6); icons bundled (lucide); new Radix deps are **build-time** (no runtime fetch). Connector status renders air-gap-from-cloud as a normal state, never an error (FR-028, SC-009); unreachable **intranet** source shows a clear failed/degraded reason without crashing (edge case). |

**Security & Compliance**: Role gating preserved unchanged (FR-006); the Connector create/edit form collects **env-var-name references** for credentials, never plaintext secrets (carries 014 FR-006/FR-010) — no secret is displayed, stored, or transmitted by the UI. Identity continues via the trusted-gateway `X-User`/`X-Role` headers already in `lib/api.ts`.

**Gate result: PASS (pre-Phase-0).** No violations; Complexity Tracking table left empty.

### Post-Design Re-evaluation (after Phase 1)

Re-checked against the Phase 1 artifacts (`research.md`, `data-model.md`, `contracts/`, `quickstart.md`). **Still PASS — no new violations:**

- **I**: Every FR traces to a design decision (research.md "Requirement → Design traceability") and to a UI contract. ✅
- **II**: `data-model.md` confirms all surfaced entities are **backend-owned**; the only write flows (connector CRUD/test, binding edits, approval decisions) reuse existing authoritative endpoints — no TTL-merge or ontology-mutation surface added. ✅
- **III**: Binding editor preserves `expected_version` optimistic concurrency + 409 handling; Approval timeline reads the existing compliance audit chain; Report Center provenance (`extractedFrom`, job/source refs) is read-only. ✅
- **IV**: `contracts/` written before implementation; `quickstart.md` is an executable zero-regression + new-page matrix; backend pytest untouched. ✅
- **V**: Design confirms the shared-component delta is bounded to what in-scope pages consume (data-model §5); Report Center/Detail and Approval are **client-side compositions over existing endpoints** — zero new backend surface. ✅
- **VI**: Research R6 fixes self-hosted fonts + bundled icons; R2/R3 keep air-gap and unreachable-source states non-crashing and correctly labeled. ✅

Design surfaced no complexity requiring justification — Complexity Tracking remains empty.

## Project Structure

### Documentation (this feature)

```text
specs/015-frontend-restyle-new-pages/
├── plan.md              # This file (/speckit-plan command output)
├── spec.md              # Feature specification (clarified)
├── research.md          # Phase 0 output (/speckit-plan)
├── data-model.md        # Phase 1 output (/speckit-plan) — UI-surfaced entities + component/token model
├── quickstart.md        # Phase 1 output (/speckit-plan) — executable zero-regression + new-page matrix
├── contracts/           # Phase 1 output (/speckit-plan) — UI contracts
│   ├── design-system.md         # Token evolution + shared-component delta + a11y contract
│   ├── app-shell.md             # Sidebar/top-bar IA, nav model, route redirects
│   ├── connector.md             # Connector page behavior (over /api/integration/connectors)
│   ├── data-mapping.md          # Data Mapping page behavior (over /api/ontology mappings + bindings)
│   ├── report-center.md         # Report Center + Report Detail (composed over existing endpoints)
│   └── approval.md              # Approval workspace (over /api/compliance/*)
├── checklists/
│   └── requirements.md  # Spec quality checklist (16/16)
└── tasks.md             # Phase 2 output (/speckit-tasks — NOT created here)
```

### Source Code (repository root)

**All changes are confined to `frontend/`. No `backend/` file changes, no Alembic migration.**

```text
frontend/
└── src/
    ├── app/
    │   ├── globals.css                         # EVOLVE token contract → design.pen (neutral base, --sidebar-*)
    │   ├── layout.tsx                           # ADD self-hosted fonts (next/font/local: Inter + CJK)
    │   ├── providers.tsx                        # reuse (React Query) — no change expected
    │   └── (dashboard)/
    │       ├── layout.tsx                       # RESTYLE app shell → design.pen Sidebar + top bar; new IA
    │       ├── overview/ ontology/ entities/ …  # RE-SKIN existing pages (token/component swap; no logic change)
    │       ├── connector/page.tsx               # NEW — Connector (数据源接入)
    │       ├── data-mapping/page.tsx            # NEW — Data Mapping (数据映射)
    │       ├── reports/page.tsx                 # NEW — Report Center (报告中心)
    │       ├── reports/[reportId]/page.tsx      # NEW — Report Detail (报告详情)
    │       ├── approval/page.tsx                # NEW — Approval (审批工作台) — supersedes /approvals
    │       ├── integration/page.tsx             # SUPERSEDE → redirect to /connector (functionality carried over)
    │       └── approvals/page.tsx               # SUPERSEDE → redirect to /approval
    ├── components/
    │   ├── ui/                                  # ADD shared primitives (YAGNI delta): breadcrumb, dropdown-menu,
    │   │                                        #   tooltip, progress, switch, checkbox, avatar, accordion,
    │   │                                        #   pagination, sidebar, data-table  (+ RESTYLE existing 14)
    │   ├── shell/                               # NEW — Sidebar, TopBar, nav model (design.pen app frame)
    │   ├── connector/ data-mapping/ reports/ approval/   # NEW — page-specific composed components
    │   └── integration/ approvals/ …           # REUSE — logic lifted into superseding pages, then retired
    ├── lib/
    │   ├── api.ts                               # ADD read-only composition helpers ONLY (report/document
    │   │                                        #   aggregation, approval-queue mapping) — NO new endpoints
    │   ├── use-identity.ts  utils.ts            # reuse
    │   └── (report-center compose, approval map helpers)
    ├── stores/                                  # reuse (zustand) as needed for 3-pane selection state
    ├── tailwind.config.ts                       # EVOLVE — register --sidebar-* token colors
    └── next.config.*                            # ADD redirects: /integration→/connector, /approvals→/approval
```

**Structure Decision**: **Option 2 (web application), frontend subtree only.** The repository already has `frontend/` (Next.js App Router). This feature (a) evolves the shared token contract in place (`globals.css` + `tailwind.config.ts`) rather than forking a parallel design system; (b) grows the shared `components/ui` library by the exact set the in-scope pages consume; (c) adds five route folders under the existing `(dashboard)` group and redirects the two superseded routes; and (d) restyles existing pages by swapping to shared components/tokens without touching their data flow. No new top-level project, no backend change, no migration.

## Complexity Tracking

> No Constitution Check violations. The six new Radix primitives are same-family, YAGNI-scoped dependencies (justified in Technical Context + research R7), not a complexity violation. Table intentionally empty.

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| — | — | — |
