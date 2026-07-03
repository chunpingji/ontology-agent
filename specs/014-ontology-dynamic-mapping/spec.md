# Feature Specification: Ontology Dynamic Object Mapping

**Feature Branch**: `014-ontology-dynamic-mapping`

**Created**: 2026-07-02

**Status**: Draft

**Input**: User description: "Make source-to-ontology mapping declaration-driven and runtime-authoritative: extend class-level mapping (E6) with source-entity binding types, add a new property-level binding layer (E6b), consume both from database and API extraction adapters, make edges→facts conversion ontology-aware (hierarchy, domain/range, external alignments, controlled vocabulary), and make entity alignment aware of the class hierarchy."

## Clarifications

### Session 2026-07-02

- Q: API source — scope for this feature (defer the adapter, build it now as P2, or exclude API entirely)? → A: In scope as P2 — build the API runtime adapter now. API sources are **internal/intranet** endpoints; the platform's air-gap posture prohibits the *public internet and public cloud*, not internal-network integration, so an internal API is an in-posture source rather than an offline-first violation.
- Q: API adapter breadth — which protocols / auth / pagination should the connector cover in this feature? → A: REST/JSON only. Auth = API key or bearer token; pagination = configurable (offset/page or cursor). GraphQL and OAuth2 authorization-code (interactive) flows are deferred; the binding model stays protocol-neutral so they can be added later without reworking declared bindings.
- Q: How does an object-property (relationship) binding resolve its target entity? → A: Support both forms, the binding declaring which — (a) *identifier reference* (target class + its identifier source field; link to the existing/aligned individual, or a new review candidate) for database foreign keys, and (b) *nested object* (recurse into an embedded/nested source structure and extract the target as its own sub-candidate) for API nested JSON.
- Q: Can one ontology class be populated from multiple sources at once (binding cardinality)? → A: Yes — a class MAY have multiple class bindings, at most one per distinct source (uniqueness per (class, source)), each with its own property bindings. An extraction job targets exactly one class binding, and candidates from different sources for the same class are reconciled through identifier-based alignment rather than duplicated.
- Q: Confirm GAP-3 (schema-derived document relation finders) and GAP-4 runtime (dynamic AST slot resolution) stay out of scope for this feature? → A: Yes — both remain out of scope and are deferred to separate later features; feature 014 stays focused on the declarative structured-source pipeline (DB + API), ontology-aware fact building, and hierarchy-aware alignment.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Declarative property-level binding drives database extraction (Priority: P1)

As a senior analyst, I want to declare — entirely in the ontology editor — how a source database table maps to an ontology class and how each of its columns maps to a data or object property (including value transforms such as controlled-vocabulary normalization), so that a database extraction run turns that source into knowledge-graph candidates without any developer writing or changing code.

**Why this priority**: This is the core missing capability. Today, mapping declarations are "dead data" that no runtime code consumes, and every new table or column requires a developer to hand-code a mapping. Delivering a complete vertical slice for the database source — declare → extract → traceable candidates — converts the declarations into the authoritative, runtime-consumed source of mapping truth. It is the minimum viable product and the foundation every other story builds on.

**Independent Test**: Declare a table→class binding plus several column→property bindings for an existing test class, run a database extraction job against a test data source, and verify the produced candidates carry the declared property identifiers, the transformed values, and a resolvable provenance reference — all without touching application code.

**Acceptance Scenarios**:

1. **Given** an analyst has declared table `drug_product` → class `DrugProduct` and column `approval_no` → property `approvalNumber`, **When** a database extraction job runs, **Then** each source row produces one candidate whose `approvalNumber` equals that row's column value.
2. **Given** a column binding declares a controlled-vocabulary transform (e.g., source value "高" → ontology term `HighRisk`), **When** extraction runs, **Then** the candidate's value is the normalized ontology term, not the raw source value.
3. **Given** an analyst renames the target property in an existing binding, **When** the next extraction job runs, **Then** the output reflects the new binding with zero code changes and zero redeployment of extraction logic.
4. **Given** a declared column does not exist in the source table at run time, **When** extraction runs, **Then** the job completes with a partial result, marks the affected mapping's health as drifted, and records a clear warning — it does not crash.
5. **Given** a binding is flagged as the identifier property for a class, **When** extraction produces candidates, **Then** those candidates are aligned against existing individuals using that identifier.
6. **Given** a foreign-key column is bound to object property `manufacturedBy` in identifier-reference mode (target class `Manufacturer`, identifier field `mfr_code`), **When** extraction runs, **Then** each row yields a `manufacturedBy` relationship linking to the `Manufacturer` individual carrying that `mfr_code`, or a new review candidate for it when none exists.

