# Phase 1 Data Model: LLM Template Design Assist + Report Enhancement

This feature is mostly stateless (suggest-slots is request/response) plus small extensions to existing report structures. No new domain tables for suggestions.

## 1. Transient DTOs (suggest-slots) — `backend/app/schemas/extraction.py`

### 1.1 `SuggestSlotsRequest`
| Field | Type | Notes |
|-------|------|-------|
| `job_id` | `UUID \| None` | Reference an existing extraction job's parsed text (P3/US4). Mutually exclusive with `document_text`. |
| `document_text` | `str \| None` | Raw plain text extracted from an uploaded document. |
| `existing_template` | `dict \| None` | Current template `schema_json` for LLM-driven round-2 dedup (FR-003). |
| `max_suggestions` | `int` | Optional override; clamped to `suggest_slots_max` (default 50, FR-004). |

Validation: exactly one of `job_id` / `document_text` must be provided. Empty/whitespace text → empty result with message (edge case).

### 1.2 `SuggestedSlot`
| Field | Type | Notes |
|-------|------|-------|
| `slot_id` | `str` | Proposed stable id (kebab/snake). |
| `label` | `str` | Human label. |
| `section` | `str` | Target section title. |
| `group` | `str` | Target group title. |
| `source_kind` | `str` | `extraction` \| `llm_extraction` \| `manual` — set by ontology binding (FR-002). |
| `source_hint` | `str \| None` | Bound class/property IRI when `source_kind = extraction`. |
| `confidence` | `float` | 0–1 model confidence. |
| `evidence_span` | `str` | Verbatim source snippet supporting the slot. |
| `evidence_offset` | `int \| None` | Char offset in document text for left-panel highlight (FR-012). |
| `reason` | `str` | Short rationale. |

### 1.3 `SuggestSlotsResponse`
| Field | Type | Notes |
|-------|------|-------|
| `sections` | `list[dict]` | Suggestions grouped into section → group → slot hierarchy for the tree UI. |
| `total_suggested` | `int` | Count returned. |
| `skipped_duplicates` | `int` | Count omitted as semantically covered (FR-003, displayed to user). |
| `document_summary` | `str` | One-line structural summary from round 1. |
| `truncated` | `bool` | True if suggestions were capped at `max_suggestions`. |

## 2. `RiskReport` extensions — `backend/app/services/reporting/risk_report_generator.py`

Add two fields to the existing `RiskReport` dataclass (both default empty → zero change when flags off):

| Field | Type | Notes |
|-------|------|-------|
| `llm_supplements` | `dict[str, str]` (default `{}`) | slot_id → LLM gap-filled value; renderer applies gray-italic + ⓘ (FR-005/006). |
| `llm_generated_fields` | `set[str]` (default `set()`) | Names of narrative fields (`subject_description`, `conclusion`, per-dimension) that are LLM-generated; renderer appends disclaimer (FR-007). |

State rule: a slot value present in `llm_supplements` MUST correspond to a coverage manifest entry flipped to `is_llm_sourced=True` (already exists on `SlotCoverage`). Deterministic rows (`assessment_rows`, risk levels, statuses) are never entries in either set (FR-009).

## 3. `GeneratedReport` async status — `backend/app/models/extraction.py` (Alembic migration)

Add two nullable columns (existing rows/sync path treat NULL as `completed`):

| Column | Type | Notes |
|--------|------|-------|
| `status` | `String(20)` nullable | `pending` → `running` → `completed` \| `failed`. Sync path inserts `completed` directly. |
| `error_message` | `Text` nullable | Populated on `failed`; surfaced to client on poll. |

No change to `file_path` / `file_size` semantics; they are populated when `status` reaches `completed`.

## 4. Narrative content (in-memory only)

`NarrativeContent` is NOT persisted (clarification C). Represented transiently inside `narrative_generator` as `{field_name, text, derived_from: list[fact_ref]}` and written directly into `RiskReport` narrative fields + `llm_generated_fields`. Regenerated fresh each run.

## 5. Config additions — `backend/app/config.py` `Settings`

| Setting | Default | Notes |
|---------|---------|-------|
| `llm_suggest_slots_enabled` | `False` | Gate for suggest-slots endpoint. |
| `llm_report_merge_values` | `False` | Gate for merging gap-fill values into report. |
| `llm_report_narrative_enabled` | `False` | Gate for narrative generation. |
| `suggest_slots_timeout_s` | `30` | Bounded server-side timeout (FR-001). |
| `suggest_slots_max` | `50` | Max suggestions per request (FR-004). |

All default-off / bounded → Constitution VI compliant, SC-005 backward compatible.
