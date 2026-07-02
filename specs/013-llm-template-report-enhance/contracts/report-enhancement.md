# Contract: Report Enhancement (value merge + narrative) & Async Generation

Extends the existing report-generation endpoint on `backend/app/api/extraction.py`.
Role-gated to `senior_analyst`. Governed by `llm_report_merge_values` and
`llm_report_narrative_enabled`.

## Execution model (FR-005a)

- **Any** LLM enhancement flag on → generation runs as an **async background job**:
  1. `POST` start → `202` with `{ "report_id": "uuid", "status": "pending" }`.
  2. Job (FastAPI `BackgroundTasks`) sets `running`, invokes enhancement, renders DOCX, sets `completed` (or `failed` + `error_message`).
  3. `GET /api/.../reports/{report_id}` → `{ status, error_message, file_size, ... }`.
  4. `GET /api/.../reports/{report_id}/download` when `status=completed` → DOCX bytes.
- **All** flags off → existing **synchronous** path unchanged; report row created directly as `completed` (SC-005, no polling required).

## Value merge (FR-005 / FR-006)

When `llm_report_merge_values` on and the coverage manifest has missing required slots and `local_llm_enabled`:
1. Call 012 `fill_coverage_gaps(manifest, document_path, template)` → synthetic edges (`source="llm"`).
2. Merge filled values into `RiskReport.llm_supplements` (slot_id → value) and flip the corresponding `SlotCoverage.is_llm_sourced=True` / status to filled.
3. `docx_renderer` renders each supplemented value **gray italic + ⓘ**; appends an end-of-report generated-content disclaimer section.
4. No missing slots, or LLM disabled/unreachable → step skipped; report renders baseline (no regression, edge case).

**Invariant**: merge only affects *slot values / display*. `assessment_rows`, pre/post-control levels, risk-level mapping, and G1 three-state remain rule-derived (FR-009). LLM values never feed back into rule evaluation or coverage validation logic.

## Narrative generation (FR-007 / FR-008)

When `llm_report_narrative_enabled` on:
1. `narrative_generator` builds prompts from extracted facts (sole data source) + existing template prose as few-shot style examples (`chat_with_schema`).
2. Generates `subject_description`, per-dimension risk narrative, and `conclusion`; writes them into `RiskReport` and records field names in `llm_generated_fields`.
3. `docx_renderer` adds a disclaimer line under each generated section: “以上内容由文档抽取结果自动生成，仅供参考，请核对后确认。”
4. Regenerated fresh each run; not persisted, not editable in-app (clarification C).
5. Flag off → rule-based/template text as before (no regression).

**Invariant**: narrative is prose only, derived exclusively from supplied facts (SC-006); it never introduces data points absent from the extraction set and never alters evaluation.

## Errors
| Status | Condition |
|--------|-----------|
| 403 | Not `senior_analyst` |
| 404 | `report_id` / `job_id` not found |
| 409 | Download requested while `status != completed` |
| 500 | Job failed → row `status=failed` + `error_message` (surfaced on poll, not raw 500 when polled) |

## Tests
- `test_report_llm_merge.py`: gap-fill values land in `llm_supplements`; manifest flipped; flags off → identical to 012 baseline (SC-005).
- `test_docx_llm_annotation.py`: supplemented values render gray-italic+ⓘ; narrative disclaimer + end disclaimer section present; 100% of LLM content annotated (SC-004).
- `test_narrative_generator.py` (mock LLM): output uses only supplied facts (SC-006); template prose passed as few-shot; regeneration is stateless.
- Async: start → poll `pending/running/completed` → download; failure → `failed` + message; all-off → synchronous `completed`.
