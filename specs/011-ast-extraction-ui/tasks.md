# Tasks: AST 提取前端 UI

**Input**: Design documents from `specs/011-ast-extraction-ui/`

**Prerequisites**: plan.md (required), spec.md (required), research.md, data-model.md, contracts/, quickstart.md

**Tests**: Test tasks included for backend endpoints (pytest). Frontend validated via quickstart.md E2E scenarios.

**Organization**: Tasks grouped by user story. US1-US4 are P1 (core); US5-US6 are P2 (management/dismissal).

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (e.g., US1, US2, US3)
- Include exact file paths in descriptions

---

## Phase 1: Setup

**Purpose**: DB migration, new model, extended coverage validator, Pydantic schemas

- [ ] T001 Add `SlotDismissal` model to `backend/app/models/extraction.py` — UUID PK, job_id FK, slot_id VARCHAR(200), dismissed_by VARCHAR(100), dismissed_at TIMESTAMP WITH TZ, unique constraint on (job_id, slot_id)
- [ ] T002 Create Alembic migration `backend/alembic/versions/0008_add_slot_dismissals.py` for `slot_dismissals` table with index on job_id and unique constraint
- [ ] T003 [P] Add `DISMISSED = "dismissed"` constant and `dismissed` counter property to `CoverageManifest` in `backend/app/services/reporting/coverage_validator.py`
- [ ] T004 [P] Add Pydantic response schemas (`SlotCoverageResponse`, `GroupCoverageResponse`, `SectionCoverageResponse`, `ASTCoverageResponse`, `SlotDismissRequest`, `SlotDismissalResponse`) to `backend/app/schemas/extraction.py` per data-model.md §3

---

## Phase 2: Foundational (Backend Endpoints)

**Purpose**: Backend API endpoints that all frontend stories depend on

**⚠️ CRITICAL**: No frontend work can begin until these endpoints are functional

- [ ] T005 Extend `validate_coverage()` in `backend/app/services/reporting/coverage_validator.py` to accept optional `dismissed_slot_ids: set[str]` parameter — when a slot resolves to `missing_required` and its slot_id (or base slot_id for assessment instance keys) is in the dismissed set, emit `status="dismissed"` instead
- [ ] T006 Implement `GET /api/extraction/jobs/{job_id}/ast-coverage` endpoint in `backend/app/api/extraction.py` — load annotation cache, validate CMCReport + edges, query `SlotDismissal` rows for job, call `validate_coverage()` with dismissed set, build nested `ASTCoverageResponse` (sections → groups → slots with coverage merged) from `ReportTemplate` structure + `CoverageManifest`
- [ ] T007 Implement `GET /api/extraction/jobs/{job_id}/reports` endpoint in `backend/app/api/extraction.py` — query `GeneratedReport` for job_id ordered by created_at desc, return list of `GeneratedReportResponse`
- [ ] T008 Implement `POST /api/extraction/jobs/{job_id}/ast-coverage/dismiss` endpoint in `backend/app/api/extraction.py` — validate job exists + CMCReport, check no duplicate dismissal (409), create `SlotDismissal` row, `audit.append()` action=`slot.dismiss`, recompute and return updated `ASTCoverageResponse`
- [ ] T009 Implement `DELETE /api/extraction/jobs/{job_id}/ast-coverage/dismiss/{slot_id}` endpoint in `backend/app/api/extraction.py` — find and delete `SlotDismissal` row (404 if not found), `audit.append()` action=`slot.undismiss`, recompute and return updated `ASTCoverageResponse`
- [ ] T010 [P] Write pytest tests for coverage, dismiss, undismiss, and reports endpoints in `backend/tests/test_reporting/test_ast_coverage_api.py` — test dismissed slot status flip, audit log entries, 409 duplicate, 404 not-found, coverage count correctness
- [ ] T011 [P] Add frontend API functions to `frontend/src/lib/api.ts` — `getAstCoverage(jobId)`, `listReports(jobId)`, `dismissSlot(jobId, slotId)`, `undismissSlot(jobId, slotId)`, `downloadReport(reportId)` using existing fetch pattern

