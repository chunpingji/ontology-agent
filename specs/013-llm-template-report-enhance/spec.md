# Feature Specification: LLM Template Design Assist + Report Enhancement

**Feature Branch**: `013-llm-template-report-enhance`

**Created**: 2026-07-02

**Status**: Draft

**Input**: User description: "LLM-assisted template slot suggestion from example documents, and LLM-enriched report generation that merges gap-filled values and generates style-consistent narrative content."

## Clarifications

### Session 2026-07-02

- Q: What execution model should LLM-enhanced report generation use? → A: Asynchronous background job; frontend polls for status and downloads the DOCX when ready (reuses existing extraction-job infrastructure).
- Q: How is "semantic equivalence" determined when deduplicating suggested slots against an existing template? → A: The LLM decides during the round-2 slot-mapping prompt, which receives the existing template structure as context and is instructed to skip semantically-covered slots.
- Q: Which ontology class/property set does slot suggestion cross-reference for IRI binding? → A: The full published/materialized ontology (Owlready2 World) — the same class set the extraction pipeline uses at runtime.
- Q: Are LLM-generated narrative sections persisted/editable, or regenerated each run? → A: Regenerated fresh on each report run; not persisted and not editable in-app (analyst edits the downloaded DOCX if corrections are needed).
- Q: What delivery model does the suggest-slots endpoint use? → A: Synchronous request; the drawer shows a loading skeleton until the response arrives, with a bounded server-side timeout and retry on failure.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - AI-Suggested Slots from Example Document (Priority: P1)

As a senior analyst, I want to upload an example document (or reference an existing extraction job) and have the system analyze the document's structure using LLM, returning a list of suggested sections, groups, and slots. I can preview these suggestions alongside the source document, selectively adopt them into my current template, and avoid manually authoring JSON slot definitions from scratch.

**Why this priority**: This directly addresses the primary user pain point — template design requires deep JSON schema knowledge. Lowering this barrier enables non-technical domain experts to create templates, which is a prerequisite for broader platform adoption.

**Independent Test**: Can be fully tested by uploading a sample GMP document, reviewing the returned slot suggestions in the UI, selectively adopting slots, and verifying they appear in the template's slot editor.

**Acceptance Scenarios**:

1. **Given** a senior analyst is on the template edit page, **When** they click "AI Analysis" and upload a sample document, **Then** a drawer opens showing the document on the left and a slot suggestion tree on the right within 30 seconds.
2. **Given** slot suggestions are displayed, **When** the analyst clicks a suggested slot in the right panel, **Then** the left panel scrolls to and highlights the corresponding evidence text in the document.
3. **Given** slot suggestions are displayed, **When** the analyst checks desired slots and clicks "Adopt Selected", **Then** the drawer closes and the adopted slots appear in the template's slot editor with a visual highlight indicating they are new.
4. **Given** an existing template with slots already defined, **When** the analyst runs AI analysis, **Then** suggestions that semantically duplicate existing slots are skipped and the skip count is displayed.
5. **Given** suggestions are displayed, **When** the analyst uploads a different document, **Then** a confirmation dialog warns that current suggestions will be cleared, and upon confirmation, the system re-analyzes from scratch.

---

### User Story 2 - LLM Gap-Filled Values Flow into Report (Priority: P2)

As a senior analyst, I want the LLM-supplemented slot values (from the AST coverage gap-filling in feature 012) to be included in the generated DOCX report instead of showing "pending evaluation (data missing)" for those fields. LLM-sourced values must be visually distinguishable in the report.

**Why this priority**: This resolves the experience gap where users see filled values in the coverage view but empty placeholders in the generated report — a direct usability and trust issue.

**Independent Test**: Can be tested by running report generation on a job with LLM-filled coverage gaps and verifying the DOCX output contains the filled values with appropriate source annotations.

**Acceptance Scenarios**:

1. **Given** a coverage manifest with missing required slots and local LLM is enabled, **When** the analyst generates a DOCX report, **Then** the system starts an asynchronous generation job, invokes LLM gap-filling, and merges the results into the report data before rendering; the analyst can poll job status and download the DOCX when it completes.
2. **Given** a report contains LLM-supplemented values, **When** the DOCX is rendered, **Then** each LLM-sourced value is rendered in a distinct visual style (gray italic with info marker) and a footnote explains the annotation.
3. **Given** local LLM is disabled, **When** the analyst generates a report with missing slots, **Then** the report renders identically to the current behavior (no regression).

