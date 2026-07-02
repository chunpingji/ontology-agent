# Tasks: AST Template Management & LLM Pipeline Enhancement

**Input**: Design documents from `specs/012-ast-template-llm-pipeline/`

**Prerequisites**: plan.md (required), spec.md (required), research.md, data-model.md, contracts/

**Organization**: Tasks are grouped by user story to enable independent implementation and testing of each story.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (e.g., US1, US2, US3)
- Include exact file paths in descriptions

---

## Phase 1: Setup

**Purpose**: Add new dependencies and configuration fields

- [x] T001 Add `openai` to optional dependency group `llm` in `backend/pyproject.toml` (pattern: existing `semantic` and `gliner` extras)
- [x] T002 Add `local_llm_*` configuration fields to `Settings` class in `backend/app/config.py` — fields: `local_llm_enabled` (bool, False), `local_llm_base_url` (str), `local_llm_model` (str), `local_llm_api_key` (str), `local_llm_max_tokens` (int, 4096), `local_llm_temperature` (float, 0.1) — see data-model.md §2.4

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: DB models, migration, and template resolution layer that ALL user stories depend on

**⚠️ CRITICAL**: No user story work can begin until this phase is complete

- [x] T003 [P] Create `AstTemplate` and `DocumentTypeMapping` SQLAlchemy models in `backend/app/models/extraction.py` — use existing `Mapped`/`mapped_column` pattern; fields per data-model.md §1.1 and §1.2; add `UniqueConstraint("name", "version")` on AstTemplate; add `relationship("AstTemplate")` on DocumentTypeMapping
- [x] T004 [P] Create Pydantic request/response schemas for template CRUD in `backend/app/schemas/extraction.py` — `AstTemplateCreate`, `AstTemplateResponse` (with computed `slot_count`), `AstTemplateUpdate`, `DocumentTypeMappingCreate`, `DocumentTypeMappingResponse`, `TemplateMatchResponse`
- [x] T005 Create Alembic migration in `backend/alembic/versions/` — create `ast_templates` and `document_type_mappings` tables; seed default template by reading `backend/app/services/reporting/templates/qs_a_020f05.json` into `ast_templates` (is_default=True); seed `DocumentTypeMapping` for CMCReport — see data-model.md §5
- [x] T006 Implement `resolve_template(doc_class_iri, db)` in `backend/app/services/reporting/ast_template.py` — three-tier fallback: (1) DocumentTypeMapping match by pattern+priority, (2) is_default=True template, (3) `load_default_template()` file fallback — see research.md R3
- [x] T007 Add `LLMExtractionSource` variant to `SlotSource` discriminated union in `backend/app/services/reporting/ast_template.py` — new class with `kind: Literal["llm_extraction"]`, `object_class_iri: str`, `data_property_iri: str`, `label: str`; update `SlotSource = Annotated[Union[...], Field(discriminator="kind")]` to include it
- [x] T008 Add `source_span: str | None = None` and `is_llm_sourced: bool = False` fields to `SlotCoverage` dataclass in `backend/app/services/reporting/coverage_validator.py`; add `source_span` and `is_llm_sourced` to `SlotCoverageResponse` schema in `backend/app/schemas/extraction.py`

**Checkpoint**: Foundation ready — DB models, migration, template resolution, and schema extensions in place

---

## Phase 3: User Story 1 + 2 — Template Management & Document Type Mapping (Priority: P1) 🎯 MVP

**Goal**: Users can upload, visually edit, and manage multiple AST templates; configure document-type-to-template mappings with priority ordering

**Independent Test**: Upload a template, verify it appears in the list, set it as default, create a document type mapping, verify template auto-resolution for a job

### Implementation for US1+US2

