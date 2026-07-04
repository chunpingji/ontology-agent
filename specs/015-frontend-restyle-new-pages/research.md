# Phase 0 Research: Frontend Restyle & New Operational Pages

**Feature**: 015-frontend-restyle-new-pages | **Date**: 2026-07-03

This document resolves every unknown implied by the spec's Technical Context. Because the feature is presentation-only, "research" is primarily **grounding each new page against the capability that already exists** and **fixing the token/asset strategy** — proving the "surface existing capabilities only, zero regression" contract (FR-004, FR-027) is achievable without backend change.

Sources inspected: `design.pen` (pencil `get_editor_state` + `get_variables`), `frontend/src/app/globals.css`, `tailwind.config.ts`, `components.json`, `package.json`, `lib/api.ts`, `app/(dashboard)/layout.tsx`, `app/layout.tsx`, `app/(dashboard)/{approvals,integration}/page.tsx`, `backend/app/main.py`, `backend/app/api/{reports,compliance,integration,ontology,extraction,entities}.py`, `specs/014-ontology-dynamic-mapping/plan.md`.

---

## R1 — Design-token strategy: evolve the existing 005 contract in place

**Decision**: Treat `design.pen`'s variables as the authority and **evolve the existing feature-005 token contract in place** (`globals.css` `:root` + `tailwind.config.ts`), rather than forking a new token system. Concrete deltas:

| Token | Existing (005) | `design.pen` | Action |
|---|---|---|---|
| `--primary` / `--ring` | `221 83% 53%` (≈`#2563EB`) | `#2463EB` | Keep — already the same blue; align exact hex. |
| `--primary-foreground` | `0 0% 100%` | `#ffffff` | Keep. |
| `--background` | `0 0% 100%` (white) | `#fafafa` (neutral-50) | Update to neutral-50. |
| `--foreground` / `--card-foreground` | slate-ish `222 47% 11%` | `#0a0a0a` (neutral-950) | Shift base ramp **slate → neutral**. |
| `--muted` / `--secondary` / `--accent` | slate `210 40% 96%` | `#f5f5f5` (neutral-100) | Shift to neutral. |
| `--muted-foreground` | `215 16% 47%` | `#737373` (neutral-500) | Shift to neutral. |
| `--border` / `--input` | `214 32% 91%` | `#e5e5e5` (neutral-200) | Shift to neutral. |
| `--destructive` | `0 72% 51%` | `#e7000b` | Align. |
| `--warning` / `--success` | amber / green (005 add) | *absent in design.pen* | **Retain** — connector status (connected/connecting/failed) needs semantic success/warning; design.pen only defines `--destructive`. |
| `--radius` | `0.5rem` | *component-level only* | Keep `0.5rem`. |
| `--sidebar*` family | *absent* | `--sidebar`, `--sidebar-accent(-foreground)`, `--sidebar-border`, `--sidebar-foreground`, `--sidebar-primary(-foreground)`, `--sidebar-ring` | **ADD** the 7-token sidebar family; register in `tailwind.config.ts`. |

**Rationale**: `design.pen` uses shadcn's canonical token names — the 005 contract is already 90% aligned; the change is a base-ramp recolor (slate→neutral) + the sidebar family, both consumed through the existing `hsl(var(--token))` indirection, so no component consuming semantic tokens hardcodes anything (FR-003/SC-004 preserved). Keeping one contract honors Constitution V (no parallel system) and the 005 convention "extends/updates that convention's tokens" (spec Assumption).

**Format note**: 005 stores HSL triplets (`221 83% 53%`); `design.pen` exports hex. Either is valid with `hsl(var(--x))` **only if** the stored value stays an HSL triplet. Decision: **keep the HSL-triplet format** in `globals.css` (convert design.pen hex → HSL on entry) so `tailwind.config.ts`'s `hsl(var(--x))` wrapper is unchanged; the sidebar family follows the same triplet convention. Alternative (switch every token to raw hex + drop the `hsl()` wrapper) rejected: larger diff, breaks the 005 contract for no visual gain.