---

### User Story 2 - Declarative mapping extends to API sources (Priority: P2)

As a senior analyst, I want to declare an API endpoint → class binding and response-field (path) → property bindings, and have an API extraction job pull data through a configured connector (which handles token-based authentication and pagination) and produce the same unified candidate output as the database adapter, so that internal (intranet) REST/JSON sources become first-class extraction inputs without code changes for each new endpoint or field.

**Why this priority**: Extends the same declaration model to a source type that is currently entirely unsupported (the pipeline rejects it today). It is high value as a new integration capability but depends on connector-runtime lifecycle work and is not required to prove the core declaration→extraction loop, so it follows the MVP.

**Independent Test**: Configure a test connector, declare an endpoint binding plus response-path→property bindings, run an API extraction job, and verify candidates match the declared bindings with correct provenance; then take the connector offline and verify graceful degradation.

**Acceptance Scenarios**:

1. **Given** endpoint `/api/drugs` → class `DrugProduct` and response path `$.data[*].name` → property `drugName`, **When** an API extraction job runs against a reachable connector, **Then** each returned item yields a candidate with the mapped values.
2. **Given** the internal API service (an intranet endpoint) is unreachable, **When** an API extraction job runs, **Then** it degrades gracefully (empty output with a recorded reason) without failing the pipeline; the platform's air-gap posture (no public internet/cloud) is never itself treated as this failure.
3. **Given** a new API source type, **When** an analyst creates and runs an extraction job for it, **Then** job creation, execution, and candidate review all work end-to-end alongside the existing source types.
4. **Given** an API item embeds a nested `manufacturer` object bound to object property `manufacturedBy` in nested-object mode, **When** extraction runs, **Then** the nested object is extracted as its own candidate and linked to the parent item via `manufacturedBy`.

---

### User Story 3 - Ontology-aware fact building for reasoning (Priority: P2)

As a platform maintainer, I want the conversion of extraction edges into reasoning facts to consult the ontology — class hierarchy, property domain/range, controlled vocabulary, and external-standard alignments — instead of relying on hardcoded class-name string matches, so that newly added subclasses, renamed properties, and external alignments (e.g., IDMP, ICH Q9) are picked up automatically by reasoning without code changes.

**Why this priority**: Removes brittle hardcoded coupling in the fact bridge and unlocks alignment-based reasoning. It is valuable and independent of the source adapters, but reasoning already functions today through the hardcoded path, so it is an enhancement rather than the MVP.

**Independent Test**: Feed edges that reference a subclass of an existing class, a newly added data property, and a class carrying an external alignment; verify the resulting facts recognize the subclass as a class member (via hierarchy), gate the property value by its declared domain, and expose the external alignment — all without code changes.

**Acceptance Scenarios**:

1. **Given** an edge whose object is a subclass of `DrugProduct`, **When** facts are built, **Then** it is recognized as a drug-class member via the ontology hierarchy rather than a hardcoded class name.
2. **Given** a data property whose declared domain does not include the subject's class, **When** facts are built, **Then** that value is not asserted onto the subject (no cross-domain leakage).
3. **Given** a class that declares an external alignment to a standard-body class, **When** facts are built, **Then** the alignment appears in the facts' alignment view so reasoning rules can consult it.
4. **Given** a scalar value that maps to a controlled-vocabulary term, **When** facts are built, **Then** the value is normalized to the ontology's standard term.

---

### User Story 4 - Hierarchy-aware entity alignment (Priority: P3)

