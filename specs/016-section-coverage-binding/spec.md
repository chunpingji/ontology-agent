# Feature Specification: Section-Level Ontology Coverage Binding

**Feature Branch**: `016-section-coverage-binding`

**Created**: 2026-07-05

**Status**: Draft

**Input**: User description: "@docs/section-coverage-declaration-design.md — replace authored/AI-invented extraction slots with section-level coverage declarations compiled from the ontology; define the no-omission signal at relationship granularity."

## Clarifications

### Session 2026-07-05

- Q: When AI analysis proposes a new ontology relationship binding for a section, what is the default `required` flag? → A: Required by default — every AI-proposed relationship binding starts `required: true`; the author demotes genuinely-conditional relationships to optional. (Consistent with the accepted §4 decision that the relationship *is* the no-omission contract.)
- Q: What happens to a section position that looks data-sourced but the AI cannot bind to any ontology relationship? → A: Surface it as an explicit **unresolved candidate** for author disposition (bind / convert to constant or human-filled / discard); it MUST NOT silently become a human-filled slot. Preserves controllability and prevents recreating the "everything → manual" defect.
- Q: When the same relationship is declared in multiple sections and is missing from a source graph, how is the omission counted? → A: Allowed, deduped globally — a missing required relationship is **one** distinct omission keyed by (document-type + relationship + target-type), however many sections declare it; sections still show the binding for narrative context. Avoids inflating the no-omission signal.

## User Scenarios & Testing *(mandatory)*

A **template author** (a GMP compliance expert / senior analyst) builds and maintains the report templates that drive "可控无遗漏" (controllable, no-omission) risk-assessment report generation. Today they run an AI-assisted analysis over a representative sample document to seed a template. Two failures make the result unusable without heavy hand-correction:

1. Every data field the analysis proposes is classified as **"human-filled" (manual)**, even fields that should be pulled automatically from the source document's knowledge graph.
2. Some fields are anchored to a **concrete value from the sample** (e.g., a specific equipment id) instead of the **type of thing** the field represents — so the template does not generalize to any other source document.

The underlying cause is that the analysis never consults the authoritative ontology of entity types and relationships; it invents field names and then attempts a last-step exact text match that nearly always fails, dropping everything to "human-filled."

This feature raises the authoring unit from *individual fields* to *section-level coverage declarations* expressed in the ontology's own vocabulary (entity types + relationships), and compiles the fine-grained coverage checklist from the ontology at validation time.

### User Story 1 - AI analysis produces ontology-grounded section coverage, not manual-everything (Priority: P1)

When a template author runs AI-assisted analysis on a sample source document, each section is mapped to the **actual relationships available from the source document's entity type** (as defined in the ontology), and graph-sourced coverage is expressed as relationship declarations — not a flat list of human-filled fields, and never anchored to a sample-specific individual.

**Why this priority**: This is the defect that makes the current workflow unusable. Without it the author must re-classify and re-anchor every field by hand, and the resulting template silently fails on the next document. It is the minimum viable slice: even alone, it turns AI analysis from "produces garbage" into "produces a correct, reusable coverage skeleton."

**Independent Test**: Run AI analysis on a CMC-type sample document. The section corresponding to "风险评估对象基本描述" returns coverage declarations for the document type's real relationships (e.g., *describes → DrugProduct*, *hasSynthesisRoute → SynthesisRoute*) rather than a pile of human-filled fields, and no declaration references a concrete instance such as an equipment id.

**Acceptance Scenarios**:

1. **Given** a sample document whose entity type has known relationships in the ontology, **When** the author runs AI analysis, **Then** each graph-sourced section is expressed as one or more ontology relationship declarations (entity type + relationship + target type), and none default to "human-filled."
2. **Given** the sample document mentions a concrete individual (a specific instance), **When** the analysis proposes coverage, **Then** the declaration references the individual's **type**, never the individual itself.
3. **Given** a section that corresponds to content not present in the entity graph, **When** analysis runs, **Then** the section is still produced (with narrative/human-filled positions) without error.

