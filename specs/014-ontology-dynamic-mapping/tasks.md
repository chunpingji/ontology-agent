---
description: "Task list for Ontology Dynamic Object Mapping"
---

# Tasks: Ontology Dynamic Object Mapping

**Input**: Design documents from `/specs/014-ontology-dynamic-mapping/`

**Prerequisites**: [plan.md](plan.md), [spec.md](spec.md), [research.md](research.md), [data-model.md](data-model.md), [contracts/](contracts/), [quickstart.md](quickstart.md)

**Tests**: INCLUDED. Constitution Principle IV (Test Discipline & Contract-First) mandates contracts-before-implementation and pytest coverage; each file under `contracts/` enumerates its pytest cases and `quickstart.md` defines executable validation. Test tasks are therefore first-class and written **before** their implementation (TDD).

**Organization**: Tasks are grouped by user story (US1–US4) so each can be implemented, tested, and delivered as an independent increment.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies on incomplete tasks)
- **[Story]**: US1 / US2 / US3 / US4 (Setup, Foundational, Polish carry no story label)
- All paths are repo-relative (backend = `backend/`, frontend = `frontend/`) per [plan.md](plan.md) Structure Decision.

## Path Conventions

- **Backend** (FastAPI): `backend/app/...`, tests `backend/tests/{contract,integration,unit}/`, migrations `backend/app/migrations/versions/`, fixtures `backend/tests/fixtures/`
- **Frontend** (React): `frontend/src/app/(dashboard)/ontology/[projectId]/`, `frontend/src/components/`, `frontend/src/lib/api.ts`

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Test scaffolding shared across multiple stories. This is an existing codebase — no project init.

- [X] T001 [P] Add shared pytest ontology fixture in `backend/tests/fixtures/` extending the test T-Box with a `DrugProduct` subclass, a `Manufacturer` class, `approvalNumber`/`riskLevel` data properties, and a `manufacturedBy` object property (consumed by US1/US3/US4)
- [X] T002 [P] Add reusable extraction test doubles in `backend/tests/fixtures/`: a paginated JSON REST stub (reuse `StubConnector`) and a small in-memory/temp source table (consumed by US1/US2)

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: The declaration model (E6 extension + E6b + CRUD + validation + routes + shared transforms) that **every** user story consumes to declare and interpret bindings.

**⚠️ CRITICAL**: No user story work can begin until this phase is complete.

- [X] T003 Extend `MAPPING_TYPES` allow-list with `db_table`, `api_endpoint`, `doc_pattern` in `backend/app/models/ontology_meta.py` (R1, FR-001)
- [X] T004 Add `OntologyPropertyBinding` (E6b) model — columns per data-model §2, `VersionMixin` + `TimestampMixin`, FK + relationship to `OntologyClassMapping` (CASCADE) — in `backend/app/models/ontology_meta.py` (R2, FR-002/FR-023/FR-004)
- [X] T005 Create Alembic migration `0013_property_binding` in `backend/app/migrations/versions/` creating `ontology_property_binding` + a conditional unique index on `(class_id, source_system)` for source-entity mapping types; **revision id ≤32 chars** (R14, FR-024) — depends on T003, T004
- [X] T006 [P] Add Pydantic schemas `PropertyBindingCreate/Update/Response` and extend `Mapping*` for source-entity types in `backend/app/schemas/ontology.py` (contracts/class-property-binding.md)
- [X] T007 [P] Add shared transform utility `apply_transform(transform_type, config, value)` (`none`/`controlled_vocab`/`pattern`/`cast`, per-value non-fatal failures) reusing E3 `controlled_vocab` + existing `tag_controlled_vocab`, in `backend/app/services/extraction/transforms.py` (R6, FR-003/FR-009)
- [X] T008 Implement class-binding (source-entity) + property-binding CRUD with `(class, source)` uniqueness enforcement in `backend/app/services/ontology_meta_store.py` (contracts/class-property-binding.md, FR-024) — depends on T004, T006
- [X] T009 Implement declaration validation in `backend/app/services/ontology_meta_store.py` — V1 domain gate (via `get_data_properties_by_domain`/`get_object_properties_by_domain`), V2 exists/enabled, V3 object-shape, V4 single-identifier, V5 transform-config; blocking errors vs non-blocking warnings (FR-005) — depends on T008
- [X] T010 Add binding API routes (property-binding CRUD nested under `/mappings/{mid}`, `POST /mappings/{mid}/validate`) with `senior_analyst` role gate + `expected_version` optimistic concurrency in `backend/app/api/ontology.py` (contracts/class-property-binding.md, FR-006) — depends on T008, T009

