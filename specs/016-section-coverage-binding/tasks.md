---
description: "Task list for Section-Level Ontology Coverage Binding"
---

# Tasks: Section-Level Ontology Coverage Binding

**Input**: Design documents from `/specs/016-section-coverage-binding/`

**Prerequisites**: [plan.md](plan.md) (required), [spec.md](spec.md) (user stories), [research.md](research.md) (D1–D13), [data-model.md](data-model.md) (entities), [contracts/](contracts/) (4 interface contracts), [quickstart.md](quickstart.md) (test matrix)

**Tests**: INCLUDED — Constitution Principle IV (Test Discipline & Contract-First) and the quickstart pytest matrix (a)–(f) require them. Backend is pytest-covered; the frontend has no JS test runner, so its behavior is verified via the quickstart editor walkthrough (Polish phase).

**Organization**: Tasks are grouped by user story (US1 P1 → US2 P2 → US3 P3). This is an **additive** change to an existing web app — no migration, no new table/column; coverage nests in the existing `AstTemplate.schema_json` JSON column.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies on incomplete tasks)
- **[Story]**: US1 / US2 / US3 (Setup/Foundational/Polish carry no story label)
- Exact file paths are included in every task

## Path Conventions

Web app (Option 2): `backend/app/…`, `backend/tests/…`, `frontend/src/…`. Backend Python runs via **`uv`** (`cd backend && uv run …`) — bare `python`/`pytest` lacks `psycopg2`.

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Establish the pre-change green baseline used as the no-regression gate (SC-006).

- [X] T001 Establish backend test baseline: run `cd backend && uv sync && uv run pytest -q` and record the current green state — specifically confirm `test_reporting/test_multi_template_e2e.py` reports `missing_required == 11` (the FR-013/SC-006 regression anchor per [contracts/coverage-validation.md](contracts/coverage-validation.md) §Regression guard).

**Checkpoint**: Baseline captured — any later red is attributable to this feature.

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: The coverage schema + test fixture that ALL user stories depend on.

**⚠️ CRITICAL**: No user story work can begin until this phase is complete.

- [X] T002 [P] Add coverage models to `backend/app/services/reporting/ast_template.py` — `OntologyRelationBinding` (`kind='ontology_relation'`, `doc_class_iri`, `predicate_iri`, `range_class_iri`, `required: bool = True`, `required_properties: list[str] = []`, `label`), `FactSourceBinding` (`kind='fact_source'`, `source`, `selector`, `label`), and `CoverageBinding = Annotated[Union[...], Field(discriminator='kind')]`, per [contracts/coverage-schema.md](contracts/coverage-schema.md) (C3–C5). Do NOT touch existing `ExtractionSource`/`LLMExtractionSource`/`SlotSource` (C6).
- [X] T003 Add the `coverage: list[CoverageBinding] = Field(default_factory=list)` field to `Section` in `backend/app/services/reporting/ast_template.py` (mirrors the additive-optional `Section.prompt`). MUST be a **declared** field — the codebase default is `extra='ignore'`, which would silently drop an un-declared key (C2). Do NOT extend `_unique_slot_ids` and do NOT add any cross-section uniqueness validator (C7 / FR-011a). Depends on T002.
- [X] T004 [P] Extend the fake engine in `backend/tests/fixtures/ontology.py::build_drug_ontology()` (`FakeFeatureEngine`) with real methods `get_relation_schema(class_iri, max_hops=4)` (verified edge shape: `hop, predicate_iri, predicate_label, domain_class_iri, range_class_iri, range_class_label, range_subclasses, range_data_properties`), `get_subclasses(class_iri, recursive=True)`, and `get_data_properties_by_domain(class_iri)`. These MUST be real methods — `__getattr__` returns `None` (not a list), which would make expansion silently no-op (memory `ontology-aware-paths-legacy-in-tests`).
- [X] T005 Add schema round-trip tests to `backend/tests/test_reporting/test_ast_template.py`: `test_section_coverage_round_trips` (coverage survives `model_validate → model_dump`) and `test_legacy_template_without_coverage_unchanged` (`coverage == []`, no shape drift), per [contracts/coverage-schema.md](contracts/coverage-schema.md) (C1, C2). Depends on T003.

