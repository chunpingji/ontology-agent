# Implementation Plan: Section-Level Ontology Coverage Binding

**Branch**: `016-section-coverage-binding` | **Date**: 2026-07-05 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `/specs/016-section-coverage-binding/spec.md`

## Summary

Raise the AST report-template authoring unit from **individual extraction slots** to **section-level ontology coverage declarations**, and compile the fine-grained "可控无遗漏" (controllable, no-omission) coverage checklist from the ontology at validation time instead of from AI-invented field names.

Two structural defects motivate this (design note `docs/section-coverage-declaration-design.md`): (1) the AI slot suggester is **ontology-blind** — it invents `slot_id`/`label` from sample text then does a last-step exact string match (`_bind_ontology_iris`, `label in dp_labels`) that nearly always misses, dropping every graph-sourced field to `manual`; (2) suggested slots anchor onto **concrete sample individuals** (e.g. equipment id `646`) rather than ontology types, so a template never generalizes to another source document. The frontend `suggestionToSlot` compounds this by collapsing every non-`extraction` slot to `manual`.

The fix is not a patch but removing the root cause: a section declares a small list of **coverage bindings** in the ontology's own vocabulary (`OntologyRelationBinding = doc entity type + relationship + target type + required`), which is exactly the shape of one edge of `OntologyEngine.get_relation_schema(doc_class_iri)`. At validation/generation time the validator expands each declared relationship into the target type's property checklist (`range_data_properties`) — the no-omission signal is defined at **relationship granularity** (a missing required relationship = a true omission), while blank individual properties under a present relationship are **informational** unless explicitly promoted. The authoring vocabulary is physically incapable of naming an individual, so defect (2) cannot recur; and "extraction" becomes an intrinsic property of a graph-sourced binding rather than something a fragile string match must *earn*, so defect (1) cannot recur.

**Technical approach — additive, no rebuild.** All work reuses existing infrastructure:
- **AST-1 (schema)**: add `Section.coverage: list[CoverageBinding]` plus `OntologyRelationBinding`/`FactSourceBinding` pydantic models to `ast_template.py`, mirroring the additive-optional `Section.prompt` field shipped in 015. **No DB migration, no new column, no new table** — coverage nests inside the existing `AstTemplate.schema_json` JSON column.
- **AST-2/AST-3 (validate + generate)**: teach `validate_coverage` to read `section.coverage`, expand each relationship via `get_relation_schema`, and emit `SlotCoverage` positions with **global dedup** keyed by `(doc_class_iri, predicate_iri, range_class_iri)` — reusing the existing `FILLED`/`BLANK_OPTIONAL`/`MISSING_REQUIRED` status vocabulary (no new status).
- **AI authoring (slot suggester)**: thread `doc_class_iri` into `suggest_slots`, inject a compact `get_relation_schema` graph into the LLM rounds so it maps sections to **real relationship IRIs**, emit `coverage` declarations (required by default) and explicit `unresolved_candidates`; delete `_bind_ontology_iris`.
- **Frontend**: render section-level coverage declarations (relationship/type selectors) and an unresolved-candidate disposition affordance; stop the two `→ manual` collapse sites; preserve rule/constant/manual and legacy extraction slots untouched. The authoring surface follows the approved visual design — `design.pen` frame **"Slot Tree Component — Full View"** (the reusable *Slot Tree* component) — which fixes the per-section coverage area, the unresolved-candidate disposition list, and the preserved non-ontology slot group (see [contracts/frontend-authoring.md](contracts/frontend-authoring.md) §Design reference).

All ontology access is **read-only** against the published, offline, `only_local` derived World (Principle II/VI); an unavailable ontology degrades gracefully with no regression to existing report generation.

## Technical Context

**Language/Version**: Python 3.11 (backend, `uv`-managed) + TypeScript 5 / React 19 (frontend). Backend is the primary surface; frontend is a thin authoring/disposition slice.

**Primary Dependencies**: FastAPI + SQLAlchemy 2.0 (ORM, `AstTemplate.schema_json` generic `JSON` column), Pydantic v2 (discriminated unions via `Field(discriminator='kind')`; default `extra='ignore'`), Owlready2 (read-only `World`, `only_local=True`) behind `OntologyEngine`, a local (air-gapped) LLM via `chat_with_schema` (`local_client.py`, `json_schema` strict mode). Frontend: Next.js 16 App Router, `@tanstack/react-query` 5, shadcn/ui (existing `Select`, `DropdownMenu`, `Badge`), existing `lib/api.ts` typed client (`getRelationSchema`, `RelationSchemaEdge` already present).

**Storage**: **No storage change.** Section coverage declarations persist inside the existing `AstTemplate.schema_json` JSON column (`backend/app/models/extraction.py:127`). Unresolved candidates are a **transient** suggest-time response artifact, persisted only if the author accepts them into `schema_json` via the existing create/update endpoints. No Alembic migration, no new column, no new table.

