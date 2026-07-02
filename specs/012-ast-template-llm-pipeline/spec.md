# Feature Specification: AST Template Management & LLM Pipeline Enhancement

**Feature Branch**: `012-ast-template-llm-pipeline`

**Created**: 2026-07-01

**Status**: Draft

**Input**: User description: Design document "支持文档模板的AST报告LLM加持管线增强方案.md" — combined feature for AST report template management (012) and LLM-assisted extraction pipeline enhancement (013).

## Clarifications

### Session 2026-07-01

- Q: Should gap filling be a system-wide admin toggle or a per-job analyst choice? → A: System-wide admin toggle — gap filling is either on or off for all evaluations, configured by an administrator.
- Q: Should the analyst UI show the same label for rule-based and gap-filled values, or visually distinguish them? → A: Visual indicator — gap-filled slots display a subtle badge or icon so analysts can see the extraction origin; full source detail remains in admin audit logs.
- Q: Should template management include a visual slot editor or be upload-only? → A: Visual editor — the application provides an in-app UI to add, remove, and reorder slots within a template.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Upload and Manage Report Templates (Priority: P1)

As a quality analyst, I need to create, visually edit, and manage different AST report templates so that the system can handle multiple document types (e.g., CMC risk assessment, stability evaluation, cleaning validation) instead of being limited to a single hardcoded template.

**Why this priority**: The template management layer is the foundation for all other capabilities in this feature. Without it, neither template switching nor LLM-based extraction enhancement can function. It also directly addresses the first bottleneck: the system only supports one report format.

**Independent Test**: Can be fully tested by uploading a new template or creating one via the visual editor, verifying it appears in the template list, editing its slots, and confirming it can be set as the default. Delivers immediate value by enabling multi-format support.

**Acceptance Scenarios**:

1. **Given** a quality analyst on the template management page, **When** they upload a valid template file, **Then** the system validates the template structure and adds it to the template list with its name, version, and slot count displayed.
2. **Given** an uploaded template, **When** the analyst sets it as the default, **Then** only that template is marked as default and all new evaluations use it by default.
3. **Given** a template that is not set as default, **When** the analyst deletes it, **Then** it is removed from the list and no longer available for selection.
4. **Given** an analyst uploads a template with invalid structure (e.g., duplicate slot identifiers, missing required fields), **When** the upload is submitted, **Then** the system rejects it and displays specific validation errors explaining what is wrong.
5. **Given** a template with version "v1", **When** the analyst updates it, **Then** a new version "v2" is created while the old version is preserved for historical reports.
6. **Given** an existing template, **When** the analyst opens the visual editor, **Then** they can add, remove, and reorder slots within sections and groups, toggle required/optional status, and save changes as a new version.

---

### User Story 2 - Configure Document Type to Template Mapping (Priority: P1)

As a quality analyst, I need to map document types to specific templates so that the system automatically selects the correct template when evaluating a document, without manual intervention each time.

**Why this priority**: Automatic template matching eliminates manual template selection for each job, reducing errors and enabling the system to scale to multiple document types seamlessly.

**Independent Test**: Can be tested by creating a mapping rule (e.g., "CMCReport → Risk Assessment Template"), processing a document of that type, and verifying the correct template is automatically applied.

**Acceptance Scenarios**:

1. **Given** an analyst on the template management page, **When** they create a mapping from a document type identifier to a template, **Then** the mapping is saved and displayed in the mapping list.
2. **Given** a mapping exists for a document type, **When** a new evaluation job is started for a document of that type, **Then** the system automatically selects the mapped template without user intervention.
3. **Given** multiple mappings exist with overlapping patterns, **When** matching is performed, **Then** the system uses the highest-priority mapping.
4. **Given** no mapping matches a document type, **When** the system attempts to resolve a template, **Then** it falls back to the default template.

---

### User Story 3 - Switch Templates on the AST Coverage Page (Priority: P2)

As a quality analyst reviewing AST coverage for a specific job, I need to switch between available templates to see how coverage changes under different evaluation criteria.

**Why this priority**: Provides analysts with flexibility to explore coverage under different templates during review, enabling better-informed decisions about which template best fits a document.

**Independent Test**: Can be tested by opening the AST coverage page for a job, selecting a different template from the dropdown, and verifying coverage metrics recalculate and refresh.

**Acceptance Scenarios**:

1. **Given** an analyst is on the AST coverage page and multiple templates exist, **When** the page loads, **Then** a template selector appears showing all available templates with the current one pre-selected.
2. **Given** the template selector is visible, **When** the analyst selects a different template, **Then** the coverage metrics recalculate and the page refreshes to show updated slot statuses.
3. **Given** only one template exists in the system, **When** the analyst opens the AST coverage page, **Then** no template selector is shown (the single template is used implicitly).

---

### User Story 4 - Automatic Extraction Gap Filling (Priority: P2)

