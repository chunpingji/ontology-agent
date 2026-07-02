# Data Model: AST Template Management & LLM Pipeline Enhancement

**Date**: 2026-07-01 | **Feature**: 012-ast-template-llm-pipeline

## 1. New Entities

### 1.1 AstTemplate

Stores report template definitions with versioning. Each row is an immutable version snapshot.

| Field | Type | Constraints | Description |
|-------|------|-------------|-------------|
| `id` | UUID | PK, auto-generated | Unique identifier |
| `name` | String(200) | NOT NULL | Human-readable template name (e.g., "QS-A-020F05 风险评估") |
| `version` | String(20) | NOT NULL | Version string (e.g., "v1", "v2") |
| `doc_no` | String(50) | nullable | Document number (e.g., "QS-A-020F05") |
| `schema_json` | JSON | NOT NULL | Complete template schema (sections/groups/slots) validated by `ReportTemplate.model_validate()` |
| `is_default` | Boolean | default=False | Only one template may be default at a time |
| `created_by` | String(100) | nullable | User who created this version |
| `created_at` | DateTime(tz) | auto | Creation timestamp |
| `updated_at` | DateTime(tz) | auto on update | Last update timestamp |

**Unique constraint**: `(name, version)` — prevents duplicate name+version combinations.

**Invariant**: At most one row has `is_default=True` at any time. Setting a new default must unset the previous one atomically.

### 1.2 DocumentTypeMapping

Maps document classification identifiers to templates for automatic resolution.

| Field | Type | Constraints | Description |
|-------|------|-------------|-------------|
| `id` | UUID | PK, auto-generated | Unique identifier |
| `doc_class_iri_pattern` | String(500) | NOT NULL | Pattern to match against document class IRI (e.g., "CMCReport", "StabilityReport") |
| `template_id` | UUID | FK → `ast_templates.id`, NOT NULL | Target template |
| `priority` | Integer | default=0 | Higher priority wins when multiple patterns match |
| `created_at` | DateTime(tz) | auto | Creation timestamp |

**Relationship**: Many-to-one → `AstTemplate`.

## 2. Extended Entities

### 2.1 SlotSource Union — New Variant

The existing `SlotSource` discriminated union in `ast_template.py` gains a new variant:

| Variant | `kind` | New Fields | Description |
|---------|--------|------------|-------------|
| `LLMExtractionSource` | `"llm_extraction"` | `object_class_iri: str`, `data_property_iri: str`, `label: str` | Marks a slot as populated by LLM extraction from ontology-driven properties |

Existing variants (`ExtractionSource`, `RuleSource`, `ManualSource`, `ConstantSource`) are unchanged.

### 2.2 SlotCoverage — Extended Fields

The existing `SlotCoverage` dataclass in `coverage_validator.py` gains:

| Field | Type | Description |
|-------|------|-------------|
| `source_span` | `str \| None` | Original text snippet from the document (populated by LLM gap filling for traceability) |

### 2.3 Extraction Edges — Source Attribution

Edges (dict format) gain a `source` field:

| Value | Meaning |
|-------|---------|
| `"rule"` | Extracted by existing rule-based finders (default, backward-compatible) |
| `"llm"` | Extracted by LLM gap filling or ontology-driven extraction |

Edges with `source: "llm"` also carry `source_span: str` for audit traceability.

### 2.4 Settings — New Configuration Fields

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `local_llm_enabled` | bool | `False` | System-wide toggle for LLM gap filling |
| `local_llm_base_url` | str | `"http://localhost:11434/v1"` | OpenAI-compatible API endpoint |
| `local_llm_model` | str | `"qwen2.5:14b"` | Model identifier |
| `local_llm_api_key` | str | `"not-needed"` | API key (local deployments typically don't require one) |
| `local_llm_max_tokens` | int | `4096` | Maximum tokens for LLM responses |
| `local_llm_temperature` | float | `0.1` | Low temperature for deterministic extraction |

## 3. Entity Relationships

```
AstTemplate (1) ←── (N) DocumentTypeMapping
    │
    │ schema_json validated as
    ▼
ReportTemplate (Pydantic model, in-memory)
    │
    │ iter_slots() → Slot → SlotSource (discriminated union)
    │                         ├── ExtractionSource (existing)
    │                         ├── RuleSource (existing)
    │                         ├── ManualSource (existing)
    │                         ├── ConstantSource (existing)
    │                         └── LLMExtractionSource (NEW)
    │
    │ validate_coverage()
    ▼
CoverageManifest
    │ slots: [SlotCoverage]
    │         └── source_span (NEW, nullable)
    │
    │ to_dict() → snapshot in GeneratedReport.rules_summary (existing)
    ▼
GeneratedReport (existing, unchanged)
```

## 4. State Transitions

### 4.1 Template Lifecycle

```
[Created] ──upload/editor──→ [Active]
                                │
                    ┌───────────┼───────────┐
                    │           │           │
              set-default   update     delete
                    │           │           │
                    ▼           ▼           ▼
              [Default]   [New Version]  [Deleted]
                                │       (if not default)
                                ▼
                          [Active] (new version)
```

- **Created → Active**: Template uploaded or created via visual editor; validated by `ReportTemplate.model_validate()`.
- **Active → Default**: `POST /set-default` atomically unsets previous default.
- **Active → New Version**: Update via re-upload or visual editor creates a new row; old version remains Active.
- **Active → Deleted**: Only if not the current default.

### 4.2 Gap-Filling Flow (per evaluation)

```
[Rule Extraction Complete]
    │
    ├── local_llm_enabled == False → [Done: Rule-only Manifest]
    │
    ├── local_llm_enabled == True
    │       │
    │       ▼
    │   [Ontology Expansion] (Mode B, if applicable)
    │       │ → expanded_template (runtime, not persisted)
    │       │ → LLM extracts from narrative paragraphs
    │       │ → edges (source="llm") merged
    │       ▼
    │   [First Coverage Validation]
    │       │
    │       ├── missing_required == 0 → [Done: Full Manifest]
    │       │
    │       ├── missing_required > 0
    │       │       │
    │       │       ▼
    │       │   [Gap Filling] (Mode A)
    │       │       │ → LLM targets specific missing slots
    │       │       │ → edges (source="llm") merged
    │       │       ▼
    │       │   [Final Coverage Validation]
    │       │       │
    │       │       ▼
    │       │   [Done: Final Manifest]
```

## 5. Migration Strategy

Alembic migration `xxx_add_ast_templates.py`:

1. Create `ast_templates` table with all fields and unique constraint.
2. Create `document_type_mappings` table with FK to `ast_templates`.
3. Seed default template:
   - Read `qs_a_020f05.json` from `backend/app/services/reporting/templates/`.
   - Insert as `AstTemplate(name="QS-A-020F05 风险评估", version="v1", doc_no="QS-A-020F05", schema_json=<contents>, is_default=True)`.
4. Seed default mapping:
   - Insert `DocumentTypeMapping(doc_class_iri_pattern="CMCReport", template_id=<seeded template id>, priority=0)`.

**Rollback**: Standard Alembic downgrade drops both tables (seed data is recreatable).
