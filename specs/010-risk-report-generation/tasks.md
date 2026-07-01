# Tasks: Risk Assessment Report Generation

**Input**: Design documents from `specs/010-risk-report-generation/`

**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/risk-report-api.md, quickstart.md

**Tests**: Included — the spec requires pytest coverage for bridge layer and generator (quickstart scenarios 4-5).

**Organization**: Tasks grouped by user story for independent implementation and testing.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (e.g., US1, US2, US3)

## Path Conventions

- **Backend**: `backend/app/` (FastAPI, SQLAlchemy 2.0)
- **Frontend**: `frontend/src/` (Next.js, React, shadcn/ui)
- **Tests**: `backend/tests/`

---

## Phase 1: Setup

**Purpose**: DB migration and rule group extension

- [x] T001 Add `"risk_assessment"` to `RULE_GROUPS` tuple in `backend/app/models/ontology_meta.py` and add `"risk_assessment"` to `DECISION_RULE_GROUPS` array and `GROUP_LABEL` map in `frontend/src/lib/api.ts` and `frontend/src/components/ontology/decision-rules-panel.tsx`
- [x] T002 Create Alembic migration for `generated_reports` table (fields: id UUID PK, job_id FK→extraction_jobs, report_type String, file_path String, file_size Integer, rules_fired_count Integer, rules_summary JSON, actor String, created_at DateTime) in `backend/alembic/versions/`
- [x] T003 Create `GeneratedReport` SQLAlchemy model in `backend/app/models/extraction.py` matching data-model.md §1.1 and add response schema `GeneratedReportResponse` to `backend/app/schemas/extraction.py`

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Bridge layer and report generator — core logic that all user stories depend on

**⚠️ CRITICAL**: No user story work can begin until this phase is complete

- [x] T004 [P] Create `edges_to_facts()` function in `backend/app/services/reasoning/fact_bridge.py` — convert relationship extraction edges list to `Facts` dataclass (map predicate_iri → relations, object_data_properties → data_values/scalars, extract drug_classes from DrugProduct edges). See research.md R4 for field mapping
- [x] T005 [P] Create `_apply_postconditions()` function in `backend/app/services/reasoning/fact_bridge.py` — shallow-copy Facts and inject postcondition keys into scalars for post-control evaluation. See research.md R5
- [x] T006 Create `RiskReport`, `RiskRow`, `EquipmentEntry` dataclasses in `backend/app/services/reporting/risk_report_generator.py` matching data-model.md §1.2-1.4
- [x] T007 Implement `RiskReportGenerator.generate()` in `backend/app/services/reporting/risk_report_generator.py` — orchestrate edges→Facts bridging, load risk_assessment DecisionRules from DB, evaluate pre/post control levels per HazID dimension, build subject description from DrugProduct edges, build equipment tables grouped by workshop from Equipment edges. See contracts/risk-report-api.md §1 for expected output
- [x] T008 Implement `render_risk_report()` in `backend/app/services/reporting/docx_renderer.py` — accept RiskReport dataclass, produce .docx bytes via python-docx with QS-A-020F05 format (header, SECTION I with equipment tables, Assessment table with bilingual headers, SECTION II placeholders). See spec.md FR-004/FR-005

**Checkpoint**: Bridge layer + generator + renderer are unit-testable

---

## Phase 3: User Story 1 — Generate Risk Assessment Report (Priority: P1) 🎯 MVP

**Goal**: User clicks button in ExtractionDrawer → backend generates report → .docx downloads

**Independent Test**: Upload CMC document, complete extraction, click "生成风险评估报告", receive valid .docx with equipment tables and assessment rows (quickstart scenario 1)

### Tests for User Story 1

- [x] T009 [P] [US1] Create unit tests for `edges_to_facts()` in `backend/tests/test_reporting/test_fact_bridge.py` — test equipment edge mapping, PDE extraction to scalars, drug class extraction, empty edges handling (quickstart scenario 4)
- [x] T010 [P] [US1] Create unit tests for `RiskReportGenerator.generate()` in `backend/tests/test_reporting/test_risk_report_generator.py` — test pre-control level evaluation (SharedLineAssessment → HighRisk), post-control level with postconditions (→ LowRisk), equipment grouping by workshop, subject description assembly (quickstart scenario 5)

### Implementation for User Story 1