**Testing**: `pytest` under `uv` (`cd backend && uv run pytest`) — bare `python`/`pytest` is anaconda and lacks `psycopg2` (project memory `backend-python-env-managed-by-uv`). New/extended cases land in `test_reporting/test_coverage_validator.py`, `test_reporting/test_multi_template_e2e.py`, `test_extraction/test_slot_suggester.py`, `test_reporting/test_ast_template.py`; a fake ontology engine (`tests/fixtures/ontology.py::build_drug_ontology`) is extended with `get_relation_schema` (memory `ontology-aware-paths-legacy-in-tests`). Frontend has no test runner today; verification is the executable `quickstart.md`.

**Target Platform**: Internal, **air-gapped** GMP system (no public internet/cloud). All ontology reads are offline against the published model; the LLM is local. No runtime egress introduced.

**Project Type**: Web application (Option 2) — existing `backend/` + `frontend/` trees. Changes span both, backend-weighted.

**Frontend design reference**: The section-coverage editor UI is specified by `design.pen` → frame **"Slot Tree Component — Full View"** (`vwW7Q`), a full-height, no-scroll render of the reusable **Slot Tree** component (`JKSZm`) — the AST editor's Right-Panel slot tree. It fixes the concrete layout of the three new surfaces: `SectionCoverageArea` (本体覆盖声明 — `relationship → target type` rows, required toggle, ontology property checklist with FILLED/MISSING/optional status pills), the unresolved-candidate disposition list (待解析候选 — bind / constant / manual / discard), and the preserved rule/constant/manual slot group. Full node→contract map in [contracts/frontend-authoring.md](contracts/frontend-authoring.md) §Design reference.