**Checkpoint**: Coverage vocabulary exists and round-trips; the fake engine can expand relationships. User stories can now begin (US1 and US2 in parallel).

---

## Phase 3: User Story 1 - AI produces ontology-grounded section coverage (Priority: P1) 🎯 MVP

**Goal**: Running AI analysis on a sample document yields section-level ontology **coverage declarations** (real relationship IRIs, required-by-default) plus explicit **unresolved candidates** — never a pile of human-filled fields, never anchored to a sample individual.

**Independent Test**: Run the suggester over a CMC-type sample; assert graph-sourced sections return `CoverageDeclaration`s referencing types (e.g. `describes → DrugProduct`), zero default-to-manual, and an unbindable position surfaces as an `unresolved_candidate` (spec US1 Independent Test; SC-001, SC-002).

### Tests for User Story 1 ⚠️ (write first, expect FAIL before T008)

- [X] T006 [P] [US1] Rewrite `backend/tests/test_extraction/test_slot_suggester.py` to the pure-coverage shape: `TestBasic` (`test_two_round_flow_returns_pure_coverage_shape`, `test_no_legacy_slot_stream_keys`, `test_empty_document_returns_empty`, `test_round1_failure_returns_empty`) + `TestOntologyCoverage` (`test_graph_sourced_section_emits_coverage` S1, `test_declarations_reference_types_never_individuals` S2, `test_unbindable_position_becomes_unresolved_candidate` S4, `test_declaration_required_true_by_default` S5, `test_doc_class_iri_optional_request_still_valid` S6) + `TestTiptapToText` (retained). **DELETE** `TestOntologyIRIBinding::test_no_match_sets_llm_extraction` (S3), `TestDeriveSourceRef`, `TestContentJsonSourceRef` (removed per-slot `source_ref` machinery). Per [contracts/suggest-slots-api.md](contracts/suggest-slots-api.md).

### Implementation for User Story 1

- [X] T007 [P] [US1] Add transport schemas to `backend/app/schemas/extraction.py`: `CoverageDeclaration` and `UnresolvedCandidate`; add `coverage: list[CoverageDeclaration] = []` and `unresolved_candidates: list[UnresolvedCandidate] = []` to `SuggestSlotsResponse`; add optional `doc_class_iri: str | None = None` to `SuggestSlotsRequest` **outside** the `model_post_init` exactly-one-of count (D10, S6). Per [contracts/suggest-slots-api.md](contracts/suggest-slots-api.md).
- [X] T008 [US1] Rewrite the ontology-grounding path in `backend/app/services/extraction/slot_suggester.py`: add `doc_class_iri` param to `suggest_slots`; inject a **compact hop-1** `get_relation_schema(doc_class_iri)` view + `get_data_properties_by_domain(doc_class_iri)` into the Round-1/Round-2 prompts (cache the schema once); have the LLM **select** predicate IRIs from that set and emit `CoverageDeclaration`s (`required=True`) + `unresolved_candidates`; dedup own output by `(doc_class_iri, predicate_iri, range_class_iri)` (S7). **Converged (T024)**: output is now **only** `{document_summary, coverage, unresolved_candidates}` — the whole slot stream (`_bind_ontology_iris`, `_group_into_sections`, `derive_source_ref`, `_collect_blocks`, `llm_extraction` tagging) is DELETED, not neutralized (S3); no-graph → empty `coverage`, no `sections`, nothing forced to manual (FR-012). Depends on T007.
- [X] T009 [US1] Wire `doc_class_iri` provenance in `backend/app/api/ast_templates.py::suggest_slots_endpoint` (~534–596): recover it from the request field, else from the job annotation cache `result['doc_class']['doc_class_iri']` (written in `api/extraction.py:390–402`); pass both `ontology_engine` (existing `Depends`) and `doc_class_iri` into `suggest_slots` (D10). Depends on T008.

