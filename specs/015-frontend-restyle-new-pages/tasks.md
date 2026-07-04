---
description: "Task list for 015 — Frontend Restyle & New Operational Pages"
---

# Tasks: Frontend Restyle & New Operational Pages (design.pen)

**Input**: Design documents from `/specs/015-frontend-restyle-new-pages/`
**Prerequisites**: [plan.md](plan.md), [spec.md](spec.md), [research.md](research.md), [data-model.md](data-model.md), [contracts/](contracts/), [quickstart.md](quickstart.md)

**Tests**: **None (automated).** Per [plan.md](plan.md) (Constitution IV, scoped) this feature adds **no backend interface**, so no pytest/Playwright/Vitest is introduced. Verification is the **executable [quickstart.md](quickstart.md) matrix** — a per-phase validation task runs the relevant slice of it. This is deliberate (YAGNI), consistent with the current frontend (`lint` only, no test runner).

**Organization**: Tasks grouped by user story. **All changes are confined to `frontend/`** — no `backend/` change, no migration (FR-027).

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependency on an incomplete task)
- **[Story]**: US1–US6 (user-story phases only; Setup/Foundational/Polish carry no story label)
- Exact file paths are included in every task

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Dependencies + offline assets in place before any styling work.

- [X] T001 Add the 6 same-family Radix primitive deps (`@radix-ui/react-dropdown-menu`, `-tooltip`, `-progress`, `-switch`, `-checkbox`, `-avatar`) to `frontend/package.json` and install from the intranet npm mirror (`@radix-ui/react-accordion` is already present — no add). Confirm no runtime CDN fetch.
- [X] T002 [P] Vendor self-hosted fonts (Inter latin + a CJK/zh-CN subset) under `frontend/src/app/fonts/` and load them via `next/font/local` in `frontend/src/app/layout.tsx`, replacing the current system-font stack (FR-028, FR-029, research R6).
- [X] T003 [P] Record the restyle baseline: run `npm run lint` and `npm run build` in `frontend/` and confirm both are green before changes (regression reference for SC-001).

---

## Phase 2: Foundational (Design System + App Shell — Blocking Prerequisites)

**Purpose**: The shared token contract, `components/ui` primitive delta, and app shell that **every** page (US1 re-skin + all 5 new pages) depends on.

**⚠️ CRITICAL**: No user story work can begin until this phase is complete.

**Token contract** (contracts/design-system.md, research R1):

- [X] T004 Evolve the semantic token contract in `frontend/src/app/globals.css` → `design.pen` values: neutral base ramp, add the 7-token `--sidebar-*` family, keep `--warning`/`--success`/`--radius`, retain HSL-triplet format (so `hsl(var(--x))` keeps working).
- [X] T005 Evolve `frontend/tailwind.config.ts` → register the `--sidebar-*` token colors as `hsl(var(--sidebar-*))` (depends on T004).

**Shared `components/ui` primitives** (YAGNI delta, contracts/design-system.md §shared-component; all depend on T004 tokens):

- [X] T006 [P] Restyle the existing 14 primitives in `frontend/src/components/ui/` (alert, badge, button, card, dialog, input, label, select, separator, sheet, skeleton, table, tabs, textarea) to the evolved tokens — public props unchanged.
- [X] T007 [P] Add `EmptyState` primitive in `frontend/src/components/ui/empty-state.tsx` (shared empty/error surface for FR-007/SC-008).
- [X] T008 [P] Add `dropdown-menu` primitive in `frontend/src/components/ui/dropdown-menu.tsx` (Radix).
- [X] T009 [P] Add `tooltip` primitive in `frontend/src/components/ui/tooltip.tsx` (Radix).
- [X] T010 [P] Add `progress` primitive in `frontend/src/components/ui/progress.tsx` (Radix).
- [X] T011 [P] Add `switch` primitive in `frontend/src/components/ui/switch.tsx` (Radix).
- [X] T012 [P] Add `checkbox` primitive in `frontend/src/components/ui/checkbox.tsx` (Radix).
- [X] T013 [P] Add `avatar` primitive in `frontend/src/components/ui/avatar.tsx` (Radix).
- [X] T014 [P] Add `accordion` wrapper in `frontend/src/components/ui/accordion.tsx` (Radix accordion already installed).
- [X] T015 [P] Add `breadcrumb` primitive in `frontend/src/components/ui/breadcrumb.tsx`.
- [X] T016 [P] Add `pagination` primitive in `frontend/src/components/ui/pagination.tsx`.
- [X] T017 [P] Add `data-table` composition in `frontend/src/components/ui/data-table.tsx` over `@tanstack/react-table` 8 (shared column/sort/pagination + loading/empty wiring).