**Checkpoint**: All 4 backend endpoints functional and tested; frontend API client ready

---

## Phase 3: User Story 1 — 文档导入与 AST 自动提取 (Priority: P1) 🎯 MVP

**Goal**: After extraction completes for a CMCReport, user sees "查看 AST" entry and navigates to a dedicated AST page showing the template tree and coverage.

**Independent Test**: Upload CMC doc → extraction done → "查看 AST" link appears → click → AST page shows tree with color-coded slots.

### Implementation for User Story 1

- [ ] T012 [US1] Create AST page route at `frontend/src/app/(dashboard)/entities/extraction/[jobId]/ast/page.tsx` — fetch coverage via `getAstCoverage(jobId)`, fetch annotated document for WordViewer, render layout with three zones (coverage summary top, AST tree left, document preview right)
- [ ] T013 [P] [US1] Create `ASTTreeView` component in `frontend/src/components/extraction/ast-tree-view.tsx` — render Section → Group → Slot tree using shadcn Collapsible/Accordion, default collapsed to Group level (FR-UI-012), 6-color status badges (filled=green, inferred=blue, missing_required=red, blank_optional=gray, manual=yellow, dismissed=gray+strikethrough), click slot emits `onSelectSlot` callback
- [ ] T014 [P] [US1] Create `CoverageSummaryCard` component in `frontend/src/components/extraction/coverage-summary-card.tsx` — display total_slots, each status count, progress bar (filled+inferred / total), warning alert when missing_required > 0, green "素材完备" when missing_required = 0
- [ ] T015 [US1] Add "查看 AST" link in job table at `frontend/src/app/(dashboard)/entities/extraction/page.tsx` — conditional on `job.status === "done"`, check annotation cache `doc_class_iri` contains "CMCReport" via a lightweight check (reuse existing `getAnnotatedDocument` or add a head-check), render as Next.js `<Link>` to `/entities/extraction/{jobId}/ast`

**Checkpoint**: User can navigate from job list to AST page; tree and coverage dashboard render correctly

---

## Phase 4: User Story 2 — AST 覆盖率仪表盘 (Priority: P1)

**Goal**: Coverage summary card accurately reflects backend data, missing slots trigger warnings, and user can navigate to missing slots.

**Independent Test**: Load AST page → verify summary counts match API → click "查看缺失详情" → page scrolls to missing slot.

### Implementation for User Story 2

- [ ] T016 [US2] Add "查看缺失详情" action to `CoverageSummaryCard` in `frontend/src/components/extraction/coverage-summary-card.tsx` — click scrolls/highlights the first `missing_required` slot in the tree (emit callback to `ASTTreeView` to expand parent group and scroll slot into view)
- [ ] T017 [US2] Wire coverage data refresh in AST page `frontend/src/app/(dashboard)/entities/extraction/[jobId]/ast/page.tsx` — use React Query `useQuery` with key `['ast-coverage', jobId]` so mutations (dismiss, generate) can invalidate and auto-refresh

**Checkpoint**: Dashboard shows accurate counts; clicking missing details navigates within tree

---

## Phase 5: User Story 3 — 槽位详情与源文档定位 (Priority: P1)

**Goal**: Clicking a slot shows its source details; clicking source_ref jumps to the document preview.

**Independent Test**: Click filled slot → detail panel shows value + source_ref → click source_ref → WordViewer highlights section.

### Implementation for User Story 3

- [ ] T018 [US3] Create `SlotDetailPanel` component in `frontend/src/components/extraction/slot-detail-panel.tsx` — receives selected `SlotCoverageResponse`, renders slot_id, label, status badge, source_kind, value (for extraction), source_ref as clickable link, rule_key + hazid (for rule slots), "预留手工填写" label (for manual slots), missing cause + suggestion (for missing_required)
- [ ] T019 [US3] Integrate `SlotDetailPanel` and `WordViewer` highlightRef in AST page `frontend/src/app/(dashboard)/entities/extraction/[jobId]/ast/page.tsx` — when user clicks source_ref link in SlotDetailPanel, set `highlightRef` state → WordViewer scrolls to and highlights the referenced section; manage `selectedSlot` state for panel display

