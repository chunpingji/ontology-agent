# Contract: Coverage Validation (AST-2 / AST-3)

**Feature**: 016-section-coverage-binding
**Files**: `backend/app/services/reporting/coverage_validator.py` (primary), `risk_report_generator.py` (wiring)

Teaches `validate_coverage` to expand `section.coverage` into synthetic `SlotCoverage` positions, reusing the existing manifest/status machinery. Additive: legacy behavior is a strict no-op when `coverage == []`.

---

## Signature change (backward-compatible)

```python
def validate_coverage(
    template: ReportTemplate,
    facts: ...,                       # existing
    *,
    job_id: str | None = None,        # existing
    db: Session | None = None,        # existing
    engine: Any | None = None,        # NEW — read-only OntologyEngine; default None
) -> CoverageManifest:
```

- `engine` **defaults to `None`** ⇒ existing callers compile unchanged (FR-013).
- `risk_report_generator.generate_with_coverage` (`risk_report_generator.py:134-137`) passes `engine=get_loaded_engine()` (already imported, `:112`).
- `api/extraction._build_ast_coverage_response` passes `engine=get_loaded_engine()` (D13).

---

## The seam (exact insertion point)

Inside the existing section loop (`coverage_validator.py:317`), **before** the group loop (`318`):

```python
seen_keys: set[tuple[str, str, str]] = set()      # module/function-scoped across all sections
schema_cache: dict[str, list[dict]] = {}          # get_relation_schema memo, keyed by doc_class_iri

for section in template.sections:
    for binding in getattr(section, "coverage", []):        # getattr → safe on legacy objects
        manifest.slots.extend(
            _resolve_coverage_binding(binding, facts, engine, seen_keys, schema_cache)
        )
    for group in section.groups:        # existing loop, unchanged
        ...
# existing per-job dismissed-flip (327-332) unchanged
```

---

## New helpers

### `_resolve_coverage_binding(binding, facts, engine, seen_keys, schema_cache) -> list[SlotCoverage]`

```
if binding.kind == "fact_source":
    return [ one non-counting MANUAL placeholder ]        # D11

# binding.kind == "ontology_relation"
key = (binding.doc_class_iri, binding.predicate_iri, binding.range_class_iri)
is_reference = key in seen_keys                            # D3: dup across sections
seen_keys.add(key)

present = _relationship_present(binding, facts, engine)   # D7

# 1) relationship-level position
rel_status = FILLED if present else (MISSING_REQUIRED if binding.required else BLANK_OPTIONAL)
positions = [ SlotCoverage(
    slot_id=f"coverage.{_short(predicate_iri)}__{_short(range_class_iri)}",
    status=(NON_COUNTING if is_reference else rel_status),  # reference → narrative-only
    source_kind="ontology_relation", source_ref=<pred+range IRIs>) ]

# 2) property-level positions — only when present AND engine available
if present and engine is not None:
    for prop in _range_data_properties(binding, engine, schema_cache):   # get_relation_schema edge
        blank = _prop_blank(prop, facts)
        promoted = prop.iri in binding.required_properties or prop.name in binding.required_properties
        status = FILLED if not blank else (MISSING_REQUIRED if promoted else BLANK_OPTIONAL)
        positions.append(SlotCoverage(slot_id=f"coverage.{...}__{_short(prop.iri)}",
                                      status=status, source_kind="ontology_relation", ...))
return positions
```

> `NON_COUNTING`: a reference position is emitted with a status that surfaces the binding for narrative context (FR-011a "sections still show the binding") but is **excluded from the omission `Counter`**. Implement as `BLANK_OPTIONAL` tagged `is_reference=True`, or reuse `FILLED` — the invariant is it MUST NOT increment `missing_required`. Choose whichever keeps `summary()` counts truthful; the counting test (below) is the gate.

### `_relationship_present(binding, facts, engine) -> bool`

```
target_types = {binding.range_class_iri}
if engine is not None:
    target_types |= { s["iri"] for s in engine.get_subclasses(binding.range_class_iri, recursive=True) }   # D7
    return any(node_type in target_types for node_type in facts.node_types())
# engine None → offline substring/local-name presence fallback (D7 / FR-012)
return _substring_present(binding.range_class_iri, facts)
```

### `_range_data_properties(binding, engine, schema_cache) -> list[{iri,name?,label}]`

```
edges = schema_cache.setdefault(binding.doc_class_iri,
                                engine.get_relation_schema(binding.doc_class_iri))   # memo per doc class
edge = next((e for e in edges
             if e["predicate_iri"] == binding.predicate_iri
             and e["range_class_iri"] == binding.range_class_iri), None)
return edge["range_data_properties"] if edge else []          # missing edge → no expansion, no error
```

---

## Contract guarantees

| # | Guarantee | Enforces |
|---|-----------|----------|
| V1 | `coverage == []` ⇒ section loop body is a no-op; manifest byte-identical to pre-feature. | FR-013 / SC-006 |
| V2 | Required relationship whose target type (or subclass) is absent ⇒ exactly one `MISSING_REQUIRED`. | FR-005 / SC-003 |
| V3 | Present relationship with blank, un-promoted properties ⇒ those props are `BLANK_OPTIONAL`; **zero** omissions from them. | FR-006 / SC-003 |
| V4 | A property in `required_properties` that is blank ⇒ `MISSING_REQUIRED`. | FR-007 |
| V5 | Same `(doc,pred,range)` key across N sections ⇒ omission counted **once**; later sections emit non-counting reference positions. | FR-011a / D3 |
| V6 | `engine is None` ⇒ relationship presence via substring fallback (still flags required omissions), property expansion skipped, **no exception**, no manifest-shape change. | FR-012 / VI |
| V7 | `get_relation_schema` called **at most once per `doc_class_iri`** per `validate_coverage` run (memoized). | Performance / D9 |
| V8 | `fact_source` binding ⇒ one non-counting `MANUAL` position; never an omission. | FR-014 / D11 |
| V9 | Manifest `to_dict()`/`summary()` shape and status vocabulary unchanged; ontology positions distinguished only by `source_kind='ontology_relation'`. | FR-011 / D12 / III |
| V10 | All engine calls are read-only (`get_relation_schema`, `get_subclasses`); no write path. | FR-015 / II |
| V11 | Engine access is defensively wrapped (try/except around engine calls, following the `edges_to_facts` 014-US3 pattern); an engine error degrades to the None path, never a 500. | FR-012 / VI |

---

## Regression guard (SC-006)

`test_multi_template_e2e.py` currently asserts `missing_required == 11` for legacy templates (no `coverage`). With V1, that count MUST remain `11` after this feature. This is the primary no-regression gate.

## Test matrix → guarantee

| Test (in `test_coverage_validator.py`) | Proves |
|----------------------------------------|--------|
| `test_coverage_expands_relationship_and_properties` | V2, V3 (expansion) |
| `test_required_relationship_absent_is_missing_required` | V2 |
| `test_present_relationship_blank_props_informational` | V3 |
| `test_promoted_property_blank_is_missing_required` | V4 |
| `test_duplicate_relationship_across_sections_counts_once` | V5 |
| `test_coverage_empty_is_noop_legacy_manifest` | V1 |
| `test_engine_none_degrades_gracefully` | V6, V11 |
| `test_fact_source_emits_noncounting_manual` | V8 |
| `test_get_relation_schema_memoized_once_per_docclass` | V7 |
