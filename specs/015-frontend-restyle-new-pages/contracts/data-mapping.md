# UI Contract: Data Mapping (数据映射)

**Feature**: 015 | Route: `/data-mapping` | User Story 3

## Backend surface (existing — feature 014, no new endpoint)

Class tree `getClassHierarchy`/`getAllClasses` · class binding `getMappings(classIri)` · property bindings `getPropertyBindings`/`createPropertyBinding`/`updatePropertyBinding`/`deletePropertyBinding` · coverage `getMappingHealth` · mapping test `validateBinding`.

## Behavior (FR-013–016)

1. **Given** the ontology class tree, **When** a class is selected, **Then** the page shows that class's source binding (`TBoxMapping`) and its property-binding table.
2. **Given** a property binding, **When** viewed, **Then** it shows source field (`source_path`), field type (`transform_type`), transform rule (`transform_config`), and identifier/label flags (`is_identifier`/`is_label`).
3. **Given** a class with partially-mapped properties, **When** viewed, **Then** coverage (mapped vs. unmapped, from `getMappingHealth`) is shown as a Progress indicator and a **mapping test** (`validateBinding`) can be run, surfacing errors/warnings.
4. **Given** a senior analyst editing a binding, **When** they save, **Then** existing save/validation/versioning applies: `expected_version` optimistic concurrency; a 409 surfaces as a `VersionConflictError` conflict prompt (unchanged from 014, FR-016).

## Invariants

- **No client-side ontology mutation** beyond the existing binding endpoints (Constitution II). The page never writes TTL/T-Box directly.
- Per-property mapping status derives from binding `status` + health buckets (`ok`/`unmapped`/`drift`/`orphan`).
- Role gating: edit gated to senior_analyst; operator/qa read-only (FR-006).

## States

Loading (Skeleton) for tree + table; empty ("未选择类" / "无属性绑定"); error (Alert); conflict prompt on 409.

## Verification

- Select class → view bindings → add/edit binding → observe coverage change → run mapping test (quickstart).
- Concurrent-edit conflict path preserved (save with stale version → conflict prompt).
