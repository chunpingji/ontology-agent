# UI Contract: Design System (tokens + shared components + a11y)

**Feature**: 015 | Consumed by: all pages | Backend surface: **none** (frontend-only)

## Token contract (FR-001, FR-003, SC-004)

- `globals.css :root` defines the semantic token set (data-model §6) as HSL triplets; `tailwind.config.ts` maps each to `hsl(var(--token))`. Values evolve to `design.pen` (research R1): neutral base ramp, `--primary`/`--ring` = the design.pen blue, retain `--warning`/`--success`, **add** the 7-token `--sidebar-*` family.
- **Invariant**: no component may hardcode a raw palette value (hex/rgb/named). All color/spacing/radius flows through tokens. Verified by grep for hex literals in `components/` and page files (quickstart offline/lint check).
- Dark-mode slot stays reserved and empty (out of scope).

## Shared-component contract (FR-002, FR-008)

- Deliver **only** the YAGNI delta (research R7): `sidebar`, `topbar`+`avatar`, `breadcrumb`, `dropdown-menu`, `tooltip`, `progress`, `switch`, `checkbox`, `accordion`, `pagination`, `data-table`, `empty-state`; restyle the existing 14 primitives to the evolved tokens.
- Each component: lives in `components/ui/` under the shadcn new-york contract; consumes tokens only; ships **visible focus state**, **keyboard operability**, and **semantic roles** for interactive elements and dialogs (Radix provides these — do not regress them).
- Unused `design.pen` components are **not** built.

## State contract (FR-007, SC-008)

Every data-backed view provides the four states with shared components: **loading** (`Skeleton`), **empty** (`EmptyState`), **error** (`Alert` destructive), **populated**. No blank/broken layout under empty or long-content conditions (SC-010).

## Offline contract (FR-028, SC-009)

Fonts self-hosted (Inter + CJK via `next/font/local`; build-time vendored) — **zero runtime CDN**. Icons via bundled lucide-react. New Radix deps are build-time only.

## Verification

- Visual conformance ≥95% to `design.pen` frames where they exist (SC-002).
- No raw-palette literal in shipped components (SC-004).
- Keyboard-only traversal reaches every interactive control; focus visible (FR-008).
- Build + run with network disabled renders all fonts/icons correctly (SC-009).