- [x] T009 [P] [US1] Implement template CRUD API endpoints in `backend/app/api/ast_templates.py` — `GET /api/ast-templates` (list), `POST /api/ast-templates` (create with `ReportTemplate.model_validate()` on schema_json), `PUT /api/ast-templates/{id}` (update → new version), `DELETE /api/ast-templates/{id}` (reject if is_default), `POST /api/ast-templates/{id}/set-default` (atomic unset previous + set new) — see contracts/ast-templates-api.md §1
- [x] T010 [P] [US2] Implement document type mapping API endpoints in `backend/app/api/ast_templates.py` — `GET /api/document-type-mappings` (list with joined template name/version), `POST /api/document-type-mappings` (create, validate template_id exists), `DELETE /api/document-type-mappings/{id}` — see contracts/ast-templates-api.md §2
- [x] T011 [P] [US1] Implement `GET /api/ast-templates/match/{job_id}` in `backend/app/api/ast_templates.py` — read job's annotation cache for doc_class_iri, call `resolve_template()`, return `TemplateMatchResponse` with `match_source` ("mapping" | "default" | "fallback") — see contracts/ast-templates-api.md §1
- [x] T012 [US1] Add template CRUD API functions to `frontend/src/lib/api.ts` — `fetchAstTemplates()`, `createAstTemplate()`, `updateAstTemplate()`, `deleteAstTemplate()`, `setDefaultTemplate()`, `fetchDocTypeMappings()`, `createDocTypeMapping()`, `deleteDocTypeMapping()` — follow existing React Query pattern
- [x] T013 [US1] Create template management page at `frontend/src/app/(dashboard)/settings/ast-templates/page.tsx` — template list table (name, version, slot_count, is_default badge, edit/delete actions); "上传模板" button with JSON file upload + validation error display; "设为默认" action per row; delete confirmation — see spec.md §3.4 wireframe
- [x] T014 [US2] Add document type mapping section to template management page in `frontend/src/app/(dashboard)/settings/ast-templates/page.tsx` — mapping list below template table showing `doc_class_iri_pattern → template name (version)`; "添加映射" form (pattern input, template selector dropdown, priority input); delete action per mapping
- [x] T015 [US1] Create visual slot editor component at `frontend/src/components/extraction/template-slot-editor.tsx` — accordion/tree UI mirroring Section → Group → Slot hierarchy; add/remove/reorder slots within groups; toggle required/optional per slot; edit slot label and metadata; save creates new template version via `updateAstTemplate()` — see research.md R6, clarification Q3 answer
- [x] T016 [US1] Wire visual slot editor into template management page — "编辑" action on template row opens slot editor in a dialog/drawer; on save, call `updateAstTemplate()` with modified `schema_json` and auto-incremented version; refresh template list after save
- [x] T017 [US1] Add pytest tests for template CRUD in `backend/tests/test_reporting/test_ast_template.py` — test `resolve_template()` three-tier fallback; test template validation rejects duplicate slot_ids; test set-default atomicity; test delete-default rejection; test version auto-increment

**Checkpoint**: Template management fully functional — upload, visual edit, version, delete, set-default, document type mappings. MVP deliverable.

---

## Phase 4: User Story 3 — Template Switching on AST Page (Priority: P2)

**Goal**: Analysts can switch between available templates on the AST coverage page to see how coverage changes under different evaluation criteria

**Independent Test**: Open AST coverage page with multiple templates, select a different template from dropdown, verify coverage metrics recalculate and display within 3 seconds

### Implementation for US3

- [x] T018 [US3] Extend `_build_ast_coverage_response()` in `backend/app/api/extraction.py` to accept optional `template_id: UUID | None` parameter — if provided, load template by ID from DB; if omitted, call `resolve_template(doc_class_iri, db)`; replace hardcoded `load_default_template()` call; add `template_name` and `template_version` to `ASTCoverageResponse`
- [x] T019 [US3] Extend `GET /api/extraction/jobs/{job_id}/ast-coverage` endpoint in `backend/app/api/extraction.py` to accept optional `template_id` query parameter — pass through to `_build_ast_coverage_response()`
- [x] T020 [US3] Add template selector to AST coverage page in `frontend/src/app/(dashboard)/entities/extraction/[jobId]/ast/page.tsx` — fetch template list via `fetchAstTemplates()`; show `<Select>` dropdown only when `templates.length > 1`; pre-select current template; on change, re-fetch coverage with `template_id` query param — see spec.md §5.2
- [x] T021 [US3] Update `ASTCoverageResponse` schema in `backend/app/schemas/extraction.py` to include `template_name: str` and `template_version: str` fields; update `GroupCoverageResponse` to include `is_dynamic: bool = False`

**Checkpoint**: Template switching works end-to-end. Analyst can compare coverage across templates.

---

## Phase 5: User Story 4 — Automatic Extraction Gap Filling (Priority: P2)

**Goal**: System automatically fills missing required slots from document text using local LLM when enabled, improving first-pass coverage rate

**Independent Test**: Process a document with known missing required slots, enable gap filling, verify previously missing slots are filled with values and source references

### Implementation for US4