**Checkpoint**: Declaration model ready — bindings can be declared, validated, and persisted (no credentials stored).

---

## Phase 3: User Story 1 - Declarative property-level binding drives database extraction (Priority: P1) 🎯 MVP

**Goal**: Declare a table→class binding + column→property bindings entirely via the editor/API, run a database extraction job, and get traceable candidates (declared identifiers, transformed values, resolvable provenance, FK relationships) with **zero code changes**.

**Independent Test**: Declare `drug_product→DrugProduct` + `approval_no→approvalNumber` (+ controlled-vocab `risk→riskLevel`, FK `mfr_code→manufacturedBy`), run a DB job against the test source, verify each row → one candidate with the declared/transformed values, an `id`-based alignment, and a resolvable `source_ref` — without touching application code (quickstart Scenario A).

### Tests for User Story 1 ⚠️ (write first, ensure they FAIL)

- [X] T011 [P] [US1] Contract test for class/property binding CRUD + validation (duplicate `(class,source)`→409, raw-DSN `source_system`→422, domain-gate/single-identifier/object-shape→422, role gate→403, cascade delete) in `backend/tests/contract/test_class_property_binding.py` (contracts/class-property-binding.md)
- [X] T012 [P] [US1] Contract test for declaration-driven DB extraction job (happy path, controlled-vocab normalization, drift→partial, identifier alignment, object id_reference, backward-compat) in `backend/tests/contract/test_extraction_job_declarative.py` (contracts/extraction-job.md)
- [X] T013 [P] [US1] Integration test: declare → run DB job → candidates carry declared identifiers, transformed values, and resolvable provenance in `backend/tests/integration/test_declarative_db_extraction.py` (US1 acceptance scenarios 1–6)

### Implementation for User Story 1

- [X] T014 [US1] Add row-reading DB adapter in `backend/app/services/extraction/db_reader.py`: resolve DSN via `os.environ[dsn_ref]`, `SELECT` bound-table columns named by property bindings, yield one `instance` `ExtractionCandidate` per row with transforms applied and `source_ref={system,entity,record}`; drift detection via SQLAlchemy `inspect()` → mark E6 `health="drift"`, warn, skip binding (R5, FR-007/FR-010/FR-020) — depends on T007
- [X] T015 [US1] Implement object-property `id_reference` resolution (link to the aligned individual carrying `target_id_path`; emit a `link` review candidate when none exists) in `backend/app/services/extraction/db_reader.py` (R4, FR-023a) — depends on T014
- [X] T016 [US1] Rewire `run_extraction_pipeline` to resolve the target class binding (E6) + its property bindings and branch `db_table` → row reader, **preserving** the legacy `column_mapping` branch unchanged in `backend/app/services/extraction/pipeline.py` (R8, FR-007/FR-018) — depends on T014
- [X] T017 [US1] Wire identifier-based alignment of produced candidates (use the `is_identifier` binding → existing `align_entity`) in `backend/app/services/extraction/pipeline.py` (FR-004, US1 AS-5) — depends on T016
- [X] T018 [US1] Extend job creation to accept `class_mapping_id` + `source_type="database"` in `backend/app/api/extraction.py` and `backend/app/schemas/extraction.py` (contracts/extraction-job.md, FR-007) — depends on T016
- [X] T019 [US1] Graceful degradation for unset/unreachable DSN (job completes with `degraded_reason`, zero candidates, never crashes) in `backend/app/services/extraction/db_reader.py` + pipeline (R12, FR-019) — depends on T014
- [X] T020 [P] [US1] Frontend property-binding editor (source path, transform config, identifier/label flags, object-resolution mode) + mapping-health badges in `frontend/src/app/(dashboard)/ontology/[projectId]/` and `frontend/src/components/`; extend `frontend/src/lib/api.ts` (SC-001)
- [X] T021 [US1] Backward-compat assertion: a legacy `column_mapping` job runs unchanged (no binding declared) in `backend/tests/integration/test_declarative_db_extraction.py` (FR-018) — depends on T016

