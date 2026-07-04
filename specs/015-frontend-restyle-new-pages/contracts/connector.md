# UI Contract: Connector (数据源接入)

**Feature**: 015 | Route: `/connector` (supersedes `/integration`) | User Story 2

## Backend surface (existing — read + reuse, no new endpoint)

`listConnectors` · `createConnector` (+ per-type builders) · `deleteConnector` · `testConnector` · `listConnectorRuns` — all under `/api/integration/connectors`.

## Behavior (FR-010–012)

1. **Given** configured connectors, **When** the page loads, **Then** they render **grouped by `system_type`** (rest_api/database/doc_repo/file), each showing name, type, connection status, endpoint/summary, last-status/last-sync.
2. **Given** a senior analyst, **When** they create/edit a connector and run its test, **Then** the page reports success or a clear failure reason (`{ ok, latency_ms, error }`) and reflects the resulting status.
3. **Given** a connection state, **When** displayed, **Then** it uses a distinct semantically-colored indicator: `connected` (success), `connecting` (muted/warning), `failed` (destructive), `not-connected` (muted) — derived from `last_status`/`last_error` (research R4).
4. **Given** an operator/qa user, **When** the page loads, **Then** view is allowed but create/edit/delete/test controls are gated per role (FR-006).

## Invariants

- **Secrets**: create/edit collects credential **env-var-name references only** (`token_env`, `api_key_env`, `dsn_env`, `token_ref`…). The form MUST NOT accept, display, store, or transmit a plaintext secret (Constitution Security; 014 FR-006).
- Air-gap-from-public-cloud is a **normal** status, never an error; only an unreachable **intranet** endpoint is `failed` with its reason (Constitution VI, edge case).
- doc_repo/file connectors created here surface their documents in Report Center (data-model §4).

## States

Loading (Skeleton), empty ("暂无连接器"), error (Alert), per-row test feedback.

## Verification

- Create REST connector → test → edit → delete round-trips; status indicator matches `last_status` (quickstart).
- No plaintext-secret field exists in the form (contract review + quickstart).
- Role gating spot-check (operator sees read-only).
