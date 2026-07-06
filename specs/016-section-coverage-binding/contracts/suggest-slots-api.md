# Contract: AI Authoring — Suggest Slots (ontology-grounded)

**Feature**: 016-section-coverage-binding
**Files**: `backend/app/services/extraction/slot_suggester.py`, `backend/app/schemas/extraction.py`, `backend/app/api/ast_templates.py`

Rewires the AI suggester from "invent slots, then fail an exact string match" to "select from the document type's real relationship edges, emit coverage declarations + explicit unresolved candidates." This is the structural fix for defect (1) on the authoring side.

**Convergence (final)**: the suggester's output is now **pure 016** — `{document_summary, coverage, unresolved_candidates}`. The legacy per-field slot suggestion stream (`sections`/`slots`/`total_suggested`/`skipped_duplicates`/`truncated` and the per-slot `source_kind="llm_extraction"` tagging) is **fully removed**, not merely neutralized. Already-persisted template slots are untouched — this contract governs only what AI analysis *emits*.

> **⚠️ SUPERSEDED (禁止原文取数候选, post-016)** — the `unresolved_candidates` stream is **removed entirely**, superseding **FR-008a / S4**. The wire contract is now the **two-key** dict `{document_summary, coverage}` — the `UnresolvedCandidate` model, `_extract_unresolved`, and the round-2 `unresolved_candidates` schema property are deleted. Positions that bind to **no** menu edge are **silently ignored** (not surfaced, never `manual`). Everywhere below reads `{document_summary, coverage, unresolved_candidates}` → **`{document_summary, coverage}`**; treat every `unresolved_candidates` / `UnresolvedCandidate` / FR-008a / S4 mention as historical. Two structural pieces of this contract still stand and are **strengthened**: (a) **selection-not-invention** ontology grounding, now widened by the **D8 supplemental edges** (`relation_extractor.supplemental_relation_edges` re-attaches CMCReport's 3 broad-domain object props — 使用设备/存放条件/含降解途径 — so the AI menu matches the extraction pipeline); (b) grounding requires a `doc_class_iri`, which the editor now drives **live** from the "关联文档类型" Select and blocks AI analysis until set.

> **➕ AMENDED (恢复结构骨架, Option A / post-`unresolved_candidates`)** — bug report「AI 分析 …Section 结构为何全丢了？」root cause: the LLM **Round-1 already computes the full document skeleton** (`_ROUND1_SCHEMA` forces `sections[].groups[].candidates[]{label, evidence_span?, evidence_offset?}`), but 016 convergence **discarded** it at the return. Fix: `suggest_slots` now returns it **verbatim** — the final wire contract is the **three-key** dict **`{document_summary, coverage, sections}`**. `sections` is the Round-1 structural skeleton returned **as-is**, with **zero ontology IRI binding**; the editor materializes each candidate into an author-fillable **`semantic`** slot (`{kind:"semantic", prompt:null, coverage_refs:[]}`), while ontology coverage keeps overlaying via `coverage`. **Critical distinction**: what defect 1 removed is the `_bind_ontology_iris` exact-match → force-collapse-to-`manual` **binding stream**, **not** the structural skeleton — the skeleton was innocent collateral and returns as **inert scaffolding** (no auto-IRI-binding revived). The skeleton materializes into the editor **only when the template has 0 sections** (mirrors `ensureSeedSection`'s gate), never clobbering author edits, never stacking on re-run. S3 is revised below; **S11/S12** are added. Read the two-key `{document_summary, coverage}` above as **`{document_summary, coverage, sections}`**.

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

class SuggestSlotsResponse(BaseModel):         # 016 output (see top AMENDED note): summary + coverage + skeleton
    document_summary: str
    coverage: list[CoverageDeclaration] = Field(default_factory=list)
    # Round-1 structural skeleton, returned VERBATIM (shape = _ROUND1_SCHEMA). No ontology IRI
    # binding — the editor materializes each candidate into an author-fillable `semantic` slot.
    sections: list[dict] = Field(default_factory=list)
    # unresolved_candidates: DELETED (see top SUPERSEDED note)
```

> The `unresolved_candidates` field / `UnresolvedCandidate` model above are **deleted** (top SUPERSEDED note). The former per-slot `slots`/`total_suggested`/`skipped_duplicates`/`truncated` fields and the `SuggestedSlot`/`SuggestedGroup`/`SuggestedSection` models stay deleted — `sections` is **not** their revival; it is the inert Round-1 skeleton (S11). The endpoint returns the raw `suggest_slots()` dict (no `response_model`), so this three-key dict shape **is** the wire contract. Shape authority for `sections` is `slot_suggester._ROUND1_SCHEMA`.

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
6. **Delete the whole slot post-processing pipeline** — not only `_bind_ontology_iris` (the `label in dp_labels` exact match, root cause of defect 1) but the entire per-slot stream that fed it: `_group_into_sections`, `derive_source_ref`, `_collect_blocks`, and the `source_kind="llm_extraction"` tagging are removed. **Kept**: `tiptap_to_text`/`_node_text`/`build_document_text` (the endpoint still serializes tiptap → LLM text) and the 016 extractors `_extract_coverage`/`_extract_unresolved`/`_build_ontology_context`.
7. **Suggester emits no slots at all** — including `rule`/`constant`/`manual`. Preservation of non-graph fields (FR-008) is now an **editor** guarantee over *already-persisted* slots (see frontend-authoring.md FE3), not a suggester output path.
8. **No-graph / degradation** — `doc_class_iri` or engine unavailable ⇒ the suggester returns `{document_summary, coverage: [], sections}` with **empty coverage**, raises no error, and force-classifies nothing to `manual` (FR-012). `sections` still carries the Round-1 skeleton — it is grounding-independent pure document structure (S11), `[]` only when Round-1 itself fails or the document is empty.

## Endpoint wiring (`api/ast_templates.py`)

`suggest_slots_endpoint` (`534-596`, `Depends(get_ontology_engine)` at `539`, reads annotation cache at `570-576`):
- Recover `doc_class_iri` from (a) request field if present, else (b) the job's annotation cache `result['doc_class']['doc_class_iri']` (`api/extraction.py:390-402`) when `job_id` given (D10).
- Pass both `ontology_engine` and `doc_class_iri` into `suggest_slots`.

---

## Capability probe — `POST /coverage-doc-classes` (bugfix: 仅启用已建模类型)

**Defect A (本体覆盖面)**: of the ~30 selectable `DOCUMENT_TYPE_GROUPS`, **only `CMCReport`** has ontology object properties (11 hop-1 edges + 3 D8 broad-domain supplements). Every other document-classification class returns **0** edges from `get_relation_schema` (they carry no `rdfs:domain` object properties), so AI coverage is **structurally always empty** for them — the authoring UI must not present them as if AI analysis will produce sections. **Decision (仅启用已建模类型)**: the editor enables only document types the ontology has actually modeled, computed **live** (auto-widens as the T-Box grows — no code change to add a type).

**Shared source of truth** (`slot_suggester.py`): the AI menu and the capability probe MUST agree — "selectable" ⇔ "AI can emit coverage". Both route through one helper:

```python
def _supplemented_schema_edges(engine, doc_class_iri) -> list[dict]:
    """get_relation_schema(doc_class_iri) + D8 supplemental_relation_edges, deduped.
    engine None / no iri / engine error → [] (graceful degradation, FR-012). Read-only."""

def coverage_capable(engine, doc_class_iri) -> bool:
    return any(e.get("hop") == 1 for e in _supplemented_schema_edges(engine, doc_class_iri))

def _build_ontology_context(engine, doc_class_iri) -> tuple[list[dict], str]:
    # now calls _supplemented_schema_edges; (edges, prompt) contract & "no hop-1 → empty prompt" unchanged
```

**Endpoint** (`api/ast_templates.py`, `Depends(_maintainer)` + `Depends(get_ontology_engine)`, same auth surface as suggest-slots):

```python
class CoverageDocClassesRequest(BaseModel):  doc_class_iris: list[str] = []
class CoverageDocClassesResponse(BaseModel): capable: list[str] = []

@router.post("/coverage-doc-classes", response_model=CoverageDocClassesResponse)
def coverage_doc_classes(req, identity=Depends(_maintainer), engine=Depends(get_ontology_engine)):
    return {"capable": [iri for iri in req.doc_class_iris if coverage_capable(engine, iri)]}
```

The frontend's full candidate IRI list is the **only** input (no server-side type registry) — avoids front/back list drift. Response is the modeled subset (today `[…CMCReport]`).

---

## Contract guarantees

| # | Guarantee | Enforces |
|---|-----------|----------|
| S1 | Graph-sourced sections return `CoverageDeclaration`s; **zero** graph-sourced positions default to `manual`. | FR-002 / SC-001 |
| S2 | Every emitted `predicate_iri`/`range_class_iri` is a class/predicate IRI from the injected schema — **never** a sample individual. | FR-003 / SC-002 |
| S3 | The **IRI-binding / auto-`manual`-collapse** stream (`_bind_ontology_iris` exact-match + `_group_into_sections`/`derive_source_ref`/`_collect_blocks` + `llm_extraction` tagging) stays removed; no path re-introduces post-hoc label matching. The response carries no `slots`/`total_suggested`/`skipped_duplicates`/`truncated`. **`sections` is exempt** — it returns as the inert Round-1 skeleton (S11), *not* the old slot stream. | Defect 1 root cause / convergence |
| S4 | Unbindable data-looking positions ⇒ `unresolved_candidates`, never silent `manual`. | FR-008a |
| S5 | `CoverageDeclaration.required` is `True` by default. | FR-005a |
| S6 | `doc_class_iri` is optional and does not break the existing exactly-one-of request validator. | D10 |
| S7 | Suggester's own output deduped by `(doc,pred,range)`. | FR-011a / D3 |
| S8 | Engine access read-only; no ontology write. | FR-015 / II |
| S9 | `coverage_capable` ⇔ the AI menu (`_build_ontology_context`) is non-empty — both consume `_supplemented_schema_edges`, so "UI enables it" ⇔ "AI can emit coverage" (no divergence). | 仅启用已建模类型 |
| S10 | `/coverage-doc-classes` returns only the input IRIs that are modeled (today `[…CMCReport]`); engine hiccup ⇒ each probe degrades to `False` (endpoint never 500s on capability). | Defect A / VI |
| S11 | `sections` is the Round-1 structural skeleton returned **verbatim** from `r1["sections"]` (`_ROUND1_SCHEMA` shape) — **zero** ontology IRI binding, **zero** `manual` collapse; `[]` when Round-1 fails or the document is empty. | Option A / defect-1 skeleton-innocent |
| S12 | The editor materializes the skeleton into sections + `semantic` slots **only when the template has 0 sections** (mirrors `ensureSeedSection`); never clobbers author edits, a re-run never stacks. | Option A / non-destructive |

## Test matrix → guarantee (`test_extraction/test_slot_suggester.py`)

| Test | Proves |
|------|--------|
| `TestBasic::test_two_round_flow_returns_pure_coverage_shape` | response keys == `{document_summary, coverage, sections}` |
| `TestBasic::test_no_legacy_slot_stream_keys` | S3 (no `total_suggested`/`skipped_duplicates`/`truncated`; `sections` **present**) |
| `TestBasic::test_skeleton_returned_verbatim_from_round1` | S11 (`sections == r1["sections"]`, no IRI binding) |
| `TestBasic::test_skeleton_empty_when_round1_fails` / `test_empty_document_returns_empty_skeleton` | S11 (`sections == []` on Round-1 fail / empty doc) |
| `TestBasic::test_empty_document_returns_empty` / `test_round1_failure_returns_empty` | graceful degradation (empty coverage, no error) |
| `TestOntologyCoverage::test_graph_sourced_section_emits_coverage` | S1 |
| `TestOntologyCoverage::test_declarations_reference_types_never_individuals` | S2 |
| `TestOntologyCoverage::test_unbindable_position_is_silently_ignored` | silent-ignore (supersedes S4) |
| `TestOntologyCoverage::test_declaration_required_true_by_default` | S5 |
| `TestOntologyCoverage::test_doc_class_iri_optional_request_still_valid` | S6 |
| `TestCoverageCapable::test_modeled_type_is_capable` (CMCReport → True) / `test_unmodeled_type_not_capable` (StabilityReport → False) | S9 |
| `TestCoverageCapable::test_build_ontology_context_contract_unchanged` | S9 (regression: `(edges, prompt)` + no-hop1→empty) |
| `test_suggest_slots_api.py::test_coverage_doc_classes_returns_modeled_subset` | S10 |
| `TestTiptapToText::*` | `tiptap_to_text` serialization retained (endpoint dependency) |

> The former `TestOntologyIRIBinding` suite asserting the `llm_extraction` fallback, plus `TestDeriveSourceRef`/`TestContentJsonSourceRef` (per-slot `source_ref` binding), are **deleted** — that behavior is the defect being removed and no longer exists.