**Checkpoint**: US1 is a complete, independently testable DB vertical slice (declare → extract → traceable candidates). **MVP deliverable.**

---

## Phase 4: User Story 2 - Declarative mapping extends to API sources (Priority: P2)

**Goal**: Declare an API endpoint→class binding + response-path→property bindings, pull through a REST/JSON connector (token auth + pagination), and produce the **same** unified candidate output as the DB adapter — including nested-object sub-candidates — with graceful degradation when the intranet source is down.

**Independent Test**: Configure a `rest_api` connector, declare `/api/drugs→DrugProduct` + `$.data[*].name→drugName` (+ nested `$.manufacturer→manufacturedBy`), run an API job, verify candidates match bindings with provenance and a linked nested sub-candidate; then take the connector offline and verify empty-output degradation (quickstart Scenario B).

### Tests for User Story 2 ⚠️ (write first, ensure they FAIL)

- [X] T022 [P] [US2] Contract test for `rest_api` connector config + `/test` (intranet-only `base_url`→422, literal-credential→422, unreachable→`{ok:false}` not exception, pagination, bearer-from-env) in `backend/tests/contract/test_source_connector.py` (contracts/source-connector.md)
- [X] T023 [P] [US2] Integration test: API declare → run → candidates; nested_object sub-candidate; unreachable-intranet degradation in `backend/tests/integration/test_api_extraction_degradation.py` (US2 acceptance scenarios 1–4)

### Implementation for User Story 2

- [X] T024 [US2] Add generic REST/JSON connector (endpoint pull; `bearer`/`api_key` from env; `offset`/`page`/`cursor` pagination; `sync_cursor` state; `last_status`/`last_error` health) in `backend/app/services/integration/rest_connector.py` (R7, FR-008)
- [X] T025 [US2] Add `rest_api` branch to `connector_for` in `backend/app/services/integration/connector_factory.py` (R7) — depends on T024
- [X] T026 [US2] Add API reader (walk each item by property-binding response paths → unified candidates, apply transforms, build `source_ref`) in `backend/app/services/extraction/api_reader.py` (R7, FR-008/FR-010) — depends on T007, T024
- [X] T027 [US2] Implement object-property `nested_object` resolution (recurse embedded structure via `nested_binding_id` → sub-candidate linked by `group_key`; bounded recursion depth) in `backend/app/services/extraction/api_reader.py` (R4, FR-023b, self-referential edge case) — depends on T026
- [X] T028 [US2] Add `api_endpoint` branch (`source_type="api"`) to `run_extraction_pipeline` in `backend/app/services/extraction/pipeline.py` (R8, FR-008/FR-011) — depends on T026, T016
- [X] T029 [US2] Connector config validation — intranet-only `base_url` (FR-022) + reject literal credentials, accept only `*_env` refs (FR-006) — in `backend/app/api/integration.py` and `backend/app/schemas/` (contracts/source-connector.md)
- [X] T030 [US2] API-source graceful degradation (unreachable intranet → empty + `degraded_reason`; air-gap-from-public-cloud never marked degraded) in `backend/app/services/extraction/api_reader.py` + pipeline (R12, FR-019, SC-008) — depends on T026
- [X] T031 [US2] Allow `source_type="api"` through job creation + lifecycle routes in `backend/app/api/extraction.py` (FR-011) — depends on T028
- [X] T032 [P] [US2] Frontend `rest_api` connector config form + API source-type option in the extraction UI in `frontend/src/app/(dashboard)/ontology/[projectId]/` and `frontend/src/components/`; extend `frontend/src/lib/api.ts` (SC-006)

**Checkpoint**: US1 (DB) and US2 (API) both work independently and share one unified candidate format.

---

## Phase 5: User Story 3 - Ontology-aware fact building for reasoning (Priority: P2)

