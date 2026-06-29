# Tasks: Word 表格实体识别优化与文档-代码对齐

**Input**: Design documents from `/specs/009-word-table-ner-optimize/`

**Prerequisites**: plan.md (required), spec.md (required), research.md, data-model.md, quickstart.md

**Tests**: Included — Constitution IV requires backend critical path tests; quickstart.md defines 6 validation scenarios.

**Organization**: Tasks grouped by user story (US1 P1, US2 P1, US3 P2) for independent implementation and testing.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story (US1, US2, US3)
- Exact file paths included

## Phase 1: Setup

**Purpose**: Prepare test infrastructure and verify baseline

- [X] T001 Create Word document test fixtures for table NER scenarios (standard 5×4 table with header, merged cells, nested table, multi-row header, empty cells, no-header table) in backend/tests/test_extraction/fixtures/
- [X] T002 Run existing test suite to verify baseline before changes: `uv run pytest backend/tests/test_extraction/ -v`

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Helper functions used by US1 and US2 table processing

**⚠️ CRITICAL**: No user story work can begin until these helpers exist

- [X] T003 Implement `_detect_header_rows(table) -> int` helper that returns header row count (default 1 for simple tables; multi-row detection via gridSpan added in US2) in backend/app/services/extraction/document_annotator.py
- [X] T004 [P] Implement `_find_table_caption(body_element, table_element) -> Optional[str]` helper that returns the paragraph text immediately preceding a table element (e.g. "表 3：原料药规格") by walking the document body's XML siblings in backend/app/services/extraction/document_annotator.py
- [X] T005 [P] Implement `_is_vmerge_continue(cell) -> bool` helper that checks `cell._tc.tcPr` for `<w:vMerge>` without `val="restart"` attribute (continuation marker) in backend/app/services/extraction/document_annotator.py

**Checkpoint**: Helpers ready — user story implementation can begin

---

## Phase 3: User Story 1 — 表格内实体准确识别 (Priority: P1) 🎯 MVP

**Goal**: Refactor table processing from per-cell to row-level context concatenation so GLiNER leverages cross-column context. Header rows skipped, table captions used as semantic prefix, span offsets corrected back to cell coordinates.

**Independent Test**: Upload a Word document with a multi-column specification table; verify entities are extracted from data rows (including short 2-3 character cells), header row produces no entities, and table caption appears as context prefix.

### Tests for User Story 1

> **NOTE: Write these tests FIRST, ensure they FAIL before implementation**

- [X] T006 [P] [US1] Write test `test_row_level_concatenation` verifying data row cells are concatenated as `"hdr1：val1 | hdr2：val2 | ..."` format, producing a single segment per row instead of per-cell segments in backend/tests/test_extraction/test_word_formatting.py
- [X] T007 [P] [US1] Write test `test_header_row_skipped` verifying the first row (header) of a standard table produces zero NER spans and is used only as column name prefix material in backend/tests/test_extraction/test_word_formatting.py
- [X] T008 [P] [US1] Write test `test_table_caption_prefix` verifying a paragraph immediately before a table (e.g. "表 3：原料药规格") is prepended to each data row segment text in backend/tests/test_extraction/test_word_formatting.py
- [X] T009 [P] [US1] Write test `test_span_offset_correction` verifying NER span `[start, end)` coordinates are mapped back to cell-relative offsets (not row-level global offsets) using the cell offset map in backend/tests/test_extraction/test_word_formatting.py
- [X] T010 [P] [US1] Write test `test_empty_cell_handling` verifying empty or whitespace-only cells are skipped in row segment assembly without errors and do not produce NER spans in backend/tests/test_extraction/test_word_formatting.py

### Implementation for User Story 1

