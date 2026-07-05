# Phase 0 Research: Section-Level Ontology Coverage Binding

**Feature**: 016-section-coverage-binding | **Date**: 2026-07-05 | **Plan**: [plan.md](plan.md)

This document resolves every open decision needed to implement the plan. All Technical Context items are known (no `NEEDS CLARIFICATION` remained after `/speckit-clarify`); the research below grounds each design choice in the **actual current code** so implementation is deterministic. Findings were produced by a parallel multi-agent code-grounding pass over `ast_template.py`, `coverage_validator.py`, `ontology_engine.py`, `slot_suggester.py`, `api/extraction.py`, `api/ast_templates.py`, `schemas/extraction.py`, `models/extraction.py`, `template-slot-editor.tsx`, `lib/api.ts`, and the backend test suite, then verified against exact signatures/line numbers.

Format per decision: **Decision / Rationale / Alternatives considered**. Decisions map to the design note `docs/section-coverage-declaration-design.md` §3–§9 and the clarified spec FRs.

---

## Grounding facts (verified against current code)

These are load-bearing invariants the decisions rely on; each was read directly, not assumed.

- **`OntologyEngine.get_relation_schema(self, class_iri, max_hops=4)`** (`ontology_engine.py:483-578`) — read-only Owlready2 BFS. Per edge returns `{hop, predicate_iri, predicate_label, domain_class_iri, domain_class_label, range_class_iri, range_class_label, range_subclasses:[{iri,label}], range_data_properties:[{iri,label}]}`. **Global first-discovery dedup** on a `visited_ranges` set (`528`, `542-544`, `553`): each range IRI is emitted **at most once** across the whole BFS. Domain membership is **exact, not inherited** (`513`, `522`); ranges are gated to **named** classes (`541`).
- **`get_subclasses(class_iri, recursive=True) -> [{iri,label}]`** (`424-444`) and **`get_data_properties_by_domain(class_iri) -> [{iri,name,label}]`** (`378-398`) — both read-only; the latter supplies hop-0 doc-class direct scalars that `get_relation_schema` (hop ≥ 1) omits.
- **Engine load is read-only** (`ontology_engine.py:124-163`): a rebuilt **derived** World over authoritative TTL, `only_local=True`, never written back.
- **`Section`** (`ast_template.py:147-154`) already carries an additive-optional `prompt: str | None = None` (shipped in 015) — the exact precedent for adding `coverage`.
- **Pydantic default is `extra='ignore'`** (no `model_config` anywhere in `ast_template.py`): an un-declared JSON key is **silently dropped** on `model_validate`. Therefore `Section.coverage` **must** be a declared field or coverage never survives a round-trip.
- **`validate_coverage`** (`coverage_validator.py:294-334`) loops `for section in template.sections:` (`317`) then `for group in section.groups:` (`318`); ends with a per-job dismissed-flip (`327-332`). `CoverageManifest`/`SlotCoverage` (`59`, `74`) expose `to_dict()`/`summary()`; status counts are a `Counter` over the flat slot list.
- **`AstTemplate.schema_json`** (`models/extraction.py:127`) is a generic `mapped_column(JSON)`; Alembic head `0017_ast_basic_info`; `main.py` swallows migration failures (`93-97`) — a structural argument **for** keeping coverage in JSON, not a new column.
- **Frontend already ships** `getRelationSchema()`/`RelationSchemaEdge` (`lib/api.ts:87-101`) and `SlotCoverageDTO`/`SectionCoverageDTO` (`1569-1608`); `suggestionToSlot` (`template-slot-editor.tsx:214-227`) and the ghost-row badge (`2099-2104`) are the **two** `→ manual` collapse sites.
- **Test fake** `tests/fixtures/ontology.py::build_drug_ontology()` — its `FakeFeatureEngine.__getattr__` returns `None` (not a list); `get_loaded_engine()` returns `None` in the test env (lifespan skipped). Both matter for the degradation path.

---

## D1 — Canonical store for coverage declarations

**Decision**: The canonical, persisted store is **per-section `Section.coverage: list[CoverageBinding]`**, nested inside the existing `AstTemplate.schema_json` JSON column. Global omission de-duplication is a **validate-time** concern, **not** a storage concern — the same relationship key may be persisted under multiple sections.