**App shell** (contracts/app-shell.md, data-model §1):

- [X] T018 Build the single-source nav model in `frontend/src/components/shell/nav.ts` (all **existing** destinations grouped per `design.pen`, `requiredRole` gating carried over verbatim, `isActive` helper). New-page entries are added by their own stories.
- [X] T019 [P] Build `Sidebar` in `frontend/src/components/shell/sidebar.tsx` (grouped nav + section titles + active state, `--sidebar-*` tokens) (depends on T018).
- [X] T020 [P] Build `TopBar` in `frontend/src/components/shell/topbar.tsx` (page title, identity/avatar via existing `useIdentity`, role switcher) (depends on T013).
- [X] T021 Restyle the app shell in `frontend/src/app/(dashboard)/layout.tsx` to render the `Sidebar` + `TopBar` frame around routed content (depends on T019, T020).

**Checkpoint**: Design system + shell ready — user stories can begin.

---

## Phase 3: User Story 1 — Unified restyle, zero functional regression (Priority: P1) 🎯 MVP

**Goal**: Every existing dashboard page adopts the `design.pen` language via shared components/tokens with **no** behavior change; IA and role gating preserved.

**Independent Test**: Navigate every existing route, exercise its primary actions across all 3 roles; confirm restyle conformance **and** zero missing/broken/changed behavior.

**Re-skin existing pages** (token/component swap only — no data-flow change; different route folders → parallel):

- [X] T022 [P] [US1] Re-skin Overview in `frontend/src/app/(dashboard)/overview/` and host the **realtime-inference dashboard** panel here (relocated from `/integration`, research R4 — nothing dropped).
- [X] T023 [P] [US1] Re-skin Ontology pages in `frontend/src/app/(dashboard)/ontology/` (+ `ontology/rules/`).
- [X] T024 [P] [US1] Re-skin Entities/Extraction in `frontend/src/app/(dashboard)/entities/` (+ `extraction/`, `[jobId]/ast/`).
- [X] T025 [P] [US1] Re-skin Analysis in `frontend/src/app/(dashboard)/analysis/`.
- [X] T026 [P] [US1] Re-skin Settings in `frontend/src/app/(dashboard)/settings/` (+ `ast-templates/`).
- [X] T027 [P] [US1] Re-skin the existing Integration page in `frontend/src/app/(dashboard)/integration/` — kept reachable + re-skinned until superseded in Polish (MVP coherence: it remains the only path to connectors until US2 ships).
- [X] T028 [P] [US1] Re-skin the existing Approvals page in `frontend/src/app/(dashboard)/approvals/` — kept reachable + re-skinned until superseded in US4.
- [X] T029 [P] [US1] Re-skin cross-page components in `frontend/src/components/{dashboard,ontology,entities,extraction,analysis,kg}/` to the evolved tokens (shared cards/tables/badges).
- [X] T030 [US1] Verify role-gating parity (senior_analyst/operator/qa) across all re-skinned pages vs pre-restyle behavior via the top-bar role switcher (FR-006).
- [X] T031 [US1] Run the **US1 zero-regression slice** of [quickstart.md](quickstart.md) §A (all existing routes × 3 roles): no missing/broken action, no layout break under empty/long content (SC-001, SC-010).

**Checkpoint**: Existing product fully restyled, zero regression — **MVP shippable**.

