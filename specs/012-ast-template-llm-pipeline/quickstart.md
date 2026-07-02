# Quickstart: AST Template Management & LLM Pipeline Enhancement

**Date**: 2026-07-01 | **Feature**: 012-ast-template-llm-pipeline

## Prerequisites

- Backend running: `cd backend && uv run uvicorn app.main:app --reload`
- Frontend running: `cd frontend && npm run dev`
- PostgreSQL with migrations applied: `cd backend && uv run alembic upgrade head`
- Ontology loaded (automatic on backend startup)

## Scenario 1: Template Management (Phase 1)

### 1.1 Verify seed data

```bash
curl -s http://localhost:8000/api/ast-templates | python -m json.tool
```

**Expected**: One template returned — "QS-A-020F05 风险评估" v1 with `is_default: true`.

### 1.2 Upload a second template

```bash
# Use a copy of the existing template with modified name for testing
curl -X POST http://localhost:8000/api/ast-templates \
  -H "Content-Type: application/json" \
  -H "X-User: test-analyst" \
  -H "X-Role: senior_analyst" \
  -d '{
    "name": "清洁验证评估",
    "version": "v1",
    "doc_no": "QS-C-001",
    "schema_json": <TEMPLATE_JSON>
  }'
```

**Expected**: `201 Created` with the new template.

### 1.3 Verify template validation

```bash
# Upload with duplicate slot_id — should fail
curl -X POST http://localhost:8000/api/ast-templates \
  -H "Content-Type: application/json" \
  -H "X-User: test-analyst" \
  -H "X-Role: senior_analyst" \
  -d '{
    "name": "Invalid Template",
    "version": "v1",
    "schema_json": {"template_id": "test", "sections": [{"section_id": "s1", "title": "S1", "groups": [{"group_id": "g1", "title": "G1", "kind": "single", "slots": [{"slot_id": "dup", "label": "A", "source": {"kind": "extraction", "object_class_iri": "X", "data_property_iri": "Y", "label": "A"}, "required": true}, {"slot_id": "dup", "label": "B", "source": {"kind": "extraction", "object_class_iri": "X", "data_property_iri": "Z", "label": "B"}, "required": false}]}]}]}
  }'
```

**Expected**: `422` with error message "duplicate slot_id: 'dup'".

### 1.4 Create document type mapping

```bash
curl -X POST http://localhost:8000/api/document-type-mappings \
  -H "Content-Type: application/json" \
  -d '{
    "doc_class_iri_pattern": "CMCReport",
    "template_id": "<DEFAULT_TEMPLATE_UUID>",
    "priority": 0
  }'
```

**Expected**: `201 Created`.

### 1.5 Set default template

```bash
curl -X POST http://localhost:8000/api/ast-templates/<NEW_TEMPLATE_UUID>/set-default
```

**Expected**: `200` with `is_default: true` on the new template.

```bash
curl -s http://localhost:8000/api/ast-templates | python -m json.tool
```

**Expected**: Original template now has `is_default: false`, new template has `is_default: true`.

### 1.6 Delete non-default template

```bash
curl -X DELETE http://localhost:8000/api/ast-templates/<OLD_TEMPLATE_UUID>
```

**Expected**: `204 No Content`. Template list now shows only the new template.

### 1.7 Cannot delete default

```bash
curl -X DELETE http://localhost:8000/api/ast-templates/<NEW_TEMPLATE_UUID>
```

**Expected**: `400` with "Cannot delete the default template".

### 1.8 UI: Template management page

1. Navigate to `/settings/ast-templates`
2. **Verify**: Template list shows all templates with name, version, slot count, default badge
3. Click "上传模板" → upload a valid JSON → verify it appears
4. Click "编辑" on a template → visual slot editor opens → add a slot → save → verify new version created
5. Click "设为默认" → verify badge moves
6. Click "删除" on non-default → verify removed

### 1.9 UI: Template selector on AST page

1. Navigate to an extraction job's AST coverage page
2. **If multiple templates exist**: Verify template selector dropdown is visible
3. Select a different template → verify coverage metrics recalculate
4. **If one template exists**: Verify no selector is shown

## Scenario 2: LLM Gap Filling (Phase 2)

### Prerequisites

- Local LLM running (e.g., `ollama serve` with `qwen2.5:14b` pulled)
- `.env` configured: `LOCAL_LLM_ENABLED=true`, `LOCAL_LLM_BASE_URL=http://localhost:11434/v1`

### 2.1 Zero regression when disabled

```bash
# Ensure LLM is disabled
LOCAL_LLM_ENABLED=false uv run pytest backend/tests/test_reporting/ -v
```

**Expected**: All existing tests pass. No behavior change.

### 2.2 Gap filling in action

1. Upload a CMC document that has known missing required slots (e.g., PDE not in a table)
2. Run extraction job
3. Navigate to AST coverage page

**Expected (LLM disabled)**: Missing required slots shown in red.

4. Enable LLM: set `LOCAL_LLM_ENABLED=true` in `.env`, restart backend
5. Refresh AST coverage page

**Expected (LLM enabled)**: Previously missing slots are now filled. Each filled slot shows:
- A visual badge/icon indicating LLM extraction origin
- Source text snippet visible in slot detail panel (click to expand)
- Audit trail shows `source: "llm"`

### 2.3 LLM unavailable graceful degradation

1. Set `LOCAL_LLM_ENABLED=true` but stop the Ollama server
2. Refresh AST coverage page

**Expected**: Page loads normally. Coverage shows rule-based results only. Backend logs contain WARNING about LLM connection failure. No error shown to user.

## Scenario 3: Ontology-Driven Expansion (Phase 3)

### 3.1 Dynamic slot expansion

1. Ensure ontology has data properties for `DrugProduct` class beyond what the static template covers
2. Enable LLM: `LOCAL_LLM_ENABLED=true`
3. Upload a CMC document and run extraction
4. Navigate to AST coverage page

**Expected**: AST tree view shows:
- Static template groups (normal styling)
- "扩展属性: ..." groups (with "本体属性" label/badge)
- Expanded slots populated from document text where applicable

### 3.2 No duplication

**Expected**: Properties already defined in the static template are NOT duplicated in the expanded groups.

## Verification Checklist

- [ ] Seed template migrated successfully (`alembic upgrade head`)
- [ ] Template CRUD endpoints work (create, list, update, delete, set-default)
- [ ] Template validation rejects invalid schemas
- [ ] Document type mapping resolves correctly (mapping → default → fallback)
- [ ] Template selector appears on AST page when multiple templates exist
- [ ] Template switch recalculates coverage within 3 seconds
- [ ] LLM disabled: zero regression (all existing tests pass)
- [ ] LLM enabled: missing slots filled with source_span
- [ ] LLM unavailable: graceful degradation (warning log, no error)
- [ ] LLM-sourced slots show visual badge in slot detail panel
- [ ] Ontology expansion generates non-duplicate dynamic slots
- [ ] Visual slot editor creates new template version on save