As a quality analyst, I want the system to automatically attempt to fill missing required slots from the document text when the initial rule-based extraction leaves coverage gaps, so that I spend less time manually hunting for information that the system missed.

**Why this priority**: Directly addresses the second bottleneck — rule-based finders miss data when document formatting varies. Automated gap filling significantly reduces manual effort and improves first-pass coverage rates.

**Independent Test**: Can be tested by processing a document where rule-based extraction misses known required slots, enabling gap-filling, and verifying that previously missing slots are now populated with correct values and source references.

**Acceptance Scenarios**:

1. **Given** a completed extraction job with missing required slots and gap-filling is enabled, **When** coverage is calculated, **Then** the system automatically attempts to extract missing values from the document text and fills any slots it can identify.
2. **Given** gap-filling has run, **When** the analyst views slot details, **Then** each filled slot shows a source text snippet from the original document for traceability.
3. **Given** gap-filling is disabled (default), **When** coverage is calculated, **Then** behavior is identical to the current system with no changes to results or performance.
4. **Given** gap-filling runs but cannot find a value for a missing slot, **When** the analyst views coverage, **Then** that slot remains marked as missing (the system does not fabricate data).

---

### User Story 5 - Ontology-Driven Dynamic Slot Expansion (Priority: P3)

As a quality analyst working with a richly modeled ontology, I want the system to automatically discover additional data properties from the ontology and create corresponding extraction slots, so that the evaluation captures information beyond what the static template defines.

**Why this priority**: Leverages the existing ontology knowledge model to dynamically enrich evaluations. Lower priority because it requires both template management and gap-filling to be in place, and its value depends on the ontology having richer data properties than the static templates.

**Independent Test**: Can be tested by ensuring an ontology class has data properties not covered by the static template, running an evaluation with expansion enabled, and verifying that new slots appear grouped separately and are populated from the document.

**Acceptance Scenarios**:

1. **Given** a document type linked to an ontology class with data properties beyond the static template, **When** the system prepares coverage evaluation, **Then** additional slots are dynamically generated from the ontology and grouped under an "Ontology Properties" section.
2. **Given** dynamically expanded slots exist, **When** the analyst views the AST tree, **Then** expanded slots are visually distinguished from static template slots (e.g., with a label indicating their origin).
3. **Given** the ontology defines properties that already exist in the static template, **When** expansion runs, **Then** those properties are not duplicated.

---

### User Story 6 - End-to-End Multi-Format Evaluation (Priority: P3)

As a quality analyst, I want to process a new document type (e.g., cleaning validation report) end-to-end — from document upload through template matching, extraction, gap filling, coverage calculation, to final report generation — without writing any new extraction rules.

**Why this priority**: Validates the complete generalization capability. Success here proves the system can scale to new document types through configuration and ontology modeling alone.

**Independent Test**: Can be tested by uploading a template for a new document type, configuring the mapping, uploading a document, and verifying the full pipeline produces a meaningful coverage report and generated document.

**Acceptance Scenarios**:

1. **Given** a new template has been uploaded and mapped to a document type, **When** a document of that type is uploaded and processed, **Then** the system produces a coverage report using the correct template without any code changes.
2. **Given** the full pipeline runs for the new document type, **When** the analyst reviews results, **Then** extracted values include source references and the coverage manifest correctly reflects the new template's required/optional slot structure.

---

### Edge Cases

- What happens when a template is deleted while active evaluation jobs reference it? The system preserves historical results using a snapshot of the template data captured at report generation time; the deleted template is no longer available for new evaluations.
- How does the system handle documents where no text sections can be identified for gap filling? Gap filling is skipped with no error; the analyst sees the same results as if gap filling were disabled.
- What happens if the local extraction model is unavailable when gap filling is enabled? The system logs a warning, skips gap filling, and returns results from rule-based extraction only (zero regression on the primary extraction path).
- What if a template update changes slot identifiers while historical reports reference old identifiers? Updating a template creates a new version; historical reports retain their association with the original version.
- What happens if ontology expansion generates hundreds of slots? The system generates all applicable slots but groups them separately so the analyst can distinguish them from the core template slots.

## Requirements *(mandatory)*

### Functional Requirements

**Template Management (Phase 1)**

- **FR-001**: System MUST allow authorized users to upload report templates with structural validation (unique slot identifiers, valid source kinds, consistent required/on-missing logic).
- **FR-001a**: System MUST provide a visual editor that allows users to add, remove, and reorder slots within a template's sections and groups, toggle slot required/optional status, and edit slot metadata — saving changes creates a new template version.
- **FR-002**: System MUST support template versioning — updates (whether via re-upload or visual editor) create new versions while preserving prior versions for historical traceability.
- **FR-003**: System MUST enforce that exactly one template is marked as the default at any time.
- **FR-004**: System MUST allow users to create, view, and delete document-type-to-template mapping rules with configurable priority ordering.
- **FR-005**: System MUST automatically resolve the appropriate template for a given document type using mapping rules, falling back to the default template when no mapping matches, and further falling back to the original built-in template when no default exists.
- **FR-006**: System MUST seed the existing built-in template as the initial default upon migration, ensuring zero regression for current workflows.
- **FR-007**: System MUST prevent deletion of the default template.

