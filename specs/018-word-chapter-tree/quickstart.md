# Quickstart: Word 章节树与分层摘要

## Prerequisites

- Backend Python 3.11+ environment installed from `backend/pyproject.toml`.
- Frontend dependencies installed.
- Optional local OpenAI-compatible endpoint only for the LLM-success scenario; all deterministic scenarios work without it.

Docling is intentionally not installed.

## 1. Deterministic backend validation

```bash
cd backend
uv run pytest tests/test_extraction/test_docx_structure.py tests/test_extraction/test_word_section_tree.py tests/test_extraction/test_page_breaks.py tests/test_extraction/test_parse_word_mapping.py -q
```

Expected:

- Heading level transitions and jumps produce the expected tree.
- Repeated headings have distinct `section:{heading_index}` IDs.
- Page fixtures report explicit/rendered/fallback modes correctly.
- Legacy `parse_word()` output remains compatible.

## 2. Summary and API validation

```bash
cd backend
uv run pytest tests/test_extraction/test_word_tree_summarizer.py tests/test_api/test_word_tree_annotation.py tests/test_api/test_document_analysis.py -q
```

Expected:

- Page batches precede deepest-to-root chapter batches.
- Disabled and failing local LLM paths still return a complete tree.
- Annotated-document includes optional `section_tree` and `pagination`.
- Stateless upload returns tree/original/pagination without creating ExtractionJob or graph fields and deletes temporary files.
- Summaries do not appear in triples or relationships.

## 3. Backend regression

```bash
cd backend
uv run pytest tests/test_extraction -q
```

All existing Word formatting, prose, classification, extraction and pagination tests must pass.

## 4. Frontend validation

```bash
cd frontend
npm run lint
npm run build
```

Then run the application and open:

```text
/analysis?tab=document
```

Verify:

1. Default `/analysis` remains on 推理.
2. 文档分析先显示 `.doc/.docx` 拖放/选择入口；选择后自动分析并显示桌面三栏。
3. Selecting two identically titled chapters locates two different source ranges.
4. Selecting a page only updates local metadata and highlights its blocks; URL remains `tab=document`.
5. Repeated node switching produces no new upload request and does not rebuild editor content.
6. When physical pages are unavailable, the UI says `章节页 N` and shows the pagination warning.
7. Refreshing the page clears the file and result; no ExtractionJob or annotated cache is created.

## 5. Offline/degradation validation

Run the backend without an LLM endpoint and with summary disabled. Word parsing, annotated-document, and stateless upload requests must succeed with `summary_status=disabled`. Then enable summary while keeping the endpoint unavailable; the request must still succeed and use `extractive_fallback` only for summary metadata.
