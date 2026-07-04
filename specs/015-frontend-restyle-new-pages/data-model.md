# Phase 1 Data Model: Frontend Restyle & New Operational Pages

**Feature**: 015-frontend-restyle-new-pages | **Date**: 2026-07-03

This feature adds **no persisted data model** — every business entity is **backend-owned** and reaches the UI through the existing `lib/api.ts` types. This document therefore captures two things: (1) the **UI-surfaced view models** the new pages render (each a projection of an existing backend DTO, with its source endpoint), and (2) the **frontend design-system model** (tokens + shared components + nav) that is genuinely new to the frontend.

> Legend — **Source**: the existing endpoint/`lib/api.ts` type the view model projects. **Owner**: backend (all business entities) or frontend (design-system model). No new tables, no migration.

---

## 1. Nav / IA model (frontend-owned)

The app-shell single source of truth (extends the existing `NAV` in `app/(dashboard)/layout.tsx`).

- **NavItem**: `{ href, label (zh-CN), icon (lucide name), requiredRole? }`
- **NavGroup**: `{ title, items: NavItem[] }`
- **Nav** = ordered `(NavItem | NavGroup)[]`.

Rules:
- Every destination reachable today MUST appear (FR-005). New entries: Connector `/connector`, Data Mapping `/data-mapping`, Report Center `/reports`, Approval `/approval`. (Report Detail `/reports/[id]` is reached from Report Center, not a top-level entry.)
- `requiredRole` gating is carried over verbatim (FR-006) — e.g. approval stays QA-gated as today.
- Legacy `/integration`, `/approvals` are **removed from nav** and redirect to their superseding pages (R9).

---

## 2. Connector (数据源) — view model

**Source**: `listConnectors()` → `Connector[]` (`GET /api/integration/connectors`); test via `testConnector(id)`; runs via `listConnectorRuns(id)`. **Owner**: backend.

| UI field | From `Connector` | Notes |
|---|---|---|
| id | `id` | |
| name | `name` | |
| type (group key) | `system_type` | `rest_api` \| `database` \| `doc_repo` \| `file` → grouped list (FR-010) |
| status | derived from `last_status` + `last_error` | → `connected` \| `connecting` \| `failed` \| `not-connected` (FR-012, research R4) |
| endpoint/summary | `connection_config` (masked) | show `base_url` / `dsn_env` **name** only — never a secret value |
| last-status/last-sync | `last_status`, latest run `finished_at` | from `listConnectorRuns` |
| active | `is_active` | Switch |

**Create/edit input** reuses the existing per-type builders (`createRestApiConnector`, `createDatabaseConnector`, `createDocRepoConnector`, `createFileConnector`) — **credentials are env-var-name references only** (`token_env`, `api_key_env`, `dsn_env`, `token_ref`…); the form never accepts or displays a plaintext secret (Constitution Security; 014 FR-006). Test → `{ ok, latency_ms, error }`.

**States**: loading (Skeleton), empty ("no connectors"), error (Alert), per-row test success/failure feedback.

---

## 3. Class / Property Binding (映射) — view model

**Source** (all existing, feature 014): class tree `getClassHierarchy` / `getAllClasses`; class binding `getMappings(classIri)` → `TBoxMapping[]`; property bindings `getPropertyBindings(mappingId)` → `PropertyBinding[]`; coverage `getMappingHealth()` → `{ ok, unmapped, drift, orphan }`; mapping test `validateBinding(mappingId)` → `BindingValidationReport`. **Owner**: backend.

**ClassBinding** (per selected class) — `TBoxMapping`: `mapping_type`, `target`, `source_system`, `health`, `version`, `status`.

**PropertyBinding row** — `PropertyBinding`:

| UI field | From | Notes |
|---|---|---|
| property | `property_iri` + label | |
| kind | `property_kind` | data \| object |
| source field | `source_path` | FR-014 |
| field type | `transform_type` (+ `transform_config`) | transform rule (FR-014) |
| identifier / label flags | `is_identifier`, `is_label` | Switch/Checkbox (FR-014) |
| mapping status | `status` + membership in health buckets | mapped \| unmapped (FR-014) |
| version | `version` | optimistic concurrency on edit (FR-016) |

**Coverage** (FR-015): mapped vs. unmapped derived from `getMappingHealth` buckets → Progress bar. **Mapping test** (FR-015): `validateBinding(mappingId)` → surfaced errors/warnings.

**Edit invariants** (unchanged from 014): writes carry `expected_version`; 409 → `VersionConflictError` surfaced as a conflict prompt; no client-side ontology mutation (Constitution II).

---

## 4. Report / Document (报告 / 文档) — unified view model