---

## Phase 4: User Story 2 — Connector page (数据源接入) (Priority: P2)

**Goal**: First-class page to view (grouped by type), create, edit, test, and remove data-source connectors. Supersedes `/integration`'s connector concern. See [contracts/connector.md](contracts/connector.md).

**Independent Test**: Add a REST connector → run its test → edit → delete; status indicators and role gating behave per design; no plaintext-secret field.

- [X] T032 [P] [US2] Add a read-only connector status-mapping helper in `frontend/src/lib/api.ts` (map `last_status`/`last_error` → `connected`/`connecting`/`failed`/`not-connected`; research R4). No new endpoint.
- [X] T033 [US2] Build `ConnectorList` grouped by `system_type` in `frontend/src/components/connector/connector-list.tsx` (name, type, status, endpoint/summary, last-sync/last-status) over existing `listConnectors` (depends on T032, T017).
- [X] T034 [US2] Build the semantically-colored `StatusIndicator` in `frontend/src/components/connector/status-indicator.tsx` (connected/connecting/failed/not-connected, FR-012).
- [X] T035 [US2] Build the create/edit dialog in `frontend/src/components/connector/connector-form.tsx` — per-type fields (rest_api/database/doc_repo/file) via existing per-type builders; **credentials collected as env-var-name references ONLY — no plaintext secret field** (Constitution Security; carries 014 FR-006).
- [X] T036 [US2] Build the connection-test action + feedback in `frontend/src/components/connector/connector-test.tsx` (`testConnector` → `{ok, latency_ms, error}`; air-gap-from-cloud = normal state, unreachable **intranet** = `failed` with reason — never a crash, FR-028 edge case).
- [X] T037 [US2] Assemble the Connector page in `frontend/src/app/(dashboard)/connector/page.tsx` (list + create/edit/delete/test; loading/empty/error states; edit/create/delete gated to senior_analyst) (depends on T033–T036).
- [X] T038 [US2] Add the Connector nav entry (数据源接入 → `/connector`) in `frontend/src/components/shell/nav.ts`.
- [X] T039 [US2] Run the **US2** slice of [quickstart.md](quickstart.md) §B (create REST connector → test → edit → delete; status matches `last_status`; no plaintext-secret field; role gating; SC-006).

**Checkpoint**: Connector page functional.

---

## Phase 5: User Story 3 — Data Mapping page (数据映射) (Priority: P2)

**Goal**: Select an ontology class and view/manage its class + property bindings, coverage, and mapping test — over existing feature-014 endpoints. See [contracts/data-mapping.md](contracts/data-mapping.md).

**Independent Test**: Pick a class → view its property-binding table → add/edit a binding → observe coverage change → run a mapping test; stale-version save surfaces a conflict.

- [X] T040 [US3] Build the ontology `ClassTree` selector in `frontend/src/components/data-mapping/class-tree.tsx` over `getClassHierarchy`/`getAllClasses` (deep-tree overflow-safe, SC-010).
- [X] T041 [US3] Build the property-`BindingTable` in `frontend/src/components/data-mapping/binding-table.tsx` (`source_path`, `transform_type`, `transform_config`, `is_identifier`/`is_label`, per-property mapped/unmapped) over `getMappings`/`getPropertyBindings` (depends on T017).
- [X] T042 [US3] Build the `Coverage` indicator in `frontend/src/components/data-mapping/coverage.tsx` over `getMappingHealth` (Progress; ok/unmapped/drift/orphan buckets) (depends on T010).
- [X] T043 [US3] Build the `BindingEditor` (create/edit/delete + mapping test) in `frontend/src/components/data-mapping/binding-editor.tsx` over `createPropertyBinding`/`updatePropertyBinding`/`deletePropertyBinding`/`validateBinding`; preserve `expected_version` optimistic concurrency and the existing `VersionConflictError` (409) conflict prompt (FR-016).
- [X] T044 [US3] Assemble the Data Mapping page in `frontend/src/app/(dashboard)/data-mapping/page.tsx` (class tree + class binding + property table + coverage + test; loading/empty/error; edit gated to senior_analyst) (depends on T040–T043).
- [X] T045 [US3] Add the Data Mapping nav entry (数据映射 → `/data-mapping`) in `frontend/src/components/shell/nav.ts`.
- [X] T046 [US3] Run the **US3** slice of [quickstart.md](quickstart.md) §B (select class → view/add/edit binding → coverage updates → run mapping test → stale-version conflict prompt).