- [x] T022 [P] [US4] Create LLM client wrapper at `backend/app/services/llm/local_client.py` — `get_local_llm() → OpenAI | None`; returns None when `local_llm_enabled=False`; import guarded with try/except for missing `openai` package (graceful degradation) — see contracts/llm-gap-filling.md §1
- [x] T023 [P] [US4] Create `__init__.py` at `backend/app/services/llm/__init__.py`
- [x] T024 [US4] Implement gap filler service at `backend/app/services/extraction/llm_gap_filler.py` — `fill_coverage_gaps(manifest, document_sections, template) → list[dict]`; construct prompt from `missing_required_slots` schema + document sections; parse JSON response; return edges with `source="llm"` and `source_span`; all failures caught → return `[]` with WARNING log — see contracts/llm-gap-filling.md §2
- [x] T025 [US4] Integrate gap filling into `_build_ast_coverage_response()` in `backend/app/api/extraction.py` — after first-pass `validate_coverage()`, if `local_llm_enabled` and `manifest.missing_required > 0`: load document sections from annotation cache, call `fill_coverage_gaps()`, merge returned edges, re-run `validate_coverage()` for final manifest
- [x] T026 [US4] Extend `_resolve_extraction()` or add `_resolve_llm_extraction()` in `backend/app/services/reporting/coverage_validator.py` — handle `LLMExtractionSource` slots; populate `SlotCoverage.source_span` from edge's `source_span` field; set `is_llm_sourced=True` — see contracts/llm-gap-filling.md §4
- [x] T027 [US4] Extend slot detail panel in `frontend/src/components/extraction/slot-detail-panel.tsx` — display subtle badge/icon for LLM-sourced slots (`is_llm_sourced: true`); show `source_span` text in an expandable section for traceability — see clarification Q2 answer
- [x] T028 [US4] Populate `is_llm_sourced` flag in `_build_ast_coverage_response()` tree builder in `backend/app/api/extraction.py` — when building `SlotCoverageResponse`, set `is_llm_sourced=True` for slots matched by LLM edges (check `source=="llm"` on matched edges or `source_span` presence)
- [x] T029 [US4] Add pytest tests for gap filling in `backend/tests/test_reporting/test_llm_gap_filler.py` — mock OpenAI client; test successful extraction returns edges with source="llm" + source_span; test LLM returning invalid JSON returns []; test LLM connection failure returns []; test `local_llm_enabled=False` skips entirely (zero regression)

**Checkpoint**: Gap filling works end-to-end when enabled. LLM-sourced values show badge + source span in UI. Disabled = identical to pre-feature behavior.

---

## Phase 6: User Story 5 — Ontology-Driven Dynamic Slot Expansion (Priority: P3)

**Goal**: System automatically discovers additional data properties from the ontology and creates corresponding extraction slots beyond the static template

**Independent Test**: Ensure ontology has data properties not in static template, run evaluation with expansion enabled, verify new slots appear grouped separately with "本体属性" label

### Implementation for US5

- [x] T030 [US5] Implement template expander at `backend/app/services/reporting/template_expander.py` — `expand_template_with_ontology(template, doc_class_iri, engine) → ReportTemplate`; traverse object properties from doc_class_iri to range classes; call `engine.get_data_properties_by_domain()` per range class; filter duplicates vs static template; create new Slots with `LLMExtractionSource`; group under "扩展属性: {class_label}" groups — see contracts/llm-gap-filling.md §3
- [x] T031 [US5] Integrate template expansion into `_build_ast_coverage_response()` in `backend/app/api/extraction.py` — if `local_llm_enabled` and doc_class_iri available: call `expand_template_with_ontology()` before coverage validation; pass expanded template to `validate_coverage()` and tree builder
- [x] T032 [US5] Extend AST tree view in `frontend/src/components/extraction/ast-tree-view.tsx` to display dynamic groups — check `is_dynamic` flag on groups; render with distinct styling (e.g., lighter background, "本体属性" badge/tag) to distinguish from static template groups — see spec.md §5.3
- [x] T033 [US5] Pass expanded template sections through `_build_ast_coverage_response()` tree builder — ensure dynamically added groups carry `is_dynamic: true` in `GroupCoverageResponse`; ensure expanded slots participate in gap filling (Mode A) if still missing after expansion

**Checkpoint**: Ontology expansion populates dynamic slots. AST tree distinguishes static vs dynamic groups. Expansion + gap filling compose correctly.

---

## Phase 7: User Story 6 — End-to-End Multi-Format Validation (Priority: P3)

**Goal**: Validate that the system can process a new document type end-to-end (upload template → mapping → extraction → coverage → report) without code changes

**Independent Test**: Upload a second template for a different document type, create mapping, upload document, verify pipeline produces meaningful coverage report

### Implementation for US6

- [x] T034 [US6] Create a second test template JSON file for a different document type (e.g., cleaning validation or stability evaluation) in `backend/app/services/reporting/templates/` — define 10-15 slots in sections/groups structure; ensure it validates via `ReportTemplate.model_validate()`
- [x] T035 [US6] Write end-to-end integration test in `backend/tests/test_reporting/test_multi_template_e2e.py` — upload second template via API, create DocumentTypeMapping, simulate extraction job for new doc type, verify `resolve_template()` selects correct template, verify coverage manifest uses new template's slot structure, verify zero regression on existing CMCReport pipeline