As a senior analyst, I want entity alignment to consider existing individuals across the subclass/superclass chain — not only the exact target class — so that a duplicate entity that was previously recorded at a different level of the class hierarchy is detected as a merge instead of being created as a new duplicate.

**Why this priority**: A data-quality and deduplication improvement that is independent of, and lower urgency than, establishing the declarative mapping pipeline. It refines an existing behavior rather than enabling a new capability.

**Independent Test**: Seed an individual under a subclass, then present a candidate typed at the parent class that matches by identifier or label; verify alignment returns a merge to the existing individual and records which hierarchy level and method produced the match.

**Acceptance Scenarios**:

1. **Given** an existing individual of a subclass and a candidate typed at its parent class with the same identifier, **When** alignment runs, **Then** it returns a merge to the existing individual.
2. **Given** candidate matches exist at multiple hierarchy levels, **When** alignment runs, **Then** resolution follows the documented precedence (subclass → same class → parent class) and records the matched level and method for audit.
3. **Given** the hierarchy match is genuinely ambiguous (multiple equally-ranked candidates), **When** alignment runs, **Then** the entity is not auto-merged and is surfaced for human review.

---

### Edge Cases

- **Source schema drift**: a declared column or response field is absent at run time → the mapping's health is marked drifted, a warning is emitted, the affected binding is skipped, and the job still completes with a partial result.
- **Missing credentials / unreachable source**: a referenced database DSN environment variable is unset, or an API connector is unreachable → the job fails with a clear, actionable reason (or, when offline, degrades to empty output) and never exposes credentials.
- **Transform failure**: a value cannot be cast, or has no controlled-vocabulary match → the individual value is recorded with an issue note while other values on the same record still succeed; the row is not discarded.
- **Domain mismatch at declaration time**: an analyst binds a property to a class not in the property's declared domain → declaration validation surfaces a warning (or block) before the mapping can drive extraction.
- **Ambiguous cross-hierarchy alignment**: matches of equal rank at different levels → no automatic merge; surfaced for review.
- **Self-referential / circular or deeply nested object-property binding** (e.g., a table with a foreign key to itself, or nested API objects that recurse) → resolution terminates safely within a bounded depth, with no infinite recursion.
- **Backward compatibility**: a source configured through the existing (pre-declaration) mechanism, with no property-level binding declared → continues to extract exactly as it does today.

## Requirements *(mandatory)*

### Functional Requirements

**Declaration model**

- **FR-001**: The system MUST allow a senior analyst to declare a binding between an ontology class and a source entity, where the source entity is a database table, an API endpoint, or a document-type pattern, extending the existing class-level mapping.
- **FR-024**: A single ontology class MAY have multiple class bindings, at most one per distinct source (uniqueness per (class, source)), each carrying its own property bindings. An extraction job MUST target exactly one class binding, and candidates originating from different sources for the same class MUST be reconciled through identifier-based entity alignment rather than duplicated.
- **FR-002**: The system MUST allow a senior analyst to declare property-level bindings that map an ontology data property or object property to a specific source field (a table column, an API response path, or a document slot key) under a parent class binding.
- **FR-023**: For object-property (relationship) bindings, the system MUST support two declared resolution modes, with each object-property binding declaring which applies: (a) **identifier reference** — the binding names the target class and its identifier source field, and the system links to the existing/aligned individual carrying that identifier, creating a review candidate when none exists (suited to database foreign keys); and (b) **nested object** — the binding names an embedded/nested source structure (e.g., a nested API object or joined rows) and the system extracts the target as its own sub-candidate, linked by construction (suited to API nested JSON).
- **FR-003**: The system MUST allow a property binding to specify a value transform — at minimum: none, controlled-vocabulary normalization, pattern validation, and type cast — together with its configuration.
- **FR-004**: The system MUST allow a property binding to be flagged as an identifier attribute and/or a label attribute for use by entity alignment.
- **FR-005**: The system MUST validate mapping declarations against the ontology — including that a bound property's declared domain includes the bound class, and that referenced classes/properties exist and are not disabled — and MUST distinguish blocking errors from non-blocking warnings.
- **FR-006**: The system MUST persist mapping declarations with version numbers and audit records (actor, action, entity, timestamp), consistent with other editable ontology objects, and MUST NOT store source credentials — only environment-variable references or connector references.