**Template Switching (Phase 1)**

- **FR-008**: The AST coverage page MUST display a template selector when multiple templates exist, allowing the analyst to switch templates and recalculate coverage in place.
- **FR-009**: When only one template exists, the template selector MUST be hidden.

**Extraction Gap Filling (Phase 2)**

- **FR-010**: When enabled and missing required slots exist after rule-based extraction, the system MUST automatically attempt to extract missing values from the document text.
- **FR-011**: Gap filling MUST be disabled by default; enabling it is a system-wide administrator setting (not per-job).
- **FR-012**: Each value produced by gap filling MUST include a source text snippet from the original document for auditability.
- **FR-013**: Gap-filled extraction results MUST be visually distinguished from rule-based results in the analyst UI via a subtle badge or icon on each gap-filled slot, and MUST be fully distinguishable in the admin audit trail.
- **FR-014**: Gap filling MUST NOT alter, replace, or override any values already extracted by rule-based finders.
- **FR-015**: When gap filling is disabled or unavailable, the system MUST behave identically to the current system with no performance degradation and no changes to output.

**Ontology-Driven Expansion (Phase 3)**

- **FR-016**: When enabled, the system MUST query the ontology knowledge model for data properties associated with the document's class and generate additional extraction slots not already present in the static template.
- **FR-017**: Dynamically generated slots MUST be grouped separately from static template slots in the coverage display.
- **FR-018**: Dynamic slot expansion MUST NOT modify the stored template — expansion is computed at evaluation time only.
- **FR-019**: Expanded slots MUST support the same gap-filling process as static template slots.

**Compliance & Auditability**

- **FR-020**: The deterministic evaluation engine (risk scoring, rule evaluation, coverage validation, report rendering) MUST NOT be modified by this feature — no probabilistic or model-based logic may enter the evaluation path.
- **FR-021**: Historical reports MUST retain a snapshot of the template and coverage data used at generation time, unaffected by subsequent template changes.
- **FR-022**: All extraction source tracking MUST distinguish between rule-based and model-based origins in audit logs.

### Key Entities

- **Report Template**: A named, versioned evaluation schema defining sections, groups, and slots that describe what information should be extracted from a document. Key attributes: name, version, document number, slot definitions, default status.
- **Document Type Mapping**: A prioritized rule linking a document classification identifier to a specific report template. Used for automatic template resolution.
- **Extraction Slot**: An individual information point within a template (e.g., "PDE value", "Drug Product Name"). Has a source type, required/optional status, and belongs to a group within a section.
- **Coverage Manifest**: The result of evaluating extracted data against a template — tracks which slots are filled, missing, or not applicable. Snapshots are preserved with generated reports.
- **Extraction Edge**: A unit of extracted data linking a document span to a knowledge graph entity. Carries source attribution (rule-based vs. gap-filled) and the original text snippet.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Users can upload and configure a new document type template in under 5 minutes without technical assistance.
- **SC-002**: Automatic template matching correctly selects the right template for at least 95% of processed documents without manual intervention.
- **SC-003**: First-pass coverage rate (percentage of required slots filled after all extraction passes) improves by at least 30% compared to rule-based extraction alone, measured on a representative sample of 10+ documents.
- **SC-004**: Switching templates on the AST coverage page recalculates and displays updated coverage within 3 seconds.
- **SC-005**: 100% of existing workflows produce identical results when gap filling and ontology expansion are disabled (zero regression).
- **SC-006**: Every gap-filled value is accompanied by a source text reference that an analyst can locate in the original document within 30 seconds.
- **SC-007**: Adapting the system to a new document type (template + mapping + ontology properties) requires zero code changes — configuration and ontology modeling only.
- **SC-008**: Gap filling completes within 10 seconds per document on the target deployment hardware.

## Assumptions

- The target deployment environment provides local compute resources capable of running a language model for extraction tasks (as specified in the design document's prerequisites).
- The existing ontology knowledge model (T-Box) contains data property definitions for the document classes that will benefit from dynamic slot expansion.
- The existing rule-based extraction finders (10 endpoint finders) will remain unchanged — this feature augments but does not replace them.
- Template structural validation reuses the existing template schema definitions already in the codebase.
- This feature builds upon the completed AST coverage contract (feature 010) and AST extraction UI (feature 011).
- Role-based access control for template management follows the existing authorization model (senior analyst permissions for write operations).
- The feature is scoped to the AST report pipeline only; other report types or evaluation pipelines are not affected.
- Gap filling and ontology expansion are independently toggleable — each can be enabled or disabled without affecting the other.