**Checkpoint**: AI analysis returns ontology-grounded coverage + unresolved candidates. US1 is independently testable via `uv run pytest tests/test_extraction/test_slot_suggester.py`.

---

## Phase 4: User Story 2 - No-omission signal at relationship granularity (Priority: P2)

**Goal**: At validation/generation, each declared relationship expands (via the ontology) into its target type's property checklist; an absent **required** relationship is a `missing_required` omission, blank properties under a present relationship are informational (`blank_optional`), promoted properties are required, and the same relationship across sections counts once. Authored coverage drives generated reports; legacy templates do not regress.

**Independent Test**: Validate one template against two source graphs of the same type — one missing the required relationship's target (⇒ one `missing_required`), one present-but-blank (⇒ zero omissions, props `blank_optional`) — per spec US2 Independent Test; SC-003. Legacy `missing_required == 11` unchanged (SC-006).

### Tests for User Story 2 ⚠️ (write first, expect FAIL before T012)

- [X] T010 [P] [US2] Add validator tests to `backend/tests/test_reporting/test_coverage_validator.py`: `test_coverage_expands_relationship_and_properties` (V2/V3), `test_required_relationship_absent_is_missing_required` (V2), `test_present_relationship_blank_props_informational` (V3), `test_promoted_property_blank_is_missing_required` (V4), `test_duplicate_relationship_across_sections_counts_once` (V5), `test_coverage_empty_is_noop_legacy_manifest` (V1), `test_engine_none_degrades_gracefully` (V6/V11), `test_fact_source_emits_noncounting_manual` (V8), `test_get_relation_schema_memoized_once_per_docclass` (V7). Per [contracts/coverage-validation.md](contracts/coverage-validation.md).
- [X] T011 [P] [US2] Add E2E tests to `backend/tests/test_reporting/test_multi_template_e2e.py`: `test_coverage_drives_generation` (DB template coverage → deduped omissions via `resolve_template`) and the regression assertion that legacy `missing_required == 11` is unchanged (SC-006, D5).

### Implementation for User Story 2

- [X] T012 [US2] Extend `backend/app/services/reporting/coverage_validator.py`: add `engine: Any | None = None` param to `validate_coverage`; add helpers `_relationship_present` (accept `range_class_iri` + `get_subclasses` recursive; offline substring fallback when `engine is None`, D7), `_range_data_properties` (memoized `get_relation_schema` per `doc_class_iri`, V7), and `_resolve_coverage_binding` (relationship + property `SlotCoverage` positions; `seen_keys` global dedup on `(doc,pred,range)`, D3; `fact_source` → one non-counting `MANUAL`, D11); insert the section-loop **seam** at line 317 **before** the group loop (318); reuse `FILLED`/`BLANK_OPTIONAL`/`MISSING_REQUIRED` (no new status, D12), tag `source_kind='ontology_relation'`, namespaced `slot_id`; wrap engine calls in try/except → degrade to None path (V6/V11). Read-only only (V10). Depends on T002, T003, T004.
- [X] T013 [P] [US2] In `backend/app/services/reporting/risk_report_generator.py::generate_with_coverage` (~134–137), pass `engine=get_loaded_engine()` (already imported ~112) into `validate_coverage`. Depends on T012.
- [X] T014 [P] [US2] In `backend/app/api/extraction.py::generate_risk_report` (~1056–1182), rewire template resolution from filesystem `load_default_template` to `resolve_template(doc_class_iri, db)` so DB-authored coverage drives generation (D5); source `doc_class_iri` from the job annotation cache. Guard with T011's regression assertion. Depends on T012.

