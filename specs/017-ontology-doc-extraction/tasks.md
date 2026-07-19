# Tasks: Ontology-Guided Word Extraction

**Input**: Design documents from /specs/017-ontology-doc-extraction/

**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/, quickstart.md

**Tests**: Required by the specification and Constitution IV. Tests are written before the corresponding implementation and must fail for the intended reason.

## Phase 1: Setup

- [x] T001 Verify the 017 branch, Spec Kit feature state, and baseline git status without staging CLAUDE.md or unrelated docs
- [x] T002 [P] Add focused test module skeletons at backend/tests/test_extraction/test_docx_structure.py and backend/tests/test_extraction/test_document_profile.py
- [x] T003 [P] Add integration test skeleton at backend/tests/integration/test_declarative_doc_extraction.py

---

## Phase 2: Foundational Profile and IR Contracts

- [x] T004 [P] Define failing shared heading/table IR tests in backend/tests/test_extraction/test_docx_structure.py for TOC, outline, Normal+bold numbering, title fallback, and multi-row headers
- [x] T005 [P] Define failing Profile parser/selector/transform/provenance tests in backend/tests/test_extraction/test_document_profile.py
- [x] T006 Define runtime Word IR provenance fields and shared heading inference in backend/app/services/extraction/docx_structure.py
- [x] T007 Reuse shared heading inference in backend/app/services/extraction/document_annotator.py without changing tiptap output
- [x] T008 Implement constrained Profile data classes, parsing, binding compilation, selectors, transforms, and DocumentReadResult in backend/app/services/extraction/document_profile.py
- [x] T009 Extend backend/app/services/ontology_engine.py to read extractionProfile and property datatype/aliases needed by binding compilation

**Checkpoint**: Shared Word IR and generic interpreter work independently of concrete ontology range classes.

---

## Phase 3: User Story 1 - Real Word Structure (Priority: P1)

**Goal**: Classification, preview, and relationship extraction see the same enterprise Word sections and full-document CMC signals.

**Independent Test**: test_docx_structure plus classifier cases prove TOC/bold/numbered sections and late signals are visible.

- [x] T010 [US1] Make parse_docx_structure use shared heading inference, normalized tables, source_filename fallback, and block provenance in backend/app/services/extraction/docx_structure.py
- [x] T011 [US1] Change full-signal classification and add explicit-type classification helper in backend/app/services/extraction/document_classifier.py
- [x] T012 [US1] Thread original filename and explicit doc_class_iri through annotation and relationship extraction in backend/app/api/extraction.py and backend/app/services/extraction/relation_extractor.py
- [x] T013 [US1] Run and pass Word IR/classifier tests, then update the annotation cache version in backend/app/api/extraction.py

**Checkpoint**: A HRS-1597-style structure is classified as CMCReport even when key signals occur after paragraph 12.

---

## Phase 4: User Story 2 - Declarative DrugProduct (Priority: P1)

**Goal**: DrugProduct identity and basic/toxicity properties come from a Profile, not a class-specific finder.

**Independent Test**: A Profile-only edge extracts HRS-1597, dosage form, appearance, and boolean variants with provenance.

- [x] T014 [P] [US2] Add failing DrugProduct Profile and no-class-dispatch tests to backend/tests/test_extraction/test_relation_extraction.py
- [x] T015 [P] [US2] Add missing DrugProduct data-property triples and stable labels to ontology/slpra/slpra-drug.ttl
- [x] T016 [US2] Add extractionProfile annotation vocabulary and DrugProduct Profile to ontology/slpra/slpra-integration.ttl
- [x] T017 [US2] Add the generic Profile strategy adapter to backend/app/services/extraction/relation_extractor.py
- [x] T018 [US2] Remove DrugProduct from class-specific method dispatch and make program-code identity/profile aliases authoritative
- [x] T019 [US2] Run DrugProduct, relation-schema, and TTL-load regression tests

**Checkpoint**: Adding a new DrugProduct field alias changes only extraction metadata/test data.

---

## Phase 5: User Story 3 - OR Headers and Wide Toxicology (Priority: P1)