**Checkpoint**: Full slot-to-source traceability working in the UI

---

## Phase 6: User Story 4 — 覆盖预检后生成报告 (Priority: P1)

**Goal**: "生成报告" button with coverage pre-check dialog; generation triggers download + dashboard refresh.

**Independent Test**: With missing slots → click "生成报告" → AlertDialog lists missing → "仍然生成" → .docx downloads → history refreshes.

### Implementation for User Story 4

- [ ] T020 [US4] Add "生成报告" button and pre-check AlertDialog to AST page `frontend/src/app/(dashboard)/entities/extraction/[jobId]/ast/page.tsx` — when `missing_required > 0` show AlertDialog listing missing slot IDs/labels with "仍然生成" and "取消"; when `missing_required = 0` generate directly; button shows "生成中..." during API call; on success trigger .docx blob download (reuse `generateRiskReport` from api.ts), invalidate `['ast-coverage', jobId]` and `['reports', jobId]` React Query keys

**Checkpoint**: Report generation flow with informed consent working end-to-end

---

## Phase 7: User Story 5 — 历史报告管理 (Priority: P2)

**Goal**: Historical reports list with download and coverage snapshot viewing.

**Independent Test**: Generate 2+ reports → history section shows all → download works → coverage snapshot expandable.

### Implementation for User Story 5

- [ ] T021 [US5] Create `ReportHistoryList` component in `frontend/src/components/extraction/report-history-list.tsx` — fetch via `useQuery(['reports', jobId], () => listReports(jobId))`, render table with columns: created_at (formatted to second), actor, rules_fired_count, coverage summary (filled/missing_required from rules_summary.coverage); "下载" button triggers `downloadReport()`; "查看覆盖" button toggles expandable row showing full coverage slot list from `rules_summary.coverage.slots`
- [ ] T022 [US5] Integrate `ReportHistoryList` into AST page `frontend/src/app/(dashboard)/entities/extraction/[jobId]/ast/page.tsx` — add as a section below the AST tree/detail area

**Checkpoint**: Historical reports fully browsable and downloadable

---

## Phase 8: User Story 6 — 缺失素材补充与标记 (Priority: P2)

**Goal**: Missing slot action bar with dismiss/undismiss; dismissed state persists and auto-refreshes coverage.

**Independent Test**: Find missing slot → "标记为不适用" → slot turns gray+strikethrough → count updates → refresh → state persists → "撤销标记" → restores.

### Implementation for User Story 6

- [ ] T023 [US6] Create `SlotActionBar` component in `frontend/src/components/extraction/slot-action-bar.tsx` — extensible props/slot pattern (FR-UI-009); for `missing_required` slots: show "标记为不适用" button (calls `dismissSlot` mutation) and "重新标注" suggestion text; for `dismissed` slots: show "撤销标记" button (calls `undismissSlot` mutation); mutations invalidate `['ast-coverage', jobId]` for auto-refresh; component accepts optional `extraActions` prop for future extensibility (internal seam, not exposed to user)
- [ ] T024 [US6] Integrate `SlotActionBar` into `SlotDetailPanel` in `frontend/src/components/extraction/slot-detail-panel.tsx` — render action bar below slot details for `missing_required` and `dismissed` status slots
- [ ] T025 [US6] Extend `.docx` renderer in `backend/app/services/reporting/docx_renderer.py` to handle `dismissed` status — render cell value as "N/A（不适用）" instead of "⚠ 待评估（数据缺失）" when slot status is `dismissed`

**Checkpoint**: Full dismiss/undismiss lifecycle working; persisted across refresh; report renders N/A correctly

---

## Phase 9: Polish & Cross-Cutting Concerns

**Purpose**: Edge cases, offline verification, and integration testing