**Goal**: Make `edges_to_facts` consult the ontology (hierarchy, property domain, controlled vocabulary, external-standard alignments) instead of hardcoded class-name string matches, so new subclasses / renamed properties / external alignments are picked up automatically.

**Independent Test**: Feed edges referencing a `DrugProduct` subclass, a data property whose domain excludes the subject, and a class carrying an external alignment; verify hierarchy-based membership, domain-gated assertion, populated `Facts.alignments`, and vocab-normalized scalars — no code changes (quickstart Scenario C).

### Tests for User Story 3 ⚠️ (write first, ensure they FAIL)

- [X] T033 [P] [US3] Unit test for ontology-aware `edges_to_facts` (hierarchy membership, domain gating / no cross-domain leakage, alignment population, controlled-vocab normalization) in `backend/tests/unit/test_ontology_aware_facts.py` (US3 acceptance scenarios 1–4)

### Implementation for User Story 3

- [X] T034 [US3] Add read method `get_class_alignments(class_iri)` returning class-level external alignments (`owl:equivalentClass` + skos exact/close match to non-managed IRIs) from the published World in `backend/app/services/ontology_engine.py` (R13, FR-014)
- [X] T035 [US3] Rewire `edges_to_facts` in `backend/app/services/reasoning/fact_bridge.py`: class membership via `get_subclasses` (replace `"DrugProduct" in obj_class`), domain-gate data values via `get_data_properties_by_domain`, populate `Facts.alignments` via `get_class_alignments`, normalize scalars via the shared transform utility (R10, FR-012/FR-013/FR-014/FR-015) — depends on T034, T007

**Checkpoint**: Facts are ontology-aware; reasoning picks up subclasses and alignments without code changes.

---

## Phase 6: User Story 4 - Hierarchy-aware entity alignment (Priority: P3)

**Goal**: Align extracted entities across the subclass/superclass chain (not only the exact class), detecting cross-level duplicates as merges with a recorded matched level and safe handling of ambiguity.

**Independent Test**: Seed an individual under a subclass, present a candidate typed at the parent class with the same identifier; verify a merge to the existing individual with `matched_level` recorded, and that equal-rank ambiguity is surfaced for review instead of auto-merged (quickstart Scenario D).

### Tests for User Story 4 ⚠️ (write first, ensure they FAIL)

- [X] T036 [P] [US4] Integration test: cross-hierarchy merge detection, precedence (subclass→same→parent), `matched_level`+`method` recorded, equal-rank ambiguity → review (no auto-merge) in `backend/tests/integration/test_hierarchy_alignment.py` (US4 acceptance scenarios 1–3)

### Implementation for User Story 4

- [X] T037 [US4] Add `matched_level` (`subclass`/`same`/`parent`/`none`) to `AlignmentResult` in `backend/app/services/extraction/aligner.py` (R9, FR-017)
- [X] T038 [US4] Extend `align_entity` to search the subclass/superclass chain (`get_subclasses` + `parent_iris` recursion), apply precedence subclass→same→parent, record `matched_level`+`method`, and NOT auto-merge equal-rank ambiguous matches in `backend/app/services/extraction/aligner.py` (R9, FR-016/FR-017) — depends on T037

**Checkpoint**: All four user stories are independently functional.

---

## Phase 7: Polish & Cross-Cutting Concerns

**Purpose**: Hardening, docs, and full-suite validation across stories.

- [X] T039 [P] Add unit tests for the transform utility edge cases (cast failure → issue note, no controlled-vocab match → issue note, pattern mismatch, sibling values still succeed) in `backend/tests/unit/test_transforms.py` (R6, transform-failure edge case)
- [X] T040 [P] Update `docs/本体动态映射能力GAP分析和改进方案.md` (or a new doc) to reference the delivered declaration-driven mapping flow and mark GAP-3 / GAP-4-runtime as deferred
- [X] T041 Run `quickstart.md` end-to-end validation (Scenarios A–D) and confirm SC-001…SC-008 via `uv run pytest` from `backend/`
- [X] T042 [P] Verify full backend suite is green — no regression to the existing structured extraction path (`uv run pytest` from `backend/`, Constitution VI)
- [ ] T043 [P] Add `controlled_vocab` editor to the data-property edit form in `frontend/src/components/ontology/data-property-panel.tsx`: expose `controlled_vocab` as an editable tag-list (add/remove values) on create and edit; replace `RISK_VOCABULARIES` hardcode in `backend/app/services/ontology_meta_store.py` with a dynamic query against existing `OntologyDataProperty.controlled_vocab` entries; retire the `/risk-vocabularies` read-only endpoint in favour of the general CRUD path (FR-009/FR-015, GAP-2 受控词表定义 UI 缺口)

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: no dependencies — start immediately
- **Foundational (Phase 2)**: depends on Setup — **BLOCKS all user stories**
- **User Stories (Phase 3–6)**: all depend on Foundational; then independent of each other (see notes)
- **Polish (Phase 7)**: depends on the targeted stories being complete