**Checkpoint**: Full pipeline validated for a second document type. System generalizes without code changes.

---

## Phase 8: Polish & Cross-Cutting Concerns

**Purpose**: Edge cases, hardening, and cleanup

- [x] T036 [P] Handle template deletion edge case — ensure `GeneratedReport.rules_summary` snapshots remain valid after template deletion; verify historical reports are unaffected — see spec.md Edge Cases
- [x] T037 [P] Add audit logging for template CRUD operations in `backend/app/api/reports.py` — log create/update/delete/set-default actions using existing `append()` audit helper from `backend/app/services/audit.py`
- [x] T038 [P] Remove hardcoded `"CMCReport"` check in `_build_ast_coverage_response()` at `backend/app/api/extraction.py:994` — replace with template resolution; any document type with a matching template should be supported
- [x] T039 Run quickstart.md validation scenarios — verify all 3 scenarios and 12-item verification checklist pass

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies — can start immediately
- **Foundational (Phase 2)**: Depends on Setup completion — BLOCKS all user stories
- **US1+US2 (Phase 3)**: Depends on Foundational — MVP deliverable
- **US3 (Phase 4)**: Depends on Foundational — can run parallel with US1+US2 (backend only needs T006)
- **US4 (Phase 5)**: Depends on Foundational — can run parallel with US1+US2/US3
- **US5 (Phase 6)**: Depends on T007 (LLMExtractionSource) and T026 (coverage resolver)
- **US6 (Phase 7)**: Depends on Phases 3-6 (validates full stack)
- **Polish (Phase 8)**: Depends on all user stories being complete

### User Story Dependencies

- **US1+US2 (P1)**: Can start after Foundational — no dependencies on other stories
- **US3 (P2)**: Can start after Foundational — needs `resolve_template()` (T006) but no other US dependency
- **US4 (P2)**: Can start after Foundational — independent of US1-3
- **US5 (P3)**: Depends on T007 and T026 from Foundational/US4 — needs LLMExtractionSource and coverage resolver
- **US6 (P3)**: Integration validation — depends on US1+US2 (template CRUD) and benefits from US4+US5

### Within Each User Story

- Models/schemas before services
- Services before API endpoints
- Backend before frontend
- Core implementation before integration/polish

### Parallel Opportunities

- T003 and T004 can run in parallel (different files)
- T009 and T010 can run in parallel (same file but independent endpoint groups)
- T022 and T023 can run in parallel (different files)
- US3 and US4 can run in parallel after Foundational phase
- All frontend tasks within a phase can overlap with backend tests

---

## Parallel Example: User Story 1+2

```text
# Launch parallel backend tasks:
T009: Template CRUD API endpoints in backend/app/api/reports.py
T010: Document type mapping endpoints in backend/app/api/reports.py
T011: Template match endpoint in backend/app/api/reports.py

# Then sequential frontend (depends on API):
T012: API functions in frontend/src/lib/api.ts
T013: Template management page
T014: Document type mapping section
T015: Visual slot editor component
T016: Wire editor into management page
T017: Backend tests
```

---

## Implementation Strategy

### MVP First (US1+US2 Only)

1. Complete Phase 1: Setup (T001-T002)
2. Complete Phase 2: Foundational (T003-T008)
3. Complete Phase 3: US1+US2 Template Management (T009-T017)
4. **STOP and VALIDATE**: Upload template, edit via visual editor, create mapping, verify resolution
5. Deploy/demo if ready — template management is independently useful

### Incremental Delivery

1. Setup + Foundational → Foundation ready
2. Add US1+US2 → Template management MVP → Deploy/Demo
3. Add US3 → Template switching on AST page → Deploy/Demo
4. Add US4 → LLM gap filling → Deploy/Demo
5. Add US5 → Ontology expansion → Deploy/Demo
6. Add US6 → End-to-end validation → Full feature complete

### Parallel Team Strategy

With multiple developers after Foundational phase:

- Developer A: US1+US2 (template CRUD + visual editor)
- Developer B: US4 (LLM gap filling — backend-heavy)
- Developer C: US3 (template switching — simpler, faster)
- Then: US5 and US6 sequentially (depend on earlier work)

---

## Notes

- [P] tasks = different files, no dependencies
- [Story] label maps task to specific user story for traceability
- Each user story should be independently completable and testable
- Commit after each task or logical group
- Stop at any checkpoint to validate story independently
- `local_llm_enabled=False` is the default — all tests must pass with it disabled (zero regression)
- Visual slot editor (T015-T016) is the most complex frontend task — plan accordingly
