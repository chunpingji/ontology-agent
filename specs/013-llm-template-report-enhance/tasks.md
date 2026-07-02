# Tasks: LLM Template Design Assist + Report Enhancement

**Input**: Design documents from `specs/013-llm-template-report-enhance/`

**Prerequisites**: plan.md (required), spec.md (required), research.md, data-model.md, contracts/, quickstart.md

**Organization**: Tasks are grouped by user story to enable independent implementation and testing of each story.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (e.g., US1, US2, US3, US4)
- Include exact file paths in descriptions

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Config flags, shared LLM helper, data model extensions, and migration

- [X] T001 [P] Add config flags (`llm_suggest_slots_enabled`, `llm_report_merge_values`, `llm_report_narrative_enabled`, `suggest_slots_timeout_s`, `suggest_slots_max`) to `backend/app/config.py` Settings class — all default off/bounded per data-model §5
- [X] T002 [P] Add `chat_with_schema()` helper to `backend/app/services/llm/local_client.py` — accepts client/system/user/schema, attempts `response_format=json_schema`, retries with prompt-based JSON fallback on API rejection or malformed content (mirrors `_build_response_format`/`_parse_llm_response` from `llm_gap_filler.py`), per research R2
- [X] T003 [P] Add Pydantic DTOs (`SuggestSlotsRequest`, `SuggestedSlot`, `SuggestSlotsResponse`) to `backend/app/schemas/extraction.py` — fields per data-model §1; validation: exactly one of `job_id`/`document_text` required
- [X] T004 [P] Extend `GeneratedReport` model in `backend/app/models/extraction.py` — add nullable `status` (String(20)) and `error_message` (Text) columns per data-model §3
- [X] T005 Create Alembic migration for `generated_reports.status` + `generated_reports.error_message` columns — existing rows default `status=NULL` (treated as `completed` by application logic); run `uv run alembic revision --autogenerate` then verify

**Checkpoint**: Config flags, shared LLM helper, DTOs, and migration ready — user story implementation can now begin

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Core data-structure extensions that all user stories depend on

**⚠️ CRITICAL**: No user story work can begin until this phase is complete

- [X] T006 [P] Extend `RiskReport` dataclass in `backend/app/services/reporting/risk_report_generator.py` — add `llm_supplements: dict[str, str] = field(default_factory=dict)` and `llm_generated_fields: set[str] = field(default_factory=set)` per data-model §2; zero impact when empty (SC-005)
- [X] T007 [P] Add LLM annotation infrastructure to `backend/app/services/reporting/docx_renderer.py` — define `_LLM_COLOR` (gray), `_LLM_INFO_GLYPH` (ⓘ), helper `_add_llm_run()` for gray-italic+ⓘ styling, and `_add_generated_disclaimer_section()` for end-of-report disclaimer block; no call sites yet (wired in US2/US3)

**Checkpoint**: Foundation ready — user story implementation can now begin in parallel

---

## Phase 3: User Story 1 — AI-Suggested Slots from Example Document (Priority: P1) 🎯 MVP

**Goal**: Senior analyst uploads a document → LLM analyzes structure → returns confidence-scored slot suggestions with evidence spans → analyst adopts selected slots into the template

**Independent Test**: Upload a sample GMP document via `POST /api/ast-templates/suggest-slots`, verify grouped suggestions with confidence + evidence spans returned; run `uv run pytest backend/tests/test_extraction/test_slot_suggester.py`

### Implementation for User Story 1

