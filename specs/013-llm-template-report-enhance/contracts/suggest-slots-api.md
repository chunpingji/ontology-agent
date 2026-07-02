# Contract: Slot Suggestion API

## `POST /api/ast-templates/suggest-slots`

Synchronous. Role-gated to `senior_analyst`. Gated by `llm_suggest_slots_enabled`.

### Request (`SuggestSlotsRequest`)
```json
{
  "job_id": "uuid-or-null",
  "document_text": "legacy plain-text fallback (see note)",
  "sample_content_json": { "type": "doc", "content": [ ... ] },
  "existing_template": { "sections": [ ... ] },
  "max_suggestions": 50
}
```
- **Exactly one** of `job_id` / `document_text` / `sample_content_json` required → else `422` (`model_post_init`).
- `sample_content_json` is the **structure-faithful** tiptap/ProseMirror doc produced by `POST /parse-sample` (see [parse-sample-api.md](parse-sample-api.md)). The LLM prompt text is derived from it **server-side** (`tiptap_to_text`), so evidence spans stay substrings of what the user sees. This is the preferred input for the DOCX *create* and *re-edit* flows — no flatten-to-text round-trip, so slot↔preview linkage is preserved.
- `document_text` is the **legacy fallback** for oversized samples and templates that persisted only flat `sample_text`.

### Response 200 (`SuggestSlotsResponse`)
```json
{
  "sections": [
    {
      "title": "一、评估对象",
      "groups": [
        {
          "title": "药品信息",
          "slots": [
            {
              "slot_id": "drug_name",
              "label": "药品名称",
              "section": "一、评估对象",
              "group": "药品信息",
              "source_kind": "extraction",
              "source_hint": "http://slpra/ontology#DrugProduct.name",
              "confidence": 0.92,
              "evidence_span": "本品为……注射液",
              "source_ref": "§ 一、评估对象",
              "evidence_offset": 128,
              "reason": "文档首段声明评估对象"
            }
          ]
        }
      ]
    }
  ],
  "total_suggested": 12,
  "skipped_duplicates": 3,
  "document_summary": "GMP 风险评估报告，含评估对象/设备清单/风险矩阵三部分",
  "truncated": false
}
```
- `source_ref` — **deterministic structural anchor** (`§ <heading>` or the raw span) derived server-side from `sample_content_json` (or a `job_id` annotated doc), never emitted by the LLM. Present only when a `content_json` source was supplied; it drives the `WordViewer` preview highlight. See Behavior step 6.
- `evidence_offset` — **deprecated**. A char offset into flattened text; superseded by `source_ref` structural anchors (offsets can't correspond to a faithfully-rendered document). Retained optional for backward compatibility; new clients should use `source_ref`.

### Behavior
1. Resolve input text: from `sample_content_json` via `tiptap_to_text` (structure-faithful), from `job_id`'s parsed/annotated document, or from `document_text` (legacy). Empty/whitespace → `200` with empty `sections` and an explanatory `document_summary` (edge case).
2. **Round 1** — LLM structure analysis → sections/groups + candidate field labels + evidence spans (`chat_with_schema`).
3. **Round 2** — LLM slot mapping given round-1 outline + `existing_template`; instructed to omit semantically-covered slots (increments `skipped_duplicates`).
4. **Ontology binding** — for each candidate, match against the published Owlready2 World (`data_property_labels`, `get_data_properties_by_domain`, `search_one`); set `source_kind` + `source_hint` (FR-002). No match → `llm_extraction` / `manual`.
5. Clamp to `max_suggestions` (≤ `suggest_slots_max`); set `truncated`.
6. **Anchor derivation (deterministic, not LLM-emitted)** — when a `content_json` source is present, each slot's `source_ref` is derived server-side (`derive_source_ref`): locate `evidence_span` in the tiptap (forward containment, then reverse containment for long spans) and return a `"§ <heading>"` anchor (or the raw block text). Because the anchor text is taken *from* the tiptap, it is guaranteed to exist in the rendered `WordViewer` DOM for highlight linkage (FR-012). No `content_json` → no `source_ref` key.

### Errors
| Status | Condition |
|--------|-----------|
| 403 | Missing/invalid role (not `senior_analyst`) |
| 404 | `job_id` not found |
| 422 | Not exactly one of `job_id` / `document_text` / `sample_content_json` supplied |
| 503 | `llm_suggest_slots_enabled=False`, LLM client unavailable, or bounded timeout exceeded — retriable (FR-001) |

### Deterministic-safety invariant
The endpoint returns **suggestions only**. It never writes a template, never touches evaluation rules, coverage, or risk levels (FR-009). Adoption into a template goes through the existing role-gated `PUT /api/ast-templates/{id}`.

### Tests
**Unit** (`test_extraction/test_slot_suggester.py`, mock LLM at the OpenAI-client level):
- Two-round flow produces grouped suggestions.
- Ontology match → `source_kind=extraction` + IRI; no match → `llm_extraction`.
- `existing_template` covering a candidate → counted in `skipped_duplicates`, absent from `sections`.
- Result capped at `max_suggestions` → `truncated=true`.
- Empty document → empty `sections` + message.
- `tiptap_to_text`: headings prefixed, list items, table rows `|`-joined, truncation marker; every round-tripped block text is a substring of the serialized text.
- `derive_source_ref`: heading and paragraph-under-heading → `§` anchor; headingless top block → raw text; reverse-containment for spans longer than the cell; `None` on no-match / empty.
- `content_json` path → every returned slot carries a structural `source_ref`; the key is absent entirely when no `content_json` is supplied.

**Endpoint** (`test_reporting/test_suggest_slots_api.py`, `TestClient`):
- `sample_content_json` path (flag on, mock local LLM) → `200`, slot `source_ref == "§ 评估对象"`.
- Flag off (`llm_suggest_slots_enabled=False`) / no local client → `503`.
- Non-senior role → `403`.
- Three-way `model_post_init`: 0 sources → error, 2 → error, 1 → ok (surfaced as `422` at the HTTP layer).
