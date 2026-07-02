# Phase 1 Data Model: LLM Template Design Assist + Report Enhancement

This feature is mostly stateless (suggest-slots is request/response) plus small extensions to existing report structures. No new domain tables for suggestions.

## 1. Transient DTOs (suggest-slots) — `backend/app/schemas/extraction.py`

### 1.1 `SuggestSlotsRequest`
| Field | Type | Notes |
|-------|------|-------|
| `job_id` | `UUID \| None` | Reference an existing extraction job's parsed/annotated text (P3/US4). |
| `document_text` | `str \| None` | **Legacy fallback** — flat plain text (oversized samples, `sample_text`-only templates). |
| `sample_content_json` | `dict \| None` | **Structure-faithful** tiptap from `POST /parse-sample`; prompt text is derived server-side via `tiptap_to_text`. Preferred input for the DOCX create + re-edit flows (no flatten-to-text round-trip → slot↔preview linkage preserved). |
| `existing_template` | `dict \| None` | Current template `schema_json` for LLM-driven round-2 dedup (FR-003). |
| `max_suggestions` | `int` | Optional override; clamped to `suggest_slots_max` (default 50, FR-004). |

Validation (`model_post_init`): **exactly one** of `job_id` / `document_text` / `sample_content_json` must be provided → else `ValueError` (`422`). Empty/whitespace text → empty result with message (edge case).

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
| `source_ref` | `str \| None` | Deterministic structural anchor (`§ <heading>` or raw span) derived server-side from `sample_content_json` via `derive_source_ref`; drives the `WordViewer` preview highlight (FR-012). Never LLM-emitted; present only when a `content_json` source was supplied. |
| `evidence_offset` | `int \| None` | **Deprecated** — char offset into flattened text; superseded by `source_ref` (offsets can't map onto a faithfully-rendered document). Retained optional for backward compatibility. |
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

## 6. `AstTemplate.sample_content_json` (persisted) — `backend/app/models/extraction.py` (Alembic `0012`)

Faithful-preview refinement (2026-07-02): the DOCX sample is parsed **backend-side** into structure-faithful tiptap and persisted, so re-opened templates preview faithfully in the AI drawer — not only at create time.

| Column | Type | Notes |
|--------|------|-------|
| `sample_content_json` | `JSON` nullable | Structure-faithful tiptap of the source DOCX, stored on create beside the existing flat `sample_text`. NULL for legacy templates → frontend falls back to wrapping `sample_text` as plain paragraphs for preview. |

Migration `0012_ast_template_sample_json` (`down_revision = "0011_ast_template_sample_text"`; the revision id is kept ≤32 chars because Alembic's `alembic_version.version_num` is `VARCHAR(32)` — a longer id fails the post-DDL version stamp and rolls the whole migration back). Applied at startup via `_run_migrations()`. `AstTemplateCreate` gains the matching `sample_content_json: dict | None = None`; `create_template` persists it and `get_template` returns it.

**Endpoints touched**: new `POST /api/ast-templates/parse-sample` (multipart DOCX → `{content_json, plain_text}`; role-gated, **not** flag-gated — see [contracts/parse-sample-api.md](contracts/parse-sample-api.md)); `POST /suggest-slots` gains the `sample_content_json` input branch. New service functions in `slot_suggester.py`: `tiptap_to_text`, `derive_source_ref`; new `parse_word_to_tiptap` wrapper in `document_annotator.py` (`annotate_word(..., structure_only=True)`).