- [X] T008 [US1] Create `backend/app/services/extraction/slot_suggester.py` — implement `suggest_slots(document_text, existing_template, max_suggestions, timeout_s)` with two-round LLM flow: round-1 structure analysis prompt → sections/groups/candidate labels/evidence spans; round-2 slot-mapping prompt receiving round-1 outline + existing template → concrete `SuggestedSlot` dicts, instructed to omit semantically covered slots (dedup per FR-003); use `chat_with_schema()` from T002; return grouped sections + `skipped_duplicates` count; enforce `suggest_slots_timeout_s` bound (FR-001); cap at `max_suggestions` (FR-004)
- [X] T009 [US1] Add ontology IRI binding to `backend/app/services/extraction/slot_suggester.py` — for each round-2 candidate, query `OntologyEngine` (inject via parameter) using `data_property_labels()`, `get_data_properties_by_domain()`, `search_one()` to match label/text to class or data-property; match → `source_kind=extraction` + `source_hint=IRI`; no match → `source_kind=llm_extraction`/`manual` (FR-002, research R3)
- [X] T010 [US1] Add `POST /api/ast-templates/suggest-slots` endpoint to `backend/app/api/ast_templates.py` — role-gated `_maintainer` (senior_analyst, FR-010); guard on `settings.llm_suggest_slots_enabled` → 503 when off; resolve input text from `job_id` (query `ExtractionJob.document_path`, read via `parse_docx_structure`) or `document_text`; call `suggest_slots()`; return `SuggestSlotsResponse`; empty/whitespace → 200 with empty sections + explanatory summary; LLM unavailable/timeout → 503 retriable (FR-001)
- [X] T011 [US1] Write tests in `backend/tests/test_extraction/test_slot_suggester.py` — mock LLM client; test two-round flow produces grouped suggestions; ontology match → `source_kind=extraction`+IRI; no match → `llm_extraction`; existing_template covering a candidate → counted in `skipped_duplicates`, absent from sections; cap at `max_suggestions` → `truncated=true`; empty doc → empty sections; flag off → 503; non-senior role → 403
- [X] T012 [P] [US1] Add `suggestSlots()` API function to `frontend/src/lib/api.ts` — `POST /api/ast-templates/suggest-slots` with `SuggestSlotsRequest`, returns typed `SuggestSlotsResponse`
- [X] T013 [US1] Create `frontend/src/components/extraction/slot-suggestion-drawer.tsx` — left panel: document text with scrollable content and highlight-on-click support (FR-012); right panel: slot suggestion tree grouped by section/group with confidence badges and checkboxes; clicking a slot in the right panel scrolls the left panel to `evidence_offset` and highlights `evidence_span`; "采纳所选" button emits selected slots to parent; loading skeleton while request in flight; re-upload confirmation dialog (FR-013)
- [X] T014 [US1] Extend `frontend/src/components/extraction/template-slot-editor.tsx` — add "AI 分析" button (visible when `llm_suggest_slots_enabled` — surface via a config API or feature flag endpoint); on click, open `SlotSuggestionDrawer` in a Sheet/Drawer; on adopt, merge selected `SuggestedSlot`s into the template's section/group/slot structure with a "new" visual highlight; update slot count

**Checkpoint**: User Story 1 fully functional — analyst can upload a doc, see AI suggestions, and adopt slots into the template

---

## Phase 4: User Story 2 — LLM Gap-Filled Values Flow into Report (Priority: P2)

**Goal**: When LLM gap-filling is enabled, filled values replace "待评估" in the generated DOCX report with gray-italic+ⓘ annotation; report generation runs as an async background job

**Independent Test**: Generate a report on a job with missing-required slots while `llm_report_merge_values=True`; verify DOCX contains gap-filled values with LLM annotation, not "待评估"; run `uv run pytest backend/tests/test_reporting/test_report_llm_merge.py backend/tests/test_reporting/test_docx_llm_annotation.py`

### Implementation for User Story 2

- [X] T015 [US2] Implement LLM value merge in `backend/app/services/reporting/risk_report_generator.py` — in `generate_with_coverage()`, when `settings.llm_report_merge_values` and `get_local_llm()` returns a client and manifest has missing_required_slots: call `fill_coverage_gaps(manifest, document_path, template)` → synthetic edges with `source="llm"`; populate `report.llm_supplements[slot_id] = value` for each filled slot; flip corresponding `SlotCoverage.is_llm_sourced=True`; MUST NOT touch `_evaluate_rules` / `_evaluate_post_control` / risk levels / G1 logic (FR-009)
- [X] T016 [US2] Wire LLM annotation rendering in `backend/app/services/reporting/docx_renderer.py` — in `_add_section_one` and value-rendering paths, check if slot_id is in `report.llm_supplements`: if so, render the value using `_add_llm_run()` (gray-italic+ⓘ from T007); at report end call `_add_generated_disclaimer_section()` if `report.llm_supplements` is non-empty (FR-006)
- [X] T017 [US2] Implement async report-generation path in `backend/app/api/extraction.py` — when any LLM report flag is on: create `GeneratedReport` row with `status="pending"`, return `202 + {report_id, status}`; enqueue via `BackgroundTasks` → set `running`, run `generate_with_coverage()` + merge + render, set `completed` + `file_path`/`file_size` (or `failed` + `error_message`); add `GET .../reports/{report_id}` status poll endpoint; add `GET .../reports/{report_id}/download` endpoint (409 if not completed); when all flags off → existing synchronous path, row created as `completed` immediately (SC-005, FR-005a)
- [X] T018 [P] [US2] Write tests in `backend/tests/test_reporting/test_report_llm_merge.py` — mock LLM; gap-fill values land in `llm_supplements`; manifest `is_llm_sourced` flipped; flags off → output identical to 012 baseline (SC-005); deterministic fields unchanged (FR-009)
- [X] T019 [P] [US2] Write tests in `backend/tests/test_reporting/test_docx_llm_annotation.py` — supplemented values render gray-italic+ⓘ; narrative disclaimer present; end-of-report disclaimer section present; 100% of LLM content visually annotated (SC-004); no LLM styling when supplements empty
- [X] T020 [P] [US2] Add async report API functions to `frontend/src/lib/api.ts` — `startReportGeneration()` → POST returns `{report_id, status}`; `pollReportStatus(report_id)` → GET returns `{status, error_message, ...}`; `downloadReport(report_id)` → GET returns DOCX blob; type `GeneratedReportDTO` extended with `status` + `error_message`
- [X] T021 [US2] Extend `frontend/src/components/extraction/report-generate-button.tsx` — detect if any LLM report flag is enabled (via config or response); if so: click → `startReportGeneration()`, show progress indicator, poll `pollReportStatus()` with React Query `refetchInterval`, on `completed` → auto-download or show download button, on `failed` → show error toast with retry; if flags off → existing synchronous flow unchanged

