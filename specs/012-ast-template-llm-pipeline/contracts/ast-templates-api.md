# API Contract: AST Template Management

**Date**: 2026-07-01 | **Feature**: 012-ast-template-llm-pipeline

All endpoints use JSON request/response bodies. Authentication via existing gateway headers (`X-User`/`X-Role`). Write operations require `senior_analyst` role.

## 1. Template CRUD

### GET /api/ast-templates

List all templates.

**Response** `200`:
```json
[
  {
    "id": "uuid",
    "name": "QS-A-020F05 风险评估",
    "version": "v1",
    "doc_no": "QS-A-020F05",
    "slot_count": 18,
    "is_default": true,
    "created_by": "analyst1",
    "created_at": "2026-07-01T00:00:00Z",
    "updated_at": "2026-07-01T00:00:00Z"
  }
]
```

### POST /api/ast-templates

Upload a new template.

**Request**:
```json
{
  "name": "稳定性评估",
  "version": "v1",
  "doc_no": "QS-B-010",
  "schema_json": { /* ReportTemplate JSON */ }
}
```

**Validation**: `schema_json` is validated via `ReportTemplate.model_validate()`. Checks:
- All `slot_id` values are unique across the template
- All `source.kind` values are valid discriminator values
- `required` and `on_missing` fields have consistent logic

**Response** `201`:
```json
{
  "id": "uuid",
  "name": "稳定性评估",
  "version": "v1",
  "doc_no": "QS-B-010",
  "slot_count": 12,
  "is_default": false,
  "created_by": "analyst1",
  "created_at": "2026-07-01T00:00:00Z",
  "updated_at": null
}
```

**Error** `422`:
```json
{
  "detail": "Template validation failed: duplicate slot_id: 'subject.pde'"
}
```

**Error** `409`:
```json
{
  "detail": "Template 'name=稳定性评估, version=v1' already exists"
}
```

### PUT /api/ast-templates/{id}

Update a template. Creates a new version; the original version is preserved.

**Request**:
```json
{
  "schema_json": { /* updated ReportTemplate JSON */ },
  "version": "v2"
}
```

`version` is optional — if omitted, auto-increments from the current version (e.g., "v1" → "v2").

**Response** `201`: New template version (same shape as POST response).

**Error** `404`: Template not found.

### DELETE /api/ast-templates/{id}

Delete a template.

**Response** `204`: No content.

**Error** `400`:
```json
{
  "detail": "Cannot delete the default template"
}
```

**Error** `404`: Template not found.

### POST /api/ast-templates/{id}/set-default

Set a template as the default. Atomically unsets the previous default.

**Response** `200`:
```json
{
  "id": "uuid",
  "name": "稳定性评估",
  "version": "v1",
  "is_default": true
}
```

**Error** `404`: Template not found.

### GET /api/ast-templates/match/{job_id}

Resolve the template for a specific job based on its document class IRI.

**Response** `200`:
```json
{
  "template_id": "uuid",
  "template_name": "QS-A-020F05 风险评估",
  "template_version": "v1",
  "match_source": "mapping" | "default" | "fallback"
}
```

`match_source` indicates how the template was resolved:
- `"mapping"`: Matched via `DocumentTypeMapping`
- `"default"`: No mapping matched, used `is_default=True` template
- `"fallback"`: No DB templates available, used built-in JSON file

## 2. Document Type Mappings

### GET /api/document-type-mappings

List all mappings.

**Response** `200`:
```json
[
  {
    "id": "uuid",
    "doc_class_iri_pattern": "CMCReport",
    "template_id": "uuid",
    "template_name": "QS-A-020F05 风险评估",
    "template_version": "v1",
    "priority": 0,
    "created_at": "2026-07-01T00:00:00Z"
  }
]
```

### POST /api/document-type-mappings

Create a new mapping.

**Request**:
```json
{
  "doc_class_iri_pattern": "StabilityReport",
  "template_id": "uuid",
  "priority": 0
}
```

**Response** `201`: Created mapping (same shape as GET item).

**Error** `404`: Template not found.

### DELETE /api/document-type-mappings/{id}

Delete a mapping.

**Response** `204`: No content.

**Error** `404`: Mapping not found.

## 3. Extended AST Coverage Endpoint

### GET /api/extraction/jobs/{job_id}/ast-coverage?template_id={uuid}

Existing endpoint extended with optional `template_id` query parameter.

**Behavior**:
- If `template_id` provided: Use that template for coverage calculation.
- If `template_id` omitted: Resolve template via `resolve_template()` (mapping → default → fallback).
- If `local_llm_enabled=True` and missing required slots exist: Automatically run gap filling before returning.

**Response** `200`: Same `ASTCoverageResponse` shape as current, with additions:

```json
{
  "template_id": "qs-a-020f05",
  "template_name": "QS-A-020F05 风险评估",
  "template_version": "v1",
  "total_slots": 18,
  "filled": 15,
  "inferred": 1,
  "missing_required": 2,
  "blank_optional": 0,
  "manual": 0,
  "dismissed": 0,
  "sections": [
    {
      "section_id": "section_1",
      "title": "SECTION I 风险评估",
      "groups": [
        {
          "group_id": "subject",
          "title": "1. 风险评估对象",
          "kind": "single",
          "is_dynamic": false,
          "slots": [
            {
              "slot_id": "subject.product_name",
              "label": "品名",
              "status": "filled",
              "source_kind": "extraction",
              "value": "注射用头孢呋辛钠",
              "source_ref": "DrugProduct",
              "source_span": null,
              "is_llm_sourced": false
            },
            {
              "slot_id": "subject.pde",
              "label": "PDE",
              "status": "filled",
              "source_kind": "extraction",
              "value": "1.80",
              "source_ref": "DrugProduct",
              "source_span": "HRS-1234 的 PDE 为 1.80mg",
              "is_llm_sourced": true
            }
          ]
        },
        {
          "group_id": "ontology_drug_product",
          "title": "产品扩展属性",
          "kind": "single",
          "is_dynamic": true,
          "slots": []
        }
      ]
    }
  ]
}
```

New fields in `SlotCoverageResponse`:
- `source_span: string | null` — Original text snippet (populated for LLM-sourced values)
- `is_llm_sourced: boolean` — Whether this value was extracted by LLM (for UI badge display)

New fields in `GroupCoverageResponse`:
- `is_dynamic: boolean` — Whether this group was generated by ontology expansion

New fields in top-level response:
- `template_name: string` — Human-readable template name
- `template_version: string` — Template version string
