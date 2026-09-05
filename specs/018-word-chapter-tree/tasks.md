# Tasks: Word 章节树与分层摘要

**Input**: `/specs/018-word-chapter-tree/` design documents

**Tests**: Required by the feature specification and Constitution.

## Phase 1: Setup and contracts

- [x] T001 Confirm SpecKit artifacts and requirements checklist under `specs/018-word-chapter-tree/`
- [x] T002 [P] Record Docling deferred decision and admission gates in `specs/018-word-chapter-tree/research.md`
- [x] T003 [P] Lock annotated-document, stateless upload, summary batch and UI contracts in `specs/018-word-chapter-tree/contracts/`

## Phase 2: Foundational canonical Word IR

- [x] T004 [US1] Add WordBlock, source range, pagination, ChapterNode, PageNode and metadata models in `backend/app/services/extraction/docx_structure.py`
- [x] T005 [US1] Traverse Word body once to preserve paragraph/table/page-event order and stable IDs in `backend/app/services/extraction/docx_structure.py`
- [x] T006 [US1] Build deterministic explicit chapter tree and leaf page nodes in `backend/app/services/extraction/docx_structure.py`
- [x] T007 [US1] Add tree and page unit tests in `backend/tests/test_extraction/test_word_section_tree.py` and `test_page_breaks.py`

## Phase 3: Legacy and provenance compatibility

- [x] T008 [US3] Adapt `parse_word()` to canonical structure while preserving old return order and mapping in `backend/app/services/extraction/parser.py`
- [x] T009 [US3] Allow annotator and relation extractor to consume an already parsed structure in `backend/app/services/extraction/`
- [x] T010 [US3] Emit stable block/source attributes in Tiptap JSON and retain old source paragraph attributes in `backend/app/services/extraction/document_annotator.py`
- [x] T011 [US3] Extend legacy, annotation and no-reparse regression tests under `backend/tests/`

## Phase 4: Layered summaries and API

- [x] T012 [US2] Add Word summary configuration with offline-safe defaults in `backend/app/config.py`
- [x] T013 [US2] Add optional per-request timeout support in `backend/app/services/llm/local_client.py`
- [x] T014 [US2] Implement batched bottom-up summaries and deterministic fallback in `backend/app/services/extraction/word_tree_summarizer.py`
- [x] T015 [US2] Add summary ordering, validation and degradation tests in `backend/tests/test_extraction/test_word_tree_summarizer.py`
- [x] T016 [US2] Integrate one-time parse, summary, response fields and cache versioning in `backend/app/api/extraction.py`
- [x] T017 [US2] Add annotated-document API contract/regression tests under `backend/tests/`

## Phase 5: Document analysis UI

- [x] T018 [US4] Add chapter tree, page, pagination and source range types to `frontend/src/lib/api.ts`
- [x] T019 [US4] Export/reuse the tree item type and support document tree selection in `frontend/src/components/tree-view.tsx`
- [x] T020 [US4] Add stable source attributes and activeLocation DOM highlighting to `frontend/src/components/extraction/word-viewer.tsx`
- [x] T021 [US4] Implement URL-controlled Analysis tabs with Suspense, keep only `tab=document` in URL and preserve the default tab in `frontend/src/app/(dashboard)/analysis/`
- [x] T022 [US4] Implement stateless upload, document toolbar, chapter tree, original pane and metadata panel under `frontend/src/components/analysis/`
- [x] T023 [US4] Verify duplicate-title location, local selection and no-reupload/no-setContent behavior with available frontend test/build tooling

## Phase 5b: Stateless document-tool correction

- [x] T027 [US4] Add `POST /api/document-analysis/word` using request-scoped temporary files and no DB/job/cache dependencies in `backend/app/api/document_analysis.py`
- [x] T028 [US4] Generate linked Tiptap with `engine=None`, `structure_only=True`, and the same canonical structure; add safe summary fallback
- [x] T029 [US4] Add API tests for temporary cleanup, no ExtractionJob creation, no graph fields, validation, and summary degradation
- [x] T030 [US4] Replace ExtractionJob/annotated-document queries with direct upload and component-local state in `document-analysis-panel.tsx`
- [x] T031 [US4] Remove legacy `job_id/node_id` URL state and document the stateless boundary

## Phase 6: Polish and validation

- [x] T024 Run focused and full backend extraction regression suites
- [ ] T025 Run frontend lint and production build
- [x] T026 Execute `specs/018-word-chapter-tree/quickstart.md`, update documentation/status and verify no Docling dependency was added

## Validation record (2026-08-25)

- Stateless API contract: 5 passed; combined Word tree/API focused suite: 23 passed; full `tests/test_extraction`: 440 passed.
- Changed backend files: Ruff passed.
- Changed frontend files: targeted ESLint passed and TypeScript reported no errors in the feature files; production compilation completed successfully.
- Repository-wide frontend build reaches TypeScript then remains blocked by pre-existing `Promise<{id}>` versus `Promise<void>` errors in mock team pages; T025 stays open.
- Dependency manifests and runtime source contain no Docling package/import/path.

## Dependencies and execution order

- Phase 2 blocks all later phases.
- Phase 3 establishes provenance before API/UI linkage.
- Phase 4 can start after Phase 2, but API integration waits for Phase 3.
- Phase 5 depends on the stateless upload contract and provenance attributes; annotated-document remains an independent compatibility consumer.
- Tests for each phase are written with or before its implementation and must pass before proceeding.