**Alternatives considered**: (a) Parallel `design.pen`-namespaced token file — rejected (two sources of truth, Constitution V). (b) Import all 8 accent themes + 5 base ramps + dark mode from design.pen — rejected as YAGNI: spec scopes **light theme, neutral base, single accent**; dark mode explicitly out of scope. The multi-theme axes in `design.pen` are a design-tool convenience, not a delivery requirement.

---

## R2 — Report Center / Detail: client-side composition over existing endpoints (no new backend)

**Context**: Clarify Q1 fixed a **unified center** (platform-generated reports **and** user-managed documents) and stated "**No new backend logic is introduced**"; FR-027 forbids new backend contracts. So the question is purely: *can the unified center be composed from endpoints that exist today?*

**Findings (endpoint audit)**:
- **Generated reports** are exposed **per job**: `GET /api/extraction/jobs/{jobId}/reports` (`listReports`), `POST …/risk-report`, `GET …/reports/{reportId}` (`pollReportStatus`), `GET …/reports/{reportId}/download` (`downloadReportById`). Each `GeneratedReportDTO` carries `report_type`, `file_path`, `file_size`, `created_at`, `actor`, `rules_summary`.
- Risk reports also exist keyed by conclusion: `GET /api/reports/{conclusion_id}` + `/pdf` (`/api/reports` router).
- **Documents** are exposed globally: `GET /api/entities?module=document` (`listDocuments`, optional `development_phase` filter) → `EntityShadow[]` with `properties_json` (doc type, phase, provenance). Content for a document renders via `GET /api/extraction/jobs/{jobId}/annotated-document` (`getAnnotatedDocument`) → tiptap `content` + `triples`/`relationships`.
- **No** global "list all generated reports across jobs" endpoint exists.