**Checkpoint**: Data Mapping functional.

---

## Phase 6: User Story 4 — Approval workspace (审批) (Priority: P2)

**Goal**: Three-pane queue/detail/timeline over the existing compliance e-signature workflow. Supersedes `/approvals`. See [contracts/approval.md](contracts/approval.md).

**Independent Test**: Filter the queue → select an item → read detail + attachments → approve or reject-with-reason → timeline updates; unauthorized role sees disabled controls.

- [X] T047 [P] [US4] Add a read-only approval-queue mapping helper in `frontend/src/lib/api.ts` (`getPendingSignatures` → queue view-model grouped/filterable by `execution_type` + `risk_level`). No new endpoint.
- [X] T048 [US4] Build the `Queue` pane in `frontend/src/components/approval/queue.tsx` (filter by type/status, select item) (depends on T047).
- [X] T049 [US4] Build the `Detail` pane in `frontend/src/components/approval/detail.tsx` (subject, applicant, submission time, attachments from conclusion + `getConclusionTrace`; **omit absent fields, never fabricate**, FR-018).
- [X] T050 [US4] Build the `Timeline` pane in `frontend/src/components/approval/timeline.tsx` over `getComplianceAudit` for the entity (read-only append-only audit chain, Constitution III).
- [X] T051 [US4] Wire decision actions reusing the existing `QaSignatureDialog`/`RejectDialog` (`signConclusion` / `rejectConclusion`; reject requires a non-empty reason) into `frontend/src/components/approval/decision-actions.tsx` (re-skinned; QA-gated).
- [X] T052 [US4] Assemble the 3-pane Approval page in `frontend/src/app/(dashboard)/approval/page.tsx` (queue/detail/timeline; loading/empty/error; unauthorized → disabled decision controls) (depends on T048–T051).
- [X] T053 [US4] Add the Approval nav entry (审批 → `/approval`) with `requiredRole` gating identical to legacy `/approvals`, in `frontend/src/components/shell/nav.ts`.
- [X] T054 [US4] Supersede `/approvals`: add the redirect `/approvals` → `/approval` in `frontend/next.config.ts` and remove the legacy `/approvals` nav entry (functionality carried over, FR-005).
- [X] T055 [US4] Run the **US4** slice of [quickstart.md](quickstart.md) §B (filter → select → detail+attachments → approve or reject-with-reason → timeline updates; unauthorized disabled; decision reachable <30s, SC-005).

**Checkpoint**: Approval functional; `/approvals` superseded.

---

## Phase 7: User Story 5 — Report Center (报告中心) (Priority: P3)

**Goal**: Unified browse/manage of platform-generated reports **and** user documents by category — composed client-side over existing endpoints (no new backend, clarify Q1). See [contracts/report-center.md](contracts/report-center.md).

**Independent Test**: Browse categories → preview/download an item → upload a document → see the empty-category state.