**Goal**: Equipment, residue, narrow toxicity, and wide toxicity templates execute through generic selectors without row mixing.

**Independent Test**: Old/new equipment headers, three residue names, two wide toxicology rows, and false PDE text all pass.

- [x] T020 [P] [US3] Add failing equipment/residue alias and wide toxicology pairing tests in backend/tests/test_extraction/test_document_profile.py and backend/tests/test_extraction/test_relation_extraction.py
- [x] T021 [P] [US3] Add missing residue domains and wide toxicology row properties to ontology/slpra/slpra-drug-development.ttl
- [x] T022 [P] [US3] Add stable Chinese labels for equipment identifiers/names/specification in ontology/slpra/slpra-equipment.ttl
- [x] T023 [US3] Add Equipment, Residue, and SharedLineAssessmentData Profiles with all-of/any-of selectors to ontology/slpra/slpra-integration.ttl
- [x] T024 [US3] Migrate those ranges to the Profile strategy and isolate remaining composite overrides in backend/app/services/extraction/relation_extractor.py
- [x] T025 [US3] Enforce row-scoped composite identity and strict PDE numeric/unit rejection in backend/app/services/extraction/document_profile.py
- [x] T026 [US3] Run alias, toxicology, relation extraction, conflict, and ontology schema regression tests

**Checkpoint**: Two toxicology rows retain independent value/source sets; no PDE definition becomes a valid property.

---

## Phase 6: User Story 4 - E6/E6b doc_pattern Runtime (Priority: P2)

**Goal**: A declared doc_pattern mapping accepts a DOCX and produces normal review candidates.

**Independent Test**: Declaration → upload → pipeline → candidates/drift/degraded semantics pass end to end.

- [x] T027 [P] [US4] Add failing multipart contract tests for missing/valid doc_pattern uploads in backend/tests/contract/test_extraction_job_declarative.py
- [x] T028 [P] [US4] Implement failing integration scenarios in backend/tests/integration/test_declarative_doc_extraction.py
- [x] T029 [US4] Persist doc_pattern uploads and pass the document path to background execution in backend/app/api/extraction.py
- [x] T030 [US4] Implement read_doc_pattern adapter using E6 target and E6b bindings in backend/app/services/extraction/document_profile.py
- [x] T031 [US4] Dispatch doc_pattern in backend/app/services/extraction/pipeline.py and preserve drift/degraded/candidate alignment semantics
- [x] T032 [US4] Run doc_pattern contract/integration tests and existing DB/API declaration regression suites

**Checkpoint**: doc_pattern no longer returns “reader pending”; DB/API declaration jobs remain unchanged.

---

## Phase 7: Polish and Cross-Cutting Validation

- [x] T033 [P] Update source comments and analysis implementation status where needed without weakening the approved scope
- [x] T034 Run all focused and regression pytest commands from specs/017-ontology-doc-extraction/quickstart.md
- [x] T035 Run ruff on changed Python files, parse all authoritative TTL modules, and run git diff --check
- [x] T036 Review git diff for no new concrete-class dispatch, no accidental unrelated-file changes, and complete provenance
- [x] T037 Mark completed tasks and record validation evidence in specs/017-ontology-doc-extraction/quickstart.md

## Dependencies and Execution Order

- Phase 2 blocks all stories.
- US1 blocks relationship-level US2/US3 because sections/tables must be visible.
- US2 and US3 share the interpreter but are otherwise independently testable.
- US4 depends on the same interpreter but not on concrete CMC Profiles.
- Polish depends on all selected stories.

## Parallel Opportunities

- T004 and T005 touch different test files.
- T014/T015 and T020/T021/T022 can proceed in separate files after the interpreter contract is stable.
- T027 and T028 touch contract and integration tests separately.
- No sub-agent delegation is used; repository instructions require local execution.

## Implementation Strategy

1. Prove shared structure with failing tests.
2. Implement the generic interpreter without concrete class branches.
3. Migrate DrugProduct as the minimal vertical slice.
4. Add table aliases and toxicology profiles.
5. Reuse the same interpreter for E6/E6b doc_pattern.
6. Validate compatibility and ontology fidelity before handoff.