**Checkpoint**: User Story 2 fully functional — gap-filled values appear in DOCX with LLM annotation; async generation works; flags off = no change

---

## Phase 5: User Story 3 — Style-Consistent Narrative Content Generation (Priority: P3)

**Goal**: When narrative generation is enabled, the system generates formal-GMP prose for subject description, per-dimension risk narratives, and conclusion using extracted facts + template prose as few-shot examples; each section carries a review disclaimer

**Independent Test**: Generate a report with `llm_report_narrative_enabled=True` and rich extracted facts; verify DOCX narratives are coherent, fact-grounded, and annotated; run `uv run pytest backend/tests/test_reporting/test_narrative_generator.py`

### Implementation for User Story 3

- [X] T022 [US3] Create `backend/app/services/reporting/narrative_generator.py` — implement `generate_narratives(edges, template, client) -> dict[str, str]` returning `{field_name: generated_text}` for `subject_description`, per-risk-dimension narratives, and `conclusion`; build prompts from extracted facts (sole data source, SC-006) + existing template prose sections as few-shot style examples (FR-008); use `chat_with_schema()` from T002; output is transient (not persisted, FR-007)
- [X] T023 [US3] Integrate narrative_generator into `backend/app/services/reporting/risk_report_generator.py` — in `generate_with_coverage()`, when `settings.llm_report_narrative_enabled` and LLM client available: call `generate_narratives()`, overwrite `report.subject_description` / `report.conclusion` / per-dimension narratives with results, add field names to `report.llm_generated_fields`; MUST NOT touch `_evaluate_rules` / risk levels / G1 (FR-009); flag off → existing rule-based template text (no regression)
- [X] T024 [US3] Wire narrative disclaimer rendering in `backend/app/services/reporting/docx_renderer.py` — for each field in `report.llm_generated_fields`, after rendering its content, add a disclaimer paragraph: "以上内容由文档抽取结果自动生成，仅供参考，请核对后确认。" in gray italic (FR-007)
- [X] T025 [US3] Write tests in `backend/tests/test_reporting/test_narrative_generator.py` — mock LLM; output uses only supplied facts (SC-006); template prose passed as few-shot context; flag off → no narrative generation; regeneration is stateless (no persistence)

**Checkpoint**: User Story 3 fully functional — narrative prose generated and annotated in DOCX; flag off = no change

---

## Phase 6: User Story 4 — Reference Existing Job for Slot Suggestion (Priority: P3)

**Goal**: Instead of uploading a document, the analyst can reference an existing extraction job to run AI slot suggestion using the job's already-parsed text

**Independent Test**: Call `POST /api/ast-templates/suggest-slots` with `job_id` of a completed extraction job; verify same suggestion flow as document-text input

### Implementation for User Story 4