**Runtime consumption**

- **FR-007**: When a database extraction job runs for a selected table-type class binding, the system MUST read that class binding and its property bindings and produce extraction output using them, with no code change required to onboard a new table or column.
- **FR-008**: When an API extraction job runs for a selected endpoint-type class binding, the system MUST pull data via the configured connector and produce the same unified output format as the database path, applying the declared property bindings. The API connector MUST support REST/JSON endpoints with API-key or bearer-token authentication and configurable pagination (offset/page or cursor); the binding model MUST remain protocol-neutral so additional protocols (e.g., GraphQL) and auth schemes can be added later without changing declared bindings.
- **FR-009**: The system MUST apply declared value transforms during extraction: normalize controlled-vocabulary values to ontology terms, cast declared types, and validate declared patterns.
- **FR-010**: Every produced candidate/edge MUST carry a complete, resolvable provenance reference identifying the source system, the source entity, and the source record.
- **FR-011**: The system MUST support the API source type end-to-end — job creation, execution, and candidate review — alongside the existing source types.

**Fact building & reasoning**

- **FR-012**: When converting extraction edges into reasoning facts, the system MUST determine class membership (e.g., drug-class markers) via the ontology class hierarchy rather than hardcoded class-name matching.
- **FR-013**: When building facts, the system MUST assert a data-property value onto a subject only when the property's declared domain includes the subject's class.
- **FR-014**: When building facts, the system MUST populate the external-alignment view from the ontology's class alignment declarations so reasoning rules can consult external-standard alignments.
- **FR-015**: When building facts, the system MUST normalize scalar values against the ontology's controlled vocabulary.

**Entity alignment**

- **FR-016**: When aligning an extracted entity, the system MUST consider existing individuals across the subclass/superclass chain, not only the exact target class.
- **FR-017**: The system MUST resolve hierarchy matches by a documented precedence (subclass → same class → parent class), MUST record the matched level and method for audit, and MUST NOT auto-merge genuinely ambiguous matches.

**Compatibility & degradation**

- **FR-018**: The system MUST remain backward compatible: sources configured through existing pre-declaration mechanisms MUST continue to function unchanged until they are migrated to declaration-driven mapping.
- **FR-019**: When an internal source is unreachable, extraction MUST degrade gracefully (empty output with a recorded reason) without failing the pipeline. The platform's air-gap posture (no public internet/cloud) MUST NOT itself be treated as a source failure or marked as degraded, because permitted sources are internal-network services.
- **FR-022**: The system MUST restrict configured database and API sources to internal-network (intranet) endpoints and MUST NOT require or initiate connections to the public internet or public cloud services at run time.
- **FR-020**: When a declared source field is absent at run time (schema drift), the system MUST mark the mapping's health as drifted, emit a warning, and skip the affected binding without aborting the job.
- **FR-021**: The system MUST reflect mapping health (ok / unmapped / drift / orphan) based on validation against the ontology and, where feasible, the source, and MUST surface it to analysts in the mapping management view.

### Key Entities

