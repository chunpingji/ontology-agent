# Quickstart: Section-Level Ontology Coverage Binding

**Feature**: 016-section-coverage-binding | **Plan**: [plan.md](plan.md) · **Contracts**: [contracts/](contracts/)

Executable validation that the feature works end-to-end. Backend is fully pytest-covered; the frontend slice is verified via an editor walkthrough (no JS test runner in the repo). All commands run from repo root.

> **Environment**: backend Python is **uv-managed** — always `cd backend && uv run …`. Bare `python`/`pytest` is anaconda and lacks `psycopg2`.

---

## Prerequisites

```bash
cd backend && uv sync            # deps
# Ontology engine is optional for most tests: the fake engine (tests/fixtures/ontology.py)
# stands in, and get_loaded_engine() returns None in the test env (lifespan skipped).
```

**Test fixture prep (one-time, part of implementation)**: extend `tests/fixtures/ontology.py::build_drug_ontology()` (`FakeFeatureEngine`) with a `get_relation_schema(class_iri, max_hops=4)` method returning the verified edge shape, plus `get_subclasses`/`get_data_properties_by_domain`. Its `__getattr__` currently returns `None` (not a list) — `get_relation_schema` MUST be a real method or expansion silently no-ops. (See memory `ontology-aware-paths-legacy-in-tests`.)

---

## Backend validation (pytest)

Run the full suite; every existing test must stay green (SC-006), and the new cases below must pass.

```bash
cd backend && uv run pytest -q
```

### (a) Relationship expansion + property informational — US2 / FR-004, FR-005, FR-006

```bash
cd backend && uv run pytest tests/test_reporting/test_coverage_validator.py \
  -k "expands_relationship or present_relationship_blank_props" -v
```

**Expected**: a required relationship whose target is present expands into its target type's properties; blank, un-promoted properties are `BLANK_OPTIONAL` (informational) and raise **zero** omissions. (Contract V2, V3.)

### (b) Required-relationship omission + global dedup — US2 / FR-005, FR-011a

```bash
cd backend && uv run pytest tests/test_reporting/test_coverage_validator.py \
  -k "required_relationship_absent or duplicate_relationship_across_sections" -v
```

**Expected**: a required relationship whose target type (and all subclasses) is absent ⇒ exactly one `MISSING_REQUIRED`. Declaring the same `(doc_class_iri, predicate_iri, range_class_iri)` in two sections ⇒ the omission is counted **once**; the second section emits a non-counting reference. (Contract V2, V5.)

### (c) Promoted property becomes required — FR-007

```bash
cd backend && uv run pytest tests/test_reporting/test_coverage_validator.py \
  -k "promoted_property_blank" -v
```

**Expected**: a property listed in `required_properties` that is blank ⇒ `MISSING_REQUIRED`; siblings not promoted stay `BLANK_OPTIONAL`. (Contract V4.)

### (d) AI authoring emits coverage + unresolved candidate, not manual — US1 / FR-002, FR-008a, SC-001, SC-002

```bash
cd backend && uv run pytest tests/test_extraction/test_slot_suggester.py \
  -k "emits_coverage or unresolved_candidate or references_types_never_individuals or pure_coverage_shape or no_legacy_slot_stream" -v
```

**Expected**: graph-sourced sections yield `CoverageDeclaration`s (zero default-to-manual); every declaration references a **type** IRI, never a sample individual; an unbindable data-looking position surfaces as an `unresolved_candidate` (not a `manual` slot). The suggester's output is **only** `{document_summary, coverage, unresolved_candidates}` — the whole legacy slot stream (`_bind_ontology_iris` exact-match, `sections`/`total_suggested`/`skipped_duplicates`/`truncated`) is gone. (Contract S1, S2, S4, S3.)

### (e) Backward compatibility — FR-013 / SC-006 (**primary regression gate**)

```bash
cd backend && uv run pytest tests/test_reporting/test_multi_template_e2e.py -v
cd backend && uv run pytest tests/test_reporting/test_coverage_validator.py -k "empty_is_noop_legacy" -v
```

