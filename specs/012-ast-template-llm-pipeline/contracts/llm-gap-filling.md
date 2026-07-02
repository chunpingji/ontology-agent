# Internal Contract: LLM Gap Filling

**Date**: 2026-07-01 | **Feature**: 012-ast-template-llm-pipeline

This contract defines the internal interfaces for LLM-based extraction gap filling and ontology-driven template expansion. These are service-layer interfaces, not public API endpoints.

## 1. LLM Client

### `get_local_llm() → OpenAI | None`

**Location**: `backend/app/services/llm/local_client.py`

Returns an OpenAI-compatible client configured with `local_llm_base_url` and `local_llm_api_key`. Returns `None` when `local_llm_enabled` is `False`.

**Failure mode**: If the LLM endpoint is unreachable, the client's `.chat.completions.create()` raises a connection error. Callers must catch and degrade gracefully.

## 2. Gap Filler (Mode A)

### `fill_coverage_gaps(manifest, document_sections, template) → list[dict]`

**Location**: `backend/app/services/extraction/llm_gap_filler.py`

**Parameters**:
- `manifest: CoverageManifest` — First-pass coverage result with `missing_required_slots`
- `document_sections: list[dict]` — Document content split by section: `[{"title": str, "text": str}]`
- `template: ReportTemplate` — The template used for coverage validation

**Returns**: List of supplemental edges (same dict format as rule-based edges), each with:
```python
{
    "subject_text": str,           # Entity mention
    "object_class_iri": str,       # Ontology class IRI
    "object_text": str,            # Extracted value
    "object_data_properties": [    # Data properties
        {"label": str, "value": str}
    ],
    "source": "llm",               # Source attribution
    "source_span": str,            # Original text snippet
}
```

**Behavior**:
- Only targets `missing_required_slots` from the manifest
- Constructs a structured prompt per R5 (research.md)
- Parses LLM JSON response; validates against expected slot types
- Returns empty list if LLM is unavailable, returns invalid JSON, or finds nothing
- Never raises — all failures are caught, logged as WARNING, and return `[]`

**Prompt template**:
```
你是一个药品 CMC 文档信息抽取助手。

以下是一份文档的原文内容（已分章节）：
{document_sections}

请从文档中提取以下缺失的信息项。每个信息项的定义如下：
{missing_slots_schema}

返回 JSON 数组，每个元素包含：
- slot_id: 槽位标识
- extracted_value: 提取的值
- source_span: 原文中的来源片段（用于溯源）

如果文档中确实不包含某项信息，该项返回 null。
仅返回 JSON，不要附加其他文字。
```

## 3. Template Expander (Mode B)

### `expand_template_with_ontology(template, doc_class_iri, engine) → ReportTemplate`

**Location**: `backend/app/services/reporting/template_expander.py`

**Parameters**:
- `template: ReportTemplate` — Static template from DB/file
- `doc_class_iri: str` — Document class IRI for ontology lookup
- `engine: OntologyEngine` — Ontology engine instance (must be loaded)

**Returns**: New `ReportTemplate` instance with additional slots from ontology data properties. The original template is not modified.

**Behavior**:
1. From `doc_class_iri`, traverse object properties to find range classes
2. For each range class, call `engine.get_data_properties_by_domain(range_class_iri)`
3. Filter out properties already declared in the static template (match by IRI)
4. For each new property, create a `Slot` with `source=LLMExtractionSource(kind="llm_extraction", ...)`
5. Group new slots under a new `Group(group_id="ontology_{class_name}", title="扩展属性: {class_label}", kind="single")`
6. Append new groups to the appropriate section (or a new "本体扩展" section)

**Invariants**:
- Never modifies the input template
- Never duplicates existing slots (dedup by property IRI)
- If ontology engine is not loaded or doc class not found, returns the original template unchanged

## 4. Coverage Validator Extension

### `_resolve_llm_extraction(slot, edges) → SlotCoverage`

**Location**: `backend/app/services/reporting/coverage_validator.py` (new function)

Mirrors `_resolve_extraction()` but matches edges with `source: "llm"` and populates `SlotCoverage.source_span` from the edge's `source_span` field. The resolution logic is identical — match by `object_class_iri` pattern.

## 5. Integration Point

### `_build_ast_coverage_response` Extension

**Location**: `backend/app/api/extraction.py`

Extended behavior:
1. Accept optional `template_id: UUID | None` query parameter
2. Resolve template via `resolve_template(doc_class_iri, db)` or by direct lookup if `template_id` provided
3. If `local_llm_enabled` and ontology expansion applicable: expand template via `expand_template_with_ontology()`
4. Run standard coverage validation (first pass)
5. If `local_llm_enabled` and `manifest.missing_required > 0`: call `fill_coverage_gaps()`
6. Merge gap-filled edges and re-run coverage validation (second pass)
7. Build response with `is_llm_sourced` and `source_span` fields populated
