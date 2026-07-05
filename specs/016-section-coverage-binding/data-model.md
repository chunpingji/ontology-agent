# Phase 1 Data Model: Section-Level Ontology Coverage Binding

**Feature**: 016-section-coverage-binding | **Date**: 2026-07-05 | **Plan**: [plan.md](plan.md) · **Research**: [research.md](research.md)

Entities are derived from spec **Key Entities** and resolved decisions D1–D13. This feature adds **no database entity** — all new state nests inside the existing `AstTemplate.schema_json` JSON column, or is transient (suggest-time response only). Field types are shown in Python/Pydantic terms for the authoritative backend model; the TypeScript editor mirror is noted where it differs.

---

## Persistence model (no migration)

```
AstTemplate (DB row, models/extraction.py:117-148)
└── schema_json : JSON                     ← existing generic JSON column, UNCHANGED
    └── ReportTemplate (ast_template.py)
        └── sections: [ Section ]
            ├── prompt: str | None          ← additive-optional, shipped 015 (precedent)
            ├── coverage: [ CoverageBinding ]  ← NEW additive-optional field (this feature)
            └── groups: [ Group ]            ← rule/constant/manual/extraction slots, UNCHANGED
```

- **No new table, no new column, no Alembic revision.** Coverage rides inside `schema_json`.
- **Backward compatibility (FR-013)**: `coverage` defaults to `[]`; a legacy template with no `coverage` key round-trips unchanged (Pydantic `extra='ignore'` drops unknown keys, but `coverage` being a **declared** field means a present key is preserved and an absent key defaults empty).
- **Critical invariant**: `Section.coverage` **must be a declared Pydantic field**. Because the codebase uses default `extra='ignore'`, an un-declared `coverage` key would be **silently dropped** on `model_validate`, and every declaration would vanish on the first save/load cycle. (Verified: `ast_template.py` defines no `model_config`.)
- **Transient state**: `UnresolvedCandidate` is **not** persisted by this feature — it exists only on the suggest-slots response until the author accepts it (then it becomes a `CoverageBinding` or a `constant`/`manual` slot via the existing create/update endpoints).

---

## Entity: CoverageBinding (discriminated union)

The section-level authoring unit. A discriminated union on `kind`, mirroring the existing `SlotSource` union pattern (`ast_template.py:97-100`).

```python
CoverageBinding = Annotated[
    Union[OntologyRelationBinding, FactSourceBinding],
    Field(discriminator="kind"),
]
```

| Rule | Value |
|------|-------|
| Discriminator | `kind` |
| Members | `OntologyRelationBinding` (`kind='ontology_relation'`), `FactSourceBinding` (`kind='fact_source'`) |
| Extensibility | New sources add a union member + `kind` literal; no consumer signature changes (FR-014) |
| Cannot reference | Any concrete individual/instance IRI (FR-003) — the model has no field that can hold one |

---

## Entity: OntologyRelationBinding

One graph-sourced coverage obligation = (document entity type, relationship, target type, required, promoted properties). This is the vocabulary that structurally prevents sample-anchoring (defect 2).

| Field | Type | Default | Rules / Source |
|-------|------|---------|----------------|
| `kind` | `Literal['ontology_relation']` | — | Discriminator |
| `doc_class_iri` | `str` (IRI) | required | The document entity **type** (domain of the relation). Class IRI only — never an individual (FR-003). |
| `predicate_iri` | `str` (IRI) | required | The relationship. Must be a real edge from `get_relation_schema(doc_class_iri)` when authored via AI (D9); free authoring validated at editor level. |
| `range_class_iri` | `str` (IRI) | required | The target entity **type** (range of the relation). Class IRI only (FR-003). |
| `required` | `bool` | `True` | Relationship-granularity no-omission flag (FR-005/FR-005a, clarification Q1). AI proposes `True`; author may demote. |
| `required_properties` | `list[str]` | `[]` | Per-section promotion of target-type data properties to required (FR-007, D6). Empty ⇒ all expanded properties are informational (FR-006). |
| `label` | `str | None` | `None` | Optional human label for editor display (narrative only; not part of omission identity). |

