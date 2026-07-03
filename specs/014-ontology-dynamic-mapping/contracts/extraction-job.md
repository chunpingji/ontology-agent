# Contract: Declaration-Driven Extraction Job (DB + API)

**Feature**: 014-ontology-dynamic-mapping · **Base router**: `app/api/extraction.py` (existing `/jobs` extended)

Goal: run an extraction job that **targets one class binding (E6)** and produces unified `ExtractionCandidate`s by consuming its property bindings (E6b) — for `db_table` and `api_endpoint` sources — replacing the hardcoded `column_mapping` path while keeping it working (FR-018).

Authorization: job creation requires an editor role consistent with existing extraction routes; candidate review is unchanged. Extraction is **read-only** against sources and the ontology; output enters `review_status="pending"` (never auto-published, Principle II).

---

## `POST /extraction/jobs` (declaration-driven mode — NEW)

Create + enqueue a job bound to a class binding. Coexists with the existing file/config-based creation (FR-018).

**Request** (declaration mode):
```json
{
  "class_mapping_id": "uuid",     // the E6 class binding to extract (targets exactly one — FR-024)
  "source_type": "database"       // "database" | "api"  (was: rejected for "api")
}
```
- When `class_mapping_id` is present, the pipeline resolves E6 + its E6b property bindings and ignores any legacy `column_mapping`.
- When absent (legacy), the existing `ExtractionConfig`/`column_mapping` path runs unchanged (FR-018).

**Responses**: **202** → `ExtractionJobResponse {id, status:"queued", source_type, …}` · **404** unknown `class_mapping_id` · **422** binding not a source-entity type (e.g. `slpra_iri`).

## `POST /extraction/jobs/{job_id}/start` · `GET /extraction/jobs/{job_id}` · `GET /extraction/jobs/{job_id}/progress`
Existing lifecycle routes — now also drive `database`(row) and `api` source types (FR-011).

---

## Runtime behavior contract

### Database source (`db_table` binding) — R5
1. Resolve DSN from `os.environ[source_system]`; unset/unreachable → job **completes** with `status="degraded"`, `degraded_reason` set, zero candidates (FR-019). *Never* crashes.
2. `SELECT` the bound `target` table's columns named by property bindings' `source_path`.
3. One `instance` candidate per row: `extracted_properties[property_iri] = transform(value)` (R6); `source_ref = {system, entity:table, record:pk}` (FR-010).
4. `is_identifier` binding → used for alignment (FR-004, R9).
5. Object property, `id_reference` → relationship to the aligned `target_class_iri` individual carrying `target_id_path`; none found → `link` candidate for review (FR-023a).
6. Missing declared column (drift) → set E6 `health="drift"`, warn, skip that binding, job completes partial (FR-020, SC-007).

### API source (`api_endpoint` binding) — R7
1. Resolve the connector (`source_system` → `IntegrationConnector`, `system_type="rest_api"`); creds from env (never DB).
2. Page through the endpoint (offset/page or cursor per `connection_config`); for each item, walk property bindings' `source_path` response paths (`$.data[*].name` style).
3. Same unified candidate output as the DB path (FR-008); same transforms, provenance, alignment.
4. Object property, `nested_object` → recurse into the embedded structure, extract a sub-candidate via `nested_binding_id`'s bindings, link by `group_key` (FR-023b); bounded recursion depth (edge case).
5. Connector unreachable (intranet down) → `degraded` + reason, empty output, job completes (FR-019). Air-gap-from-public-cloud is **never** this failure (SC-008, R12).

### Unified candidate (both sources)
```json
{
  "target_class_iri": "…/DrugProduct",
  "candidate_kind": "instance",
  "extracted_properties": { "…/approvalNumber": "国药准字H20003007", "…/riskLevel": "HighRisk" },
  "source_ref": { "system": "DRUG_DB_DSN", "entity": "drug_product", "record": "42" },
  "review_status": "pending",
  "degraded_reason": null
}
```

---

## Contract tests (pytest)

- **DB happy path**: declared `drug_product→DrugProduct`, `approval_no→approvalNumber` → each row → candidate whose `approvalNumber` == row value (US1 AS-1).
- **Controlled vocab**: source `高` → candidate value `HighRisk` (US1 AS-2).
- **Rename property, re-run**: output reflects new binding, no code change (US1 AS-3, SC-002).
- **Drift**: declared column absent → partial job, E6 `health="drift"`, warning (US1 AS-4, SC-007).
- **Identifier alignment**: identifier binding drives alignment against existing individuals (US1 AS-5).
- **Object id_reference**: FK `mfr_code`→`manufacturedBy` links to `Manufacturer`, or `link` candidate when absent (US1 AS-6, FR-023a).
- **API happy path** (stub connector): `$.data[*].name`→`drugName` → candidate per item (US2 AS-1).
- **API nested_object**: nested `manufacturer` → own sub-candidate linked via `manufacturedBy` (US2 AS-4, FR-023b).
- **API unreachable**: degrades to empty + reason, pipeline not failed (US2 AS-2, FR-019).
- **Backward compat**: legacy `column_mapping` job runs unchanged (FR-018).
- **Provenance**: 100% of candidates carry a resolvable `source_ref` (SC-003).