---

### User Story 2 - No-omission signal defined at relationship granularity (Priority: P2)

At validation / report-generation time, the system expands each declared relationship into its target type's property checklist using the ontology. A declared relationship that is **entirely absent** from a given source document's graph is a **true omission** and must be flagged; individual properties left blank under a **present** relationship are **informational** (not omissions) unless a property has been explicitly promoted to required.

**Why this priority**: This is what makes the coverage contract trustworthy. It converts "the author enumerated fields by hand" into "the ontology enumerates positions, and the omission signal is calibrated so it isn't drowned out." It builds directly on US1's declarations.

**Independent Test**: Validate a template against two source graphs of the same type: one missing the target entity of a required relationship entirely, one containing that entity but with some blank properties. The first flags the relationship as a required omission; the second raises zero omission flags and instead marks the blank properties as informational.

**Acceptance Scenarios**:

1. **Given** a required relationship declaration and a source graph that contains no instance of its target type, **When** coverage is validated, **Then** the relationship is reported as a required omission (a "无数据 / true omission" signal), distinguishable from "confirmed low risk / present-but-empty."
2. **Given** a required relationship whose target instance is present but some of its properties are blank, **When** coverage is validated, **Then** those properties are reported as informational (blank, no flag), and the section is **not** reported as an omission.
3. **Given** a property that has been explicitly promoted to required, **When** that property is blank in the source graph, **Then** it is reported as a required omission.

---

### User Story 3 - Author edits section coverage; non-graph fields are preserved (Priority: P3)

The template editor presents **section-level coverage declarations** (entity-type + relationship selectors) for the author to review and adjust, and no longer forces graph-sourced coverage into "human-filled." Fields that genuinely are not in the entity graph — rule-derived risk dimensions, constant boilerplate, and truly human-filled signatures — remain distinct field types and are preserved untouched.

**Why this priority**: It closes the loop for the author (review/override the compiled coverage) and guarantees the change does not regress the legitimately non-graph parts of a template. It is valuable but depends on US1/US2 existing first.

**Independent Test**: Open a template in the editor. Graph-sourced coverage appears as editable relationship declarations; rule / constant / human-filled slots appear unchanged and still generate correctly.

**Acceptance Scenarios**:

1. **Given** a template with graph-sourced coverage, **When** the author opens the editor, **Then** that coverage is shown as section-level relationship declarations the author can review, add, or remove — not as per-field extraction bindings.
2. **Given** a template that also contains rule-derived, constant, and human-filled fields, **When** the author opens it, **Then** those fields are unchanged and are not re-classified.
3. **Given** an author removes or marks-optional a relationship declaration, **When** the template is saved and re-validated, **Then** the omission signal reflects the change.
4. **Given** AI analysis produced an unresolved candidate (a data-sourced-looking position it could not bind), **When** the author opens the editor, **Then** the candidate is shown as pending disposition and the author can bind it to a relationship, convert it to constant/human-filled, or discard it — it is not pre-classified as human-filled.

**Design reference**: The authoring surface for this story is specified visually by the design file `design.pen` → frame **"Slot Tree Component — Full View"**. Per section it shows: an **ontology coverage declaration** area (本体覆盖声明) listing declarations as *relationship → target type* with a required/optional toggle and an ontology-expanded property checklist (scenarios 1 & 3); an **unresolved-candidates** pending list (待解析候选) offering *bind / constant / human-filled / discard* disposition (scenario 4); and the section's **rule / constant / human-filled slots preserved as a distinct group** that does not participate in ontology coverage (scenario 2). An AI-proposed declaration appears as a coverage declaration awaiting *adopt / ignore* — never as a pre-classified human-filled field.

---

### Edge Cases

