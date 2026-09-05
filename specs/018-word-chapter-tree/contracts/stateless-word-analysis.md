# Contract: Stateless Word Analysis API

## Endpoint

```http
POST /api/document-analysis/word
Content-Type: multipart/form-data

file=<one .doc or .docx file>
```

The endpoint is an immediate tool operation. It has no ExtractionJob, database session, job status, cache key, SSE progress, graph classification, NER, triple, relationship, or candidate side effect.

## Processing

1. Stream the upload into a request-scoped `TemporaryDirectory`.
2. Convert `.doc` with the existing local `ensure_docx_async`; use `.docx` unchanged.
3. Call `parse_docx_structure()` exactly once.
4. Call `annotate_word(engine=None, structure_only=True, rich_style=True, structure=structure)`.
5. Call optional `summarize_word_tree()`; unexpected failure applies deterministic fallback without failing structure analysis.
6. Serialize the response and delete all temporary source/conversion files when the request exits.

## Success response

```json
{
  "filename": "report.docx",
  "content": {"type": "doc", "content": []},
  "warnings": [],
  "section_tree": {"node_id": "document", "children": []},
  "pagination": {
    "mode": "single_page_fallback",
    "physical_page_numbers_available": false,
    "is_estimated": true,
    "warning": "Word 未保存可靠分页标记；章节页为逻辑片段，不代表物理页码。"
  },
  "parser_version": 2,
  "summary_prompt_version": "word-tree-summary-v1"
}
```

`section_tree` uses the ChapterNode/PageNode schema in `annotated-document.md`, but the response deliberately omits `job_id`, `status`, `source_type`, `triples`, `relationships`, and `doc_class`.

## Errors and degradation

- Non-Word or empty upload: `422`.
- `.doc` conversion unavailable/fails: `422` with a user-readable conversion detail.
- Invalid/corrupt Word: `422`.
- Summary disabled: `200` with `disabled/none` metadata.
- Summary call failure: `200` with per-node extractive fallback where possible.
