# Implementation Plan: Ontology Dynamic Object Mapping

**Branch**: `014-ontology-dynamic-mapping` | **Date**: 2026-07-02 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `/specs/014-ontology-dynamic-mapping/spec.md`

## Summary

Make source-to-ontology mapping **declaration-driven and runtime-authoritative**. Today the class-level mapping table (E6 `OntologyClassMapping`) is *dead data* — no runtime code reads it; every new table or column requires a developer to hand-edit `ExtractionConfig.column_mapping` or the pipeline itself. This feature closes six documented gaps by:

1. Extending E6 with source-entity **class bindings** (table / API endpoint / doc pattern) and adding a new **property binding** layer (E6b) that maps each ontology data/object property to a concrete source field, with value transforms, identifier/label flags, and object-property resolution modes.
2. Making the extraction pipeline **consume those declarations** instead of hardcoded config — for both a new row-reading **database adapter** and a new generic **REST/JSON API adapter** (layered on the existing `IntegrationConnector` lifecycle), producing one unified candidate format.
3. Making **edges→facts** ontology-aware — class membership via the hierarchy, data-value assertion gated by declared domain, external-standard alignments populated into the existing (currently-empty) `Facts.alignments` view, and scalars normalized against controlled vocabulary — replacing the hardcoded `"DrugProduct" in obj_class` string match.
4. Making **entity alignment** hierarchy-aware (subclass→same→parent precedence) with a recorded `matched_level`.

The technical approach is **wiring, not greenfield**: `ExtractionCandidate` already carries `source_ref`, `degraded_reason`, `candidate_kind`, `group_key`; `IntegrationConnector` already carries `sync_cursor`/`last_status`; `Facts.alignments` and the interpreter's `external_alignment`/`class_membership` ops already exist; `OntologyEngine` already exposes `get_subclasses`, `get_data_properties_by_domain`, `get_object_properties_by_domain`, and `parent_iris`. The genuinely new build is: the E6b model + E6 `mapping_type` extension, a row-reading DB adapter, a generic REST/JSON reader, a declaration-consuming pipeline branch, a hierarchy-aware aligner field, and an ontology-aware `edges_to_facts`.

## Technical Context

**Language/Version**: Python 3.11 (backend, `uv`-managed venv); TypeScript / React (frontend)

**Primary Dependencies**: FastAPI (APIRouter + Depends), SQLAlchemy 2.0 (`Mapped`/`mapped_column`), Owlready2 (OWL/RDFS T-Box authority), Alembic (migrations), psycopg2 (PostgreSQL driver), httpx/requests (REST connector — reuse whatever the existing connector stack uses); React + React Query + d3 + `lib/api.ts` (frontend)

**Storage**: PostgreSQL for editable metadata (E1–E13 tables incl. E6/new E6b) and extraction rows; TTL files as the published T-Box authority via Owlready2 World. Dual-storage write consistency governed by Constitution II. Source **data** is read from external internal-network databases (via DSN env-ref) and REST/JSON APIs — never persisted as ontology truth; it enters the review queue.

**Testing**: pytest via `uv run` (unit + contract + integration); executable `quickstart.md`. New tests: E6b CRUD + domain-validation, declaration-driven DB extraction, REST/JSON adapter (against a stub connector), hierarchy-aware alignment, ontology-aware `edges_to_facts`, backward-compat of the pre-declaration path.

**Target Platform**: Linux server (internal, air-gapped from public internet/cloud; intranet-reachable). No public-internet or public-cloud calls at runtime.

**Project Type**: Web application (Option 2) — `backend/` (FastAPI service) + `frontend/` (React SPA).

**Performance Goals**: No high-throughput target (internal, low-concurrency, long-lived platform per Assumptions). Extraction is a batch/job operation; standard interactive responsiveness for the mapping-management UI. Bounded recursion depth for nested/self-referential object-property resolution.

**Constraints**: Air-gap posture (no public internet/cloud; intranet allowed — see [[air-gap-allows-intranet-sources]]); credentials never in DB or git (env-var refs / connector refs only); `senior_analyst` role to edit/publish mappings, `operator`/`qa` read-only; adapters are read-only consumers of the ontology (Principle II); extraction output is never auto-published (enters `review_status="pending"`); offline/air-gap MUST NOT be marked `degraded`; Alembic revision id ≤32 chars (see [[alembic-revision-id-32-char-limit]]).

