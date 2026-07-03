# Phase 1 Data Model: Ontology Dynamic Object Mapping

**Feature**: 014-ontology-dynamic-mapping · **Date**: 2026-07-02 · **Source**: [spec.md](spec.md) Key Entities + [research.md](research.md)

Entities below map the spec's stakeholder-facing names to concrete models. **New** work is one table (E6b) plus additive columns/enum values on existing models. Everything else is *reuse* — annotated as such.

---

## 1. Class Binding — `OntologyClassMapping` (E6, EXTENDED)

File: `backend/app/models/ontology_meta.py`. The association from an ontology class to a source entity. **Extends** the existing table (no rename).

**Changes**:
- `MAPPING_TYPES` allow-list gains: `"db_table"`, `"api_endpoint"`, `"doc_pattern"` (was `slpra_iri`, `bfo`, `source_field`).
- No new columns required — existing columns carry the semantics:

| Field | Type | Role for source bindings |
|---|---|---|
| `id` | UUID PK | class-binding id (a job targets exactly one) |
| `class_id` | FK ontology_class | the bound ontology class |
| `mapping_type` | String(20) | one of the new source-entity types |
| `target` | String(500) | table name / endpoint path / doc pattern |
| `source_system` | String(50), nullable | **Source Connection** ref: DSN env-var name or connector id (NO credential) |
| `health` | String(20), default `"ok"` | `ok`\|`unmapped`\|`drift`\|`orphan` (FR-021) |
| `version`, `status` | VersionMixin | optimistic concurrency + lifecycle (Principle III) |
| `created_at`, `updated_at` | TimestampMixin | audit timestamps |

**Relationships**: one E6 → many `OntologyPropertyBinding` (E6b) via `class_mapping_id` (CASCADE delete).

**Constraints / validation**:
- **C1 (FR-024)**: uniqueness on `(class_id, source_system)` for source-entity `mapping_type`s — at most one binding per (class, source). Enforced by a conditional unique index (source-entity types only) and app-level validation in the store.
- **C2 (FR-005)**: `target` required (non-empty) for source-entity types.
- **C3 (FR-006)**: `source_system` MUST be a reference (env-var name or connector id), never a raw DSN/credential — validated on create/update.

---

## 2. Property Binding — `OntologyPropertyBinding` (E6b, NEW)

File: `backend/app/models/ontology_meta.py` (new model). The association from an ontology data/object property to a source field, owned by a Class Binding.

| Field | Type | Notes |
|---|---|---|
| `id` | UUID PK | |
| `class_mapping_id` | FK → ontology_class_mapping, CASCADE | owning class binding |
| `property_iri` | String(500), required | bound data/object property |
| `property_kind` | String(10) | `data` \| `object` |
| `source_path` | String(500), required | column / API response path / doc slot key |
| `transform_type` | String(20), default `none` | `none`\|`controlled_vocab`\|`pattern`\|`cast` |
| `transform_config` | JSON, nullable | vocab map / regex / target type |
| `is_identifier` | Boolean, default false | alignment identity (FR-004) |
| `is_label` | Boolean, default false | alignment label (FR-004) |
| `object_resolution` | String(20), nullable | object only: `id_reference` \| `nested_object` |
| `target_class_iri` | String(500), nullable | object/`id_reference`: related class |
| `target_id_path` | String(500), nullable | object/`id_reference`: identifier field on target |
| `nested_binding_id` | FK → ontology_class_mapping, nullable | object/`nested_object`: child class binding |
| `version`, `status` | VersionMixin | |
| `created_at`, `updated_at` | TimestampMixin | |

**Validation rules**:
- **V1 (FR-005, domain gate)**: `property_iri`'s declared **domain MUST include** the owning binding's `class_id` class — blocking error otherwise. Uses `OntologyEngine.get_data_properties_by_domain` / `get_object_properties_by_domain`.
- **V2 (FR-005)**: `property_iri` MUST exist and not be disabled; else blocking.
- **V3 (object props)**: if `property_kind="object"`, `object_resolution` is required; `id_reference` ⇒ `target_class_iri` + `target_id_path` required; `nested_object` ⇒ `nested_binding_id` required.
- **V4 (FR-004)**: at most one `is_identifier=true` binding per class binding (the identity attribute); `is_label` may repeat.
- **V5 (transforms)**: `transform_config` shape validated per `transform_type` (vocab map for `controlled_vocab`, valid regex for `pattern`, known datatype for `cast`).
- Warnings (non-blocking) vs errors (blocking) distinguished per FR-005.

**State**: follows VersionMixin `status` lifecycle consistent with other E-tables (draft/published semantics as used elsewhere in ontology_meta).

---

## 3. Source Connection — `IntegrationConnector` (REUSED) + DSN env-ref

File: `backend/app/models/integration.py`. A credential-safe handle to a source system. **Reused as-is**; this feature adds a `system_type="rest_api"` usage and a generic reader (no model change).