**Source** (composition, research R2 — **no new endpoint**): documents `listDocuments()` (`GET /api/entities?module=document`); generated reports = aggregate `listReports(jobId)` over `listExtractionJobs()`; document content `getAnnotatedDocument(jobId)`; report download `downloadReportById(jobId, reportId)`; linked entities `listExtractedFrom(docIri)`. **Owner**: backend.

**ReportOrDocument** (normalized union):

| UI field | Generated report (`GeneratedReportDTO`) | Document (`EntityShadow`, module=document) |
|---|---|---|
| id | `id` (+ `job_id`) | `iri` |
| kind | `"generated report"` | `"uploaded document"` |
| title | derived (`report_type` + job) | `label_zh` / `label_en` |
| category | `report_type` | doc-type (`DOC_TYPE_LABELS`) / phase (`DEVELOPMENT_PHASES`) |
| type/format | `report_type` (DOCX/PDF) | `properties_json` doc type |
| date | `created_at` | `properties_json` created/ingested |
| size | `file_size` | n/a (—) |
| content (Detail) | preview + `downloadReportById` | tiptap `getAnnotatedDocument(...).content` |
| outline (Detail) | report sections (AST coverage, if any) | tiptap headings |
| linked entities (Detail) | via job candidates / `extractedFrom` | `listExtractedFrom(iri)` |

**Category tree** (FR-021): derived from existing controlled vocab (report types ∪ `DOC_TYPE_LABELS` ∪ `DEVELOPMENT_PHASES`). **States**: empty per category (FR-023), loading, error. **Upload** (FR-022/023): documents via the existing doc-repo `upload` access mode (`createDocRepoConnector` upload payload) / entity ingestion — no new endpoint. **Delete/preview/download** gated by role (FR-022).

---

## 5. Approval Item (审批项) — view model

**Source** (existing compliance workflow, research R3): queue `getPendingSignatures()` → `PendingConclusion[]`; decision `signConclusion(...)` (approve) / `rejectConclusion(...)` (reject); timeline `getComplianceAudit({ entity_iri })`; optional detail `getConclusionTrace(id)`. **Owner**: backend.

| UI field | From | Notes |
|---|---|---|
| id / subject | `PendingConclusion.id`, `risk_level`, `execution_type` | queue grouping/filter (FR-017) |
| detail | conclusion fields + `getConclusionTrace` | applicant/time/attachments where available; omit absent fields (FR-018) |
| decision | `signConclusion` / `rejectConclusion(reason)` | recorded via existing audit chain (FR-019) |
| timeline | `getComplianceAudit` entries for the item | history (FR-020) |

**Invariants**: decision controls honor existing role gating (QA to sign; FR-006); rejection requires a reason (existing `RejectDialog`); the audit chain is append-only/read-only.

---

## 6. Design-system model (frontend-owned — the genuinely new frontend model)

**Design Token (设计令牌)** — CSS variable in `globals.css :root`, consumed via `hsl(var(--token))` (see research R1). Semantic set: `--background --foreground --card(-foreground) --popover(-foreground) --primary(-foreground) --secondary(-foreground) --muted(-foreground) --accent(-foreground) --destructive(-foreground) --warning(-foreground) --success(-foreground) --border --input --ring --radius` **+ new sidebar family** `--sidebar --sidebar-foreground --sidebar-accent(-foreground) --sidebar-border --sidebar-primary(-foreground) --sidebar-ring`. Invariant: **no component hardcodes a raw palette value** (FR-003/SC-004); dark-mode slot reserved but empty (out of scope).

**Shared UI Component (共享组件)** — a reusable primitive in `components/ui/` under the shadcn new-york contract. Delta set to deliver (research R7): `sidebar`, `topbar`+`avatar`, `breadcrumb`, `dropdown-menu`, `tooltip`, `progress`, `switch`, `checkbox`, `accordion`, `pagination`, `data-table`, `empty-state`; plus restyle of the existing 14. Invariant: each consumes tokens, exposes visible focus / keyboard operability / semantic roles (FR-008), and is the **only** styling source for its role (no bespoke per-page duplicates, FR-002).

---

## Entity relationships (surface only)

```
Nav ──┬─> Connector page ........ Connector[]            (/api/integration/connectors)
      ├─> Data Mapping page ..... Class ─1:N─ TBoxMapping ─1:N─ PropertyBinding
      │                                                    (/api/ontology/...)
      ├─> Report Center ......... ReportOrDocument[]  (compose: entities?module=document
      │        └─> Report Detail ...  + jobs→reports; annotated-document; extractedFrom)
      └─> Approval page ......... PendingConclusion[] ─> audit timeline  (/api/compliance/*)

Design Tokens ──consumed-by──> Shared UI Components ──compose──> every page
```

No new persisted entity, no state machine beyond what the backend already owns. All lifecycle/versioning/audit semantics remain in the existing endpoints.