- [X] T011 [US1] Implement `_build_row_segment(row, headers, caption) -> tuple[str, list[tuple[int,int,int]]]` that concatenates non-empty row cells with header prefixes (`hdr：val`) and ` | ` separator, returning the segment text and cell offset map `[(cell_start, cell_end, col_idx), ...]` in backend/app/services/extraction/document_annotator.py
- [X] T012 [US1] Implement span offset correction logic: given NER spans on a row-level segment and the cell offset map, map each span's `[start, end)` to the originating cell index and cell-relative coordinates for tiptap rendering in backend/app/services/extraction/document_annotator.py
- [X] T013 [US1] Refactor `annotate_word` table processing loop to replace per-cell segment collection with row-level segments: use `_detect_header_rows` to identify header rows, `_find_table_caption` for caption prefix, `_build_row_segment` for data row assembly; skip header rows from NER; apply span offset correction after NER in backend/app/services/extraction/document_annotator.py
- [X] T014 [US1] Ensure `segment_texts` passed to `_type_and_filter_spans` matches the row-level texts used in `_extract_spans_batch`, so `_span_with_context` window=40 covers cross-column context (research R5) in backend/app/services/extraction/document_annotator.py
- [X] T015 [US1] Run US1 tests and verify all pass: `uv run pytest backend/tests/test_extraction/test_word_formatting.py -k "row_level or header_row_skipped or caption_prefix or span_offset or empty_cell" -v`

**Checkpoint**: Row-level concatenation working — table entities extractable with cross-column context. US1 independently testable.

---

## Phase 4: User Story 2 — 合并单元格与嵌套表格正确处理 (Priority: P1)

**Goal**: Handle complex table structures — multi-row headers (gridSpan detection), vertical merge dedup, nested table recursion — so no content is duplicated or lost.

**Independent Test**: Upload a Word document with vertically merged cells and a nested table; verify merged cell text appears only once and nested table content is extracted.

### Tests for User Story 2

> **NOTE: Write these tests FIRST, ensure they FAIL before implementation**

- [X] T016 [P] [US2] Write test `test_vmerge_dedup` verifying vertically merged cell text is only processed in the first (restart) row; continuation rows' merged cells are skipped while non-merged cells in the same row are processed normally in backend/tests/test_extraction/test_word_formatting.py
- [X] T017 [P] [US2] Write test `test_multi_row_header` verifying tables whose first row contains gridSpan > 1 cells detect 2+ header rows, all header rows are skipped from NER, and the last header row provides column name prefixes for data rows in backend/tests/test_extraction/test_word_formatting.py
- [X] T018 [P] [US2] Write test `test_nested_table_recursion` verifying a cell containing an embedded `<w:tbl>` table has its nested content recursively processed and entities appear in NER results in backend/tests/test_extraction/test_word_formatting.py
- [X] T019 [P] [US2] Write test `test_nesting_depth_limit` verifying recursion stops at `max_nesting_depth=5` without error for deeply nested tables in backend/tests/test_extraction/test_word_formatting.py

### Implementation for User Story 2

- [X] T020 [US2] Integrate `_is_vmerge_continue` into `_build_row_segment`: when building a row segment, skip cells where vMerge indicates continuation so their text is not duplicated in the row-level segment in backend/app/services/extraction/document_annotator.py
- [X] T021 [US2] Extend `_detect_header_rows` to handle multi-row headers: when first row contains cells with `<w:gridSpan>` val > 1, continue scanning subsequent rows until a row has no horizontal merge features; return total header row count in backend/app/services/extraction/document_annotator.py
- [X] T022 [US2] Implement `_process_nested_tables(cell, headers_context, caption, depth, max_depth=5) -> tuple[list[str], list[tuple]]` that discovers `<w:tbl>` elements inside a cell via `cell._tc.findall(qn('w:tbl'))`, constructs `Table` objects, and recursively applies the same row-level processing logic in backend/app/services/extraction/document_annotator.py
- [X] T023 [US2] Integrate nested table processing into `annotate_word` table loop: after building each cell's contribution to the row segment, check for nested tables and append their segments and element metadata to `all_texts` and `all_elements` in backend/app/services/extraction/document_annotator.py
- [X] T024 [US2] Run US2 tests and verify all pass: `uv run pytest backend/tests/test_extraction/test_word_formatting.py -k "vmerge or multi_row_header or nested_table or nesting_depth" -v`