- [X] T026 [US4] Extend `POST /api/ast-templates/suggest-slots` in `backend/app/api/ast_templates.py` — when `job_id` is provided: query `ExtractionJob` by id, verify exists (404 if not), read document text from `ExtractionJob.document_path` via `parse_docx_structure()` (reuse from `llm_gap_filler` imports), feed into `suggest_slots()`; this largely validates the path already wired in T010 and ensures the `parse_docx_structure` integration works end-to-end
- [X] T027 [US4] Extend `frontend/src/components/extraction/slot-suggestion-drawer.tsx` — add a "从作业" (From Job) tab/mode alongside the document-upload mode; show a searchable list of completed extraction jobs (fetch from existing jobs API); on job select, call `suggestSlots({ job_id })` and display results in the same suggestion tree

**Checkpoint**: User Story 4 fully functional — existing jobs can feed slot suggestion

---

## Phase 7: Polish & Cross-Cutting Concerns

**Purpose**: Backward compatibility verification, end-to-end validation, final checks

- [X] T028 SC-005 backward compatibility verification — with all three LLM flags `False` (default): generate a report, verify output is byte-identical to 012 baseline; suggest-slots returns 503; no LLM annotations in DOCX; no async polling required
- [X] T029 Run quickstart.md validation — execute all 4 scenarios (A: suggest-slots, B: gap-fill merge, C: narrative, D: backward compat) per `specs/013-llm-template-report-enhance/quickstart.md`
- [X] T030 Run full test suite — `uv run pytest backend/tests/test_extraction/test_slot_suggester.py backend/tests/test_reporting/test_report_llm_merge.py backend/tests/test_reporting/test_docx_llm_annotation.py backend/tests/test_reporting/test_narrative_generator.py` — all pass; existing tests unaffected

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies — can start immediately
- **Foundational (Phase 2)**: Depends on Phase 1 completion (T001–T005) — BLOCKS all user stories
- **US1 (Phase 3)**: Depends on Foundational; no dependencies on US2/US3/US4
- **US2 (Phase 4)**: Depends on Foundational; no dependencies on US1/US3/US4
- **US3 (Phase 5)**: Depends on Foundational; reuses async path from US2 (T017) if both enabled, but can be implemented independently
- **US4 (Phase 6)**: Depends on US1 (T008–T010 must exist for the endpoint to extend)
- **Polish (Phase 7)**: Depends on all desired user stories being complete

### User Story Dependencies

- **US1 (P1)**: Independent after Foundational — **MVP scope**
- **US2 (P2)**: Independent after Foundational — builds on Foundational `RiskReport` extensions (T006) and DOCX annotation (T007)
- **US3 (P3)**: Independent after Foundational — reuses `chat_with_schema` (T002) and DOCX annotation infra (T007); if both US2+US3 enabled, they share the async generation path (T017)
- **US4 (P3)**: Depends on US1 (extends the suggest-slots endpoint and drawer)

### Within Each User Story

- Backend service → endpoint → tests → frontend API → frontend UI
- Core implementation before integration
- Tests can be written alongside or after implementation (not TDD-required)

### Parallel Opportunities

- **Phase 1**: T001, T002, T003, T004 all touch different files — fully parallel
- **Phase 2**: T006, T007 touch different files — fully parallel
- **Phase 3**: T012 (frontend api.ts) can parallel with T008–T011 (backend); T013/T014 depend on T012
- **Phase 4**: T018, T019, T020 touch different files — parallel after T015/T016/T017
- **US1 and US2**: Can be developed in parallel by different developers after Foundational
- **US3**: Can parallel with US1/US2 after Foundational

---

## Parallel Example: User Story 1

```bash
# Backend service + frontend API can start in parallel:
Task T008: "Create slot_suggester.py — two-round LLM flow"
Task T012: "Add suggestSlots() to api.ts"  # [P] — different codebase

# After T008+T009 complete, these can parallel:
Task T010: "POST /suggest-slots endpoint"
Task T011: "test_slot_suggester.py"  # [P] — test file

# After T010+T012 complete, frontend UI tasks:
Task T013: "slot-suggestion-drawer.tsx"
Task T014: "template-slot-editor.tsx AI 分析 button"
```

## Parallel Example: User Story 2

```bash
# After T015/T016/T017 (merge + annotation + async endpoint):
Task T018: "test_report_llm_merge.py"      # [P]
Task T019: "test_docx_llm_annotation.py"   # [P]
Task T020: "api.ts async report functions"  # [P] — different codebase
```

---

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Complete Phase 1: Setup (T001–T005)
2. Complete Phase 2: Foundational (T006–T007)
3. Complete Phase 3: User Story 1 (T008–T014)
4. **STOP and VALIDATE**: Run test_slot_suggester.py + manual UI test
5. Deploy/demo: analysts can use AI to suggest slots from documents