| Field | Role |
|---|---|
| `system_type` | `"rest_api"` for the new generic REST/JSON connector (existing values unchanged) |
| `connection_config` | JSON: base URL, pagination style (offset/page/cursor), auth scheme (api_key/bearer) — **no credentials** (R7/FR-006) |
| `field_mapping` | JSON (existing) — not used for semantics here; property bindings own field mapping |
| `sync_cursor` | JSON — pagination cursor state (existing) |
| `last_status`, `last_error` | health signals for FR-021/FR-019 |
| `is_active`, `poll_interval_seconds`, `ingest_mode` | existing lifecycle |

**Database sources**: no connector row; the E6 `source_system` holds a **DSN env-var name**, resolved at runtime via `os.environ[dsn_ref]` (reuse `db_reader._resolve_dsn`). Credentials live only in the environment (FR-006).

---

## 4. Extraction Edge / Candidate — `ExtractionCandidate` (REUSED)

File: `backend/app/models/extraction.py`. The unified output of any adapter. **No schema change** — existing fields carry the new semantics:

| Field | Use in this feature |
|---|---|
| `target_class_iri` | subject class of the candidate |
| `extracted_properties` (JSON) | property_iri → transformed value (data props); relationship refs (object props) |
| `candidate_kind` | `instance` (rows/items), `link` (unresolved id_reference target — FR-023a) |
| `group_key` | ties nested sub-candidate (FR-023b) to its parent |
| `is_canonical` | canonical row among a group (reuse `_mark_canonical`) |
| `source_ref` (JSON) | provenance `{system, entity, record}` (FR-010, R11) |
| `alignment_result`, `aligned_iri`, `match_score` | alignment output |
| `review_status` | default `"pending"` — never auto-published (Principle II) |
| `degraded_reason` | non-empty only on real degradation (FR-019, R12) |

---

## 5. Alignment Result — `AlignmentResult` (EXTENDED)

File: `backend/app/services/extraction/aligner.py`. **Add** `matched_level` to the existing dataclass.

| Field | Type | Notes |
|---|---|---|
| `action` | `new`\|`merge`\|`skip` | existing |
| `match_iri`, `match_score`, `match_label` | existing | |
| `method` | `id`\|`lexical`\|`semantic`\|`none` | existing |
| `matched_level` | **NEW** `subclass`\|`same`\|`parent`\|`none` | which hierarchy level produced the match (FR-017) |

**Rules**: search across subclass/superclass chain (FR-016); precedence subclass → same → parent (FR-017); equal-rank ambiguity ⇒ `action` stays non-merge, surfaced for review (no auto-merge).

---

## 6. Fact View — `Facts` (REUSED, populated)

File: `backend/app/services/reasoning/interpreter.py`. **No schema change** — `edges_to_facts` now *populates* fields the ontology-aware path fills:

| Field | Population change |
|---|---|
| `drug_classes` | membership via hierarchy (`get_subclasses`), not string match (FR-012) |
| `data_values` | asserted only if property domain includes subject class (FR-013) |
| `alignments` | **now populated** from `OntologyEngine.get_class_alignments` (FR-014) — was always empty |
| `scalars` | normalized against controlled vocab (FR-015) |
| `relations` | unchanged shape |

---

## 7. Engine read surface — `OntologyEngine` (EXTENDED)

File: `backend/app/services/ontology_engine.py`. **Add** one read method; reuse the rest.

| Method | Status | Use |
|---|---|---|
| `get_subclasses(iri)` | exists | hierarchy membership (R10), alignment search (R9) |
| `get_data_properties_by_domain(iri)` | exists | domain gating (FR-013), declaration validation (V1) |
| `get_object_properties_by_domain(iri)` | exists | object-property declaration validation (V1) |
| `get_class_detail(iri)` / `parent_iris` | exists | ancestor chain for alignment (R9) |
| `get_class_alignments(iri)` | **NEW** | class-level external alignments for FR-014 (R13) |

All engine access is **read-only** (Principle II).

---

## Entity relationship (new/changed portion)

```text
OntologyClass ──1:N──> OntologyClassMapping (E6, class binding)
                              │  mapping_type ∈ {db_table, api_endpoint, doc_pattern, slpra_iri, bfo, source_field}
                              │  source_system ──ref──> IntegrationConnector (rest_api)  |  or DSN env-var name
                              │
                              └──1:N──> OntologyPropertyBinding (E6b)
                                             │  property_kind ∈ {data, object}
                                             │  transform_type ∈ {none, controlled_vocab, pattern, cast}
                                             └── object/nested_object ──> nested_binding_id ─> OntologyClassMapping (child)

ExtractionJob ──targets──> one OntologyClassMapping (E6)  ──produces──> ExtractionCandidate (source_ref, review_status=pending)
                                                                                 │
                                                            edges_to_facts ──> Facts (hierarchy/domain/alignments/vocab)
```