- **Entity type with no relationships in the ontology**: the section is produced with no ontology bindings (narrative / human-filled positions), never an error.
- **Unbindable data-sourced position**: AI finds a field that looks data-sourced but matches no relationship edge → surfaced as an unresolved candidate for author disposition, never silently made human-filled.
- **Declared relationship present in ontology but zero instances in a source graph**: the core omission case — flagged as required omission when required.
- **Target type with no data properties**: relationship-level presence check only; no property checklist to expand.
- **Ontology unavailable / read-only access down**: graceful degradation — existing report generation continues via the legacy path with no regression, and no false "degraded" state for a normally-offline system.
- **Legacy templates** carrying per-field extraction bindings authored before this feature: continue to resolve and generate reports unchanged.
- **Overlapping declarations** across sections (same relationship covered twice): allowed; the omission is deduped globally and counted once (keyed by document-type + relationship + target-type), so overlap never double-counts or produces conflicting omission signals.
- **Fact-source–style sections** (e.g., organization/department responsible persons) whose data source is not yet implemented: modeled as a declared placeholder; positions remain human-filled until that data source lands.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The system MUST allow a template author to declare, at the **section** level, which source-document relationships a section covers, using entity types and relationships defined in the authoritative ontology (not free-text or AI-invented field names).
- **FR-002**: When AI-assisted analysis runs on a sample source document, the system MUST map each section to the relationships actually available from the source document's entity type, and MUST express graph-sourced coverage as ontology relationship declarations rather than defaulting them to human-filled fields.
- **FR-003**: The system MUST NOT permit a coverage declaration to reference a concrete instance/individual from the sample; declarations MUST reference entity **types** and relationships only.
- **FR-004**: At validation / generation time, the system MUST expand each declared relationship into its target type's property checklist using the ontology, without the author enumerating properties by hand.
- **FR-005**: The system MUST classify a **required** declared relationship that is entirely absent from a source document's graph as a **required omission** (the no-omission signal), distinguishable from "present but empty."
- **FR-005a**: An AI-proposed relationship binding MUST default to **required**; the author MAY demote a genuinely-conditional relationship to optional. (A relationship binding is the no-omission contract, so its default state is the flagged state.)
- **FR-006**: The system MUST classify blank individual properties under a **present** relationship as **informational** (blank, not an omission) by default, and MUST NOT raise omission flags for them.
- **FR-007**: The system MUST allow a property to be **explicitly promoted to required**, after which its absence is reported as a required omission.
- **FR-008**: The system MUST preserve section-level fields that are not sourced from the entity graph — rule-derived (risk) dimensions, constant/boilerplate values, and genuinely human-filled fields — as distinct field types, and MUST NOT collapse graph-sourced coverage into human-filled fields.
- **FR-008a**: When AI analysis identifies a data-sourced-looking position that it cannot bind to any ontology relationship, the system MUST surface it as an explicit **unresolved candidate** for author disposition (bind to a relationship / convert to constant or human-filled / discard). The system MUST NOT silently classify an unbindable position as a human-filled slot.
- **FR-009**: The template editor MUST present section-level coverage declarations (entity-type + relationship selectors) for author review and adjustment, in place of per-field extraction binding.
- **FR-010**: A template authored from one sample document MUST resolve against other source documents of the same entity type without re-authoring — i.e., coverage MUST NOT depend on any sample-specific value.
- **FR-011**: The no-omission proof (coverage manifest) MUST continue to enumerate per-position coverage status (filled / informational-blank / required-omission / human-filled), with positions derived from the ontology rather than author- or AI-invented.
- **FR-011a**: The same relationship MAY be declared in multiple sections. A missing required relationship MUST be reported as a **single** distinct omission, keyed by (document entity type + relationship + target entity type), regardless of how many sections declare it. Each declaring section MAY still surface the binding for narrative context, but the omission count MUST NOT be inflated by overlap.
- **FR-012**: When the ontology is unavailable, or a section maps to no ontology relationships, the system MUST degrade gracefully — produce the section with no ontology bindings, raise no error, and cause no regression to existing report generation — consistent with the platform's offline-first / graceful-degradation posture.
- **FR-013**: Existing templates carrying legacy per-field extraction bindings MUST continue to resolve and generate reports without change (backward compatibility).
- **FR-014**: The section coverage model MUST be extensible to a future non-graph **fact-source** binding (e.g., organization/department responsible persons) as a declared placeholder, without blocking the ontology-relationship capability. The fact-source data itself is out of scope for this feature (positions remain human-filled until that source is implemented).
- **FR-015**: All ontology access for coverage compilation MUST be **read-only** (no writes to the authoritative model).

