# UI Contract: Application Shell (sidebar / top bar / routing)

**Feature**: 015 | Consumed by: all pages | Backend surface: **none**

## Layout (FR-001, FR-009)

- `(dashboard)/layout.tsx` renders the `design.pen` app frame: **Sidebar** (grouped nav + section titles + active state) + **top bar** (title, identity/avatar, role switcher — reuse existing `useIdentity`). Content area hosts the routed page.
- Existing pages re-skin in place (no layout redesign for unframed pages, FR-009).

## Nav / IA (FR-005)

- Single-source nav model (data-model §1) extends the existing `NAV`. New entries: 数据源接入 `/connector`, 数据映射 `/data-mapping`, 报告中心 `/reports`, 审批 `/approval`.
- **Every** destination reachable today remains reachable; `requiredRole` gating carried over verbatim (FR-006).
- Active state: exact match or nested sub-path (reuse existing `isActive`).

## Redirects / supersession (FR-005, clarify Q3)

- `/integration` → `/connector`; `/approvals` → `/approval` (Next.js `redirects()` + in-page guard). Legacy entries removed from nav. The realtime-inference dashboard formerly under `/integration` is retained on Overview (research R4) — **not dropped**.

## Behavior

1. **Given** any existing destination, **When** the shell renders, **Then** it appears in nav (or is reachable from a page that superseded it) and routes correctly.
2. **Given** a legacy `/integration` or `/approvals` URL, **When** visited, **Then** it redirects to the superseding page with functionality intact.
3. **Given** a role (senior_analyst/operator/qa), **When** the shell renders, **Then** role-gated entries show/hide exactly as before the restyle.

## Verification

- Nav enumerates all prior + 5 new destinations (quickstart IA check).
- Both redirects resolve; superseded functionality present on the new pages (quickstart carry-over matrix).
- Role switch toggles gated entries identically to pre-restyle.