- [ ] T026 [P] Handle non-CMCReport direct URL access in AST page `frontend/src/app/(dashboard)/entities/extraction/[jobId]/ast/page.tsx` — if coverage API returns 422, show error message "该文档类型不支持风险评估" instead of broken UI
- [ ] T027 [P] Handle extraction-failed jobs in AST page — if job status is not `done` or annotation cache missing, show appropriate message
- [ ] T028 [P] Add loading skeleton states to AST page — while coverage API is in flight, show Skeleton placeholders for tree and summary card
- [ ] T029 Run quickstart.md validation scenarios 1-7 and verify all pass

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies — start immediately
- **Foundational (Phase 2)**: Depends on Phase 1 (T001-T004 complete)
- **User Stories (Phase 3-8)**: All depend on Phase 2 completion (endpoints + API client ready)
  - US1 (Phase 3): No story dependencies — MVP entry point
  - US2 (Phase 4): Extends US1's `CoverageSummaryCard` — best after US1
  - US3 (Phase 5): Extends US1's AST page — best after US1
  - US4 (Phase 6): Uses US1's page + US2's dashboard — best after US1+US2
  - US5 (Phase 7): Independent component, wired into US1's page — can parallel US2-US4
  - US6 (Phase 8): Extends US3's `SlotDetailPanel` — best after US3; also needs backend T025
- **Polish (Phase 9)**: After all desired stories complete

### User Story Dependencies

```
Phase 1 (Setup) → Phase 2 (Foundational)
                       │
                       ├─→ US1 (tree + page) ──→ US2 (dashboard details)
                       │         │                       │
                       │         ├─→ US3 (slot detail) ──┼─→ US4 (generate)
                       │         │         │              │
                       │         │         └─→ US6 (dismiss)
                       │         │
                       │         └─→ US5 (history) [independent]
                       │
                       └─→ Phase 9 (Polish)
```

### Within Each User Story

- Backend service logic before API endpoints
- API endpoints before frontend components
- Parent components before child integrations
- State management (React Query) wired at page level

### Parallel Opportunities

- **Phase 1**: T003 and T004 are parallel (different files)
- **Phase 2**: T010 and T011 are parallel with each other and with T006-T009 (test file vs. endpoint file)
- **Phase 3**: T013 and T014 are parallel (different component files)
- **Phase 7-8**: US5 and US6 can run in parallel (different component files, different endpoints)
- **Phase 9**: T026, T027, T028 are all parallel (different concerns in same file but independent blocks)

---

## Parallel Example: Phase 1 Setup

```
# Launch in parallel:
Task T003: "Add DISMISSED constant to coverage_validator.py"
Task T004: "Add Pydantic schemas to schemas/extraction.py"
```

## Parallel Example: User Story 1

```
# Launch in parallel:
Task T013: "Create ASTTreeView component"
Task T014: "Create CoverageSummaryCard component"
# Then sequentially:
Task T012: "Create AST page (depends on T013, T014)"
Task T015: "Add 查看 AST link to job table"
```

---

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Complete Phase 1: Setup (T001-T004)
2. Complete Phase 2: Foundational (T005-T011) — all endpoints + API client
3. Complete Phase 3: User Story 1 (T012-T015) — AST page with tree + coverage
4. **STOP and VALIDATE**: Test US1 independently per quickstart.md Scenario 1
5. Deploy/demo if ready — users can already see AST coverage

### Incremental Delivery

1. Setup + Foundational → Backend ready
2. US1 (AST tree + coverage dashboard) → First visible value
3. US2 (dashboard details) + US3 (slot detail + source jump) → Traceability
4. US4 (pre-check generate) → Informed report generation
5. US5 (history) + US6 (dismissal) → Full management capability
6. Polish → Edge cases and validation

### Single Developer Strategy

Follow phases sequentially: 1 → 2 → 3 → 4 → 5 → 6 → 7 → 8 → 9. Each phase is a natural commit point.

---

## Notes

- [P] tasks = different files, no dependencies
- [Story] label maps task to specific user story for traceability
- Backend env: `uv run pytest`, `uv run alembic upgrade head`
- Frontend env: `npm run dev` in `frontend/`
- All UI components use shadcn/ui — no external CDN/fonts (Constitution VI)
- Dismiss/undismiss API returns updated `ASTCoverageResponse` — frontend refreshes from response, no second request needed
- `SlotActionBar` uses props/slot pattern for extensibility — LLM seam is internal only (spec clarification)