**Omission identity (FR-011a)**: keyed by the tuple **`(doc_class_iri, predicate_iri, range_class_iri)`**. The same key may appear in multiple sections; it is **one** coverage obligation. `required` and `required_properties` are per-section modifiers on the shared obligation — see the validation contract for precedence when the same key is declared with differing `required` across sections (first-declared counts; a conflicting later declaration is a non-counting reference).

**Validation rules**:
- All three IRI fields must be non-empty strings shaped like class/predicate IRIs (no individual-instance IRI).
- `required_properties` entries are data-property IRIs or local-names of the `range_class_iri`'s type; unknown entries are ignored at expansion (logged), never an error (graceful, FR-012).

---

## Entity: FactSourceBinding (placeholder — FR-014)

Modeled now, provider deferred (Out of Scope). Discriminator-only shape so the union is future-proof.

| Field | Type | Default | Rules / Source |
|-------|------|---------|----------------|
| `kind` | `Literal['fact_source']` | — | Discriminator |
| `source` | `str` | required | Logical fact-source id (e.g., `organization_roles`). Opaque until a provider lands. |
| `selector` | `str | None` | `None` | Query/selector against the fact source; unused this iteration. |
| `label` | `str | None` | `None` | Editor display label. |

**Validation behavior**: emits a single **non-counting `MANUAL`** placeholder position at validation (D11); positions remain human-filled until a provider is implemented. Never contributes to the omission count.

---

## Entity: Unresolved Candidate (transient — FR-008a)

A data-sourced-looking position the AI could **not** bind to any relationship. Lives on the suggest-slots response only; awaits explicit author disposition. **Never** auto-classified as a `manual` slot (this is the structural fix for defect 1 on the authoring side).

| Field | Type | Rules / Source |
|-------|------|----------------|
| `proposed_label` | `str` | The label the AI would have invented (shown to author for context). |
| `evidence` | `str` | Sample snippet / reason the AI thought it was data-sourced. |
| `reason_unbound` | `str` | Why no relationship matched (e.g., "no predicate in doc-class graph matches"). |
| `suggested_disposition` | `Literal['bind','constant','manual','discard'] | None` | Optional AI hint; author decides. |

**State transitions (author disposition)** — all performed by the author, persisted via existing create/update endpoints:

```
UnresolvedCandidate ──bind──────▶ OntologyRelationBinding (added to Section.coverage)
                    ──constant──▶ ConstantSource slot   (existing slot kind, unchanged)
                    ──manual────▶ ManualSource slot      (existing slot kind, unchanged)
                    ──discard───▶ (dropped, not persisted)
```

Until disposed, a candidate contributes **nothing** to a generated report or manifest (it is not a persisted position).

---

## Entity: Relationship Graph (of a document entity type) — read-only, ephemeral

The class-level structure coverage anchors to; produced on demand by the ontology, never stored.

| Source | Shape (per edge, verified `ontology_engine.py:483-578`) |
|--------|--------|
| `get_relation_schema(doc_class_iri, max_hops)` | `{hop, predicate_iri, predicate_label, domain_class_iri, domain_class_label, range_class_iri, range_class_label, range_subclasses:[{iri,label}], range_data_properties:[{iri,label}]}` |
| `get_data_properties_by_domain(doc_class_iri)` | `[{iri, name, label}]` — hop-0 doc-class direct scalars (complements hop≥1 edges) |
| `get_subclasses(range_class_iri, recursive=True)` | `[{iri, label}]` — accepted target subtypes for presence check (D7) |

**Known constraint (D8)**: `get_relation_schema` dedups ranges **globally** (`visited_ranges`) — a given range IRI is emitted once across the BFS. At single-hop authoring this is acceptable; documented so a missing property list under a second predicate to the same range is understood, not treated as a defect.

