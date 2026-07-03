# Phase 0 Research: Ontology Dynamic Object Mapping

**Feature**: 014-ontology-dynamic-mapping · **Date**: 2026-07-02

This document resolves every open decision needed before design. Each entry is **Decision / Rationale / Alternatives considered**, and closes the NEEDS-CLARIFICATION items implicit in the Technical Context. All decisions are grounded in the *actual* codebase seams discovered during exploration (file paths are current as of this feature branch).

---

## R1. E6 class-binding: extend `mapping_type`, not a new table

**Decision**: Extend the existing `OntologyClassMapping` (E6) rather than introduce a parallel class-binding table. Add source-entity mapping types to the existing `MAPPING_TYPES` allow-list:

```
MAPPING_TYPES = ("slpra_iri", "bfo", "source_field",   # existing
                 "db_table", "api_endpoint", "doc_pattern")   # new (source-entity bindings)
```

`target` continues to hold the entity locator (table name / endpoint path / doc pattern). `source_system` (already nullable `String(50)`) holds the **Source Connection** reference (a DSN env-var *name* or an `IntegrationConnector` id) — never a credential. `health` reuses the existing `HEALTH_STATES = ("ok","unmapped","drift","orphan")` (FR-021).

**Rationale**: E6 already has exactly the shape a class binding needs (class_id FK, mapping_type, target, source_system, health, VersionMixin, TimestampMixin). The spec's "Class Binding **extends the existing class-level mapping**" (Key Entities) is a literal instruction. Reuse satisfies Constitution V and keeps the audit/versioning path unchanged (Principle III). New enum members are additive and backward compatible — existing `slpra_iri`/`bfo` rows are untouched (FR-018).

**Alternatives considered**: (a) A brand-new `ClassBinding` table — rejected: duplicates E6's columns and audit wiring, orphans the existing "mapping health" UI, violates YAGNI. (b) Overload `target` with a JSON blob encoding source type — rejected: loses queryability and the clean `mapping_type` discriminator the health view already keys on.

---

## R2. E6b Property Binding — a new table `OntologyPropertyBinding`

**Decision**: Add one new table, `OntologyPropertyBinding` (codename E6b), owned by an E6 class binding. Core columns:

| Column | Type | Purpose |
|---|---|---|
| `id` | UUID PK | |
| `class_mapping_id` | FK → ontology_class_mapping | owning class binding (CASCADE) |
| `property_iri` | String(500) | the bound ontology data/object property |
| `property_kind` | String(10) | `"data"` \| `"object"` |
| `source_path` | String(500) | column name / API response path / doc slot key |
| `transform_type` | String(20) | `none`\|`controlled_vocab`\|`pattern`\|`cast` (R6) |
| `transform_config` | JSON | transform parameters (vocab map, regex, target type) |
| `is_identifier` | Boolean | flag for alignment identity (FR-004) |
| `is_label` | Boolean | flag for alignment label (FR-004) |
| `object_resolution` | String(20) nullable | object props only: `id_reference`\|`nested_object` (R4) |
| `target_class_iri` | String(500) nullable | object/`id_reference`: the related class |
| `target_id_path` | String(500) nullable | object/`id_reference`: identifier field on the target |
| `nested_binding_id` | FK → self nullable | object/`nested_object`: child class binding that types the sub-candidate |

Plus `VersionMixin` (version, status) + `TimestampMixin` for Principle III.

**Rationale**: This is the "core missing piece" (spec US1 rationale). Property-level granularity, per-property transforms, and per-property alignment flags cannot live on E6 (which is one row per class-source). A dedicated child table with a FK to E6 gives natural cascade, per-property versioning, and a clean CRUD surface. Nullable object-resolution columns keep data-property rows simple.

**Alternatives considered**: (a) Store property bindings as a JSON array on E6 — rejected: loses per-row versioning/audit, per-property validation, and indexable queries (e.g., "find the identifier binding for this class"); contradicts the dual-storage/audit discipline. (b) Reuse `ExtractionConfig.column_mapping` JSON — rejected: that is the *pre-declaration* legacy path we are superseding (kept only for FR-018 backward-compat).

---