- [x] T011 [US1] Implement POST `/api/extraction/jobs/{job_id}/risk-report` endpoint in `backend/app/api/extraction.py` — load job from DB, validate doc_class is CMCReport (422 if not), validate relationships exist (422 if empty), call RiskReportGenerator.generate(), call render_risk_report(), persist .docx to `data/reports/{job_id}_{timestamp}.docx`, insert GeneratedReport row, call audit.append() with action="report.generate", return FileResponse. See contracts/risk-report-api.md §1
- [x] T012 [US1] Implement GET `/api/extraction/jobs/{job_id}/risk-report` endpoint in `backend/app/api/extraction.py` — query latest GeneratedReport for job_id, return persisted .docx file as FileResponse (404 if no report exists). See contracts/risk-report-api.md §2
- [x] T013 [US1] Ensure `data/reports/` directory exists (create in app startup or endpoint) and add to `.gitignore` if not already excluded

**Checkpoint**: API endpoints work end-to-end via curl (quickstart scenario 3)

---

## Phase 4: User Story 2 — Conditional Button Visibility (Priority: P1)

**Goal**: "生成风险评估报告" button appears in ExtractionDrawer only when doc is CMCReport with relationships

**Independent Test**: Open ExtractionDrawer for documents at different stages and verify button shows/hides correctly (quickstart scenario 2)

### Implementation for User Story 2

- [x] T014 [US2] Add `generateRiskReport(jobId: string)` function to `frontend/src/lib/api.ts` — POST to `/api/extraction/jobs/${jobId}/risk-report` with identity headers, return blob response for download
- [x] T015 [US2] Add report generation button and handler to `frontend/src/components/extraction/extraction-drawer.tsx` — add `reportGenerating` state, add "生成风险评估报告" button in status bar (after "重新标注" button), visible only when `doc.doc_class?.doc_class_iri` includes "CMCReport" AND `doc.relationships?.length > 0` AND `!rerunning`, disabled while generating, shows "生成中..." during generation. See contracts/risk-report-api.md §3
- [x] T016 [US2] Implement `handleGenerateReport()` download handler in `frontend/src/components/extraction/extraction-drawer.tsx` — call generateRiskReport(), create blob URL, trigger download with filename `风险评估表_{doc.filename without .docx}.docx`, revoke URL, handle errors. See spec.md US4 acceptance scenarios

**Checkpoint**: Full UI flow works — button appears for CMCReport, downloads .docx (quickstart scenarios 1-2)

---

## Phase 5: User Story 3 — Risk Rule Evaluation with Pre/Post Control Levels (Priority: P1)

**Goal**: Assessment table correctly reflects deterministic pre/post risk levels from DecisionRule evaluation

**Independent Test**: Provide known edges to bridge layer, verify evaluate() produces correct levels per rule (quickstart scenario 5)

> Note: The core implementation is in Phase 2 (T004-T007). This phase adds the risk assessment rule seed data needed for end-to-end validation.

### Implementation for User Story 3

- [x] T017 [US3] Create seed risk assessment DecisionRules (5 rules for 人员/生产设备/物料管理/文件/三废处理 HazID dimensions) as a seed function in `backend/app/services/reasoning/defaults.py` — each rule uses rule_group="risk_assessment", antecedent patterns from spec.md §5 (adapted to use existing interpreter vocabulary: class_membership, some_values_from, datatype_facet, literal_eq), consequent dict with risk_level/category/description/control_measure/traceability_docs/postconditions per research.md R1
- [x] T018 [US3] Wire seed function into `backend/app/services/reasoning/seed_declarative.py` to auto-seed risk_assessment rules on startup (following the existing pattern for equipment_dedication/scenario_identification/contamination_risk rules)

**Checkpoint**: Rules seeded, evaluate() produces correct pre/post levels for HRS-1234 test case

---

## Phase 6: User Story 4 — Report Download as .docx (Priority: P2)

**Goal**: Downloaded file has correct name, opens in Word, bilingual headers preserved

**Independent Test**: Click button, verify filename matches source document, open in Word (quickstart scenario 1 step 6)

> Note: Download mechanics implemented in Phase 4 (T016). This phase focuses on .docx quality.

### Implementation for User Story 4

- [x] T019 [US4] Refine `render_risk_report()` in `backend/app/services/reporting/docx_renderer.py` — ensure Table Grid style, bilingual column headers (e.g., "HazID\n风险类型"), page header with doc_no/revision/date, proper cell widths for assessment table columns, consistent font sizing

**Checkpoint**: .docx opens cleanly in Word/LibreOffice with correct formatting

---

## Phase 7: User Story 5 — Error Handling for Incomplete Data (Priority: P2)

**Goal**: Clear error messages when prerequisites are not met

**Independent Test**: Call API directly with missing doc_class or empty relationships (quickstart scenario 3 error cases)

> Note: Error validation is part of T011 endpoint implementation. This phase adds explicit test coverage.

### Implementation for User Story 5

- [x] T020 [US5] Add error handling tests to `backend/tests/test_reporting/test_risk_report_generator.py` — test 422 for missing doc_class, 422 for non-CMCReport type, 422 for zero relationships, UNKNOWN evaluation maps to "低" risk level (spec edge case 1)