**Checkpoint**: Omission signal calibrated at relationship granularity; authored coverage drives reports; legacy no-regression. US2 testable via `uv run pytest tests/test_reporting/`.

---

## Phase 5: User Story 3 - Author edits section coverage; non-graph fields preserved (Priority: P3)

**Goal**: The editor presents section-level coverage declarations (relationship/type selectors) + unresolved-candidate disposition, stops both `→ manual` collapse sites, and leaves rule/constant/manual slots untouched. The coverage-view API surfaces ontology positions without double-emitting via `template_expander`.

**Independent Test**: Open a template in the editor — graph-sourced coverage shows as editable relationship declarations (naming types, not individuals); an unresolved candidate is pending disposition; rule/constant/manual slots are unchanged; save→reload persists; mark-optional→re-validate changes the omission signal (spec US3 scenarios; FE1–FE5).

> **Design reference (frontend)**: T016–T019 implement the approved visual design in `design.pen` → frame **"Slot Tree Component — Full View"** (`vwW7Q`), a full-height render of the reusable **Slot Tree** component (`JKSZm`). Node IDs cited in these tasks are nodes of that frame; the complete node→contract map is in [contracts/frontend-authoring.md](contracts/frontend-authoring.md) §Design reference.

> **⚠️ SUPERSEDED (禁止原文取数候选, post-016)** — **T019** and the `unresolved_candidates` half of **T016** are reverted. AI analysis now returns **only** `{document_summary, coverage}`; positions that bind to no ontology menu edge are **silently ignored** (this supersedes **FR-008a** and «不会自动降级为人工插槽»). The `待解析候选` panel (`pV1Mc`), candidate cards, `UnresolvedCandidate` type, and disposition UI were **deleted** from `template-slot-editor.tsx` + `api.ts`. Two things from this slice DO stand: coverage authoring (T017/T018), and grounding — "关联文档类型" drives `docClassIri` **live** from the metadata Select (not the saved prop) and AI analysis is blocked until it is set.

### Implementation for User Story 3

- [X] T015 [US3] In `backend/app/api/extraction.py::_build_ast_coverage_response` (~1284–1360), pass `engine=get_loaded_engine()` into `validate_coverage` and add a coverage-emission branch to the response tree so `section.coverage` positions surface as ghost/coverage rows; **guard against double-emit** — skip the `template_expander` `ontology_expansion` section for any section that has `coverage` (D13). Depends on T012.
- [X] T016 [P] [US3] Add types to `frontend/src/lib/api.ts`: `OntologyRelationBinding`, `FactSourceBinding`, `CoverageBinding`, `UnresolvedCandidate`; add `coverage` + `unresolved_candidates` to `SuggestSlotsResponse` and `doc_class_iri?` to `SuggestSlotsRequest`. Reuse existing `getRelationSchema`/`RelationSchemaEdge` — **no new endpoint** (FE5). *Design:* each `OntologyRelationBinding` is one Coverage Decl row (`WrePQ` `describes→药品产品`, `DFavp` `hasTestItem→检测项目`); each `UnresolvedCandidate` is one card in the 待解析候选 panel (`pV1Mc`).
- [X] T017 [US3] In `frontend/src/components/extraction/template-slot-editor.tsx`, add `CoverageBinding` types + `SectionDef.coverage`. **Converged (T025)**: rather than merely neutralizing them, **DELETE both collapse sites and the machinery that fed them** — `suggestionToSlot`, the ghost-row block, `pending`/`aiSkipped` state, «全部采纳»/«已跳过» controls, and the `SuggestedSlot`/`SuggestedGroup`/`SuggestedSection` imports (F1/F2). *Design invariant:* the frame never renders a graph-sourced / AI position as a 人工/manual slot — an AI-proposed binding is the amber Coverage Decl card (`ikcOK`, «AI 建议» / «采纳·忽略»), and «人工» appears **only** inside the explicitly non-ontology slot groups (`rXC8T` / `lusM4`). Already-persisted slots still render/edit via the normal slot tree. Depends on T016.
- [X] T018 [US3] In `frontend/src/components/extraction/template-slot-editor.tsx`, add `SectionCoverageArea` (modeled on `SectionPromptArea` ~2198, mounted ~2148–2157): relationship selector populated from `getRelationSchema(doc_class_iri)`, target-type display, `required` toggle, add/remove (F3, FE2). *Design:* the 本体覆盖声明 area (`DcRjT` §概述, `zt2iu` §检测结果) — header Object Chip = doc entity type; each row = predicate (JetBrains Mono local-name) + `arrow-right` + target type + required toggle (`qkoDx` 必填-on / `vrXJb` 选填-off) + `slpra:*` type IRI + status pill; the «属性覆盖 · N 项» checklist (`LrfhC` / `SbBzd`) is the ontology property expansion (green `#16A34A` = FILLED, amber `#FBBF24` = MISSING_REQUIRED). Depends on T017.
- [X] T019 [US3] In `frontend/src/components/extraction/template-slot-editor.tsx`, add the unresolved-candidate disposition UI (pending list with bind / convert-to-constant / convert-to-manual / discard actions); a candidate is NOT pre-classified as manual (F4/FE4). Preserve `SlotInlineEditor` rule/constant/manual editing unchanged (F5/FE3). *Design:* the 待解析候选 panel (`pV1Mc`) — each candidate card (`OpHeT` «设备编号 646», `RT3aU` «SOP-QC-114») shows evidence + reason and the four buttons 绑定关系 / 设为常量 / 设为人工 / 丢弃; header hint «不会自动降级为人工插槽»; the preserved `SlotInlineEditor` is `LrIAX`. Depends on T017.