---

## Entity: Coverage Manifest / SlotCoverage (existing — reused, extended in shape only by tag)

The no-omission proof. **Reused as-is** (`coverage_validator.py:59,74`); this feature emits **additional synthetic positions** into it, tagged for provenance. No new status, no changed `to_dict()`/`summary()` shape (FR-011, D12).

**SlotCoverage — synthetic positions emitted for coverage (new instances, same class)**:

| Field | Relationship position | Property position |
|-------|----------------------|-------------------|
| `slot_id` | `coverage.<pred_short>__<range_short>` | `coverage.<pred_short>__<range_short>__<prop_short>` |
| `status` | `MISSING_REQUIRED` (required & target absent) / `FILLED` (present) | `FILLED` (value) / `BLANK_OPTIONAL` (blank, informational) / `MISSING_REQUIRED` (blank & promoted) |
| `source_kind` | `'ontology_relation'` | `'ontology_relation'` |
| `source_ref` | predicate + range IRI provenance | + data-property IRI |
| counts toward omission? | first-declared key only (D3) | only if promoted (D6) |

**Status semantics (reused constants, FR-011/D12)**:

| Status | Meaning in coverage context |
|--------|------------------------------|
| `FILLED` | Relationship target present / property has a value |
| `BLANK_OPTIONAL` | Property blank under a present relationship → **informational**, no flag (FR-006) |
| `MISSING_REQUIRED` | Required relationship's target entirely absent (FR-005), or a **promoted** property blank (FR-007) |
| `MANUAL` | Non-graph slot (rule/constant/manual) or fact-source placeholder (D11) |
| `DISMISSED` | Per-job dismissed (existing dismissed-flip, `coverage_validator.py:327-332`) — unchanged |

**Manifest-level invariant (FR-011a / D3)**: the omission count in `summary()` counts each `(doc_class_iri, predicate_iri, range_class_iri)` key **once**, no matter how many sections declare it.

---

## Non-graph field (retained slot-level — FR-008)

Unchanged existing slot kinds; explicitly **preserved, not collapsed** into coverage:

| Slot kind | Model (`ast_template.py`) | Preserved because |
|-----------|---------------------------|-------------------|
| `rule` | `RuleSource:60-72` | Risk dimensions come from decision rules, not the entity graph (design note §5). |
| `constant` | `ConstantSource:81-85` | Template boilerplate. |
| `manual` | `ManualSource:75-78` | Genuinely human-filled (e.g., signatures); explicit fallback, **not** a bind-failure dumping ground. |
| `extraction` | `ExtractionSource:38-57` | **Legacy** per-field bindings (FR-013) — resolve unchanged; no longer the authoring unit for new graph-sourced content. |
| `llm_extraction` | `LLMExtractionSource:88-94` | Runtime-derived by `template_expander`; retained for the legacy expansion path (guarded against double-emit, D13). |

---

## Field-level traceability (entity → FR)

| Entity / Field | FR |
|----------------|-----|
| `Section.coverage` | FR-001, FR-009 |
| `OntologyRelationBinding.{doc_class_iri,predicate_iri,range_class_iri}` (types only) | FR-001, FR-003, FR-010 |
| `OntologyRelationBinding.required` (default True) | FR-005, FR-005a |
| `OntologyRelationBinding.required_properties` | FR-007 |
| Relationship→property expansion (validate-time) | FR-004 |
| `BLANK_OPTIONAL` for un-promoted blank props | FR-006 |
| `MISSING_REQUIRED` for absent required relationship | FR-005 |
| Omission identity key `(doc,pred,range)` | FR-011a |
| `UnresolvedCandidate` | FR-008a |
| Retained rule/constant/manual/extraction slots | FR-008, FR-013 |
| `FactSourceBinding` placeholder | FR-014 |
| Read-only ontology queries only | FR-015 |
| Engine-none degradation | FR-012 |