---

### User Story 3 - Style-Consistent Narrative Content Generation (Priority: P3)

As a senior analyst, I want the system to generate narrative paragraphs (subject description, risk assessment narratives, conclusion) that match the writing style of the existing template, using extracted facts as the sole data source. Generated narratives must be clearly marked for human review.

**Why this priority**: Narrative generation adds polish to reports but is lower priority than ensuring data completeness (P2) and template creation ease (P1). It builds on the data layer established by P2.

**Independent Test**: Can be tested by generating a report with narrative generation enabled and verifying the output contains contextually appropriate prose that references only extracted facts, with clear LLM-source annotations.

**Acceptance Scenarios**:

1. **Given** extracted facts and narrative generation is enabled, **When** the analyst generates a report, **Then** the subject description section contains a coherent paragraph written in formal GMP compliance language using only the provided facts.
2. **Given** the template contains existing prose in other sections, **When** narrative content is generated, **Then** the generated text matches the terminology, sentence structure, and paragraph style of the existing template prose.
3. **Given** a generated report with narrative content, **When** the DOCX is rendered, **Then** each LLM-generated narrative section has a disclaimer line: "The above content was automatically generated from document extraction results — for reference only, please verify before confirming."
4. **Given** narrative generation is disabled, **When** the analyst generates a report, **Then** the report uses rule-based template text as before (no regression).

---

### User Story 4 - Reference Existing Job for Slot Suggestion (Priority: P3)

As a senior analyst, I want to reference an already-completed extraction job (instead of uploading a new document) when requesting AI slot suggestions, so I can leverage already-parsed text without re-uploading.

**Why this priority**: Convenience feature that reuses existing infrastructure; lower priority than the core AI analysis flow.

**Independent Test**: Can be tested by selecting an existing job in the suggestion panel and verifying the same suggestion flow works as with a freshly uploaded document.

**Acceptance Scenarios**:

1. **Given** the AI analysis drawer is open, **When** the analyst selects "From Job" and picks an existing extraction job, **Then** the system uses the job's extracted text as input for slot suggestion without requiring a file upload.

---

### Edge Cases