**Checkpoint**: All error cases return correct HTTP status and Chinese error messages

---

## Phase 8: Polish & Cross-Cutting Concerns

**Purpose**: Audit verification, edge cases, and documentation

- [x] T021 [P] Verify audit chain integrity after report generation — run `audit.verify()` and confirm report.generate entries are present with correct details (quickstart scenario 6)
- [x] T022 [P] Handle edge case: equipment edges without workshop grouping in `backend/app/services/reporting/risk_report_generator.py` — group into single ungrouped table with note (spec edge case 2)
- [x] T023 Run full quickstart.md validation — execute all 6 scenarios end-to-end and verify pass criteria

---

## Dependencies & Execution Order

### Phase Dependencies

- **Phase 1 (Setup)**: No dependencies — start immediately
- **Phase 2 (Foundational)**: Depends on Phase 1 (T001-T003 for model/migration)
- **Phase 3 (US1)**: Depends on Phase 2 (bridge + generator + renderer)
- **Phase 4 (US2)**: Depends on Phase 3 (API endpoints must exist for frontend to call)
- **Phase 5 (US3)**: Depends on Phase 2 (rule evaluation infra); can parallel with Phase 3-4
- **Phase 6 (US4)**: Depends on Phase 2 (renderer); can parallel with Phase 3-5
- **Phase 7 (US5)**: Depends on Phase 3 (endpoint exists for error testing)
- **Phase 8 (Polish)**: Depends on all prior phases

### User Story Dependencies

- **US1 (P1)**: Core flow — no dependencies on other stories
- **US2 (P1)**: Frontend depends on US1 API endpoint existing
- **US3 (P1)**: Rule seeding independent of US1/US2; can parallel after Phase 2
- **US4 (P2)**: .docx polish independent; can parallel after Phase 2
- **US5 (P2)**: Error tests depend on US1 endpoint

### Parallel Opportunities

Within Phase 2: T004 and T005 can run in parallel (different functions, same file)
Within Phase 3: T009 and T010 can run in parallel (different test files)
Phase 5 (US3) can run in parallel with Phase 3 (US1) after Phase 2 completes
Phase 6 (US4) can run in parallel with Phase 3-5 after Phase 2 completes

---

## Parallel Example: Foundational Phase

```text
# These can run simultaneously (different files):
T004: Create edges_to_facts() in backend/app/services/reasoning/fact_bridge.py
T005: Create _apply_postconditions() in backend/app/services/reasoning/fact_bridge.py

# After T004+T005 complete:
T006: Create dataclasses in backend/app/services/reporting/risk_report_generator.py
T007: Implement generate() — depends on T004, T005, T006
T008: Implement render_risk_report() — depends on T006
```

---

## Implementation Strategy

### MVP First (User Story 1 + 2 Only)

1. Complete Phase 1: Setup (T001-T003)
2. Complete Phase 2: Foundational (T004-T008)
3. Complete Phase 3: US1 API endpoints (T009-T013)
4. Complete Phase 4: US2 Frontend button (T014-T016)
5. **STOP and VALIDATE**: Full UI flow works end-to-end
6. Seed rules (Phase 5) for real-data validation

### Incremental Delivery

1. Setup + Foundational → Core logic ready
2. Add US1 → API testable via curl → Deploy/Demo
3. Add US2 → UI button + download → Deploy/Demo (MVP!)
4. Add US3 → Real rules seeded → Full assessment accuracy
5. Add US4 → .docx formatting polished
6. Add US5 → Error handling hardened
7. Polish → Audit verification + edge cases

---

## Notes

- [P] tasks = different files, no dependencies
- [Story] label maps task to specific user story for traceability
- Risk assessment rules are **data configuration** seeded via defaults.py, not hardcoded logic
- The existing `evaluate()` engine and `Facts` dataclass are reused without modification
- All .docx rendering uses python-docx (already a project dependency)
- Audit logging uses existing `audit.append()` hash-chain mechanism
- Commit after each task or logical group

---

## Phase 9: Convergence

**Purpose**: Close gaps between spec/plan intent and current implementation

- [ ] T024 Propagate edge `source_ref` values into the traceability field of `RiskRow` in `backend/app/services/reporting/risk_report_generator.py` — collect source_refs from edges that contributed to each HazID dimension and append them to `traceability_docs` so the report traces back to source document sections per FR-009 (partial)
- [ ] T025 [P] Add test for waste treatment (三废处理) rule evaluation with non-high-activity compound (PDE > 10μg) in `backend/tests/test_reporting/test_risk_report_generator.py` — verify pre-control = "低" and status = "可以接受" per US3/AC3 (partial)