- [X] T056 [P] [US5] Add read-only aggregation helpers in `frontend/src/lib/api.ts` composing `listDocuments` + `listReports` over `listExtractionJobs` into a category-organized unified view-model — **bounded fan-out with a visible count / "load more", NO silent truncation** (research R2).
- [X] T057 [US5] Build the `CategoryTree` in `frontend/src/components/reports/category-tree.tsx` (report types ∪ `DOC_TYPE_LABELS` ∪ `DEVELOPMENT_PHASES`) (depends on T056).
- [X] T058 [US5] Build the `ItemList` in `frontend/src/components/reports/item-list.tsx` (title, type, date, size; row actions via dropdown-menu: preview/download/delete-if-authorized) (depends on T008, T017).
- [X] T059 [US5] Build the `Upload` affordance in `frontend/src/components/reports/upload.tsx` (button + drag-and-drop over existing doc ingestion; authorized only, FR-022/023).
- [X] T060 [US5] Assemble the Report Center page in `frontend/src/app/(dashboard)/reports/page.tsx` (category tree + list + upload; defined empty-category state; role-gated delete/upload) (depends on T057–T059).
- [X] T061 [US5] Add the Report Center nav entry (报告中心 → `/reports`) in `frontend/src/components/shell/nav.ts`.
- [X] T062 [US5] Run the **US5** slice of [quickstart.md](quickstart.md) §B (browse categories → preview/download → upload → empty-category state; SC-007).

**Checkpoint**: Report Center functional.

---

## Phase 8: User Story 6 — Report Detail (报告详情) (Priority: P3)

**Goal**: Focused reading pane with outline navigation, related-info panel, breadcrumb, and download/share — reached from Report Center. See [contracts/report-center.md](contracts/report-center.md).

**Independent Test**: Open an item → reading pane renders → outline entry scrolls the pane → related-info shows linked entities → breadcrumb returns to Center.

- [X] T063 [US6] Build the `ReadingPane` in `frontend/src/components/reports/reading-pane.tsx` (document via tiptap `getAnnotatedDocument`, or generated-report preview + download; DOCX preview best-effort, file always downloadable, FR-024).
- [X] T064 [US6] Build the `Outline` navigation in `frontend/src/components/reports/outline.tsx` (tiptap headings / report sections; selecting an entry scrolls the reading pane, FR-025).
- [X] T065 [US6] Build the `RelatedInfo` panel in `frontend/src/components/reports/related-info.tsx` over `listExtractedFrom` (linked ontology entities/metadata, FR-026).
- [X] T066 [US6] Assemble the Report Detail page in `frontend/src/app/(dashboard)/reports/[reportId]/page.tsx` (reading pane + outline + related-info + breadcrumb back to `/reports` + download/share; loading/empty/error) (depends on T063–T065, T015).
- [X] T067 [US6] Run the **US6** slice of [quickstart.md](quickstart.md) §B (open item → reading pane → outline scrolls pane → related-info → breadcrumb returns).

**Checkpoint**: Report journey complete.

---

## Phase 9: Polish & Cross-Cutting Concerns

**Purpose**: Legacy supersession cutover (after all superseding destinations exist), cleanup, and full acceptance.

- [X] T068 Supersede `/integration` (after US2 Connector + US5 Report Center exist): add the redirect `/integration` → `/connector` in `frontend/next.config.ts`, remove the legacy integration nav entry, and confirm all 3 concerns are redistributed — connectors→Connector, doc-repo docs→Report Center, realtime dashboard→Overview — nothing dropped (FR-005).
- [X] T069 [P] Retire superseded page-specific components in `frontend/src/components/integration/` and `frontend/src/components/approvals/` whose logic was lifted into the new pages (dead-code cleanup; keep anything still referenced, e.g. reused dialogs).
- [X] T070 [P] Design-conformance + token review: spot-check each framed page ≥95% vs its `design.pen` frame (SC-002) and grep `frontend/src/components/` + page files for hex/rgb literals → none (tokens only, SC-004).
- [X] T071 Offline validation ([quickstart.md](quickstart.md) §C): build, disable network, load every page — all fonts (Inter + CJK) + icons render, zero CDN requests, intranet-unreachable degrades gracefully without crash (FR-028, SC-009).
- [X] T072 Full acceptance: run the complete [quickstart.md](quickstart.md) "Done when" matrix (all existing routes zero-regression × 3 roles + all 5 new pages + offline + conformance) and confirm `npm run lint` + `npm run build` green.

---

## Dependencies & Execution Order

### Phase dependencies

