# Contract: Slot Suggestion API

## `POST /api/ast-templates/suggest-slots`

Synchronous. Role-gated to `senior_analyst`. Gated by `llm_suggest_slots_enabled`.

### Request (`SuggestSlotsRequest`)
```json
{
  "job_id": "uuid-or-null",
  "document_text": "plain text (mutually exclusive with job_id)",
  "existing_template": { "sections": [ ... ] },
  "max_suggestions": 50
}
```
- Exactly one of `job_id` / `document_text` required → else `422`.

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

### Behavior
1. Resolve input text (from `job_id`'s parsed document or `document_text`); empty/whitespace → `200` with empty `sections` and an explanatory `document_summary` (edge case).
2. **Round 1** — LLM structure analysis → sections/groups + candidate field labels + evidence spans (`chat_with_schema`).
3. **Round 2** — LLM slot mapping given round-1 outline + `existing_template`; instructed to omit semantically-covered slots (increments `skipped_duplicates`).
4. **Ontology binding** — for each candidate, match against the published Owlready2 World (`data_property_labels`, `get_data_properties_by_domain`, `search_one`); set `source_kind` + `source_hint` (FR-002). No match → `llm_extraction` / `manual`.
5. Clamp to `max_suggestions` (≤ `suggest_slots_max`); set `truncated`.

### Errors
| Status | Condition |
|--------|-----------|
| 403 | Missing/invalid role (not `senior_analyst`) |
| 404 | `job_id` not found |
| 422 | Neither or both of `job_id`/`document_text` supplied |
| 503 | `llm_suggest_slots_enabled=False`, LLM client unavailable, or bounded timeout exceeded — retriable (FR-001) |

### Deterministic-safety invariant
The endpoint returns **suggestions only**. It never writes a template, never touches evaluation rules, coverage, or risk levels (FR-009). Adoption into a template goes through the existing role-gated `PUT /api/ast-templates/{id}`.

### Tests (`test_extraction/test_slot_suggester.py`, mock LLM)
- Two-round flow produces grouped suggestions.
- Ontology match → `source_kind=extraction` + IRI; no match → `llm_extraction`.
- `existing_template` covering a candidate → counted in `skipped_duplicates`, absent from `sections`.
- Result capped at `max_suggestions` → `truncated=true`.
- Empty document → empty `sections` + message.
- Flag off / LLM unavailable → `503` retriable.
- Non-senior role → `403`.