**Rationale**: FR-011a explicitly allows the same relationship in multiple sections (each shows it for narrative context) while counting the omission once. Storing per-section keeps authoring self-contained (a section owns its declarations, matching the editor's mental model and the `Section.prompt` precedent) and requires no new column/table — the additive field rides inside `schema_json`. Deduplication belongs at the moment of counting, where a `seen_keys` set is cheap and correct.

**Alternatives considered**:
- *Template-level `coverage` map with section back-references* — would centralize dedup but breaks the section-owns-its-contract authoring model, complicates the editor, and needs a migration of the JSON shape. Rejected (violates Minimal Complexity; no FR demands it).
- *Compile declarations into persisted `ExtractionSource` slots at save time* — resurrects the "slot as persisted artifact" the feature is removing (design note §2/§3), and re-introduces sample-anchoring risk. Rejected.

---

## D2 — How the validator consumes coverage (declarations → SlotCoverage)

**Decision**: `validate_coverage` consumes `section.coverage` **directly**, emitting **synthetic `SlotCoverage` positions** at validation time. Coverage declarations do **NOT** compile to persisted `ExtractionSource`. `ExtractionSource`/`LLMExtractionSource` are **retained** for (a) legacy per-field templates and (b) the runtime `template_expander` path; they are simply no longer the authoring unit for graph-sourced content.

**Rationale**: Matches design note §3 ("`ExtractionSource` 从持久化编写工件降级为运行时中间产物") and §8. Emitting synthetic `SlotCoverage` reuses the entire existing manifest/status/`to_dict()` machinery (Principle V) and keeps the no-omission proof shape identical for `rules_summary` consumers (Principle III). The seam is inside the existing section loop (`coverage_validator.py:317`), **before** the group loop (`318`), so coverage positions and legacy slot positions coexist in one manifest.

**Alternatives considered**:
- *A separate `validate_coverage_v2` path* — parallel framework, violates Principle V, doubles the manifest surface. Rejected.
- *Remove `ExtractionSource` entirely* — breaks FR-013 backward compat and the `template_expander` runtime path. Rejected.

---

## D3 — Global omission de-duplication (FR-011a)

**Decision**: De-duplicate at emission via a `seen_keys: set` keyed by **`(doc_class_iri, predicate_iri, range_class_iri)`**. The first section to declare a relationship emits the counting `SlotCoverage` position(s); a later section declaring the same key emits a **non-counting reference marker** (surfaced for narrative context, excluded from the omission/`missing_required` count). **No schema-level uniqueness validator** is added.

**Rationale**: FR-011a requires exactly-once counting keyed by (document-type + relationship + target-type), regardless of how many sections declare it, while still letting each section show the binding. A validate-time `seen_keys` set is the minimal mechanism. A schema uniqueness validator would **reject** legal multi-section declarations (breaking the clarified Q3 answer) and is therefore explicitly avoided (confirmed: `ast_template.py` has no cross-section coverage uniqueness check today — none must be added).

**Alternatives considered**:
- *Dedup at storage (reject duplicate keys on save)* — contradicts FR-011a's "allowed, deduped globally." Rejected.
- *Dedup only within a section* — would double-count across sections, inflating the omission signal (the exact anti-goal of §4). Rejected.

---

## D4 — Unresolved candidates (FR-008a)

**Decision**: Unresolved candidates are a **new transient field on the suggest-slots response** (`SuggestSlotsResponse.unresolved_candidates: list[UnresolvedCandidate]`), never a persisted slot. Each carries the AI's evidence (proposed label, sample snippet, why-unbound) for author disposition. A candidate is persisted **only** if the author accepts it — as either a `CoverageBinding` (bind) or a `constant`/`manual` slot (convert) — via the existing create/update endpoints. An unbindable position **never** silently becomes a `manual` slot.

**Rationale**: FR-008a + clarification Q2 require explicit author disposition and forbid the "everything → manual" recreation. Modeling candidates as transient response data (not schema) keeps `schema_json` clean and makes "did the author decide?" observable. This is the structural fix for defect (1) on the authoring side.

**Alternatives considered**:
- *Persist candidates as a `pending` slot kind* — pollutes the persisted schema with undecided state and risks a generation path treating them as real positions. Rejected.
- *Auto-convert unbindable → manual with a flag* — indistinguishable in practice from today's silent collapse; violates FR-008a. Rejected.

---

## D5 — Report-generation template resolution (rewire, FR-010/SC-006)

**Decision**: Rewire `generate_risk_report` (`api/extraction.py:1056-1182`, currently `RiskReportGenerator(db)` → filesystem `load_default_template`) to resolve the **DB-persisted** template via **`resolve_template(doc_class_iri, db)`** (the 3-tier resolver at `ast_template.py:217-258`), so authored coverage actually drives generation. Guard with the existing regression test (`test_multi_template_e2e.py`, `missing_required == 11`) to prove SC-006.

**Rationale**: Coverage lives on DB templates; if generation keeps loading the filesystem default, authored coverage is inert. `resolve_template` already implements iri-pattern → is_default → filesystem fallback, so the rewire reuses shipped logic and preserves the filesystem template as the final fallback (offline-safe). This is in-scope because FR-010/SC-004 require a DB-authored template to drive generation for same-type documents.

**Alternatives considered**:
- *Leave generation on the filesystem default; only surface coverage in the coverage-view API* — authored coverage would never affect a generated report, defeating US1/US2 end-to-end. Rejected.
- *New resolver* — `resolve_template` already exists and is tested. Rejected (Principle V).

---

## D6 — Property → required promotion mechanism (FR-007)

**Decision**: Promotion is a **per-section coverage override**: `OntologyRelationBinding.required_properties: list[str]` (data-property IRIs/local-names). A property listed there is emitted `MISSING_REQUIRED` when blank; all other expanded properties default to `BLANK_OPTIONAL` (informational). **Not** an ontology annotation.

**Rationale**: The ontology is read-only in this posture (Principle II), so required-ness cannot be written back as a T-Box annotation. Per-section override keeps authoring self-contained and matches the spec Assumption ("per-section coverage override, author-controlled"). Relationship-granularity omission (the relationship's absence) remains the primary signal (§4); property promotion is the explicit, opt-in exception (FR-006/FR-007).

**Alternatives considered**:
- *Ontology-annotation-driven promotion* — requires a write path or a side annotation store; explicitly Out of Scope for this iteration and blocked by read-only posture. Rejected (deferred per spec).
- *Global (template-level) required-property list* — less precise; the same property may matter in one section and not another. Rejected.

---

## D7 — Subclass acceptance & offline range matching (FR-004, Principle VI)

**Decision**: When checking whether a source graph satisfies a declared relationship's target type, accept the declared `range_class_iri` **or any of its subclasses** via `get_subclasses(range_class_iri, recursive=True)`. When the engine is unavailable, fall back to a **substring/local-name presence** check over the source graph's node types.

**Rationale**: A source graph often instantiates a subclass of the declared range (e.g., declared `DrugProduct`, graph has `BiologicalProduct`); subclass acceptance prevents false omissions (FR-004 fidelity). The offline substring fallback preserves the required-relationship omission signal even with no engine (FR-012), degrading precision, not correctness of the primary signal.

**Alternatives considered**:
- *Exact range match only* — would false-flag legitimate subclass instances as omissions, undermining SC-003. Rejected.
- *Skip presence check entirely when offline* — would silently drop the omission signal, the opposite of no-omission. Rejected.

---

## D8 — Authoring depth & the `visited_ranges` limitation (Assumption: single-hop)

**Decision**: Authoring anchors to **direct (single-hop, hop-1) relationships** of the document entity type. The validator expands the target type's **own** data properties (hop-0 of the range). The known `get_relation_schema` **global first-discovery dedup** (a range IRI emitted at most once across the BFS) is documented as an accepted constraint for this iteration: if two distinct predicates lead to the same range type, only the first predicate's edge carries that range's property list.

**Rationale**: Single-hop is the spec Assumption and matches the design note's "边的两端永远是类" model. Multi-hop authoring is Out of Scope. The `visited_ranges` limitation is real but low-impact at single-hop authoring (authors pick specific predicate edges), and fixing the BFS is a separate ontology-engine change outside this feature's read-only-consumer scope. Documenting it prevents a silent coverage gap being mistaken for a bug.

**Alternatives considered**:
- *Multi-hop chained authoring* — Out of Scope; exponential UI/validation complexity. Rejected (deferred).
- *Patch `get_relation_schema` to emit per-predicate ranges* — mutates a shared read-only query used by `ontology_typer`; out of this feature's blast radius and risks regressions. Rejected (flagged as a known limitation instead).

---

## D9 — LLM graph injection for the suggester (FR-002, performance)

**Decision**: In `slot_suggester.suggest_slots`, inject a **compact hop-1 view** of `get_relation_schema(doc_class_iri)` (predicate IRI/label, range IRI/label, and a pruned property-name list) **plus** `get_data_properties_by_domain(doc_class_iri)` for hop-0 doc-class scalars, into the Round-1/Round-2 prompts. The LLM's job narrows to "which of THESE real edges does each section cover" — it selects predicate IRIs, it does not invent them. **Delete** `_bind_ontology_iris` (the post-hoc `label in dp_labels` exact match).

**Rationale**: This is the structural fix for defect (1) on the AI side (design note §8): grounding the model in the real edge set makes "extraction" intrinsic to a graph-sourced binding rather than something a fragile string match must earn. Pruning the injected graph (labels + names only, no full IRIs where a short handle suffices) keeps the prompt within the local model's context budget. Caching `get_relation_schema` once per `doc_class_iri` avoids recomputation across rounds.

**Alternatives considered**:
- *Keep `_bind_ontology_iris`, just improve the string match (fuzzy/embedding)* — still ontology-blind during generation, still post-hoc, still fragile across vocabularies. Rejected (patches the symptom, not the root cause).
- *Inject the full multi-hop schema* — blows the local context budget and invites multi-hop hallucination. Rejected.

---

## D10 — `doc_class_iri` provenance (FR-002, Assumption: upstream input)

**Decision**: `doc_class_iri` is threaded to the suggester from two provenance sources, both **optional** and **outside** the request's exactly-one-of validator:
- **Server-side**: recover from the annotation cache (`result['doc_class'] = {doc_class_iri, label, score, signals}`, written in `api/extraction.py:390-402`) when a `job_id` is supplied.
- **Client-side**: an optional `doc_class_iri` field on `SuggestSlotsRequest` (`schemas/extraction.py:306-323`), sourced from the editor's `sourceDocClass`/`iriPattern` prop when the author already knows the type.

**Rationale**: The spec Assumption states the document entity type is an upstream classification input, not something this feature computes. `SuggestSlotsRequest.model_post_init` enforces exactly-one-of `{job_id, document_text, sample_content_json}`; `doc_class_iri` must therefore be an **additional** optional field that does not participate in that count. If neither provenance yields a type, the suggester degrades to the no-graph path (still emits sections; graph-sourced coverage simply cannot be proposed).

**Alternatives considered**:
- *Classify the document inside the suggester* — duplicates upstream classification, Out of Scope, and couples authoring to the typer. Rejected.
- *Make `doc_class_iri` required* — breaks the `document_text`/`sample_content_json` request variants that have no job/classification. Rejected.

---

## D11 — Fact-source binding (FR-014, YAGNI placeholder)

**Decision**: `FactSourceBinding(kind='fact_source', ...)` is modeled as a **discriminator-only placeholder** in the `CoverageBinding` union. At validation time it emits a **single non-counting `MANUAL` placeholder** position (positions remain human-filled until the fact-source data provider lands). No fact-source query engine is built.

**Rationale**: FR-014 requires the model be **extensible** to fact-source without building the provider (explicitly Out of Scope). A discriminator member + a manual-placeholder emission satisfies extensibility with zero speculative machinery (Principle V / YAGNI). It also proves the discriminated-union shape works for a second `kind`, de-risking the future addition.

**Alternatives considered**:
- *Build the fact-source query interface now* — Out of Scope; speculative. Rejected.
- *Omit `FactSourceBinding` entirely* — fails FR-014 extensibility and forces a union-shape change later. Rejected.

---

## D12 — Status vocabulary (FR-011, Principle III)

**Decision**: **Reuse the existing status constants** — `FILLED`, `BLANK_OPTIONAL`, `MISSING_REQUIRED` (plus `MANUAL`, `DISMISSED`). **No new status enum.** Ontology-derived positions are distinguished by a `source_kind='ontology_relation'` tag on the synthetic `SlotCoverage`, and synthesized `slot_id`s namespaced `coverage.<pred_short>__<range_short>` (relationship) and `…__<prop_short>` (property).

**Rationale**: FR-011 requires the manifest keep enumerating per-position status (filled / informational-blank / required-omission / human-filled) — those map 1:1 onto existing constants. Adding a status would break every `rules_summary` consumer and the `Counter`-based `summary()`. A `source_kind` tag + namespaced `slot_id` gives full provenance/traceability (Principle III) without touching the status axis.

**Alternatives considered**:
- *Add `INFORMATIONAL` / `OMISSION_RELATIONSHIP` statuses* — breaks manifest consumers and the dismissed-flip logic; unnecessary since `BLANK_OPTIONAL`/`MISSING_REQUIRED` already mean exactly this. Rejected.

---

## D13 — Coverage-view API emission (FR-009, US3 editor round-trip)

**Decision**: In `_build_ast_coverage_response` (`api/extraction.py:1284-1360`), pass `engine=get_loaded_engine()` into `validate_coverage` and add a **coverage-emission branch** to the response tree so section-level ontology positions surface as ghost/coverage rows in the editor. This runs the coverage path **instead of** relying on `template_expander`'s AI-invented `ontology_expansion` `llm_extraction` section — the two must **not** double-emit the same positions.

**Rationale**: The `template_expander` (invoked at `1337`, appends an `ontology_expansion` section) is the AI-invented path this feature replaces (design note §8). Emitting coverage positions from `section.coverage` gives the editor a faithful, ontology-grounded ghost view (US3) and avoids the sample-anchored expansion. Guarding against double-emit keeps the manifest's dedup honest.

**Alternatives considered**:
- *Keep `template_expander` and also emit coverage* — double-counts positions and reintroduces sample-anchored ghosts. Rejected.
- *Drop `template_expander` outright* — it still serves legacy templates with no `coverage`; keep it as a fallback only when a section has no coverage. Accepted as the guard condition (coverage present → skip expander for that section).

---

## Deferred-clarify resolutions (non-blocking, resolved here)

- **Performance / SLA**: No hard latency SLA at platform scale (~200 classes, single document, low concurrency). Mitigation: cache `get_relation_schema` **once per `doc_class_iri` per `validate_coverage` run** (bindings under one doc class share the edge list); inject only a pruned hop-1 view into the LLM. Target: coverage adds no user-perceptible latency to generation. (Resolved — no NEEDS CLARIFICATION.)
- **Frontend test strategy**: The repo has no JS test runner today; frontend behavior (stop-the-collapse, coverage rendering, candidate disposition) is verified through the executable `quickstart.md` steps and manual editor walkthrough. Backend is fully pytest-covered. (Resolved — consistent with Principle IV's "pytest on backend critical paths.")
- **Legacy migration**: **Not** auto-migrated (Out of Scope). Legacy per-field templates resolve unchanged via the retained `ExtractionSource` path (FR-013); authors opt into declarations by re-running AI analysis or editing. (Resolved.)

---

## Summary of decisions

| ID | Area | Decision (one line) |
|----|------|----------------------|
| D1 | Storage | Canonical store = per-section `Section.coverage` in `schema_json`; dedup is validate-time |
| D2 | Validator | Consume `section.coverage` directly → synthetic `SlotCoverage`; keep `ExtractionSource` for legacy/runtime |
| D3 | Dedup | Validate-time `seen_keys` on `(doc,pred,range)`; first counts, rest are references; no schema uniqueness check |
| D4 | Candidates | Transient `unresolved_candidates` on suggest response; persisted only on author acceptance; never silent manual |
| D5 | Generation | Rewire `generate_risk_report` → `resolve_template(doc_class_iri, db)`; guard with `missing_required==11` |
| D6 | Required props | Per-section `required_properties` override (read-only-safe); not ontology annotation |
| D7 | Range match | Accept range subclasses via `get_subclasses`; offline substring presence fallback |
| D8 | Depth | Single-hop authoring; document `visited_ranges` first-discovery limitation as accepted constraint |
| D9 | AI grounding | Inject compact hop-1 `get_relation_schema` + doc-class scalars; delete `_bind_ontology_iris` |
| D10 | Provenance | `doc_class_iri` optional, outside exactly-one-of; server=annotation cache, client=editor prop |
| D11 | Fact-source | Discriminator-only placeholder → single non-counting MANUAL position; no provider built |
| D12 | Status | Reuse `FILLED`/`BLANK_OPTIONAL`/`MISSING_REQUIRED`; distinguish via `source_kind='ontology_relation'` |
| D13 | Coverage view | Emit coverage positions in `_build_ast_coverage_response`; guard against `template_expander` double-emit |

**All Technical Context unknowns resolved. Ready for Phase 1.**