- What happens when the LLM returns malformed JSON? The system retries with prompt-based JSON parsing fallback (already implemented in 012).
- What happens when the uploaded document is empty or contains only images? The system returns an empty suggestion list with a user-facing message explaining no text content was found.
- What happens when all suggested slots duplicate existing template slots? The system displays "0 new suggestions (N skipped as duplicates)" with no adoption action available.
- What happens when the LLM service is unreachable (local endpoint down)? The system shows an error toast with retry option; no partial state is persisted.
- What happens when the analyst closes the suggestion drawer mid-analysis? The in-flight request is cancelled; no suggestions are persisted.
- What happens when report generation is invoked with LLM enabled but no missing slots? The LLM gap-filling step is skipped and the report generates normally.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST provide a `POST /api/ast-templates/suggest-slots` endpoint that accepts either a job reference or raw document text, analyzes the content via two-round LLM prompting (structure analysis, then slot mapping), and returns a structured list of suggested slots with confidence scores and evidence spans. The endpoint responds synchronously; the frontend shows a loading skeleton until the response arrives. The endpoint MUST enforce a bounded server-side timeout and surface a retriable error if exceeded.
- **FR-002**: System MUST cross-reference suggested slots against the full published/materialized ontology (the Owlready2 World — the same class set the extraction pipeline uses at runtime) to set appropriate `source_kind` (extraction vs llm_extraction vs manual) and bind matching class/property IRIs. Slots with no ontology match default to `llm_extraction` or `manual`.
- **FR-003**: System MUST deduplicate suggested slots against any provided existing template, skipping semantically equivalent slots (not just matching by `slot_id`). Deduplication is performed by the LLM during the round-2 slot-mapping prompt, which receives the existing template structure as context and is instructed to omit slots already semantically covered. Skipped slots are counted and reported to the user.
- **FR-004**: System MUST enforce a configurable maximum suggestion count per request (default 50) to bound LLM cost and response time.
- **FR-005**: System MUST merge LLM gap-filled slot values into the `RiskReport` data structure when `llm_report_merge_values` is enabled, updating the coverage manifest to reflect filled status.
- **FR-005a**: When any LLM report enhancement is enabled, report generation MUST execute as an asynchronous background job; the client MUST be able to poll job status and download the completed DOCX when ready. When all LLM enhancements are disabled, report generation MAY use the existing synchronous path unchanged.
- **FR-006**: System MUST render LLM-sourced values in the DOCX output with a distinct visual style (gray italic + info marker) and append a "generated content disclaimer" section at the report end.
- **FR-007**: System MUST generate style-consistent narrative content for subject description, per-risk-dimension assessment, and conclusion sections when `llm_report_narrative_enabled` is enabled. Narratives are regenerated fresh on each report run and are NOT persisted or editable in-app; analysts edit the downloaded DOCX directly if corrections are required.
- **FR-008**: System MUST include existing template prose as few-shot style examples in the narrative generation prompt to ensure terminology and tone consistency.
- **FR-009**: System MUST NOT allow LLM to participate in deterministic evaluation paths: rule evaluation, coverage validation, risk level mapping, or G1 three-state logic remain purely rule-driven.
- **FR-010**: System MUST gate the `suggest-slots` endpoint and narrative-enhanced report generation behind `senior_analyst` role authorization.
- **FR-011**: All new LLM capabilities MUST be individually toggle-able via configuration flags (`llm_suggest_slots_enabled`, `llm_report_merge_values`, `llm_report_narrative_enabled`), defaulting to disabled.
- **FR-012**: System MUST provide a left-right linked panel UI in the template editor where the left panel shows the source document and the right panel shows the slot suggestion tree, with bidirectional scroll-and-highlight linking.
- **FR-013**: When the analyst replaces the source document in the suggestion panel, the system MUST clear all existing suggestions and re-analyze from scratch (no incremental merge).

### Key Entities

- **SuggestedSlot**: A proposed slot definition including slot_id, label, group/section assignment, source_kind, source_hint (IRI binding), confidence score, evidence span, and reason.
- **SuggestSlotsResponse**: The complete suggestion result including section hierarchy, total/skipped counts, and a document structure summary.
- **NarrativeContent**: LLM-generated prose for a specific report section, tagged with its source (LLM) and linked to the facts it was derived from.
- **LLM Supplement**: A key-value pair of slot_id to LLM-filled value, tracked in `RiskReport.llm_supplements` for rendering annotation.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A senior analyst can go from uploading a sample document to having a template with adopted slots in under 5 minutes (vs. 30+ minutes of manual JSON authoring today).
- **SC-002**: At least 70% of LLM-suggested slots for a typical GMP document are accepted by the analyst without modification (measuring suggestion quality).
- **SC-003**: Reports generated with LLM gap-filling show zero "pending evaluation (data missing)" entries for slots that were successfully gap-filled in the coverage view.
- **SC-004**: 100% of LLM-sourced content in the generated report is visually distinguishable from rule-derived content, satisfying GMP audit traceability requirements.
- **SC-005**: Disabling all LLM feature flags results in zero behavioral changes compared to the 012 baseline (full backward compatibility).
- **SC-006**: Generated narrative text uses only facts from the extracted data — zero fabricated data points in the output.

## Assumptions

- Local LLM endpoint (OpenAI-compatible) is available and accessible at the configured URL; the system does not manage LLM deployment.
- The LLM client infrastructure from feature 012 (`get_local_llm`, `chat_with_schema`, structured output with fallback) is fully operational and reusable.
- Template editing, slot editor, and coverage view UI from features 011/012 are stable and available for integration.
- The `fill_coverage_gaps` function from 012 is functional and its results can be merged into the report generation pipeline.
- Users understand GMP compliance document structure sufficiently to evaluate slot suggestions (the system assists, not replaces, domain expertise).
- The existing `docx_renderer` supports paragraph-level style customization (italic, color, footnotes) needed for LLM content annotation.
- Document text for suggestion analysis is plain text extracted from uploaded files; OCR or image-based extraction is out of scope.