**Performance Goals**: Internal, low-concurrency, single-document workflow. No throughput SLA. `get_relation_schema` is a locked Owlready2 BFS over object/data properties; the plan **caches its edge list once per `doc_class_iri` per `validate_coverage` run** (multiple bindings share one doc class) and injects only a **compact hop-1 view** into the LLM prompt (pruned to fit the local model's context). Target: coverage validation adds no user-perceptible latency to report generation at platform scale (~200 ontology classes, single source document).

**Constraints**: Ontology access **read-only** (FR-015, Principle II) — no TTL write, no surgical merge, no A-Box mutation; coverage is application data in `schema_json`, never in the authoritative model. Declarations reference types + predicates by IRI and are **physically incapable of naming an individual** (FR-003). **Zero regression** on legacy templates (FR-013/SC-006): `Section.coverage` defaults to `[]`, `validate_coverage`'s new `engine` param defaults `None`, and the coverage section-loop is a strict no-op when coverage is empty. **Graceful degradation** (FR-012, Principle VI): engine-unavailable still fires required-relationship omissions via substring fallback, skips property expansion, raises no error, and never renders a false "degraded" state for a normally-offline system. Role gating (`senior_analyst` author) unchanged.

**Scale/Scope**: Backend: 1 schema file (`ast_template.py`, +2 models +1 field), 1 validator (`coverage_validator.py`, +2 helpers +1 seam +`engine` param), 1 suggester (`slot_suggester.py`, inject graph / emit coverage+candidates / delete `_bind_ontology_iris`), response schemas (`schemas/extraction.py`), 2 wiring points (`api/extraction.py` report-gen + coverage-view, `risk_report_generator.py`). Frontend: 1 component (`template-slot-editor.tsx`) + `lib/api.ts` type additions (no new endpoint). ~6 new pytest classes.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

Constitution **v1.1.0** — six principles evaluated. This is a **backend-weighted, additive** feature that consumes existing read-only ontology structure; no ontology-mutation, storage, or migration surface is added.

| Principle | Gate | Status | Notes |
|---|---|---|---|
| **I. Spec-Driven Development** | Feature originates from an approved, clarified spec; plan traces to FRs. | ✅ PASS | `spec.md` clarified (3 Qs, Session 2026-07-05); checklist 16/16. Every FR maps to a design decision in `research.md`. The two design-note open items (property→required promotion; unresolved-candidate handling) were resolved in `clarify`/Assumptions, not deferred into code. |
| **II. Ontology Authority & Fidelity** (NON-NEGOTIABLE) | T-Box stays authoritative; no unmanaged mutation; dual-store consistency; triple-diff preview for TTL change. | ✅ PASS | **No TTL/ontology write surface added.** Coverage compilation uses only read-only structural queries — `get_relation_schema`, `get_subclasses`, `get_data_properties_by_domain` — against the published `World` (rebuilt derived cache of authoritative TTL, `only_local=True`, never written back; `ontology_engine.py:129-173`). Coverage declarations live in `schema_json` (application data), never in the authoritative model (FR-015). Declarations name **types + predicates by IRI**, structurally unable to reference an individual (FR-003). |
| **III. Traceability & Auditability** | Versioning + optimistic concurrency; audit log; provenance. | ✅ PASS | The coverage manifest (no-omission proof) enumerates per-position status (`filled`/`blank_optional`/`missing_required`/`manual`) with `source_ref` provenance and `source_kind='ontology_relation'` tagging ontology-derived positions (FR-011); reuses the existing `GeneratedReport.rules_summary` persistence — no new audit path. Template create/update keep the existing optimistic-concurrency + validation gate. |
| **IV. Test Discipline & Contract-First** | Contracts before implementation; pytest on backend critical paths; executable quickstart; health gates. | ✅ PASS | Backend interface contracts (`contracts/`: pydantic models, `validate_coverage` signature, suggest-slots response) authored **before** implementation. Pytest cases (a)–(f) cover expansion, global dedup, blank-optional, unresolved candidate, legacy no-regression, and degradation, under `uv run pytest`. Existing suites stay green (SC-006). Frontend verified via executable `quickstart.md` (no test runner in repo today). |
| **V. Minimal Complexity & Reuse** | Reuse existing stack; YAGNI; new deps minimized + justified; no parallel framework. | ✅ PASS | **Zero new dependencies, zero DB migration, zero new table/column.** Reuses `get_relation_schema` (already shipped: endpoint + TS client + `ontology_typer` consumer), `CoverageManifest`/`SlotCoverage` and its status constants, the additive-optional `Section` field pattern from 015, and the frontend `getRelationSchema` + existing shadcn primitives. YAGNI: `FactSourceBinding` is a discriminator-only placeholder (FR-014); multi-hop authoring and legacy auto-migration are out of scope. Not a violation → Complexity Tracking stays empty. |
| **VI. Offline-First & Graceful Degradation** | Air-gap default; offline ≠ `degraded`; bundled assets; no runtime egress. | ✅ PASS | All ontology reads are offline against the published model; the LLM is local. Engine-unavailable degrades gracefully (FR-012): `validate_coverage(engine=None)` still flags required-relationship omissions via substring fallback and simply skips property-level expansion — no exception, no regression, no false "degraded" banner for a normally-offline system. Defensive engine gating follows the `edges_to_facts` (014 US3) try/except pattern. |

**Security & Compliance**: No secrets touched; no plaintext credential path. Role gating (`senior_analyst` author; QA/reviewer consume the manifest) unchanged. Identity continues via existing gateway headers. All ontology I/O is read-only and offline.

**Gate result: PASS (pre-Phase-0).** No violations; Complexity Tracking table left empty.

### Post-Design Re-evaluation (after Phase 1)

Re-checked against the Phase 1 artifacts (`research.md`, `data-model.md`, `contracts/`, `quickstart.md`). **Still PASS — no new violations:**

- **I**: Every FR traces to a decision in `research.md` and a contract; no scope resolved in code. ✅
- **II**: `data-model.md` confirms coverage is application data in `schema_json`; `contracts/coverage-schema.md` and `contracts/coverage-validation.md` use only read-only `get_relation_schema`/`get_subclasses`; no ontology-mutation or TTL-merge surface exists in any contract. ✅
- **III**: `contracts/coverage-validation.md` preserves the manifest shape (`to_dict()`/`summary()` unchanged, no new status), so `rules_summary` consumers and audit provenance are intact; dedup keeps the omission count truthful (FR-011a). ✅
- **IV**: `contracts/` written before implementation; `quickstart.md` is an executable expansion/dedup/degradation matrix; existing pytest untouched and green. ✅
- **V**: Design confirms no migration/table/dependency; `data-model.md §Persistence` shows the additive `schema_json` nesting and the 015 `prompt` precedent. ✅
- **VI**: `contracts/coverage-validation.md` specifies the `engine=None` degradation path (substring presence fallback, property expansion skipped, no error). ✅

Design surfaced no complexity requiring justification — Complexity Tracking remains empty.

## Project Structure

### Documentation (this feature)

```text
specs/016-section-coverage-binding/
├── plan.md              # This file (/speckit-plan command output)
├── spec.md              # Feature specification (clarified — 3 Qs)
├── research.md          # Phase 0 output (/speckit-plan) — design decisions D1–D13
├── data-model.md        # Phase 1 output (/speckit-plan) — coverage entities + persistence model
├── quickstart.md        # Phase 1 output (/speckit-plan) — executable expansion/dedup/degradation matrix
├── contracts/           # Phase 1 output (/speckit-plan) — backend interface + frontend authoring contracts
│   ├── coverage-schema.md       # Pydantic models: OntologyRelationBinding / FactSourceBinding / CoverageBinding / Section.coverage
│   ├── coverage-validation.md   # validate_coverage(engine) seam, relationship→property expansion, global dedup, degradation
│   ├── suggest-slots-api.md      # AI authoring: doc_class_iri threading, injected graph, coverage + unresolved_candidates response
│   └── frontend-authoring.md    # Section coverage UI, unresolved-candidate disposition, stop-the-collapse contract
├── checklists/
│   └── requirements.md  # Spec quality checklist (16/16)
└── tasks.md             # Phase 2 output (/speckit-tasks — NOT created here)
```

### Source Code (repository root)

**Changes span `backend/` (primary) and `frontend/` (thin slice). No Alembic migration, no new DB column/table.**

```text
backend/
└── app/
    ├── services/reporting/
    │   ├── ast_template.py            # AST-1: +OntologyRelationBinding, +FactSourceBinding, +CoverageBinding union,
    │   │                              #        +Section.coverage: list[CoverageBinding] = Field(default_factory=list)
    │   │                              #        (mirrors additive-optional Section.prompt from 015; KEEP Extraction/LLMExtraction)
    │   ├── coverage_validator.py      # AST-2: +engine param (default None); +_relationship_present, +_resolve_coverage_binding;
    │   │                              #        section-loop seam BEFORE groups; global dedup via seen_keys; reuse FILLED/
    │   │                              #        BLANK_OPTIONAL/MISSING_REQUIRED (no new status); source_kind='ontology_relation'
    │   └── risk_report_generator.py   # AST-3: pass engine=get_loaded_engine() into validate_coverage
    ├── services/extraction/
    │   └── slot_suggester.py          # AI: +doc_class_iri param; inject compact get_relation_schema graph into LLM rounds;
    │                                  #     emit coverage[] + unresolved_candidates[] (required=True default); DELETE _bind_ontology_iris
    ├── api/
    │   ├── extraction.py              # report-gen: resolve DB template via resolve_template(doc_class_iri) (was load_default_template);
    │   │                              #     coverage-view: pass engine + emit section.coverage positions in response tree
    │   └── ast_templates.py           # suggest-slots endpoint: recover doc_class_iri (job cache / req field) → suggest_slots
    ├── schemas/
    │   └── extraction.py              # +CoverageDeclaration, +UnresolvedCandidate; +coverage/+unresolved_candidates on
    │                                  #     SuggestSlotsResponse; +doc_class_iri (optional) on SuggestSlotsRequest;
    │                                  #     +coverage positions on SectionCoverageResponse
    └── tests/
        ├── fixtures/ontology.py       # extend build_drug_ontology() fake with get_relation_schema (+ shape guard)
        ├── test_reporting/test_coverage_validator.py   # (a) expansion (b) global dedup (c) blank-optional (e) legacy (f) degradation
        ├── test_reporting/test_multi_template_e2e.py   # e2e: DB template coverage → deduped omissions; regression missing_required==11
        └── test_extraction/test_slot_suggester.py      # (d) unresolved candidate; required-by-default; replace _bind_ontology_iris tests

frontend/
└── src/
    ├── components/extraction/
    │   └── template-slot-editor.tsx   # +CoverageBinding types +SectionDef.coverage; +SectionCoverageArea (modeled on
    │                                  #     SectionPromptArea); unresolved-candidate disposition dropdown; STOP both
    │                                  #     →manual collapses (suggestionToSlot + ghost-row badge); preserve slot editing
    │                                  #     LAYOUT per design.pen frame "Slot Tree Component — Full View" (vwW7Q / JKSZm)
    └── lib/api.ts                     # +OntologyRelationBinding/UnresolvedCandidate types; +coverage/unresolved_candidates
                                       #     on SuggestSlotsResponse; +doc_class_iri on SuggestSlotsRequest (NO new endpoint)
```

**Structure Decision**: **Option 2 (web application), backend-weighted.** The repository already has `backend/` (FastAPI) and `frontend/` (Next.js). This feature (a) evolves the declarative template schema in place with an additive-optional `Section.coverage` field (no fork, no migration — coverage nests in the existing `schema_json` JSON column); (b) extends the existing `coverage_validator` with a section-level seam and the existing `get_relation_schema` read-only query, reusing the `CoverageManifest`/`SlotCoverage` model and status vocabulary; (c) rewires the AI suggester and its response schema to emit ontology-grounded declarations instead of invented slots; and (d) adds a section-coverage authoring surface + unresolved-candidate disposition to the single existing editor component, reusing already-imported shadcn primitives and the existing `getRelationSchema` client. No new top-level project, no new dependency, no DB change.

## Complexity Tracking

> No Constitution Check violations. Coverage lives in the existing `schema_json` JSON column (no migration/table/column), reuses shipped ontology queries and the existing coverage manifest, and adds no dependency. `FactSourceBinding` is a YAGNI placeholder (discriminator only). Table intentionally empty.

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| — | — | — |
