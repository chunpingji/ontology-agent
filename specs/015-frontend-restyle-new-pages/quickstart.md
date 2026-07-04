# Quickstart — 015 Frontend Restyle + New Pages

Executable validation guide. Proves: (A) **zero functional regression** across all existing pages after the restyle, (B) each of the **5 new pages** meets its acceptance scenarios, (C) **offline** rendering. Details live in the [contracts](./contracts/) and [data-model.md](./data-model.md) — this file is the run/verify matrix, not implementation.

## Prerequisites

- Backend running (uv-managed): `cd backend && uv run uvicorn app.main:app --reload` — provides the existing endpoints all pages consume (no new endpoint is added by this feature).
- Frontend deps installed: `cd frontend && npm install`.
- Seeded ontology + at least one connector, one extraction job with a report, and one pending compliance signature (reuse existing seed/fixtures) so lists are non-empty.

## Setup / run

```bash
cd frontend
npm run lint        # must pass — no raw-palette literals, no unused imports
npm run build       # must succeed — self-hosted fonts vendored, no CDN fetch at build
npm run dev         # http://localhost:3000
```

## A. Zero-regression matrix (US1 · FR-009 · SC-001)

For **every** existing route, confirm behavior is unchanged after restyle — only the visual skin differs. Exercise each with all three roles (senior_analyst / operator / qa) via the top-bar role switcher; role-gated visibility must match pre-restyle exactly (FR-006).

| Route | Must still work (functional, unchanged) |
|-------|------------------------------------------|
| `/overview` | KPIs/panels load; **realtime-inference dashboard** (migrated from `/integration`) renders here (R4) |
| `/ontology`, `/ontology/rules` | Class/rule views load, edits + versioning intact |
| `/entities` (+`/extraction`, `/[jobId]/ast`) | Entity lists, extraction launch, AST editor all function |
| `/analysis` | Graph/analysis views render |
| `/settings` (+`/ast-templates`) | Settings + AST template CRUD unchanged |
| `/integration` → **redirects** to `/connector` | Old URL lands on Connector, all connector functions present |
| `/approvals` → **redirects** to `/approval` | Old URL lands on Approval, QA sign/reject intact |

**Pass:** no console errors; every action that worked before works; no layout breaks under empty/long content (SC-010); no orphaned/unreachable prior destination ([app-shell contract](./contracts/app-shell.md)).

## B. New-page acceptance

Run each page against its contract's Behavior + Verification section.

| Page | Route | Contract | Key acceptance |
|------|-------|----------|----------------|
| Connector | `/connector` | [connector.md](./contracts/connector.md) | Create REST connector → test (success/failure reason) → status indicator matches `last_status`; **no plaintext-secret field**; grouped by type (US2) |
| Data Mapping | `/data-mapping` | [data-mapping.md](./contracts/data-mapping.md) | Select class → view/add/edit property binding → coverage Progress updates → run mapping test; stale-version save → conflict prompt (US3) |
| Report Center | `/reports` | [report-center.md](./contracts/report-center.md) | Category tree + list (title/type/date/size); preview + download; upload adds item; empty category state (US5) |
| Report Detail | `/reports/[reportId]` | [report-center.md](./contracts/report-center.md) | Reading pane renders; outline entry scrolls pane; related-info shows linked entities; breadcrumb returns to center (US6) |
| Approval | `/approval` | [approval.md](./contracts/approval.md) | Queue filter by type/status → select → detail + timeline; approve or reject-with-reason updates audit timeline; unauthorized role sees disabled controls (US4) |

**Pass criteria per page:** all four UI states reachable (loading/empty/error/populated, FR-007); role gating enforced; primary task reachable quickly (Approval decision <30s, SC-005).

## C. Offline check (FR-028 · SC-009 · Constitution VI)

1. Build, then **disable network** (or block egress).
2. Load the app and each new page.
3. **Pass:** all fonts (Inter + CJK) and icons render correctly with zero failed CDN/network requests; pages that read intranet endpoints degrade gracefully (clear message, not a crash) and offline is never labeled an error state.

## D. Design conformance (SC-002 · SC-004)

- Spot-compare each framed page against its `design.pen` frame — ≥95% visual conformance.
- Grep shipped `components/`+page files for hex/rgb literals → none (tokens only, [design-system contract](./contracts/design-system.md)).

## Done when

- [ ] A: every existing route passes the regression matrix for all 3 roles; both redirects resolve.
- [ ] B: all 5 new pages pass their contract acceptance + 4-state coverage.
- [ ] C: offline load renders fully; no runtime network dependency.
- [ ] D: visual conformance + no raw-palette literals; `lint` + `build` green.