### User Story Dependencies

- **US1 (P1)**: after Foundational. Establishes the pipeline binding-consumption seam (T016) and the existing-aligner wiring.
- **US2 (P2)**: after Foundational. Independent of US1 functionally, but **T028 touches the same `pipeline.py` branch dispatcher as T016** — sequence T016 before T028 (or coordinate) to avoid a merge conflict. Otherwise US2 stands alone.
- **US3 (P2)**: after Foundational. Fully independent (fact_bridge + engine read). Reuses the shared transform utility T007.
- **US4 (P3)**: after Foundational. Fully independent (aligner only). US1 uses the pre-existing exact-class aligner; US4 upgrades it without breaking US1.

### Within Each User Story

- Tests (US1: T011–T013, US2: T022–T023, US3: T033, US4: T036) written and FAILING before implementation
- Models → services → endpoints → integration
- Story complete and independently testable before the next priority

### Parallel Opportunities

- Setup: T001, T002 in parallel
- Foundational: T006 and T007 in parallel (different files); T003/T004 same file (sequence); T005 after T003/T004
- Once Foundational done, **US1, US2, US3, US4 can be staffed in parallel** (distinct files, per above caveat on pipeline.py between T016/T028)
- All `[P]` test tasks within a story run in parallel
- Frontend tasks (T020, T032) parallel with their story's backend work

---

## Parallel Example: User Story 1

```bash
# Tests for US1 together (write first, must fail):
Task: "Contract test for class/property binding CRUD in backend/tests/contract/test_class_property_binding.py"
Task: "Contract test for declaration-driven DB extraction in backend/tests/contract/test_extraction_job_declarative.py"
Task: "Integration test for declare→extract→candidates in backend/tests/integration/test_declarative_db_extraction.py"

# Foundational parallelizable pair:
Task: "Pydantic binding schemas in backend/app/schemas/ontology.py"
Task: "Shared transform utility in backend/app/services/extraction/transforms.py"
```

---

## Implementation Strategy

### MVP First (User Story 1 only)

1. Phase 1 Setup → 2. Phase 2 Foundational (declaration model — blocks everything) → 3. Phase 3 US1 → 4. **STOP & VALIDATE**: run quickstart Scenario A, confirm SC-001/002/003/007 → 5. Demo the declare→extract→review loop for a DB source.

### Incremental Delivery

1. Setup + Foundational → declaration model ready
2. US1 → DB extraction MVP (declare → extract → traceable candidates) → demo
3. US2 → API sources (same unified output, degradation) → demo
4. US3 → ontology-aware facts (hierarchy/domain/alignment/vocab) → demo
5. US4 → hierarchy-aware alignment (cross-level dedup) → demo

### Parallel Team Strategy

After Foundational: Dev A → US1, Dev B → US2 (coordinate the `pipeline.py` dispatcher edit), Dev C → US3, Dev D → US4. Stories integrate independently.

---

## Notes

- `[P]` = different files, no incomplete-task dependencies.
- `[Story]` label maps each task to its user story for traceability; Setup/Foundational/Polish carry none.
- **Credential safety**: no task persists a DSN/API key — only env-var names / connector refs (FR-006); enforced in T010/T029.
- **Ontology authority**: all adapters/engine reads are read-only; candidates stay `review_status="pending"` (Constitution II) — never auto-published.
- Verify each story's tests fail before implementing; commit after each task or logical group.
- Stop at any checkpoint to validate a story independently.