**Checkpoint**: Complex table structures handled — merged cells deduplicated, nested tables recursively processed. US2 independently testable.

---

## Phase 5: User Story 3 — 技术文档与代码实现一致 (Priority: P2)

**Goal**: Fix all GAP analysis items (B1-B3 偏差, C1-C5 未记录能力) in the technical specification document so documentation matches current code implementation.

**Independent Test**: Read through the technical spec document and verify each GAP item against current code: no misleading examples, no stale line references, all implemented features documented.

### Implementation for User Story 3

- [X] T025 [P] [US3] Fix B1: update §4.2 seed label examples to remove "制剂剂型" (phase-3 attribute label, not phase-1 class label) and add annotation explaining the three-stage label pipeline (stage 1: class labels, stage 2: embedding match, stage 3: property labels) in docs/Word-PDF文档实体识别优化技术方案.md
- [X] T026 [P] [US3] Fix B2: replace all hardcoded line number references (e.g. "L462-478", "L320") with function name anchors (e.g. "`annotate_word` → `_build_row_segment`") throughout docs/Word-PDF文档实体识别优化技术方案.md
- [X] T027 [P] [US3] Fix B3: update reference to `data_property_labels` docstring from "第三源" to "阶段三属性标签查询" to match actual code semantics in docs/Word-PDF文档实体识别优化技术方案.md
- [X] T028 [P] [US3] Add C1+C2: create new §11 documenting annotation task lifecycle control (pause via `cancel_annotation`/resume via re-run, checkpoint serialization of intermediate state) and real-time sub-stage progress SSE push (`/ws/annotation-progress`) in docs/Word-PDF文档实体识别优化技术方案.md
- [X] T029 [P] [US3] Add C3: append subsection to §4 documenting the NER triple → ExtractionCandidate review queue mechanism (how recognized spans become review candidates) in docs/Word-PDF文档实体识别优化技术方案.md
- [X] T030 [P] [US3] Add C5: update §6.3 with complete 3-tier font-size → heading level thresholds (≥20pt→h1, ≥16pt→h2, ≥14pt→h3) replacing the incomplete description in docs/Word-PDF文档实体识别优化技术方案.md

**Checkpoint**: All GAP items (B1-B3, C1-C3, C5) resolved — documentation accurately reflects current code.

---

## Phase 6: Polish & Cross-Cutting Concerns

**Purpose**: Full regression verification and end-to-end validation

- [X] T031 Run full existing test suite to verify zero regression on paragraph processing: `uv run pytest backend/tests/test_extraction/test_document_annotator.py -v`
- [X] T032 Run complete new table NER test suite: `uv run pytest backend/tests/test_extraction/test_word_formatting.py -v`
- [X] T033 Run quickstart.md validation scenarios 1-5 (row concatenation, multi-row header, vMerge dedup, nested table recursion, paragraph regression)
- [X] T034 Verify spec edge cases not covered by named tests: no-header tables (all rows = data), multi-paragraph cells (newline-separated content within cell), vMerge spanning entire column, tables with 0 data rows after header detection

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies — start immediately
- **Foundational (Phase 2)**: Depends on Setup (T001 fixtures needed for development)
- **US1 (Phase 3)**: Depends on Foundational (T003 `_detect_header_rows`, T004 `_find_table_caption`)
- **US2 (Phase 4)**: Depends on US1 (extends the row-level processing loop established by T013)
- **US3 (Phase 5)**: No code dependencies — can start after Setup; recommended after US1/US2 for accurate code references
- **Polish (Phase 6)**: Depends on US1 + US2 completion

### User Story Dependencies

- **US1 (P1)**: Depends on Foundational (Phase 2) — core MVP deliverable
- **US2 (P1)**: Depends on US1 (Phase 3) — extends row-level loop with merge/nesting handling
- **US3 (P2)**: Independent of US1/US2 at code level; recommended after for accurate references

### Within Each User Story

- Tests written FIRST and verified to FAIL before implementation
- Helper functions before integration
- Core logic before edge cases
- Story-specific test verification as final task

### Parallel Opportunities