**Scale/Scope**: ~200 ontology classes; single-digit to low-tens of source connectors; extraction jobs over thousands–tens-of-thousands of rows per run. New: 1 model table (E6b) + E6 column additions; ~2 new adapters; ~3 service seams rewired; ~5 new API endpoints; frontend mapping-management extensions.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

Constitution **v1.1.0** — six principles evaluated below.

| Principle | Gate | Status | Notes |
|---|---|---|---|
| **I. Spec-Driven Development** | Feature originates from an approved spec; plan traces to FRs; tasks derive from plan. | ✅ PASS | `spec.md` clarified (5 Qs), checklist 16/16. This plan maps every FR to a design element (see research.md traceability). |
| **II. Ontology Authority & Fidelity** (NON-NEGOTIABLE) | Ontology (T-Box) stays authoritative; no unmanaged mutation; dual-storage write consistency; triple-level diff preview for any TTL change. | ✅ PASS | Adapters are **read-only** consumers of the ontology (spec Assumption "Ontology authority"). E6/E6b are *source-to-ontology* metadata; they do **not** modify the T-Box. Extraction output is candidates in a review queue, never auto-materialized. No new TTL-merge surface. |
| **III. Traceability & Auditability** | Version numbers + optimistic concurrency; audit log (actor/action/entity/timestamp); provenance on outputs. | ✅ PASS | E6b reuses `VersionMixin` + `TimestampMixin` and the existing audit path (FR-006). Every candidate carries a resolvable `source_ref` (FR-010, SC-003). Alignment records `matched_level`+`method` (FR-017). |
| **IV. Test Discipline & Contract-First** | Contracts before implementation; pytest for CRUD/concurrency/dual-storage; executable quickstart; health checks. | ✅ PASS | Phase 1 emits `contracts/` (E6b CRUD, extraction-job, connector) before impl. `quickstart.md` is runnable. Mapping health (ok/unmapped/drift/orphan) reused per FR-021. |
| **V. Minimal Complexity & Reuse** | Reuse existing stack; YAGNI; no new frameworks without justification. | ✅ PASS | Reuses `IntegrationConnector` lifecycle, `ExtractionCandidate` provenance fields, `Facts.alignments`, interpreter ops, `OntologyEngine` hierarchy/domain queries. One new table (E6b), no new framework. GAP-3/GAP-4-runtime explicitly deferred (spec Assumptions). |
| **VI. Offline-First & Graceful Degradation** | Air-gap default; offline ≠ `degraded`; silent degradation with zero regression to the structured path; `local_files_only=True` for any model load. | ✅ PASS | Sources restricted to intranet (FR-022); unreachable intranet source → empty output + `degraded_reason` (FR-019), never crashes. Air-gap-from-public-cloud is never marked degraded (SC-008). No new model weights introduced. |

**Gate result: PASS (pre-Phase-0).** No violations; Complexity Tracking table left empty.

### Post-Design Re-evaluation (after Phase 1)

Re-checked against the Phase 1 artifacts (`research.md`, `data-model.md`, `contracts/`, `quickstart.md`). **Still PASS — no new violations:**

- **I**: Every FR traces to a design decision (research.md "Requirement → Design traceability" table) and to a contract test. ✅
- **II**: Design confirms adapters/engine access is **read-only** (`get_class_alignments` and all engine reads); E6/E6b are source-metadata only; no TTL-merge surface added; candidates stay `review_status="pending"`. The one write path (E6b CRUD) touches metadata tables, not the T-Box. ✅
- **III**: E6b carries `VersionMixin`+`TimestampMixin`; contracts specify `expected_version` optimistic concurrency + audit on every mutation; `source_ref` provenance is mandatory (SC-003). ✅
- **IV**: `contracts/` written before implementation; each contract enumerates pytest cases; `quickstart.md` is runnable end-to-end. ✅
- **V**: Design reuses `IntegrationConnector`, `ExtractionCandidate` fields, `Facts`/interpreter ops, and existing engine queries; net-new surface is **one** table (E6b), two adapters, one engine read method. GAP-3/GAP-4-runtime deferred. ✅
- **VI**: `degraded` reserved for real unreachable-intranet cases (R12); air-gap-from-public-cloud never marked degraded (SC-008); `base_url` intranet validation (FR-022); no new model weights. ✅

