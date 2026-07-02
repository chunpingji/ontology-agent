# Contract: Sample Document Parse API

## `POST /api/ast-templates/parse-sample`

Synchronous, `multipart/form-data` upload. Role-gated to `senior_analyst`. **Not** gated by `llm_suggest_slots_enabled` — this is a deterministic offline parse, so users can preview a sample's structure even with the LLM disabled.

### Request (`multipart/form-data`)
| Field | Type | Notes |
|-------|------|-------|
| `file` | binary | A `.docx` sample document. Non-`.docx` filename → `422`. |

The upload is **transient**: written to a tempfile, parsed, unlinked immediately. This endpoint persists nothing server-side (persistence of `sample_content_json` happens later, on template create — see [data-model.md](../data-model.md) §6).

### Response 200
```json
{
  "content_json": { "type": "doc", "content": [ /* tiptap / ProseMirror nodes */ ] },
  "plain_text": "## 评估对象\n本品为……注射液，剂型为注射剂。\n[表格]\n设备编号 | CT64201"
}
```
- `content_json` — **structure-faithful** tiptap (headings, paragraphs, lists, tables) from `parse_word_to_tiptap(path)` = `annotate_word(path, engine=None, structure_only=True)[0]`. Contains **zero `entity-annotation` marks** (NER is skipped in `structure_only` mode); inline formatting marks (bold/italic/underline/strike) may survive.
- `plain_text` — `tiptap_to_text(content_json)`: the flattened form, used by the frontend only for the char-count hint and persisted as legacy `sample_text` on create.

### Behavior
1. Reject a non-`.docx` filename → `422` "仅支持 .docx 文件" (checked after the role dependency).
2. Write the upload to a tempfile; parse with `parse_word_to_tiptap` (`engine=None` is safe — no NER); derive `plain_text` via `tiptap_to_text`; unlink the tempfile in a `finally`.
3. Empty extracted text → `422` "无法从文档中提取文本内容".

### Errors
| Status | Condition |
|--------|-----------|
| 403 | Missing/invalid role (not `senior_analyst`) |
| 422 | Non-`.docx` upload, or no extractable text |

### Deterministic-safety invariant
Pure offline document parsing — no LLM call, no ontology write, no evaluation. Independent of every `llm_*` flag (the ontology World is never touched). This is why it is role-gated but **not** flag-gated: structure preview must work in a fully offline / LLM-off deployment.

### Downstream
`content_json` is posted back to `POST /suggest-slots` as `sample_content_json` (a structure-faithful round-trip — no flatten-to-text) and rendered in the drawer's `WordViewer` preview, so suggested slots link to the source document by structural anchor (`source_ref`) rather than a char offset into flattened text. See [suggest-slots-api.md](suggest-slots-api.md).

### Tests (`test_reporting/test_suggest_slots_api.py`, `TestClient`)
- Happy path: `.docx` bytes → `200`, `content_json.type == "doc"`, `plain_text` contains the heading text.
- Non-`.docx` upload → `422`.
- Non-senior role (e.g. `operator`) → `403` (role dependency fires before body handling).