**Checkpoint**: Editor authors coverage declarations, disposes candidates, preserves non-graph slots; coverage round-trips through `schema_json`.

---

## Phase 6: Polish & Cross-Cutting Concerns

**Purpose**: End-to-end validation and documenting the known constraint.

- [X] T020 Run the full backend gate: `cd backend && uv run pytest -q` (all green, no regressions) plus the quickstart matrix (a)–(f) in [quickstart.md](quickstart.md). Confirm legacy `missing_required == 11` (SC-006).
- [X] T021 Execute the frontend editor walkthrough (6 steps) in [quickstart.md](quickstart.md) §Frontend walkthrough: coverage rendered (SC-001), type-not-individual (SC-002), candidate pending list, non-graph slots untouched, save→reload persistence, mark-optional→re-validate signal change. Cross-check the rendered editor layout against the design reference — `design.pen` frame **"Slot Tree Component — Full View"** (`vwW7Q`).
- [X] T022 [P] Document the `get_relation_schema` `visited_ranges` global first-discovery / single-hop authoring limitation (D8) as a code comment in `backend/app/services/reporting/coverage_validator.py` (near `_range_data_properties`) so a missing second-predicate property list is understood as an accepted constraint, not a bug.

---

## Phase 7: Convergence — remove legacy AI slot stream + required document type

**Purpose**: Act on the post-delivery directive «彻底遗留实现，点击AI分析按016全新逻辑实现» and the clarification that `关联文档类型` is a **required** attribute (FR-002a, FR-016). Phases 3/5 shipped 016 *alongside* the legacy per-field suggestion stream; this phase removes that stream entirely and makes the document type an authored requirement. Scope = code + this spec sync. Zero regression on already-persisted slots.