**Expected**: legacy templates (no `coverage`) produce **identical** coverage outcomes — `missing_required == 11` unchanged; the coverage section-loop is a strict no-op when `coverage == []`. (Contract V1.)

### (f) Graceful degradation (engine unavailable) — FR-012 / Principle VI

```bash
cd backend && uv run pytest tests/test_reporting/test_coverage_validator.py \
  -k "engine_none_degrades" -v
```

**Expected**: `validate_coverage(engine=None)` still flags required-relationship omissions via the substring presence fallback, **skips** property expansion, raises **no** exception, and returns a manifest of unchanged shape. No false "degraded" state. (Contract V6, V11.)

### Full green gate

```bash
cd backend && uv run pytest -q        # expect: all pass, no regressions
```

---

## Schema round-trip (contract-level) — FR-013, D1

```bash
cd backend && uv run pytest tests/test_reporting/test_ast_template.py \
  -k "coverage_round_trips or legacy_template_without_coverage" -v
```

**Expected**: a template with `section.coverage` survives `model_validate → model_dump` (the field is declared, so `extra='ignore'` does not drop it); a legacy template with no `coverage` normalizes with no shape drift and `coverage == []`. (Contract C1, C2.)

---

## End-to-end generation drives DB template — FR-010 / SC-004, D5

```bash
cd backend && uv run pytest tests/test_reporting/test_multi_template_e2e.py \
  -k "resolve_template or coverage_drives_generation" -v
```

**Expected**: `generate_risk_report` resolves the DB-persisted template via `resolve_template(doc_class_iri, db)` (not the filesystem default), so authored coverage actually drives the generated report and its manifest; a template authored from one sample validates against a different same-type document with no re-authoring. (Decision D5.)

---

## Frontend walkthrough (manual — no JS test runner)

Start the app. First confirm the **required document type** gate, then run AI analysis:

| Step | Expected | Contract |
|------|----------|----------|
| 0. Create/edit a template | `关联文档类型` is a **required** select (`RegulatoryDocument` subclasses, e.g. CMCReport). The create wizard's «进入编辑器» and the editor's Basic-Info save are **disabled/blocked** until it is chosen; selecting it grounds `doc_class_iri`. | FE F9 / FR-016 |
| 1. Run AI analysis | Section "风险评估对象基本描述" shows **coverage declarations** (e.g., `describes → DrugProduct`, `hasSynthesisRoute → SynthesisRoute`), NOT a list of "human-filled" fields. There is **no** ghost-slot stream, no «全部采纳»/«已跳过» controls — output is coverage + unresolved candidates + a summary only. | FE1 / FR-002a / SC-001 |
| 2. Inspect a declaration | It names a **type** (`DrugProduct`); no concrete id (e.g., no `EQUIP_REF_646`). | FE2 / SC-002 |
| 3. Unresolved candidate | A data-looking position the AI couldn't bind appears in a **pending disposition** list with bind / constant / manual / discard actions. | FE4 / FR-008a |
| 4. Non-graph slots | `rule` / `constant` / `manual` slots in the same template render and edit **unchanged**. | FE3 / FR-008 |
| 5. Save → reload | Coverage persists (opaque `schema_json` round-trip); no api.ts/page.tsx change needed. | FE5 |
| 6. Mark a declaration optional → save → re-validate | The omission signal reflects the change (required omission disappears). | F8 / US3-3 |

---

## Success-criteria mapping

| Criterion | Validated by |
|-----------|--------------|
| SC-001 zero graph-sourced fields default to manual | (d), FE step 1 |
| SC-002 100% declarations reference type, 0 individuals | (d), FE step 2 |
| SC-003 omission calibrated (absent→flag, blank→no flag) | (a), (b) |
| SC-004 one-sample template validates other same-type doc | E2E generation section |
| SC-005 ≥80% fewer manual corrections | FE walkthrough (qualitative; measured against per-field baseline) |
| SC-006 no regression on legacy templates | (e), full green gate |

**Done when**: `uv run pytest -q` is fully green (no regressions) and the six frontend walkthrough steps pass.
