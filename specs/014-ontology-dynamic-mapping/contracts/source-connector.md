# Contract: REST/JSON Source Connector

**Feature**: 014-ontology-dynamic-mapping · **Base router**: `app/api/integration.py` (existing `/connectors` reused)

A **Source Connection** for API sources is an `IntegrationConnector` with `system_type="rest_api"`, driven by a new generic reader (`services/integration/rest_connector.py` + `services/extraction/api_reader.py`). Reuses the existing connector CRUD, `/test`, and health/`runs` routes — this contract specifies the `rest_api` config shape and the generic pull behavior. GraphQL / OAuth2 authorization-code are **deferred** (Q2); the config keeps auth/protocol pluggable.

Authorization: connector CRUD per existing integration routes. **No credential is ever stored** in `connection_config` or returned by any route (FR-006) — credentials are injected from environment variables named by the config.

---

## `POST /integration/connectors` (system_type = "rest_api")

**Request** (`ConnectorCreate`):
```json
{
  "name": "internal-drug-registry",
  "system_type": "rest_api",
  "connection_config": {
    "base_url": "http://drug-registry.intranet.local",   // intranet only (FR-022)
    "endpoint": "/api/drugs",
    "auth": { "scheme": "bearer", "token_env": "DRUG_API_TOKEN" },   // scheme: api_key | bearer; *_env names, not values
    "pagination": { "style": "cursor", "cursor_path": "$.next", "page_size": 200 }  // style: offset | page | cursor
  },
  "is_active": true
}
```
**Validation**:
- `base_url` host MUST be internal-network (intranet); public-internet/cloud hosts → **422** (FR-022).
- `auth` carries only `*_env` names (e.g. `token_env`, `api_key_env`), never literal secrets → **422** if a literal secret-looking value is supplied (FR-006).
- `pagination.style` ∈ {offset, page, cursor}.

**Responses**: **201** → `ConnectorResponse` (config echoed **without** any secret) · **422** validation.

## `POST /integration/connectors/{connector_id}/test`
Reachability + auth probe (existing route). For `rest_api`: issue a minimal request using env-injected creds.

**200** → `{ "ok": true, "detail": "reachable, 1 page fetched" }`
**200** (degraded, not an error) → `{ "ok": false, "detail": "unreachable: <reason>" }` — an unreachable **intranet** endpoint is a graceful-degradation signal, not a crash (FR-019, R12).

## `GET /integration/connectors` · `DELETE /integration/connectors/{id}` · `GET /integration/connectors/{id}/runs`
Existing routes, reused unchanged for `rest_api` connectors.

---

## Reader behavior contract (generic REST/JSON pull)

| Concern | Behavior |
|---|---|
| **Auth** | `bearer` → `Authorization: Bearer $<token_env>`; `api_key` → header/query per config; value read from env at call time, never persisted |
| **Pagination** | `offset`/`page` → increment until an empty page; `cursor` → follow `cursor_path` until absent; persist progress in `sync_cursor` |
| **Item walk** | property bindings' `source_path` are JSON paths (`$.data[*].field`); missing path on an item = drift for that binding (warn, skip value) |
| **Nested** | `nested_object` bindings recurse into embedded objects → sub-candidates (bounded depth) |
| **Output** | identical unified `ExtractionCandidate` shape as the DB adapter (FR-008) |
| **Degradation** | connection refused/timeout on an intranet host → empty output + `degraded_reason`, job completes (FR-019); public-cloud posture never marked degraded (SC-008) |
| **Protocol-neutral** | binding model unchanged if GraphQL/OAuth2 added later (Q2) — only the connector/reader change |

---

## Contract tests (pytest, against a stub connector — reuse `StubConnector`)

- Create `rest_api` connector with `token_env` → 201; response contains no secret.
- `base_url` on a public host → 422 (FR-022).
- `auth` with a literal token value → 422 (FR-006).
- `/test` on a reachable stub → `{ok:true}`; on an unreachable host → `{ok:false}` (no exception, R12).
- Reader paginates a multi-page stub (cursor) and yields all items.
- Reader applies bearer auth header from env.
- Missing response path on an item → value skipped + drift warning, item still yields a candidate.
