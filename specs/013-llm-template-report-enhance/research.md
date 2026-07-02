# Phase 0 Research: LLM Template Design Assist + Report Enhancement

All items resolved. No open NEEDS CLARIFICATION.

## R1 — Two-round LLM slot suggestion pattern

**Decision**: Reuse the 012 `llm_gap_filler` structured-output pattern (json_schema request format with a prompt-based JSON fallback) for both rounds. Round 1: send document text → get a structural outline (sections/groups + candidate field labels + evidence spans). Round 2: send round-1 outline **plus** the existing template structure → get concrete `SuggestedSlot` objects, instructing the model to omit any slot already semantically covered by the existing template (LLM-driven dedup, per clarification).

**Rationale**: Splitting structure discovery from slot mapping keeps each prompt focused and bounds output size; the same fallback machinery already proven in 012 avoids new parsing code. Existing template as round-2 context is the mechanism the clarification chose for semantic dedup.

**Alternatives considered**: Single-round prompt (rejected — conflates layout discovery with IRI mapping, degrades recall on long docs); embedding-similarity dedup against existing slots (rejected — clarification chose LLM-driven dedup; adds an embedding dependency and a gold threshold that does not survive domain shift, consistent with prior 009 findings).

## R2 — `chat_with_schema()` helper location & shape

**Decision**: Add `chat_with_schema(client, *, system, user, schema, model=None, temperature=None, max_tokens=None) -> dict` to the existing `backend/app/services/llm/local_client.py`. It attempts `response_format={"type": "json_schema", ...}`; on API rejection or malformed content it retries with a plain prompt that appends the schema and parses the first JSON object (mirroring `_build_response_format` / `_parse_llm_response` in `llm_gap_filler`). Returns `None`-safe: callers already gate on `get_local_llm()` returning a client.

**Rationale**: Centralizes the fallback so `slot_suggester` and `narrative_generator` both reuse it; keeps `get_local_llm` unchanged (backward compatible). Assumptions in spec already name `chat_with_schema` as expected-reusable infra.

**Alternatives considered**: New `llm/schema_client.py` module (rejected — unnecessary file; the helper is <40 lines and belongs with the client factory). Duplicating fallback logic in each service (rejected — violates Principle V reuse).

## R3 — Ontology IRI binding for suggested slots (FR-002)

**Decision**: For each round-2 candidate, resolve against the **published Owlready2 World** via `OntologyEngine`: match candidate label/text to class or data-property labels. Reuse existing engine methods — `data_property_labels()`, `data_property_domain_classes()`, `get_data_properties_by_domain(class_iri)`, and `search_one(iri=...)` — for lookup. On a confident label/IRI match → `source_kind = extraction` with the bound class/property IRI as `source_hint`; on no ontology match → `source_kind = llm_extraction` (or `manual` for free-text-only slots).

**Rationale**: The clarification fixed the reference set to the full published/materialized ontology (the same class set the extraction pipeline uses). These engine methods already exist and are read-only, satisfying Principle II. Matching is done in Python against engine output — the LLM is not the authority for IRI truth.

**Alternatives considered**: Let the LLM emit IRIs directly (rejected — hallucination risk; IRIs must be verified against the real World). SPARQL per candidate (rejected — engine helper methods are simpler and already cached in memory).

## R4 — Async report-generation status tracking

**Decision**: Use FastAPI `BackgroundTasks` to run enhanced generation. Track status on the existing `generated_reports` flow by introducing a lightweight status lifecycle (`pending → running → completed | failed`) on the report row (add nullable `status` + `error_message` columns via Alembic migration; existing synchronous rows are created directly as `completed`). Client starts a job (`202` + report id), polls `GET .../reports/{id}` for status, and downloads the DOCX when `completed`. When all LLM flags are off, the existing synchronous path creates a `completed` row immediately (no polling needed) — zero behavioral change (SC-005).

**Rationale**: Reuses `GeneratedReport` (already the download artifact record) rather than adding a parallel job table; mirrors the `ExtractionJob` status idiom already in the codebase. `BackgroundTasks` is sufficient for < 10 concurrent single-tenant users and needs no external worker (offline-first).

**Alternatives considered**: New `report_jobs` table (rejected — duplicates `generated_reports`; more migration surface). Celery/RQ worker (rejected — adds a broker dependency, violates offline-first minimalism). Reusing `ExtractionJob` (rejected — semantically it tracks extraction, not report rendering).

## R5 — DOCX annotation of LLM content (FR-006, SC-004)

**Decision**: Extend `docx_renderer` with a gray-italic run style plus an ⓘ marker for any value sourced from `RiskReport.llm_supplements`, and a per-narrative disclaimer line for `llm_generated_fields`. Append a single end-of-report "generated content disclaimer" section. Reuse the existing `_WARN_GLYPH` / `RGBColor` mechanism already used for warnings.

**Rationale**: The renderer already customizes run color/style for warnings, so LLM annotation is an incremental style variant, not new infrastructure (spec assumption confirmed). Visual distinction satisfies GMP audit traceability (SC-004) at 100% because rendering keys off the source-tag sets, not heuristics.

**Alternatives considered**: Word comments/footnotes only (rejected — less visible in print; keep inline style as primary, disclaimer section as reinforcement). Separate "LLM appendix" (rejected — divorces values from their table context, hurting readability).

## R6 — Narrative generation grounded in facts only (FR-007/008, SC-006)

**Decision**: `narrative_generator.py` builds prompts from (a) the extracted facts/edges already assembled for the report and (b) existing template prose as few-shot style exemplars, with an explicit instruction to use only the provided facts and invent no data. Narratives are regenerated per run, not persisted, not editable in-app (clarification C). Output feeds `RiskReport` narrative fields tagged as LLM-generated for annotation.

**Rationale**: Passing facts as the sole data source plus few-shot template prose is the standard grounding technique for style-consistent, non-fabricating generation; regeneration-per-run avoids stale/edited-state divergence and keeps the DB free of mutable prose. SC-006 (zero fabricated data) is enforced by prompt constraint + human review annotation, and validated in tests by asserting output tokens are drawn from the supplied fact set.

**Alternatives considered**: Persist + allow in-app editing (rejected by clarification C — analyst edits the downloaded DOCX). Template-string narratives (rejected — that is the existing non-LLM baseline retained when the flag is off).

## R7 — Feature flags & role gating

**Decision**: Add `llm_suggest_slots_enabled`, `llm_report_merge_values`, `llm_report_narrative_enabled` (all default `False`) plus `suggest_slots_timeout_s` and `suggest_slots_max` (default 50) to `Settings` in `config.py`. Gate `POST /suggest-slots` and narrative-enhanced generation behind `require_role(ROLE_SENIOR_ANALYST)` (the existing `_maintainer` dependency pattern in `ast_templates.py`).

**Rationale**: Matches the 012 `local_llm_*` flag convention and the established RBAC dependency factory; per-capability flags let operators enable merge without narrative (or neither). Offline-first default-off satisfies Constitution VI.

**Alternatives considered**: One master `llm_report_enabled` flag (rejected — spec requires individually toggleable capabilities, FR-011). New RBAC role (rejected — `senior_analyst` already the write/publish tier).