- **Setup (P1)** → no deps.
- **Foundational (P2)** → depends on Setup; **blocks all user stories**. Internal order: T004 → T005; T004 → {T006–T017 all [P]}; T018 → {T019 [P], T020 (needs T013)} → T021.
- **US1 (P3, P1-MVP)** → depends on Foundational. T022–T029 all [P]; then T030 → T031.
- **US2 / US3 / US4 (P2)** → each depends only on Foundational; independent of each other (can run in parallel by different developers). Within: helpers/leaf components → page assembly → nav entry → quickstart slice.
- **US5 (P3)** → depends on Foundational; independent.
- **US6 (P3)** → depends on Foundational; consumes Report Center's item model but the page itself is independently testable from a direct `/reports/[reportId]` URL.
- **Polish (P9)** → T068 depends on **US2 + US5** (both `/integration` destinations must exist before its redirect flips); T069–T072 depend on all desired stories being complete.

### Story independence

- **US1** delivers standalone value (restyled existing product). **US2–US6** each add a new page and can be delivered/tested in isolation. Legacy routes stay reachable+re-skinned until their superseding page ships (US4 flips `/approvals`; Polish flips `/integration`), so no increment loses a destination.

### Parallel opportunities

- Setup: T002, T003 [P].
- Foundational: the entire primitive delta **T006–T017 [P]** (12 distinct files) after T004; T019/T020 [P].
- US1: **T022–T029 [P]** (8 distinct route/component folders).
- Across teams: once Foundational is done, **US2, US3, US4, US5 can proceed in parallel**; US6 follows US5's item model.
- Each `[P]` helper (T032, T047, T056) is a distinct `lib/api.ts` addition — sequence these three if edited literally in the same file, else treat as parallel with care.

---

## Parallel Example

```bash
# Foundational — the shared ui primitive delta (after T004/T005):
Task: "Add dropdown-menu in frontend/src/components/ui/dropdown-menu.tsx"      # T008
Task: "Add tooltip in frontend/src/components/ui/tooltip.tsx"                   # T009
Task: "Add progress in frontend/src/components/ui/progress.tsx"                 # T010
Task: "Add data-table in frontend/src/components/ui/data-table.tsx"            # T017
# …T006, T007, T011–T016 in the same batch

# US1 — re-skin existing pages (different folders, no shared file):
Task: "Re-skin Overview in .../overview/"                                       # T022
Task: "Re-skin Ontology in .../ontology/"                                       # T023
Task: "Re-skin Entities/Extraction in .../entities/"                            # T024
# …T025–T029 in the same batch

# Post-Foundational — whole stories in parallel across developers:
Dev A: US2 Connector (T032–T039)
Dev B: US3 Data Mapping (T040–T046)
Dev C: US4 Approval (T047–T055)
```

---

## Implementation Strategy

### MVP first (Setup + Foundational + US1)

1. Phase 1 Setup → Phase 2 Foundational (design system + shell).
2. Phase 3 US1 → re-skin every existing page, zero regression.
3. **STOP & VALIDATE** with quickstart §A across all 3 roles → ship the modernized product (MVP).

### Incremental delivery

Add US2 → US3 → US4 (each a first-class P2 page, `/approvals` superseded at US4) → US5 → US6 (P3 report journey) → Polish (flip `/integration`, cleanup, full acceptance). Each story is independently testable and adds value without breaking prior ones.

---

## Notes

- **No automated tests** by design (plan.md, Constitution IV scoped) — validation is the executable [quickstart.md](quickstart.md); the per-phase quickstart tasks (T031, T039, T046, T055, T062, T067, T071, T072) are the "does it work" gates.
- `[P]` = different files, no incomplete dependency. `[Story]` maps each task to its user story for traceability.
- **Invariants to hold in every relevant task**: credentials as env-var-name references only (no plaintext); role gating (senior_analyst/operator/qa) unchanged; tokens only (no raw palette); zh-CN; offline-first (bundled fonts/icons; air-gap-from-cloud is normal, not an error); no new backend endpoint/logic (FR-027).
- Commit after each task or logical group; stop at any checkpoint to validate a story independently.
