# Quickstart: Ontology Dynamic Object Mapping

**Feature**: 014-ontology-dynamic-mapping · Validation guide for the declaration-driven mapping pipeline.

This is a **validation/run guide**, not implementation. It proves the feature end-to-end: declare bindings → run extraction → get traceable candidates → ontology-aware facts → hierarchy-aware alignment. Contract details live in [contracts/](contracts/); entity shapes in [data-model.md](data-model.md).

---

## Prerequisites

- Backend deps installed via `uv` (env is `uv`-managed — run everything with `uv run`; bare `python` is anaconda and lacks psycopg2 — see [[backend-python-env-managed-by-uv]]).
- PostgreSQL reachable; Alembic at head **after** applying migration `0013_property_binding` (revision id ≤32 chars — see [[alembic-revision-id-32-char-limit]]).
- A published test ontology with `DrugProduct` (+ at least one subclass), `approvalNumber`/`riskLevel` data properties, `manufacturedBy` object property, and a `Manufacturer` class.
- A test source database reachable via an env-var DSN (e.g. `export DRUG_DB_DSN=postgresql://…intranet…`), and a stub REST connector (reuse `StubConnector`).
- Role `senior_analyst` for declaration steps (via `X-User`/`X-Role` headers).

Apply the migration:
```bash
cd backend && uv run alembic upgrade head
```

---

## Setup commands

```bash
# From repo root
cd backend
uv run uvicorn app.main:app --reload   # or the project's usual run command
```

Environment (credentials only in env — never DB/git, FR-006):
```bash
export DRUG_DB_DSN="postgresql://reader@drug-db.intranet.local/registry"
export DRUG_API_TOKEN="…"   # consumed by the rest_api connector at call time
```

---

## Scenario A — US1: declarative DB extraction (P1, MVP)

Proves: declare → extract → traceable candidates, zero code change (SC-001/002/003, FR-007/009/010).

1. **Declare the class binding** (`POST /ontology/classes/DrugProduct/mappings`, role senior_analyst):
   `mapping_type=db_table`, `target=drug_product`, `source_system=DRUG_DB_DSN`.
2. **Declare property bindings** (`POST /ontology/mappings/{mid}/property-bindings`):
   - `approval_no → approvalNumber` (`is_identifier=true`).
   - `risk → riskLevel`, `transform_type=controlled_vocab`, map `{高→HighRisk,…}`.
   - FK `mfr_code → manufacturedBy` (`object`, `id_reference`, `target_class_iri=Manufacturer`, `target_id_path=mfr_code`).
3. **Run the job** (`POST /extraction/jobs` with `class_mapping_id={mid}`, `source_type=database`; then `/start`).
4. **Inspect candidates** (`GET /extraction/jobs/{job_id}` → candidates).

**Expected**: one candidate per row; `approvalNumber` equals the row value; `riskLevel` is the **normalized** ontology term (`HighRisk`), not `高`; each candidate has a resolvable `source_ref={system,entity,record}`; the `manufacturedBy` edge links to the `Manufacturer` individual (or a `link` review candidate when absent). Rename `riskLevel`'s binding and re-run → output reflects it with **no** app-code change.

**Drift check**: drop/rename a declared column → job **completes partial**, the binding's E6 `health="drift"`, a warning is recorded, no crash (SC-007).

---

## Scenario B — US2: declarative API extraction (P2)

Proves: same unified output for REST/JSON; graceful degradation (FR-008/011/019, SC-006/008).

1. **Create a `rest_api` connector** (`POST /integration/connectors`, `system_type=rest_api`) with `base_url` on the intranet, `auth.token_env=DRUG_API_TOKEN`, `pagination`. See [source-connector contract](contracts/source-connector.md).
2. **Test it** (`POST /integration/connectors/{id}/test`) → `{ok:true}`.
3. **Declare** an `api_endpoint` class binding (`target=/api/drugs`, `source_system={connector_id}`) + response-path property bindings (`$.data[*].name → drugName`; nested `$.manufacturer → manufacturedBy` in `nested_object` mode).
4. **Run** the job (`source_type=api`) and inspect candidates.

**Expected**: one candidate per returned item, values per bindings; nested `manufacturer` becomes its **own** sub-candidate linked via `manufacturedBy` (FR-023b). Take the connector offline → job **completes** with empty output + `degraded_reason` (FR-019); the air-gap-from-public-cloud posture is **never** flagged as this degradation (SC-008).

---

## Scenario C — US3: ontology-aware fact building (P2)

Proves: hierarchy/domain/alignment/vocab replace hardcoded matching (FR-012–015, SC-004).

1. Feed `edges_to_facts` edges whose object is a **subclass** of `DrugProduct`, a newly added data property, and a class carrying an external alignment.
2. Build facts and inspect the `Facts` view.

**Expected**: the subclass is recognized as a drug-class member **via the hierarchy** (not a hardcoded name); a data value whose declared domain excludes the subject is **not** asserted (no cross-domain leakage); the class's external alignment appears in `Facts.alignments` (previously always empty); a controlled-vocab scalar is normalized to the standard term.

---

## Scenario D — US4: hierarchy-aware alignment (P3)

Proves: cross-level dedup (FR-016/017, SC-005).

1. Seed an existing individual under a **subclass** of `DrugProduct` with identifier `X`.
2. Present a candidate typed at the **parent** class with identifier `X`.
3. Run alignment.

**Expected**: alignment returns a **merge** to the existing individual (not a new duplicate); the result records `matched_level` (`subclass`/`same`/`parent`) and `method`. With multiple equal-rank matches → **no auto-merge**, surfaced for review.

---

## Automated validation (pytest)

Run the feature's contract + integration + unit tests:
```bash
cd backend
uv run pytest tests/contract/test_class_property_binding.py \
              tests/contract/test_extraction_job_declarative.py \
              tests/contract/test_source_connector.py \
              tests/integration/test_declarative_db_extraction.py \
              tests/integration/test_api_extraction_degradation.py \
              tests/integration/test_hierarchy_alignment.py \
              tests/unit/test_ontology_aware_facts.py -v
```

**Expected**: all pass. Backward-compat suite (legacy `column_mapping` path) also passes unchanged (FR-018). Full suite stays green (no regression to the existing structured path — Constitution VI).

---

## Definition of done (maps to Success Criteria)

- [ ] SC-001/002: new table + property rename onboarded with zero code change (Scenario A).
- [ ] SC-003: 100% of candidates carry a resolvable `source_ref`.
- [ ] SC-004: subclasses & aligned classes recognized in facts (Scenario C).
- [ ] SC-005: cross-hierarchy duplicates detected as merges (Scenario D).
- [ ] SC-006: API source declared/run/reviewed end-to-end (Scenario B).
- [ ] SC-007: drift → partial job + health warning, never a crash.
- [ ] SC-008: offline/unreachable runs never falsely marked degraded for air-gap posture.