**Decision**: Report Center builds its list **client-side** by composition over the existing endpoints — **zero new backend**:
- **Documents** → `listDocuments()` (already global); category = doc-type (`DOC_TYPE_LABELS`) and/or development phase (`DEVELOPMENT_PHASES`).
- **Generated reports** → aggregate `listReports(jobId)` over `listExtractionJobs()` (a bounded fan-out at the platform's realistic job count), tagging each row `kind: "generated report"`; category = `report_type`.
- The unified list is the concatenation, each item normalized to the `Report / Document` UI entity (title, category, kind, type, date, size) — see data-model §3.
- Report **Detail** renders: (a) documents → existing tiptap `WordViewer` from `getAnnotatedDocument`; (b) generated reports → best-effort preview from report metadata/section outline + the authoritative **download** (`downloadReportById`). Related-info panel = linked ontology entities via the `extractedFrom` back-link (`listExtractedFrom`) — existing.

**Rationale**: Honors clarify Q1 + FR-027 exactly (presentation-only). Reuses `lib/api.ts` verbatim plus **read-only composition helpers** (no endpoints). The category tree is derived from existing controlled vocab (`DOC_TYPE_LABELS`, `DEVELOPMENT_PHASES`, report types) — no schema.

**Risk / explicitly logged (no silent cap)**: cross-job report aggregation is an N+1 client fan-out. At the platform's scale (internal, tens of jobs) this is acceptable; if job count grows large, a **future** read-only `GET /api/reports?all` endpoint would replace the fan-out — that is a **separate, out-of-scope** backend change, not part of 015. The plan does **not** silently truncate: if aggregation is bounded for responsiveness, the UI shows a "load more"/count affordance rather than hiding items.

**Alternatives considered**: Add a read-only aggregate endpoint now — rejected: violates FR-027 and clarify Q1 ("no new backend logic"); YAGNI until scale demands it.

---

## R3 — Approval workspace maps onto the existing compliance workflow

**Findings**: Today's `/approvals` page is backed entirely by the **compliance** router: `GET /api/compliance/signatures/pending` (`getPendingSignatures` → `PendingConclusion[]`), `POST /api/compliance/signatures` (`signConclusion` — Part-11 e-signature = **approve**), `POST /api/compliance/reject` (`rejectConclusion` — **reject** with reason), `GET /api/compliance/audit` (`getComplianceAudit` — hash-chained history), `GET /api/compliance/audit/verify`. Current nav gates `/approvals` behind `requiredRole: "qa"`.

**Decision**: The Approval **three-pane workspace** (queue / detail / timeline) is a **re-presentation** of this exact workflow — no new endpoint:
- **Queue** (FR-017) = `getPendingSignatures()`, groupable/filterable by conclusion `execution_type` / `risk_level` / status.
- **Detail** (FR-018) = the selected pending conclusion's subject/metadata; "applicant/submission time/attachments" map to available conclusion fields + its trace (`getConclusionTrace`); where a field has no backend source, the pane omits it rather than inventing data.
- **Decision** (FR-019) = `signConclusion` (approve) / `rejectConclusion` (reject with reason), recorded through the existing compliance audit chain; keep the existing `QaSignatureDialog` / `RejectDialog` logic, re-skinned.
- **Timeline** (FR-020) = `getComplianceAudit({ entity_iri })` for the selected item.

**Rationale**: Preserves the Part-11 signature semantics and audit chain (Constitution III); "supersede `/approvals`" (clarify Q3) = lift this logic into the new 3-pane page and redirect the old route. **Role gating unchanged** (FR-006): the sign action stays QA-gated exactly as today.

**Alternatives considered**: Build a generic approval-object model — rejected: no such backend entity exists; FR-027 forbids adding one. The concrete, existing instance of "approval" on this platform **is** compliance e-signature.

---

## R4 — Connector & Data Mapping are fully backed by feature-014 endpoints

**Findings** — every field the two pages need already exists:
- **Connector**: `GET/POST/DELETE /api/integration/connectors`, `POST …/{id}/test` (`{ok, latency_ms, error}`), `GET …/{id}/runs`. `Connector` carries `system_type` (`rest_api` | `database` | `doc_repo` | `file`), `name`, `last_status`, `last_error`, `connection_config`. `lib/api.ts` already provides per-type builders (`createRestApiConnector`, `createDatabaseConnector`, `createDocRepoConnector`, `createFileConnector`) that store **credentials as env-var-name references only** (never plaintext — 014 FR-006).
- **Data Mapping**: class tree via `getClassHierarchy`/`getAllClasses`; class binding via `getMappings(classIri)` (E6, `mapping_type`/`target`/`health`); property bindings via `getPropertyBindings(mappingId)` (E6b — `source_path`, `property_kind`, `transform_type`, `is_identifier`, `is_label`, `object_resolution`, `status`, `version`); coverage/health via `getMappingHealth()` (`ok/unmapped/drift/orphan`); mapping test via `validateBinding(mappingId)` (`BindingValidationReport`). Edits carry `expected_version` optimistic concurrency (409 → `VersionConflictError`, already handled).

**Decision**: Both pages are **pure presentations** of the 014 surface. Connector groups the flat `listConnectors()` by `system_type` (FR-010); status indicator maps `last_status`/`last_error` → connected/connecting/failed/not-connected semantic colors (FR-012, R5-status below). Data Mapping is a class-tree master + property-binding-table detail with a coverage bar from `getMappingHealth` and a "mapping test" button calling `validateBinding` (FR-013–016). All create/edit/delete keep the existing optimistic-concurrency + secret-as-env-ref contracts.

**`/integration` supersession (carry-over completeness)**: today's `/integration` page has **three** concerns — *connectors*, *doc-repo (研发文档溯源)*, *realtime inference dashboard (compatibility matrix / schedule risks via `getDashboard`)*. Superseding it (clarify Q3) redistributes, **dropping nothing**:
- connectors → **Connector** page;
- doc-repo → **Connector** (as the `doc_repo`/`file` source type) **and** its documents surface in **Report Center**;
- realtime inference dashboard → retained on **Overview (首页)** (which `design.pen` frames as a dashboard) or a retained panel reachable from nav.
This redistribution is a first-class task and is asserted by the zero-regression matrix (quickstart) so `getDashboard`/realtime inference remains reachable (FR-004/FR-005).

**Status mapping (FR-012)**: `last_status`/`last_error` → `connected` (last ok, no error), `connecting`/`syncing` (run in progress), `failed` (last_error set / test failed), `not-connected` (never tested/synced). Air-gap-from-public-cloud is **not** a failure state — an intranet connector simply shows its real last status; only a genuinely unreachable **intranet** endpoint is `failed` with its reason (Constitution VI, spec edge case).

---

## R5 — Shared-component states & interaction primitives

**Decision**: Every data-backed view implements the four canonical states with shared components (FR-007): **loading** (existing `Skeleton`), **empty** (a shared `EmptyState` composed from `Card` + icon + message — new small composition), **error** (existing `Alert` variant `destructive`), **populated**. Row/context actions use the new `DropdownMenu`; coverage/progress uses the new `Progress`; boolean flags (identifier/label, connector active) use the new `Switch`/`Checkbox`; the Report Detail back-nav uses the new `Breadcrumb`; long lists use the new `Pagination` (or react-table pagination). These are enumerated as the YAGNI delta in R7.

**Rationale**: One consistent state vocabulary across all pages satisfies FR-007/SC-008 and the componentization convention (no bespoke per-page empty/error markup).

---

## R6 — Offline fonts & icons (air-gap)

**Findings**: Root `app/layout.tsx` currently imports **no** web font (system-font fallback) — so there is no CDN dependency today, but also no Inter. `design.pen` specifies **Inter** for Latin text; the UI is primarily **zh-CN** (FR-029) and needs a CJK face.

**Decision**: Self-host fonts, no runtime CDN (Constitution VI, FR-028, SC-009):
- Inter via `next/font/local` (or `next/font/google`, which **self-hosts at build time** — the bytes are vendored into the build, no runtime fetch) applied on `<body>`.
- A bundled CJK stack for zh-CN — prefer a self-hosted subset (e.g. Noto Sans SC) via `next/font/local`, or fall back to the platform's system CJK stack if bundling a full CJK face is disproportionate. Decision: **bundle a subset**; document the exact face in the design-system contract.
- Icons: continue with **lucide-react** (already bundled, matches `components.json iconLibrary: lucide` and `design.pen`'s lucide usage). No icon CDN.

**Rationale**: Faithful to `design.pen` typography while guaranteeing air-gap operation. Build-time vendoring keeps runtime egress at zero — verified as a quickstart offline check.

**Alternatives considered**: Runtime Google Fonts `<link>` — **rejected** (Constitution VI violation). System-font-only — rejected (fails FR-001 "adopt design.pen … typography" fidelity for Latin).

---

## R7 — Shared-component delta (YAGNI-scoped, FR-002)

**Findings**: `components/ui/` ships **14** primitives today (alert, badge, button, card, dialog, input, label, select, separator, sheet, skeleton, table, tabs, textarea). `design.pen` exposes 87 reusable components; clarify Q4 scopes delivery to **only** those the in-scope pages consume.

**Decision — build/restyle this bounded set, and no more**:

| New shared component | New npm dep? | Consumed by (in-scope) |
|---|---|---|
| `sidebar` (+ nav item/section) | no (composed) | App shell (all pages) |
| `topbar` (+ `avatar`) | `@radix-ui/react-avatar` | App shell |
| `breadcrumb` | no (composed) | Report Detail (FR-024), Data Mapping |
| `dropdown-menu` | `@radix-ui/react-dropdown-menu` | Connector/report row actions, Report Center |
| `tooltip` | `@radix-ui/react-tooltip` | icon buttons, status hints |
| `progress` | `@radix-ui/react-progress` | Data Mapping coverage (FR-015), connector |
| `switch` | `@radix-ui/react-switch` | binding identifier/label flags, connector active |
| `checkbox` | `@radix-ui/react-checkbox` | list selection, binding flags |
| `accordion` | **none** (dep already present) | Data Mapping class tree / grouped sections |
| `pagination` | no (composed) | Report Center, long tables |
| `data-table` | no (`@tanstack/react-table` present) | Connector/Data-Mapping/Report tables |
| `empty-state` | no (composed) | all data-backed views (FR-007) |

Existing 14 primitives are **restyled** to the evolved tokens (mostly automatic — they already consume `--token`s). **Unused** `design.pen` components (OTP input, combobox, mesh cards, etc.) are **not** built (out of scope, spec Out-of-Scope). Each delivered component lands in `components/ui/` under the shadcn new-york contract and is recorded in the componentization convention (spec Assumption).

**Rationale**: Minimal, in-family growth (Constitution V); the 6 new Radix deps are each demanded by a concrete component above; installed from the intranet mirror at build time (Constitution VI). If a future page needs more of the 87, the library grows then (clarify Q4).

---

## R8 — Restyle breadth & zero-regression method (FR-004, FR-009, SC-001)

**Decision**: **Global adoption** (clarify Q2). Every `(dashboard)` route is re-skinned:
- Pages with a `design.pen` frame (Overview/首页, Ontology, + the 5 new) match the frame (SC-002 ≥95% conformance).
- Pages **without** a frame (Analysis, Entities, Extraction + AST, Settings, Rules, ast-templates) adopt the language via shared components/tokens **without a layout redesign** (FR-009) — i.e. their JSX swaps bespoke markup for shared primitives and drops hardcoded colors, but their information architecture and data flow are untouched.

**Zero-regression method**: because pages keep their existing `lib/api.ts` calls and handlers, regression risk is confined to markup. The `quickstart.md` encodes a **per-page primary-action matrix** (create/edit/save, run extraction, publish, approve/reject, filter/search, run assessment, SPARQL, etc.) plus role-gating spot checks — the executable proof of SC-001. Restyle proceeds **one page at a time** (design.pen guidance: never leave a screen half-migrated).

---

## R9 — Routing, IA & redirects (FR-005)

**Decision**: New routes under `(dashboard)`: `/connector`, `/data-mapping`, `/reports` (center), `/reports/[reportId]` (detail), `/approval`. Legacy `/integration` → redirect `/connector`; `/approvals` → redirect `/approval` (Next.js `redirects()` in `next.config` for permanent nav, plus in-page `redirect()` guards). The sidebar nav model (single source of truth in the shell) is reorganized to `design.pen`'s grouped IA, adds the five entries, preserves every existing destination, and keeps role-gating flags (`requiredRole`) intact.

**Rationale**: Satisfies "supersede with redirect, functionality carried over" (clarify Q3) and "every existing destination reachable" (FR-005); the nav model already exists in `(dashboard)/layout.tsx` and is extended, not replaced.

---

## Requirement → Design traceability

| FR | Resolved by |
|---|---|
| FR-001 (adopt design.pen visual authority) | R1 (tokens), R6 (fonts), R7 (components), R8 (breadth) |
| FR-002 (shared library, YAGNI) | R7 |
| FR-003 (semantic tokens, no raw palette) | R1 |
| FR-004 (zero regression) | R8 (method), R2/R3/R4 (reuse existing calls) |
| FR-005 (nav preserved + 5 new; supersede/redirect) | R9 |
| FR-006 (role gating unchanged) | R3, R4, R8 (spot checks) |
| FR-007 (loading/empty/error states) | R5 |
| FR-008 (accessibility preserved) | R7 (Radix a11y), design-system contract |
| FR-009 (all pages re-skinned) | R8 |
| FR-010–012 (Connector) | R4, R5 (status) |
| FR-013–016 (Data Mapping) | R4 |
| FR-017–020 (Approval) | R3 |
| FR-021–023 (Report Center) | R2 |
| FR-024–026 (Report Detail) | R2 |
| FR-027 (surface existing only, no backend change) | R2, R3, R4 (all compositions over existing endpoints) |
| FR-028 (offline fonts/icons) | R6 |
| FR-029 (zh-CN) | R6 (CJK font), existing copy |

**All Technical-Context unknowns resolved. No `NEEDS CLARIFICATION` remains.** Proceed to Phase 1.