Design surfaced no complexity requiring justification — Complexity Tracking remains empty.

## Project Structure

### Documentation (this feature)

```text
specs/014-ontology-dynamic-mapping/
├── plan.md              # This file (/speckit-plan command output)
├── spec.md              # Feature specification (clarified)
├── research.md          # Phase 0 output (/speckit-plan)
├── data-model.md        # Phase 1 output (/speckit-plan)
├── quickstart.md        # Phase 1 output (/speckit-plan)
├── contracts/           # Phase 1 output (/speckit-plan)
│   ├── class-property-binding.md   # E6/E6b CRUD + validation contract
│   ├── extraction-job.md           # DB/API declaration-driven job contract
│   └── source-connector.md         # REST/JSON connector config + test contract
├── checklists/
│   └── requirements.md  # Spec quality checklist (16/16)
└── tasks.md             # Phase 2 output (/speckit-tasks — NOT created here)
```

### Source Code (repository root)

```text
backend/
├── app/
│   ├── models/
│   │   ├── ontology_meta.py       # EXTEND E6 OntologyClassMapping (mapping_type, source ref);
│   │   │                          #   ADD E6b OntologyPropertyBinding
│   │   ├── extraction.py          # ExtractionCandidate/Job/Config (reuse; no schema change expected)
│   │   └── integration.py         # IntegrationConnector (reuse; add "rest_api" system_type usage)
│   ├── schemas/
│   │   ├── ontology.py            # ADD PropertyBinding create/update/response; extend Mapping*
│   │   └── extraction.py          # extend job create for source_type="api" / class-binding target
│   ├── services/
│   │   ├── ontology_meta_store.py # ADD property-binding CRUD + declaration validation (domain gate)
│   │   ├── extraction/
│   │   │   ├── pipeline.py        # REWIRE to consume E6/E6b; add "database"(row) + "api" branches
│   │   │   ├── db_reader.py       # ADD row-reading adapter (today: structure reflection only)
│   │   │   ├── api_reader.py      # NEW generic REST/JSON reader (paths, pagination, auth)
│   │   │   └── aligner.py         # ADD hierarchy-aware matching + matched_level
│   │   ├── integration/
│   │   │   ├── connector_factory.py  # ADD "rest_api" branch
│   │   │   └── rest_connector.py     # NEW generic endpoint-path pull (base ABC is domain-shaped)
│   │   ├── reasoning/
│   │   │   └── fact_bridge.py     # REWIRE edges_to_facts → ontology-aware (hierarchy/domain/align/vocab)
│   │   └── ontology_engine.py     # ADD get_class_alignments() read (class-level external alignments)
│   ├── api/
│   │   ├── ontology.py            # ADD property-binding routes under /classes/{iri}/.../bindings
│   │   ├── extraction.py          # ALLOW api source_type; class-binding-targeted job creation
│   │   └── integration.py         # reuse connector CRUD/test
│   └── migrations/versions/
│       └── 0013_*.py             # E6 columns + E6b table (revision id ≤32 chars)
└── tests/
    ├── contract/                  # E6b CRUD, extraction-job, connector contracts
    ├── integration/               # declare→extract→candidate; api-degradation; hierarchy-align
    └── unit/                      # transforms, domain-gate, edges_to_facts, matched_level

frontend/
└── src/
    ├── app/(dashboard)/ontology/[projectId]/   # mapping-management view: property bindings, health
    ├── components/                # binding editor, transform config, health badges
    └── lib/api.ts                 # add binding + api-source client calls
```

**Structure Decision**: **Option 2 (web application)** — the repository already has `backend/` (FastAPI) and `frontend/` (React) top-level trees; this feature extends both. No new top-level projects. Backend changes are concentrated in `models/ontology_meta.py`, the `services/extraction/` package, `services/reasoning/fact_bridge.py`, and `services/ontology_engine.py`; frontend changes extend the existing ontology mapping-management screens.

## Complexity Tracking

> No Constitution Check violations. Table intentionally empty.

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| — | — | — |