### Incremental Delivery

1. Setup + Foundational → Foundation ready
2. Add US1 → Test independently → Deploy/Demo (**MVP!**)
3. Add US2 → Test independently → Deploy/Demo (reports now include LLM values)
4. Add US3 → Test independently → Deploy/Demo (narrative prose in reports)
5. Add US4 → Test independently → Deploy/Demo (job reference for suggestions)
6. Each story adds value without breaking previous stories

### Parallel Team Strategy

With multiple developers:

1. Team completes Setup + Foundational together
2. Once Foundational is done:
   - Developer A: User Story 1 (P1 — MVP)
   - Developer B: User Story 2 (P2 — report enhancement)
   - Developer C: User Story 3 (P3 — narrative)
3. User Story 4 starts after US1 completes (extends its endpoint)
4. Stories integrate independently; SC-005 verified at the end

---

## Notes

- [P] tasks = different files, no dependencies on incomplete tasks
- [Story] label maps task to specific user story for traceability
- Each user story is independently completable and testable
- All LLM calls mocked in tests — suite runs offline
- Commit after each task or logical group
- Stop at any checkpoint to validate story independently
- FR-009 invariant: every task touching risk_report_generator MUST NOT alter `_evaluate_rules`, `_evaluate_post_control`, risk-level mapping, or G1 three-state logic

---

## Phase 8: Convergence

- [ ] T031 Enforce `suggest_slots_timeout_s` as a bounded server-side timeout on the suggest-slots endpoint in `backend/app/api/ast_templates.py` — wrap the `suggest_slots()` call with a timeout guard using the configured `settings.suggest_slots_timeout_s` (default 30s); on timeout, return 503 retriable error per FR-001 (partial)
- [ ] T032 Fix `downloadReport()` in `frontend/src/lib/api.ts` — the function at line 1158 ignores its `reportId` parameter and fetches from the old `GET /risk-report` URL; update to use `GET /api/extraction/jobs/${jobId}/reports/${reportId}/download` so downloads work for async-generated reports; verify callers in `frontend/src/app/(dashboard)/entities/extraction/[jobId]/ast/page.tsx` per FR-005a (partial)
- [ ] T033 Add re-analysis confirmation dialog to `frontend/src/components/extraction/slot-suggestion-drawer.tsx` — when the user triggers a new analysis while existing suggestions are displayed, show a confirmation dialog warning that current suggestions will be cleared before proceeding per FR-013 / US1/AC5 (partial)
- [ ] T034 [P] Gate the LLM-enhanced report generation path in `backend/app/api/extraction.py` to `senior_analyst` role — when `_llm_report_flags_active()` is true, the `generate_risk_report` endpoint should require `_analyst` or equivalent senior_analyst dependency instead of `get_current_user` per FR-010; the existing synchronous path may remain as-is for backward compatibility (partial)
- [ ] T035 [P] Remove or fix dead `startReportGeneration()` function in `frontend/src/lib/api.ts` at line 1334 — it calls `POST /api/.../reports` but no such backend endpoint exists (actual is `POST .../risk-report`); either fix the URL or remove the unused export to prevent future confusion (unrequested)

---

## Phase 9: Faithful Preview Refinement (US1 — structure-faithful sample parsing)

**Purpose**: Fix the reported UX defect — the AI-suggest drawer flattened the sample DOCX to plain text and round-tripped it (losing structure, breaking slot↔preview linkage). Parse backend-side into structure-faithful tiptap, render via `WordViewer`, link slots by deterministic `source_ref` anchor instead of char offsets. Scope: create flow **and** re-edit path, with persistence. Contracts: [parse-sample-api.md](contracts/parse-sample-api.md), [suggest-slots-api.md](contracts/suggest-slots-api.md); data-model §1/§6. All done in this refinement pass.