- **Class Binding** (extends the existing class-level mapping): the association from an ontology class to a source entity. Carries a mapping type (existing ontology/upper-ontology alignments plus new types for database table, API endpoint, and document-type pattern), a target identifier (table name / endpoint path / document pattern), and a source-system reference (a DSN environment-variable name or a connector reference). Stores no credentials. A class may own multiple class bindings — at most one per distinct source — so it can be populated from several sources concurrently; each binding owns its own property bindings.
- **Property Binding** (new): the association from an ontology data or object property to a source field, owned by a Class Binding. Carries the source path (column name / response path / document slot key), the value transform and its configuration, and flags marking it as an identifier and/or label attribute for alignment. For object properties, it additionally carries a resolution mode — *identifier reference* (target class plus the target identifier source field) or *nested object* (a nested/embedded source path plus the child Class Binding that types the extracted sub-candidate) — determining how the related individual is resolved.
- **Source Connection** (source registry / connector): a credential-safe, configured handle to a source system (a database DSN reference or an API connector) used at run time. Owns lifecycle concerns — authentication, pagination, health — not mapping semantics. For this feature the API connector targets REST/JSON endpoints with API-key or bearer-token authentication and configurable pagination; GraphQL and OAuth2 authorization-code flows are deferred.
- **Extraction Edge / Candidate** (existing): the unified output of any source adapter — subject class, predicate, object class, data properties, and a provenance reference — consumed downstream by fact building and human review.
- **Fact View** (existing, enhanced): the normalized reasoning input built from edges. Gains ontology-aware population — hierarchy-based class membership, domain-gated data values, external alignments, and controlled-vocabulary-normalized scalars.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: An analyst can bring a new database table into the knowledge graph by declaring its class and column bindings entirely in the ontology editor, with zero code changes and no developer involvement.
- **SC-002**: Adding or renaming an ontology property and re-running extraction reflects the change in the output with zero application code files modified for that scenario.
- **SC-003**: 100% of extraction outputs carry a complete, resolvable provenance reference back to their source system and record.
- **SC-004**: In the defined test scenarios, newly added subclasses and externally-aligned classes are recognized in reasoning facts 100% of the time, compared with 0% under the previous hardcoded fact-building path.
- **SC-005**: Cross-hierarchy duplicate entities that the previous same-class-only alignment missed are detected as merges in 100% of the defined duplicate-across-levels test scenarios.
- **SC-006**: An analyst can configure and run an API source — declare, run, review — end-to-end for a new endpoint and field set with zero code changes.
- **SC-007**: Source schema drift (a removed column or field) never crashes an extraction job: 100% of drift cases produce a health warning and a completed partial job.
- **SC-008**: 100% of offline or unreachable-source runs complete without being falsely marked as degraded, consistent with the offline-first policy.

## Assumptions

- **Scope**: This feature covers declarative source→ontology mapping at both the class and property level, and the runtime consumption that makes those declarations authoritative for **structured sources — database tables and REST/JSON APIs** — plus ontology-aware fact building and hierarchy-aware entity alignment.
- **Out of scope (documented gaps, deferred)**: (a) Replacing the hardcoded document relation-extraction endpoint finders with schema-derived extraction — free-text document relation logic remains as-is in this feature; (b) making AST report-template slot resolution runtime-dynamic — slot suggestion remains a one-time design-time assist, and although the new property-binding model could later feed it, that integration is not built here; (c) broader API surface — GraphQL endpoints and OAuth2 authorization-code (interactive) auth flows: the API adapter targets REST/JSON with API-key/bearer-token auth in this feature, and the binding model is kept protocol-neutral so these can be added later.
- **Connector reuse**: The existing Integration Connector framework is reused and extended for the API source lifecycle (authentication, pagination, health). This feature adds mapping semantics on top of it, not a new connector framework.
- **Backward compatibility**: Existing extraction configuration (column mapping, target class) and the current document/Excel/Word extraction paths continue to work unchanged; migration to declaration-driven mapping is incremental and opt-in.
- **Credential safety**: Credentials (database DSNs, API keys) are never stored in the database or version control; only environment-variable names or connector references are persisted, consistent with the platform security policy.
- **Offline-first / air-gap boundary**: The platform is isolated from the *public internet and public cloud services*, but it CAN reach *internal/intranet* services. Database and API sources MUST therefore be internal-network endpoints; reaching them is normal air-gap operation, not an out-networking violation. A source being unreachable (an intranet service that is down) is a graceful-degradation case — distinct from, and never conflated with, the air-gap-from-public-cloud posture.
- **Roles & permissions**: Declaring and editing mappings requires the `senior_analyst` role; `operator` and `qa` roles are read-only, consistent with existing governance.
- **Scale**: The platform is an internal, low-concurrency, long-lived system; standard responsiveness expectations apply and no high-throughput ingestion target is assumed.
- **Ontology authority**: All adapters are read-only consumers of the ontology; mapping declarations describe source-to-ontology relationships and never modify the authoritative ontology (T-Box). Extraction output enters the human review queue and is never auto-published as authoritative.
