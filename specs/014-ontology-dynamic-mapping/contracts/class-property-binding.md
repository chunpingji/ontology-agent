# Contract: Class & Property Binding API (E6 / E6b)

**Feature**: 014-ontology-dynamic-mapping · **Base router**: `app/api/ontology.py` (existing mapping routes extended)

Authorization: **`senior_analyst`** for all mutating routes (create/update/delete); `operator`/`qa` read-only (GET). Identity via `X-User`/`X-Role` gateway headers (existing `identity` dependency). All mutations write an audit record (actor, action, entity, timestamp) and bump `version`.

Conventions: `{iri:path}` is a URL-encoded class IRI. Optimistic concurrency via `expected_version` on delete/update (existing pattern). Credentials are never accepted or returned — only `source_system` refs.

---

## Class bindings (E6 — extends existing `/mappings`)

### `GET /ontology/classes/{iri:path}/mappings`
List all class bindings for a class (existing route; now returns source-entity types too).

**200** → `[Mapping]` where `Mapping` gains source-entity `mapping_type` values:
```json
{
  "id": "uuid",
  "class_iri": "…/DrugProduct",
  "mapping_type": "db_table",            // db_table | api_endpoint | doc_pattern | slpra_iri | bfo | source_field
  "target": "drug_product",              // table name | endpoint path | doc pattern
  "source_system": "DRUG_DB_DSN",        // env-var name OR connector id — never a credential
  "health": "ok",                        // ok | unmapped | drift | orphan
  "version": 1,
  "status": "draft"
}
```

### `POST /ontology/classes/{iri:path}/mappings`
Create a class binding. **Role**: senior_analyst.

**Request** (`MappingCreate`, extended):
```json
{ "mapping_type": "db_table", "target": "drug_product", "source_system": "DRUG_DB_DSN" }
```
**Validation**:
- `mapping_type` ∈ allow-list; source-entity types require non-empty `target` (C2).
- `(class, source_system)` unique for source-entity types → **409 Conflict** on duplicate (C1/FR-024).
- `source_system` must be an env-var name or connector id, not a DSN string (C3) → **422** otherwise.

**Responses**: **201** → `Mapping` · **403** wrong role · **409** duplicate (class,source) · **422** validation.

### `PUT /ontology/mappings/{mid}` · `DELETE /ontology/mappings/{mid}`
Update / delete a class binding (existing routes). Delete cascades its property bindings. **204** on delete; **409** on version mismatch.

### `GET /ontology/mappings/health`
Existing aggregate health view — now includes source-entity bindings (FR-021).

---

## Property bindings (E6b — NEW, nested under a class binding)

### `GET /ontology/mappings/{mid}/property-bindings`
List property bindings owned by class binding `{mid}`.

**200** → `[PropertyBinding]`:
```json
{
  "id": "uuid",
  "class_mapping_id": "uuid",
  "property_iri": "…/approvalNumber",
  "property_kind": "data",                 // data | object
  "source_path": "approval_no",            // column | $.data[*].name | slot key
  "transform_type": "none",                // none | controlled_vocab | pattern | cast
  "transform_config": null,
  "is_identifier": true,
  "is_label": false,
  "object_resolution": null,               // object only: id_reference | nested_object
  "target_class_iri": null,
  "target_id_path": null,
  "nested_binding_id": null,
  "version": 1,
  "status": "draft"
}
```

### `POST /ontology/mappings/{mid}/property-bindings`
Create a property binding. **Role**: senior_analyst.

**Request** — data property:
```json
{ "property_iri": "…/riskLevel", "property_kind": "data",
  "source_path": "risk", "transform_type": "controlled_vocab",
  "transform_config": {"map": {"高": "HighRisk", "中": "MediumRisk", "低": "LowRisk"}},
  "is_identifier": false, "is_label": false }
```
**Request** — object property, id_reference (FR-023a):
```json
{ "property_iri": "…/manufacturedBy", "property_kind": "object",
  "source_path": "mfr_code", "object_resolution": "id_reference",
  "target_class_iri": "…/Manufacturer", "target_id_path": "mfr_code" }
```
**Request** — object property, nested_object (FR-023b):
```json
{ "property_iri": "…/manufacturedBy", "property_kind": "object",
  "source_path": "$.manufacturer", "object_resolution": "nested_object",
  "nested_binding_id": "uuid-of-manufacturer-class-binding" }
```

**Validation** (FR-005 — blocking `error` vs non-blocking `warning`):
| Rule | Kind | Condition |
|---|---|---|
| V1 domain gate | error | property's declared domain excludes the class binding's class |
| V2 exists/enabled | error | `property_iri` missing or disabled |
| V3 object shape | error | object prop missing `object_resolution`, or mode-required fields absent |
| V4 single identifier | error | a second `is_identifier=true` on same class binding |
| V5 transform config | error | malformed `transform_config` for the `transform_type` |
| domain-at-declaration warn | warning | soft domain concerns per FR-005 (surfaced, non-blocking) |

**Responses**: **201** → `PropertyBinding` · **403** · **422** with `{errors:[…], warnings:[…]}`.

### `PUT /ontology/property-bindings/{pid}` · `DELETE /ontology/property-bindings/{pid}`
Update / delete a property binding. `expected_version` for optimistic concurrency. **200** / **204**; **409** on version mismatch; **403** wrong role.

### `POST /ontology/mappings/{mid}/validate`
Validate a full class binding + its property bindings against the ontology without extracting (drives FR-005/FR-021 in the UI).

**200** → `{ "health": "ok|drift|orphan|unmapped", "errors": [ … ], "warnings": [ … ] }`.

---

## Contract tests (pytest)

- Create db_table binding + column property binding → 201; readback carries declared values.
- Duplicate `(class, source_system)` → 409 (C1).
- `source_system` as raw DSN string → 422 (C3).
- Property binding whose property domain excludes the class → 422 with a V1 error.
- Second `is_identifier=true` → 422 (V4).
- object/id_reference missing `target_id_path` → 422 (V3).
- Non-`senior_analyst` role on any POST/PUT/DELETE → 403.
- Delete class binding cascades property bindings.