- [X] T036 [US1] Add `structure_only: bool = False` to `annotate_word` and a `parse_word_to_tiptap(file_path) -> dict` wrapper in `backend/app/services/extraction/document_annotator.py` — when set, skip `_annotate_texts` (NER), so `engine=None` is safe and output carries zero `entity-annotation` marks; suppress the irrelevant GLiNER domain warning
- [X] T037 [US1] Add `tiptap_to_text(content_json) -> str`, `derive_source_ref(evidence_span, content_json) -> str | None`, and a `content_json` kwarg to `suggest_slots()` in `backend/app/services/extraction/slot_suggester.py` — server-side prompt-text derivation (reusing `_MAX_DOC_CHARS` truncation) + deterministic `§ <heading>`/raw-span anchor (forward then reverse containment); set `slot["source_ref"]` per slot before cap; `_ROUND2_SCHEMA` untouched (LLM never emits `source_ref`)
- [X] T038 [P] [US1] Extend DTOs in `backend/app/schemas/extraction.py` — `SuggestSlotsRequest.sample_content_json: dict | None` with three-way `model_post_init` (exactly one of `job_id`/`document_text`/`sample_content_json`); `SuggestedSlot.source_ref: str | None` (mark `evidence_offset` deprecated-optional); `AstTemplateCreate.sample_content_json: dict | None` per data-model §1/§6
- [X] T039 [US1] Add `POST /api/ast-templates/parse-sample` to `backend/app/api/ast_templates.py` (role-gated `_maintainer`, **not** flag-gated; `.docx`-only 422; tempfile write/parse/unlink; `parse_word_to_tiptap` + `tiptap_to_text` → `{content_json, plain_text}`); add the `sample_content_json` input branch to `/suggest-slots` (→ `tiptap_to_text` + pass `content_json`); persist/return `sample_content_json` in create/get
- [X] T040 [US1] Add nullable `sample_content_json: Mapped[dict | None] = mapped_column(JSON)` to `AstTemplate` in `backend/app/models/extraction.py` beside `sample_text`; author Alembic `0012_ast_template_sample_json` (`down_revision = "0011_ast_template_sample_text"`; id ≤32 chars — `alembic_version.version_num` is `VARCHAR(32)`) — applied at startup via `_run_migrations()`
- [X] T041 [P] [US1] Replace `extractDocxText` with `parseSample(file) -> {content_json, plain_text}` (path `/parse-sample`) in `frontend/src/lib/api.ts`; add `sample_content_json?` to `SuggestSlotsRequest` + template DTOs and `source_ref?: string | null` to `SuggestedSlot`; add `TiptapContent` type
- [X] T042 [US1] Wire `parseSample` + `sampleContent` (tiptap) into `frontend/src/app/(dashboard)/settings/ast-templates/page.tsx` — `handleDocxFileChange` → `parseSample`; drawer `request` uses `sample_content_json`; `handleCreateFromSuggestions` persists `sample_content_json` (+ `sample_text` from `plain_text`); `handleOpenEditor` loads `full.sample_content_json` into the re-editor
- [X] T043 [US1] Rework `frontend/src/components/extraction/slot-suggestion-drawer.tsx` — add `contentJson` prop; replace the `<pre>` + `evidence_offset`/`data-offset` machinery with `<WordViewer content={contentJson} highlightRef={activeRef} />`; `handleSlotClick` → `setActiveRef(slot.source_ref ?? slot.evidence_span)`; `job_id` branch fetches faithful content via `getAnnotatedDocument` (guard 404 → summary-only)
- [X] T044 [P] [US1] Add a body `p, li` highlight tier to `applyHighlight` in `frontend/src/components/extraction/word-viewer.tsx` after the heading and `th/td` tiers (`textContent?.includes(kw)`) — makes body-text evidence spans locatable; headings/tables still win first; backward-compatible
- [X] T045 [US1] Add a `sampleContentJson?: TiptapContent | null` prop to `frontend/src/components/extraction/template-slot-editor.tsx` (re-edit path) — AI request prefers `sample_content_json`, falls back to `document_text` for legacy; `previewJson` wraps legacy `sample_text` into a minimal tiptap doc; pass `contentJson` to the drawer
- [X] T046 [P] [US1] Tests via `uv run pytest` — extend `backend/tests/test_extraction/test_slot_suggester.py` (`tiptap_to_text`, `derive_source_ref`, `content_json` → per-slot `source_ref`) and `backend/tests/test_extraction/test_document_annotator.py` (`parse_word_to_tiptap` structure, zero `entity-annotation` marks); new `backend/tests/test_reporting/test_suggest_slots_api.py` (parse-sample happy/422/403; suggest-slots `sample_content_json` path + 503/403; three-way `model_post_init`) — all green (370 passed)
- [X] T047 [P] [US1] Docs — add `specs/013-llm-template-report-enhance/contracts/parse-sample-api.md`; update `contracts/suggest-slots-api.md` (three-way input, `source_ref`, `evidence_offset` deprecated, test split), `data-model.md` (§1 request/slot, new §6 persisted column), and `plan.md` (Refinement section)