- **Phase 2**: T003, T004, T005 can run in parallel (independent helper functions)
- **Phase 3 Tests**: T006-T010 are independent test functions — all [P]
- **Phase 4 Tests**: T016-T019 are independent test functions — all [P]
- **Phase 5**: T025-T030 are independent document sections — all [P]
- **US3 can overlap with US1/US2**: Documentation tasks touch only `docs/`, not code files

---

## Parallel Example: User Story 1

```bash
# Launch all US1 tests together (write FIRST, verify FAIL):
Task T006: "test_row_level_concatenation" in test_word_formatting.py
Task T007: "test_header_row_skipped" in test_word_formatting.py
Task T008: "test_table_caption_prefix" in test_word_formatting.py
Task T009: "test_span_offset_correction" in test_word_formatting.py
Task T010: "test_empty_cell_handling" in test_word_formatting.py

# Then implement sequentially:
Task T011: _build_row_segment helper (no prior dependency)
Task T012: span offset correction logic (no prior dependency)
Task T013: refactor annotate_word loop (depends on T011, T012)
Task T014: _type_and_filter_spans alignment (depends on T013)
```

## Parallel Example: User Story 3

```bash
# All documentation tasks target different sections — full parallel:
Task T025: Fix B1 in §4.2
Task T026: Fix B2 line references throughout
Task T027: Fix B3 docstring reference
Task T028: Add C1+C2 as new §11
Task T029: Add C3 to §4
Task T030: Add C5 to §6.3
```

---

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Complete Phase 1: Setup (fixtures + baseline verification)
2. Complete Phase 2: Foundational helpers
3. Complete Phase 3: User Story 1 (row-level concatenation + offset correction)
4. **STOP and VALIDATE**: Run US1 tests + regression suite
5. Table entity recall should already be significantly improved

### Incremental Delivery

1. Setup + Foundational → Helper infrastructure ready
2. Add US1 → Row-level concatenation working → Test independently → **MVP!**
3. Add US2 → Complex structures handled → Test independently
4. Add US3 → Documentation aligned → Verify all GAP items
5. Polish → Full regression + edge case verification

### Single Developer Execution Order

Phase 1 → Phase 2 → Phase 3 (US1) → Phase 4 (US2) → Phase 5 (US3) → Phase 6

---

## Notes

- All code changes in ONE file: `backend/app/services/extraction/document_annotator.py`
- All new tests in ONE file: `backend/tests/test_extraction/test_word_formatting.py`
- All doc updates in ONE file: `docs/Word-PDF文档实体识别优化技术方案.md`
- No DB migrations, no frontend changes, no new dependencies
- Run tests via `uv run pytest` (bare `python` is anaconda, lacks deps)
- Offline-only: `local_files_only=True`, `HF_HUB_OFFLINE=1`
- CPU inference offloaded via `asyncio.to_thread` — no GPU required

---

## Phase 7: Convergence

**Purpose**: Close gaps identified by convergence assessment against spec.md FR/AC requirements

- [X] T035 [US2] Implement nested table structural recursion in `annotate_word`: for each data row cell, discover `<w:tbl>` elements via `tc_elem.findall(qn('w:tbl'))`, apply `_detect_header_rows` + `_build_row_segment` to each inner table's rows, and append resulting segments to `all_texts` / `seg_cell_offsets`; cap recursion at depth 5 per FR-005 (partial)
- [X] T036 [US2] Update `test_nested_table_recursion` to call `annotate_word` end-to-end on a document with a nested table and verify that nested table text appears in the tiptap output with entity-annotation marks (or at minimum as text content in a tableCell node) per US2/AC2 (partial)
- [X] T037 Fix `_tc_text` to insert a space between paragraphs within a cell: replace bare `iter(qn("w:t"))` join with paragraph-aware extraction that adds `" "` between distinct `<w:p>` elements, matching python-docx `cell.text` behavior per edge case "multi-paragraph cells" (partial)
- [X] T038 Run full test suite to verify convergence fixes introduce no regression: `uv run pytest backend/tests/test_extraction/ -v`