### Key Entities *(include if feature involves data)*

- **Section Coverage Declaration**: a section's ordered list of coverage bindings. Replaces the section's authored graph-sourced fields as the unit of authoring.
- **Ontology Relationship Binding**: one coverage binding = (document entity type, relationship, target entity type, required flag). The authoring vocabulary; physically incapable of naming a concrete individual. Its omission identity is keyed by (document entity type + relationship + target entity type) — the same key may appear in multiple sections but is one distinct coverage obligation.
- **Unresolved Candidate**: a data-sourced-looking position that AI analysis could not bind to any relationship; awaits explicit author disposition (bind / convert to constant or human-filled / discard) and is never auto-classified as human-filled.
- **Fact-Source Binding** *(placeholder)*: one coverage binding = (fact source, selector/query). Modeled now, data source deferred.
- **Relationship Graph (of a document entity type)**: the set of relationships available from an entity type, each carrying its target type and that type's properties — the class-level structure that coverage declarations anchor to and that validation expands.
- **Coverage Manifest (no-omission proof)**: the per-position coverage report produced at validation/generation, with positions compiled from the ontology; statuses include filled, informational-blank, required-omission, and human-filled.
- **Non-graph field** (retained slot-level): rule-derived risk dimension, constant/boilerplate value, or genuinely human-filled field — preserved, not collapsed.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: After AI analysis of a representative source document, **zero** graph-sourced fields default to "human-filled"; every section that corresponds to source-graph content is expressed as ontology relationship declarations.
- **SC-002**: **100%** of coverage declarations reference an entity type + relationship; **zero** reference a concrete individual/instance.
- **SC-003**: Omission detection is calibrated: a source graph missing a required relationship's target entity is flagged as a required omission **100%** of the time, while a source graph where the relationship is present but properties are blank produces **zero** false omission flags.
- **SC-004**: A template built from one sample document validates and generates correctly against a **different** source document of the same entity type **without any re-authoring**.
- **SC-005**: For a representative sample document, the number of manual author corrections needed to turn the AI-analysis output into a usable template drops by at least **80%** compared with the current per-field workflow.
- **SC-006**: Existing (legacy) templates generate reports with **no regression** — identical coverage outcomes before and after this feature.

## Assumptions

- **Document entity type is an upstream input**: the source document's entity type (e.g., CMCReport) is already determined by document classification and is provided to this feature; classifying the document is out of scope.
- **Property→required promotion mechanism**: the default promotion path is a **per-section coverage override** (author-controlled), not an ontology annotation, because the ontology is read-only in this posture and per-section keeps authoring self-contained. (The design note leaves ontology-annotation-based promotion as a possible later addition.)
- **Relationship depth**: authoring anchors to **direct (single-hop)** relationships of the document entity type; the ontology expands the target type's properties. Deeper multi-hop chains are out of scope for authoring in this iteration.
- **Fact-source data source** (organization/department): out of scope here; only the binding placeholder is modeled. See the R&D document fact-source design for the eventual data model.
- **Backward compatibility**: previously persisted templates with per-field extraction bindings keep working via a backward-compatible resolution path; migration to declarations is not forced.
- **Actor / roles**: the template author is a `senior_analyst`; report reviewers/QA consume the coverage manifest. Existing role gating applies unchanged.
- **Read-only, air-gapped operation**: all ontology reads occur offline against the published model; no external network dependency is introduced.

## Out of Scope

- Implementing the organization/department fact-source data provider (only the binding placeholder is defined here).
- Ontology-annotation-driven required-property promotion (per-section override only, for this iteration).
- Multi-hop (chained) relationship authoring.
- Automatic migration of legacy templates to the new declaration model.
