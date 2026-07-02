# Quickstart / Validation Guide: LLM Template Assist + Report Enhancement

Validates feature 013 end-to-end. All LLM capabilities are **off by default**;
enable per scenario. Run backend commands via `uv run` (see project memory).

## Prerequisites
- 012 baseline working (AST templates, `fill_coverage_gaps`, coverage view).
- A local OpenAI-compatible LLM endpoint reachable at `local_llm_base_url`
  (only needed for the enabled scenarios).
- Alembic migration for `generated_reports.status` / `error_message` applied:
  `uv run alembic upgrade head`.
- Caller sends `X-User` / `X-Role: senior_analyst` headers.

## Scenario A — AI slot suggestion (P1)
1. Enable: `local_llm_enabled=True`, `llm_suggest_slots_enabled=True`.
2. `POST /api/ast-templates/suggest-slots` with `document_text` of a sample GMP report (or `job_id` of a completed extraction job, US4).
3. **Expect**: `200` within the bounded timeout; grouped `sections` with confidence + `evidence_span`; ontology-matched slots show `source_kind=extraction` + `source_hint` IRI; `skipped_duplicates` > 0 when `existing_template` covers some slots.
4. UI: open template editor → “AI 分析” → drawer shows document left / suggestion tree right; clicking a slot highlights its evidence span (FR-012); “采纳所选” merges into the slot editor with new-slot highlight.
5. Negative: role `operator` → `403`; flag off → `503`.

## Scenario B — Gap-filled values in report (P2)
1. Enable: `local_llm_enabled=True`, `llm_report_merge_values=True`.
2. Pick a job whose coverage manifest has missing-required slots; start report generation.
3. **Expect**: `202` + `report_id`; poll status `pending → running → completed`; download DOCX.
4. Verify DOCX: previously-“待评估” slots now show the gap-filled value in **gray italic + ⓘ**; an end-of-report disclaimer section is present. `assessment_rows` / risk levels unchanged vs rule output (FR-009).

## Scenario C — Style-consistent narrative (P3)
1. Enable: `local_llm_enabled=True`, `llm_report_narrative_enabled=True`.
2. Generate a report on a job with rich extracted facts.
3. **Expect**: async job as in B; DOCX subject description / conclusion / per-dimension narrative are coherent formal-GMP prose using **only** extracted facts (SC-006); each generated section carries the review disclaimer line; tone matches template prose (FR-008).

## Scenario D — Backward compatibility (SC-005)
1. All three flags `False` (default).
2. Generate a report the existing way.
3. **Expect**: synchronous path, `completed` immediately, DOCX byte-identical to 012 output; no `llm_supplements` / narrative annotations; suggest-slots endpoint returns `503`.

## Test suite
```bash
uv run pytest backend/tests/test_extraction/test_slot_suggester.py \
              backend/tests/test_reporting/test_report_llm_merge.py \
              backend/tests/test_reporting/test_docx_llm_annotation.py \
              backend/tests/test_reporting/test_narrative_generator.py
```
All LLM calls are mocked; suite runs offline. See [contracts/](contracts/) and
[data-model.md](data-model.md) for field-level detail.