- [X] T023 Converge `backend/app/schemas/extraction.py::SuggestSlotsResponse` to `{document_summary, coverage, unresolved_candidates}`; delete `SuggestedSlot`/`SuggestedGroup`/`SuggestedSection` (dead once the stream is gone). Keep `doc_class_iri` optional on `SuggestSlotsRequest` (FR-002a). Per [contracts/suggest-slots-api.md](contracts/suggest-slots-api.md).
- [X] T024 Converge `backend/app/services/extraction/slot_suggester.py` to emit **only** the pure 016 dict (see T008): delete `_group_into_sections`, `derive_source_ref`, `_collect_blocks`, the `llm_extraction` tagging, and the `sections`/`total_suggested`/`skipped_duplicates`/`truncated` fields from all return paths; strip `slots`/`skipped_duplicates` from the Round-2 schema (`required: []`). Keep `tiptap_to_text`/`_node_text`/`build_document_text` + `_extract_coverage`/`_extract_unresolved`/`_build_ontology_context`. (Supersedes T008's incremental form.)
- [X] T025 Converge `frontend/src/components/extraction/template-slot-editor.tsx` (see T017): delete `suggestionToSlot`, the ghost-row block, `pending`/`aiSkipped` state, «全部采纳»/«已跳过» controls, `ConfidenceBadge`, `newSlotIds`, and the `SuggestedSlot` import; `runAiAnalysis` sets only `aiSummary`/`pendingCoverage`/`unresolvedCandidates`; `buildTree` returns non-nullable `RenderSection`/`RenderGroup` over real sections only. Already-persisted extraction/manual/rule/constant slots still render/edit (zero regression). Verified `npx tsc --noEmit` clean.
- [X] T026 Converge `frontend/src/lib/api.ts::SuggestSlotsResponse` to `{document_summary, coverage, unresolved_candidates}`; delete `SuggestedSlot`/`SuggestedGroup`/`SuggestedSection`; keep `OntologyRelationBinding`/`FactSourceBinding`/`CoverageBinding`/`UnresolvedCandidate`/`SuggestSlotsRequest.doc_class_iri`. Per [contracts/frontend-authoring.md](contracts/frontend-authoring.md).
- [X] T027 [FR-016] Make `关联文档类型` a **required** template attribute (UI-only; `iri_pattern` column stays nullable, no migration): (a) create wizard `frontend/src/app/(dashboard)/settings/ast-templates/page.tsx` — required `DOCUMENT_TYPE_GROUPS` select writing the full class IRI into `uploadIriPattern`, «进入编辑器» disabled until set; (b) editor Basic-Info `template-slot-editor.tsx` — remove the `未指定`/`__none__` option, block `handleSaveMeta` when `metaForm.iriPattern` is empty; (c) flip `docClassIri` precedence to prefer the declared `iriPattern` full IRI, falling back to `sourceDocClass?.doc_class_iri` for legacy templates.
- [X] T028 Sync spec docs to the convergence: `spec.md` (Clarifications + FR-002a + FR-016 + US1 + Assumptions), `plan.md` (approach + file tree), `tasks.md` (this phase + T006/T008/T017 corrections), `contracts/suggest-slots-api.md` + `contracts/frontend-authoring.md` (pure response shape, deleted stream, required attribute).

**Checkpoint**: Clicking «AI分析» runs pure 016 logic (coverage + unresolved candidates + summary, no ghost slots); `关联文档类型` is required to create/save a template; legacy templates' persisted slots are unaffected. `uv run pytest tests/test_extraction/test_slot_suggester.py tests/test_reporting/test_coverage_validator.py` green; `npx tsc --noEmit` clean.

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: no dependencies — start immediately.
- **Foundational (Phase 2)**: depends on Setup — **BLOCKS all user stories** (schema + fixture).
- **US1 (Phase 3)** and **US2 (Phase 4)**: both depend only on Foundational — can run **in parallel** (disjoint files: `slot_suggester.py`/`schemas` vs `coverage_validator.py`).
- **US3 (Phase 5)**: frontend + coverage-view. `T015` depends on US2's `T012`; the pure editor/authoring slice (`T016`–`T019`) depends only on Foundational schema. So US3 can largely proceed after Foundational, with `T015`'s ghost-row status display gated on US2.
- **Polish (Phase 6)**: depends on all targeted stories.

### User Story Dependencies

- **US1 (P1)**: Foundational only. Independently testable (suggester + schemas + endpoint).
- **US2 (P2)**: Foundational only. Independently testable (validator + generation). No dependency on US1.
- **US3 (P3)**: Foundational for authoring/round-trip; US2 (`T012`) for the coverage-status ghost view (`T015`).

### Within Each User Story

- Tests written first and expected to FAIL before implementation (US1: T006 → T008; US2: T010/T011 → T012).
- Schemas before services (T007 → T008); validator before its wiring (T012 → T013/T014).
- Frontend types before components (T016 → T017 → T018/T019).

### Parallel Opportunities

- **Foundational**: T002 [P] (ast_template.py) ∥ T004 [P] (tests/fixtures/ontology.py). T003 after T002; T005 after T003.
- **US1**: T006 [P] (test) ∥ T007 [P] (schemas). Then T008 → T009.
- **US2**: T010 [P] ∥ T011 [P] (distinct test files). Then T012 → {T013 [P] ∥ T014 [P]} (distinct files).
- **US3**: T016 [P] (api.ts) ∥ T015 (backend). T017 → {T018, T019} (same file — sequential).
- **Cross-story**: once Foundational is done, one dev takes US1, another US2, in parallel.

---

## Parallel Example: Foundational + US2 kickoff

```bash
# Foundational — two files, no shared state:
Task: "T002 Add coverage models to backend/app/services/reporting/ast_template.py"
Task: "T004 Extend fake engine in backend/tests/fixtures/ontology.py"

# US2 tests together (distinct files) before implementing the validator:
Task: "T010 Validator tests in backend/tests/test_reporting/test_coverage_validator.py"
Task: "T011 E2E tests in backend/tests/test_reporting/test_multi_template_e2e.py"

# US2 wiring after T012 — distinct files:
Task: "T013 engine wiring in backend/app/services/reporting/risk_report_generator.py"
Task: "T014 resolve_template rewire in backend/app/api/extraction.py"
```

---

## Implementation Strategy

### MVP First (User Story 1 only)

1. Phase 1 Setup → 2. Phase 2 Foundational (schema + fixture — blocks everything) → 3. Phase 3 US1.
4. **STOP and VALIDATE**: `uv run pytest tests/test_extraction/test_slot_suggester.py` — AI analysis now emits ontology-grounded coverage + unresolved candidates (SC-001/SC-002). Demo the "no more manual-everything" fix.

### Incremental Delivery

1. Setup + Foundational → schema ready.
2. US1 → AI authoring fixed (MVP demo).
3. US2 → omission signal calibrated + generation drives DB coverage + legacy no-regression (SC-003/SC-006).
4. US3 → editor authoring + candidate disposition + non-graph preservation (SC-005).
5. Polish → full green gate + walkthrough + document D8 constraint.

### Parallel Team Strategy

After Foundational: Dev A → US1 (backend authoring), Dev B → US2 (validator/generation), Dev C → US3 frontend authoring slice (T016–T019); T015 lands once US2's T012 is merged.

---

## Notes

- [P] = different files, no incomplete dependencies. `[US#]` maps each task to its story for traceability.
- **No DB migration / no new table or column** — coverage nests in `AstTemplate.schema_json`; the critical invariant is that `Section.coverage` is a **declared** field (T003) or `extra='ignore'` drops it.
- **No new status enum** — reuse `FILLED`/`BLANK_OPTIONAL`/`MISSING_REQUIRED`, distinguish via `source_kind='ontology_relation'` (D12).
- **All ontology access read-only** (FR-015 / Principle II); engine-none degrades gracefully (FR-012 / Principle VI).
- Run backend via `cd backend && uv run pytest`. Verify each test task FAILS before its implementation task. Commit after each task or logical group.
- The SC-006 anchor (`missing_required == 11`) is the primary regression tripwire — check it after T012, T014, and T020.
