# Contract: Coverage Schema (AST-1)

**Feature**: 016-section-coverage-binding | **File**: `backend/app/services/reporting/ast_template.py`

Additive-only pydantic contract. Adds the section-level coverage vocabulary and one field on `Section`. **No existing model is removed or its shape changed** (backward compatibility, FR-013). Mirrors the additive-optional `Section.prompt` field precedent (015).

---

## New models

```python
# --- Coverage binding members (discriminated on `kind`) ---

class OntologyRelationBinding(BaseModel):
    kind: Literal["ontology_relation"] = "ontology_relation"
    doc_class_iri: str                       # document entity TYPE (domain). class IRI only (FR-003)
    predicate_iri: str                       # the relationship (real edge of get_relation_schema)
    range_class_iri: str                     # target entity TYPE (range). class IRI only (FR-003)
    required: bool = True                    # FR-005a / clarification Q1 — required by default
    required_properties: list[str] = Field(default_factory=list)  # FR-007 per-section promotion
    label: str | None = None                 # narrative display only

class FactSourceBinding(BaseModel):          # FR-014 placeholder (D11)
    kind: Literal["fact_source"] = "fact_source"
    source: str
    selector: str | None = None
    label: str | None = None

CoverageBinding = Annotated[
    Union[OntologyRelationBinding, FactSourceBinding],
    Field(discriminator="kind"),
]
```

## Modified model (one additive field)

```python
class Section(BaseModel):
    # ... existing fields unchanged (section_id, title, prompt, groups, ...) ...
    coverage: list[CoverageBinding] = Field(default_factory=list)   # NEW — FR-001, FR-009
```

---

## Contract guarantees

| # | Guarantee | Enforces |
|---|-----------|----------|
| C1 | `Section.coverage` defaults to `[]`; a template with no `coverage` key validates and round-trips identically to before. | FR-013 / SC-006 |
| C2 | `coverage` is a **declared** field (NOT relying on `extra`) so a present key survives `model_validate`→`model_dump` (the codebase default is `extra='ignore'`, which would otherwise drop it). | D1, persistence invariant |
| C3 | `OntologyRelationBinding` has **no field** capable of holding an individual/instance IRI; all three IRIs are class/predicate types. | FR-003 |
| C4 | `required` defaults to `True`. | FR-005a |
| C5 | Adding a future binding = new union member + `kind` literal; `CoverageBinding` consumers need no signature change. | FR-014 |
| C6 | `ExtractionSource`, `LLMExtractionSource`, `RuleSource`, `ConstantSource`, `ManualSource`, and the `SlotSource` union are **unchanged**. | FR-008, FR-013 |
| C7 | `ReportTemplate._unique_slot_ids` (`ast_template.py:174`) is **not** extended to inspect `coverage` (coverage IDs are synthesized at validate-time, not persisted slot_ids); no cross-section uniqueness validator is added (would break FR-011a). | FR-011a, D3 |

## Round-trip test (contract-level)

```python
def test_section_coverage_round_trips():
    t = ReportTemplate.model_validate(template_json_with_coverage)
    assert t.sections[0].coverage[0].kind == "ontology_relation"
    assert t.model_dump()["sections"][0]["coverage"][0]["required"] is True

def test_legacy_template_without_coverage_unchanged():
    t = ReportTemplate.model_validate(legacy_json_no_coverage)
    assert t.sections[0].coverage == []          # default, not dropped
    assert t.model_dump() == legacy_json_no_coverage_normalized   # no shape drift
```

**Contract-first note**: these two tests are authored before the field is added and must pass immediately after.
