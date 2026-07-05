# Contract: AI Authoring — Suggest Slots (ontology-grounded)

**Feature**: 016-section-coverage-binding
**Files**: `backend/app/services/extraction/slot_suggester.py`, `backend/app/schemas/extraction.py`, `backend/app/api/ast_templates.py`

Rewires the AI suggester from "invent slots, then fail an exact string match" to "select from the document type's real relationship edges, emit coverage declarations + explicit unresolved candidates." This is the structural fix for defect (1) on the authoring side.

---

## Request schema change (`schemas/extraction.py`)

`SuggestSlotsRequest` (`306-323`) enforces exactly-one-of `{job_id, document_text, sample_content_json}` in `model_post_init`. Add `doc_class_iri` as an **additional optional** field that does **not** participate in that count (D10):

```python
class SuggestSlotsRequest(BaseModel):
    # existing: job_id | document_text | sample_content_json  (exactly one — unchanged validator)
    doc_class_iri: str | None = None          # NEW — optional; outside the exactly-one-of count
```

## Response schema change (`schemas/extraction.py`)

```python
class CoverageDeclaration(BaseModel):         # NEW — mirrors OntologyRelationBinding for transport
    kind: Literal["ontology_relation"] = "ontology_relation"
    doc_class_iri: str
    predicate_iri: str
    range_class_iri: str
    required: bool = True                      # required-by-default (FR-005a / Q1)
    label: str | None = None

class UnresolvedCandidate(BaseModel):          # NEW — FR-008a (transient)
    proposed_label: str
    evidence: str
    reason_unbound: str
    suggested_disposition: Literal["bind","constant","manual","discard"] | None = None

class SuggestSlotsResponse(BaseModel):         # (326-331) extended, additively
    # ... existing fields (sections/slots) ...
    coverage: list[CoverageDeclaration] = Field(default_factory=list)          # NEW
    unresolved_candidates: list[UnresolvedCandidate] = Field(default_factory=list)  # NEW
```

---

## Suggester signature & behavior (`slot_suggester.py`)

```python
def suggest_slots(..., ontology_engine=None, doc_class_iri: str | None = None) -> ...:
```

**Behavior contract**:

1. **Graph injection (D9)** — when `doc_class_iri` and `ontology_engine` are available, inject into the Round-1/Round-2 LLM prompt a **compact** view of:
   - `ontology_engine.get_relation_schema(doc_class_iri)` → `[{predicate_iri, predicate_label, range_class_iri, range_class_label, range_data_properties:[label…]}]` (pruned; property **labels/names**, not full IRIs, to fit local-model context), and
   - `ontology_engine.get_data_properties_by_domain(doc_class_iri)` → hop-0 doc-class scalars.
2. **Selection, not invention** — the model maps each section to **predicate IRIs drawn from the injected edge set**; it MUST NOT emit a `predicate_iri` absent from that set. Graph-sourced coverage is returned as `CoverageDeclaration`s.
3. **Required by default** — every emitted `CoverageDeclaration.required = True` (FR-005a); the author demotes later.
4. **Global dedup (Q3/D3)** — the suggester de-duplicates its own emitted declarations by `(doc_class_iri, predicate_iri, range_class_iri)` so it never proposes the same obligation twice.
5. **Unresolved candidates (FR-008a)** — a data-sourced-looking position with no matching edge is emitted as an `UnresolvedCandidate`, **never** as a `manual` slot.
6. **Delete `_bind_ontology_iris`** (`239-273`) and its call (`206-212`) — the `label in dp_labels` exact match is removed entirely (root cause of defect 1).
7. **Non-graph slots preserved** — `rule`/`constant`/`manual` suggestions continue through the existing path unchanged (FR-008).
8. **No-graph degradation** — `doc_class_iri`/engine unavailable ⇒ suggester still returns sections; `coverage` may be empty; nothing is force-classified to `manual` (FR-012).

## Endpoint wiring (`api/ast_templates.py`)

`suggest_slots_endpoint` (`534-596`, `Depends(get_ontology_engine)` at `539`, reads annotation cache at `570-576`):
- Recover `doc_class_iri` from (a) request field if present, else (b) the job's annotation cache `result['doc_class']['doc_class_iri']` (`api/extraction.py:390-402`) when `job_id` given (D10).
- Pass both `ontology_engine` and `doc_class_iri` into `suggest_slots`.

---

## Contract guarantees

| # | Guarantee | Enforces |
|---|-----------|----------|
| S1 | Graph-sourced sections return `CoverageDeclaration`s; **zero** graph-sourced positions default to `manual`. | FR-002 / SC-001 |
| S2 | Every emitted `predicate_iri`/`range_class_iri` is a class/predicate IRI from the injected schema — **never** a sample individual. | FR-003 / SC-002 |
| S3 | `_bind_ontology_iris` and its exact-match are removed; no code path re-introduces post-hoc label matching. | Defect 1 root cause |
| S4 | Unbindable data-looking positions ⇒ `unresolved_candidates`, never silent `manual`. | FR-008a |
| S5 | `CoverageDeclaration.required` is `True` by default. | FR-005a |
| S6 | `doc_class_iri` is optional and does not break the existing exactly-one-of request validator. | D10 |
| S7 | Suggester's own output deduped by `(doc,pred,range)`. | FR-011a / D3 |
| S8 | Engine access read-only; no ontology write. | FR-015 / II |

## Test matrix → guarantee (`test_extraction/test_slot_suggester.py`)

| Test | Proves |
|------|--------|
| `test_graph_sourced_section_emits_coverage_not_manual` | S1 |
| `test_declarations_reference_types_never_individuals` | S2 |
| `test_unbindable_position_becomes_unresolved_candidate` | S4 |
| `test_declaration_required_true_by_default` | S5 |
| `test_doc_class_iri_optional_request_still_valid` | S6 |
| (replaces) `TestOntologyIRIBinding::test_no_match_sets_llm_extraction` | S3 (old exact-match path deleted) |

> The existing `TestOntologyIRIBinding` suite asserting the `llm_extraction` fallback is **rewritten** — that behavior is the defect being removed.