## R3. Multiple class bindings per class — uniqueness per (class, source)

**Decision**: Enforce a DB uniqueness constraint on `(class_id, source_system)` scoped to source-entity mapping types, so a class may have at most one binding per distinct source but many across sources (FR-024). An extraction job targets **exactly one** class binding (by its E6 id). Candidates from different sources for the same class are reconciled downstream by **identifier-based alignment** (R9), never merged at declaration time.

**Rationale**: Directly encodes clarification Q4. The per-source uniqueness prevents ambiguous "which binding drives this table" while allowing multi-source population. Targeting one binding per job keeps provenance unambiguous (each candidate's `source_ref` names one source) and lets the existing job model stand unchanged.

**Alternatives considered**: (a) One binding per class (original E6 assumption) — rejected by Q4. (b) Allow duplicate (class, source) bindings disambiguated by name — rejected: reintroduces the ambiguity uniqueness is meant to remove, and no requirement needs it.

---

## R4. Object-property resolution — two declared modes

**Decision**: Support both modes from clarification Q3 (FR-023), each object-property binding declaring which via `object_resolution`:

- **`id_reference`** (DB foreign keys): binding sets `target_class_iri` + `target_id_path`. At extraction, emit a relationship edge whose object is the existing/aligned individual carrying that identifier; if none exists, emit a **review candidate** for the target (candidate_kind `"link"` referencing an unresolved id). Reuses `ExtractionCandidate.candidate_kind` and the aligner.
- **`nested_object`** (API nested JSON / joined rows): binding sets `nested_binding_id` → a child E6 class binding. At extraction, recurse into the nested source structure, extract the target as its **own sub-candidate** using the child binding's property bindings, and link parent→child by construction (`group_key` ties the sub-candidate to its parent).

Recursion is **depth-bounded** (default max depth, e.g. 5) to satisfy the self-referential/circular edge case (spec Edge Cases) — bounded traversal, no infinite recursion.

**Rationale**: The two source families genuinely differ — relational FKs resolve by identifier against already-extracted rows; nested JSON carries the child inline. Modeling both as declared modes (not inferred) keeps the binding explicit and testable. `candidate_kind`/`group_key`/`is_canonical` already exist on `ExtractionCandidate` for exactly this parent/child + link shape.

**Alternatives considered**: (a) Only id_reference (defer nested) — rejected by Q3 (both required; nested is the natural API-JSON shape US2 needs). (b) Auto-detect mode from source — rejected: fragile, and the same endpoint could plausibly support either; explicit declaration is auditable.

---

## R5. Row-reading database adapter (db_reader is structure-only today)

**Decision**: Add a **row-reading** extraction path in `db_reader.py` alongside the existing `reflect_database` (which only reflects *structure* → class/link candidates). The new path: resolve the DSN via `os.environ[dsn_ref]` (reuse `_resolve_dsn`), open a SQLAlchemy connection, `SELECT` the bound table's columns named by the class binding's property bindings, and yield one **instance** `ExtractionCandidate` per row with `extracted_properties` keyed by `property_iri`, transforms applied (R6), and a `source_ref` = `{system, table, pk}` (R11). Reuse `DBSourceError` for unreachable/missing DSN → graceful degradation (R12/FR-019).

**Rationale**: The spec's US1 MVP is *row → candidate*, which `reflect_database` explicitly does not do (memory + code confirm: "STRUCTURE-ONLY, no row reads"). Reusing DSN resolution, the error type, and the `ExtractionCandidate` shape keeps this a focused addition, not a new subsystem.

**Alternatives considered**: (a) Extend `reflect_database` to also read rows — rejected: conflates two distinct concerns (schema discovery vs data extraction); the structure path feeds a different candidate_kind. (b) Raw psycopg2 cursors — rejected: SQLAlchemy is already the project idiom and gives dialect-neutral reflection for column existence checks (drift detection, FR-020).

---

## R6. Value transforms — four declared types

**Decision**: `transform_type ∈ {none, controlled_vocab, pattern, cast}` (FR-003), applied uniformly at extraction in both adapters and re-used by fact building (R10, FR-015):

- **`controlled_vocab`**: map raw source value → ontology term. Config is the vocab source; align with `OntologyDataProperty.controlled_vocab` (E3, already JSON) and the existing `tag_controlled_vocab` helper in `pipeline.py`.
- **`pattern`**: regex validation (config = pattern). On no-match → record an issue note on that value, keep the row (transform-failure edge case).
- **`cast`**: coerce to declared datatype (config = target type; align with `OntologyDataProperty.datatype`).
- **`none`**: pass-through.

Transform failures are **per-value**, non-fatal: the offending value gets an issue note, sibling values on the same record still succeed, the row is not discarded (spec Edge Cases).

**Rationale**: These four cover the declared FR-003 minimum and reuse E3's existing `controlled_vocab`/`datatype`/`unit` metadata plus the existing `tag_controlled_vocab` seam — no new normalization engine (Principle V). Per-value isolation matches the resilience the edge cases demand.

**Alternatives considered**: (a) Arbitrary transform expressions / a mini-DSL — rejected: YAGNI, un-auditable, security surface. (b) Transforms only at fact-building time — rejected: candidates must carry normalized values for review (US1 scenario 2 shows the normalized term in the candidate).

---

## R7. Generic REST/JSON API reader (base connector is domain-shaped)

**Decision**: Add `services/integration/rest_connector.py` — a generic endpoint-path pull — and a `"rest_api"` branch in `connector_factory.connector_for`. It reuses the `IntegrationConnector` model (system_type=`"rest_api"`, `connection_config` for base URL/pagination style, `sync_cursor` for cursor state, `last_status`/`last_error` for health) and env-injected credentials (API key / bearer token — never in DB, R7/FR-006). `services/extraction/api_reader.py` drives it: fetch pages (offset/page or cursor per config), walk each item by the property bindings' response **paths** (`$.data[*].name` style, R11), apply transforms (R6), and yield the *same* unified `ExtractionCandidate` shape as the DB adapter (FR-008 "same unified output format"). Nested-object bindings (R4) recurse into embedded JSON.

The binding model stays **protocol-neutral**: GraphQL and OAuth2 authorization-code are deferred (Q2) but adding them later touches only the connector/reader, not declared bindings.

**Rationale**: The existing `ExternalSystemConnector` ABC is **domain-shaped** (`fetch_production_schedule`, `fetch_lab_results`, …), not a generic record fetch — confirmed in `services/integration/base.py`. A generic reader is genuinely new, but layering it on the existing connector *lifecycle* (config/cursor/status/health) honors "reuse the connector framework, add mapping semantics on top" (spec Assumption). `connector_factory` already dispatches by `system_type` with a clean fallback — a `"rest_api"` branch is the documented insertion point.

**Alternatives considered**: (a) Add a `fetch_records()` method to the domain ABC — rejected: pollutes a domain interface with a generic concern and forces every domain connector to implement it. (b) A new connector framework — rejected by Principle V and spec Assumption ("not a new connector framework").

---

## R8. Pipeline consumes E6/E6b (today it consumes `ExtractionConfig.column_mapping`)

**Decision**: In `run_extraction_pipeline`, add resolution of the target **class binding** (E6 id on the job) and its property bindings, and branch on the binding's `mapping_type`: `db_table` → R5 row reader, `api_endpoint` → R7 API reader. Both produce unified candidates via the shared property-binding application. The legacy `else: raise ValueError("Unsupported source type")` is replaced by these branches; `"api"` is no longer rejected (FR-011). The **pre-declaration** path (a job with an `ExtractionConfig` + `column_mapping` and no class binding) continues down the existing branch unchanged (FR-018 backward-compat).

**Rationale**: This is the single most important wiring change — it is what makes declarations *runtime-authoritative* (the whole feature thesis). Keeping the legacy branch intact behind a "no class binding declared" check delivers FR-018 with zero behavior change for existing configs.

**Alternatives considered**: (a) A parallel new pipeline entrypoint — rejected: fragments the job/candidate/review flow the UI already drives; the branch-by-binding approach keeps one pipeline. (b) Migrate all existing configs to E6/E6b immediately — rejected: spec mandates *incremental, opt-in* migration.

---

## R9. Hierarchy-aware alignment — `matched_level` + precedence

**Decision**: Extend `align_entity` / `AlignmentResult` (aligner.py) to search existing individuals across the **subclass/superclass chain**, not only the exact `target_class_iri`. Use `OntologyEngine.get_subclasses` (recursive descendants, already exists) plus `parent_iris` recursion (ancestor chain) to build the candidate set. Add `matched_level ∈ {subclass, same, parent}` to `AlignmentResult`. Resolution precedence **subclass → same → parent** (FR-017); genuinely ambiguous equal-rank matches → **no auto-merge**, surfaced for review (FR-017, edge case). Record `matched_level` + `method` (`id`/`lexical`/`semantic`) for audit.

**Rationale**: Today `align_entity` filters to `target_class_iri in ind.class_iris` (exact class only) — the documented GAP-6 that lets cross-level duplicates slip through (SC-005). The engine already exposes the traversal primitives, so this is a search-scope widening + one result field, not a new matcher.

**Alternatives considered**: (a) Flat search across *all* individuals ignoring hierarchy — rejected: loses the precedence signal FR-017 requires and risks over-merging unrelated classes. (b) Add `get_superclasses` engine method — rejected: `parent_iris` recursion already yields the ancestor chain; no new engine surface needed.

---

## R10. Ontology-aware `edges_to_facts` (replace hardcoded string match)

**Decision**: Rewrite `edges_to_facts` (fact_bridge.py) to consult the ontology instead of `if "DrugProduct" in obj_class` / `"分类"/"类别"` label matches:

- **Class membership (FR-012)**: an object class is a drug-class member iff it is `DrugProduct` **or a subclass** of it, via `OntologyEngine.get_subclasses`.
- **Domain gating (FR-013)**: assert a data-property value onto the subject only when the property's declared domain includes the subject's class, via `get_data_properties_by_domain` — prevents cross-domain leakage.
- **External alignments (FR-014)**: populate the currently-empty `Facts.alignments` from class-level external-standard alignments via a new `OntologyEngine.get_class_alignments(class_iri)` (R13). The interpreter already has `external_alignment` and `class_membership` ops that consume this — US3 "populates what reasoning already understands."
- **Controlled vocab (FR-015)**: normalize scalar values against E3 `controlled_vocab` (reuse R6 transform logic).

**Rationale**: Removes the brittle hardcoded coupling (GAP-2). Everything downstream (`Facts` dataclass fields incl. `alignments`, interpreter vocabulary) already exists — this is population, not new reasoning machinery.

**Alternatives considered**: (a) Config-driven class-name list instead of hierarchy — rejected: still breaks on new subclasses (the exact failure SC-004 targets). (b) Populate alignments from ABox API individuals (as `reasoning/engine.py` does) — rejected: FR-014 specifies *class-level* alignment declarations from the ontology, which the extraction-edge path lacks; hence R13.

---

## R11. Provenance `source_ref` shape

**Decision**: Every candidate/edge carries `source_ref` (field already on `ExtractionCandidate`) as a resolvable descriptor: `{ "system": <connector/dsn ref>, "entity": <table|endpoint>, "record": <pk value|response index/id> }` (FR-010, SC-003). For nested sub-candidates, `record` includes the parent record key so the chain is resolvable.

**Rationale**: `source_ref` already exists for exactly this; standardizing its shape makes SC-003's "100% resolvable" testable. No credentials appear in provenance (only refs).

**Alternatives considered**: Free-form string — rejected: not machine-resolvable, fails SC-003's "resolvable back to source system and record."

---

## R12. Graceful degradation vs air-gap posture

**Decision**: Distinguish two cases explicitly (see [[air-gap-allows-intranet-sources]]): (a) an **intranet source is unreachable/down** → empty output + non-empty `degraded_reason` on the job/candidate (`ExtractionCandidate.degraded_reason` already exists), job still completes (FR-019). (b) the **air-gap-from-public-cloud posture** is normal operation → **never** marked `degraded` (SC-008). Sources are validated as internal-network endpoints at declaration time (FR-022).

**Rationale**: Encodes the user's key correction from clarify. `degraded` means "an explicitly-enabled capability is unfulfillable," never "we are offline from the public internet."

**Alternatives considered**: Treat any network failure uniformly — rejected: would falsely mark normal air-gap runs as degraded (fails SC-008).

---

## R13. Source of class-level external alignments (FR-014 open question)

**Decision**: Add `OntologyEngine.get_class_alignments(class_iri) -> list[str]` reading the published World for the class's external-standard alignment axioms — `owl:equivalentClass` and skos mapping predicates (`skos:exactMatch`/`closeMatch`) pointing to non-managed (external) IRIs. `edges_to_facts` calls it to populate `Facts.alignments` (R10). This is consistent with how alignments are authored: `surgical_merge` strips `rdfs:subClassOf` on managed classes and external alignment goes through skos/owl:equivalentClass (see [[external-alignment-must-use-nonmanaged-predicates]]); `ttl_merge.py` already handles equivalentClass on the write side.

**Rationale**: Exploration found **no** existing engine method exposing class-level external alignments for reading (only `reasoning/engine.py` builds ABox alignments from API individuals — a different path). FR-014 needs class-declaration alignments, so a read method is required. Anchoring it to the same predicates the write path uses guarantees round-trip fidelity.

**Alternatives considered**: (a) Read alignments from E11 criteria / a metadata table — rejected: the authoritative alignment axioms live in the TTL/World (Principle II), and reading them there avoids a second source of truth. (b) Reuse `reasoning/engine.py`'s ABox alignment builder — rejected: it aligns *individuals* via API classes, not *class declarations* (wrong granularity for FR-014).

---

## R14. Migration & schema-drift detection

**Decision**: One Alembic migration `0013_*` (next after `0012_ast_template_sample_json`) adds the E6 `mapping_type` values (no DDL — it's an app-level allow-list) plus E6 has all needed columns already, and creates the `ontology_property_binding` table (E6b) with the `(class_id, source_system)` uniqueness on E6 handled as a partial/conditional constraint or app-level validation. **Revision id ≤ 32 chars** (see [[alembic-revision-id-32-char-limit]]) — e.g. `0013_property_binding`. Schema-drift detection (FR-020): before/at extraction, check declared `source_path`s exist against the reflected source schema (DB: SQLAlchemy `inspect()`; API: key presence per item); missing → mark the binding's E6 `health="drift"`, emit a warning, skip that binding, job completes partial (SC-007).

**Rationale**: Keeps the migration minimal and within the hard 32-char revision-id limit that has previously caused silent schema drift + 500s. Reuses the existing `health` enum and structure-reflection tooling.

**Alternatives considered**: Longer descriptive revision id — rejected: `version_num` is `VARCHAR(32)`; overflow silently fails the migration.

---

## Requirement → Design traceability

| FR | Resolved by |
|---|---|
| FR-001, FR-024 | R1 (mapping_type), R3 (uniqueness per class,source) |
| FR-002 | R2 (E6b table) |
| FR-023 | R4 (id_reference / nested_object modes) |
| FR-003, FR-009 | R6 (four transforms) |
| FR-004 | R2 (is_identifier/is_label) |
| FR-005 | R2 + declaration validation (domain gate), R14 (drift) |
| FR-006 | R1/R2 (VersionMixin+audit, no credentials) |
| FR-007 | R5 (row reader) + R8 (pipeline consumes E6/E6b) |
| FR-008, FR-011 | R7 (REST/JSON reader), R8 (api branch) |
| FR-010 | R11 (source_ref shape) |
| FR-012 | R10 (hierarchy membership) |
| FR-013 | R10 (domain gating) |
| FR-014 | R10 + R13 (get_class_alignments) |
| FR-015 | R6 + R10 (controlled vocab) |
| FR-016, FR-017 | R9 (hierarchy-aware alignment, matched_level) |
| FR-018 | R8 (legacy branch preserved) |
| FR-019, SC-008 | R12 (degradation vs air-gap) |
| FR-020, FR-021, SC-007 | R14 (drift → health), R1 (health enum) |
| FR-022 | R7/R12 (internal-network only) |

**All NEEDS CLARIFICATION resolved.** Ready for Phase 1.
