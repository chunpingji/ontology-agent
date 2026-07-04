# Feature Specification: Frontend Restyle & New Operational Pages (design.pen)

**Feature Branch**: `015-frontend-restyle-new-pages`

**Created**: 2026-07-03

**Status**: Draft

**Input**: User description: "保持现有主体功能不变的前提下，按 @design.pen 替换前端风格，开发@design.pen中新增的各页面，Connector, Data Mapping, Report Center, Report Detail, Approval。规约：符合前端组件化开发规约"

## Overview

Adopt the visual language defined in `design.pen` as the platform's single UI source of truth, re-skinning the entire application shell and every existing page **without changing any existing functionality**, and building the five operational pages that `design.pen` introduces — **Connector (数据源接入)**, **Data Mapping (数据映射)**, **Report Center (报告中心)**, **Report Detail (报告详情)**, and **Approval (审批工作台)**. All work MUST comply with the platform's established frontend componentization convention (shared reusable components + semantic design tokens, no bespoke one-off styling).

This is a **presentation-layer feature**: it changes how the platform looks and organizes its screens, and surfaces already-existing backend capabilities through new dedicated pages. It does not add, remove, or alter business logic, data models, or backend contracts.

## Clarifications

### Session 2026-07-03

- Q: Report Center/Detail content model — platform-generated reports, a general document library, or both? → A: **Unified center (both)** — Report Center lists both platform-generated risk reports and user-managed documents; Report Detail renders generated-report content and previews uploaded document files alike, with a related-info panel showing linked ontology entities. No new backend logic is introduced.
- Q: Restyle breadth — all existing pages, only pages with a `design.pen` frame, or shell + new pages only? → A: **Global adoption** — every existing dashboard page is re-skinned to the new design system; pages with a dedicated `design.pen` frame match that frame, pages without one inherit the new visual language via shared components and tokens without a layout redesign.
- Q: Do the new pages replace the existing `/integration` and `/approvals` routes, or coexist with them? → A: **Replace (supersede)** — Connector + Data Mapping supersede `/integration` (its functions distributed across the two) and the Approval workspace supersedes `/approvals`; the legacy routes are removed/redirected with all functionality carried over (no duplicate surfaces).
- Q: Deliver the full 87-component `design.pen` design system, or only the components in-scope pages use? → A: **As-needed (YAGNI)** — build/restyle only the shared components the in-scope pages actually use, each living in the shared library + convention contract (no one-offs); unused `design.pen` components are out of scope and the library grows as future pages need it.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Unified visual restyle of the whole application, with zero functional regression (Priority: P1)

As any platform user, when I open the application after the redesign, every screen I already use — overview, ontology, entities/extraction, analysis, approvals, integration, settings — presents the new `design.pen` visual language (sidebar, top bar, cards, tables, badges, forms, dialogs), while every action, form, table, filter, and role-gated control I relied on still works exactly as before.

**Why this priority**: This is the foundation. The redesign's core promise is "new style, same behavior." A consistent design system (tokens + shared components) and a restyled shell must exist before any new page can be built on top of it, and preserving existing functionality is the hard non-negotiable constraint. Shipping only this slice already delivers a coherent, modernized product.

**Independent Test**: Navigate through every existing page and exercise its primary actions (create/edit/save, run extraction, publish, approve, filter, search). Confirm (a) each screen matches the `design.pen` language and (b) no previously available action is missing, broken, or behaviorally changed.

**Acceptance Scenarios**:

1. **Given** an existing page (e.g., Ontology or Extraction), **When** I open it after the restyle, **Then** its appearance matches the `design.pen` design system (colors, typography, spacing, component styling) and all its prior controls and data are present.
2. **Given** I perform a previously supported action (e.g., save an edited property binding, publish a batch, run an extraction job), **When** I complete it on the restyled page, **Then** the outcome and validation behavior are identical to before the restyle.
3. **Given** the sidebar and top navigation, **When** I look for any destination that existed before, **Then** it is still reachable (information architecture preserved, reorganized only as `design.pen` prescribes) and new destinations for the five new pages are present.
4. **Given** my role (senior_analyst / operator / qa), **When** I view restyled pages, **Then** the same actions are enabled or restricted for my role as before the restyle.

---

### User Story 2 - Connector page: manage data-source connectors (Priority: P2)

As a senior analyst, I open **数据源接入 (Connector)** to see all configured data sources grouped by type (REST API, database, document source), understand each one's connection status at a glance, and create, edit, test, or remove a connector.

**Why this priority**: Connector management is the entry point to the platform's dynamic source-to-ontology mapping capability; a clear, first-class page replaces ad-hoc access and unlocks the Data Mapping workflow that follows.

**Independent Test**: Open the Connector page, add a new REST API connector, run its connection test, edit it, and delete it — confirming the list, status indicators, and actions behave per the design and respect role gating.

**Acceptance Scenarios**:

1. **Given** configured connectors, **When** I open the Connector page, **Then** they are listed grouped by type, each showing name, type, connection status, endpoint/summary, and last-sync/last-status.
2. **Given** I am a senior analyst, **When** I create or edit a connector and run its connection test, **Then** the page reports success or a clear failure reason and reflects the resulting status.
3. **Given** a connector's connection state (connected / connecting / failed / not-connected), **When** it is displayed, **Then** the state is conveyed with a distinct, semantically-colored status indicator.
4. **Given** I am an operator or qa user, **When** I open the Connector page, **Then** I can view connectors but editing/creation/deletion controls are restricted per my role.

---

### User Story 3 - Data Mapping page: bind ontology classes and properties to source fields (Priority: P2)

As a senior analyst, I open **数据映射 (Data Mapping)**, select an ontology class from the class tree, and view/manage the property bindings that map each ontology property to a concrete source field — including source field, field type, transform rule, identifier/label flags, and mapping status — plus overall mapping coverage and a mapping test.

**Why this priority**: This page makes the declaration-driven mapping capability usable by domain experts without editing config by hand; it is the payoff of the Connector page and directly serves the platform's core purpose.

**Independent Test**: Open Data Mapping, pick a class from the tree, view its property-binding table, add/edit a binding, observe the mapped/unmapped status and coverage, and run a mapping test.

**Acceptance Scenarios**:

1. **Given** the ontology class tree, **When** I select a class, **Then** the page shows that class's source binding and its property-binding table with per-property mapping status (mapped / unmapped).
2. **Given** a property binding, **When** I view it, **Then** I see its source field, field type, transform rule, and identifier/label flags.
3. **Given** a class with partially-mapped properties, **When** I view its mapping, **Then** the page communicates coverage (mapped vs. unmapped) and lets me run a mapping test.
4. **Given** I am a senior analyst editing a binding, **When** I save, **Then** the existing save/validation/versioning behavior applies and conflicting concurrent edits are handled as they are today.

---

### User Story 4 - Approval workspace: review and decide on pending items (Priority: P2)

As an authorized reviewer, I open **审批 (Approval)** to work a queue of pending approval items, select one to see its full detail, applicant, submission time, and attachments, then approve or reject it (with a reason) and review the item's approval history/timeline.

**Why this priority**: Approval gates model/publish changes; the richer three-pane workspace materially improves reviewer throughput over the current page while reusing the existing approval workflow.

**Independent Test**: Open Approval, filter the queue, select an item, read its detail and attachments, submit an approve or reject decision with a reason, and confirm the timeline updates — all respecting role gating.

**Acceptance Scenarios**:

1. **Given** pending approvals, **When** I open the Approval page, **Then** I see a queue grouped/filterable by type and status.
2. **Given** I select a queue item, **When** it loads, **Then** the detail pane shows its subject, applicant, submission time, and associated attachments.
3. **Given** I am authorized to decide, **When** I approve or reject with a reason, **Then** the decision is recorded through the existing approval workflow and the item's timeline reflects the outcome.
4. **Given** an item I am not authorized to decide, **When** I view it, **Then** the decision controls are unavailable to me.

---

### User Story 5 - Report Center: browse and manage risk reports (Priority: P3)

As a platform user, I open **报告中心 (Report Center)** to browse both platform-generated risk reports and user-managed documents organized by category, see each item's title, type, date, and size, and preview, download, upload, or (if authorized) delete an item — with a clear empty state when a category has none.

**Why this priority**: Reports are the platform's user-facing output; a dedicated center makes them findable and manageable, but it depends on report generation that already exists and follows the higher-priority pages.

**Independent Test**: Open Report Center, browse categories, open a report's context actions to preview/download, upload a report, and view the empty state for an empty category.

**Acceptance Scenarios**:

1. **Given** reports organized by category, **When** I open Report Center, **Then** I see the category tree and a report list with title, type, date, and size per report.
2. **Given** a report in the list, **When** I open its actions, **Then** I can preview, download, and (if authorized) delete it.
3. **Given** I want to add a report, **When** I use the upload affordance (button or drag-and-drop area), **Then** the report is uploaded and appears in the list.
4. **Given** a category with no reports, **When** I open it, **Then** a clear empty state is shown.

---

### User Story 6 - Report Detail: read a report with structure navigation and related context (Priority: P3)

As a platform user, I open an item from Report Center into **报告详情 (Report Detail)**, read its content in a focused reading pane — a generated report's rendered content or a preview of an uploaded document file — jump around via a document structure/outline, review related information (linked ontology entities/metadata), and download or share it, returning to Report Center via breadcrumb.

**Why this priority**: Detail reading completes the report journey started in Report Center; it is valuable but strictly downstream of having reports to open.

**Independent Test**: From Report Center, open a report; confirm the reading pane renders content, the outline navigates to sections, the related-info panel shows linked context, and download/share plus breadcrumb-back work.

**Acceptance Scenarios**:

1. **Given** a selected report, **When** Report Detail opens, **Then** its content is rendered in a main reading pane with a breadcrumb back to Report Center.
2. **Given** a long report, **When** I use the structure/outline navigation, **Then** selecting an entry moves the reading pane to that section.
3. **Given** a report with linked context, **When** I open the related-information panel, **Then** I see the associated entities/metadata.
4. **Given** an open report, **When** I choose download or share, **Then** the corresponding action is available.

---

### Edge Cases

- **Missing design coverage**: For an existing page that has no dedicated `design.pen` frame (e.g., settings, rules), the page still adopts the new design language via shared components and tokens; it is re-skinned, not re-architected.
- **Unreachable / failing data source**: On the Connector page, a source that cannot be reached shows a clear failed/degraded status with a reason and does not crash the page. (Air-gap-from-public-cloud is a normal state, never shown as an error.)
- **Empty / partial data**: Empty lists (no connectors, no reports in a category, no pending approvals), partially-mapped classes, and reports with no linked context each render a defined empty/partial state rather than a blank or broken layout.
- **Loading & error**: Every data-backed view has defined loading (skeleton/placeholder) and error states consistent with the design system.
- **Long content & overflow**: Long names, deep class trees, large tables, and long documents render without broken, collapsed, or overflowing layout.
- **Role restriction**: A user lacking permission for an action sees it disabled/hidden rather than encountering an error on attempt.
- **Offline fonts/icons**: All typography and iconography render correctly with no public-internet access.

## Requirements *(mandatory)*

### Functional Requirements

**Design system & global restyle**

- **FR-001**: The application MUST adopt the visual design defined in `design.pen` (color, typography, spacing, iconography, and component styling) as the authoritative specification for UI appearance across the application shell and all pages.
- **FR-002**: The UI MUST be composed from a shared, reusable component library (a design system) consistent with the platform's frontend componentization convention; screens MUST reuse these shared components rather than duplicating bespoke, one-off styling. Only the shared components the in-scope pages actually use need be delivered (built or restyled to match `design.pen`); each delivered component MUST live in the shared library and its convention contract. Unused `design.pen` components are out of scope.
- **FR-003**: UI surfaces MUST consume semantic design tokens (e.g., background/foreground/primary/muted/border/destructive/warning/success) and MUST NOT hardcode raw palette values.
- **FR-004**: The restyle MUST preserve all existing user-accessible functionality on existing pages — no previously available action, field, filter, validation, or data view may be removed, broken, or behaviorally changed.
- **FR-005**: Navigation (sidebar and top bar) MUST keep every destination that exists today reachable (information architecture preserved, reorganized only as `design.pen` prescribes) and MUST add entries for the five new pages. The legacy `/integration` and `/approvals` routes MUST be superseded by the new Connector + Data Mapping and Approval pages respectively, with all functionality carried over and old paths redirecting to the superseding pages.
- **FR-006**: Role-based enabling/disabling of actions (senior_analyst / operator / qa) MUST behave identically after the restyle as before it.
- **FR-007**: Every data-backed view MUST present defined loading, empty, and error states consistent with the design system.
- **FR-008**: All existing and new UI MUST retain the accessibility guarantees of the componentization convention (visible focus states, keyboard operability, semantic roles for interactive elements and dialogs).
- **FR-009**: The restyle MUST cover **all** existing dashboard pages: pages with a dedicated `design.pen` frame MUST match that frame; pages without one (e.g., Analysis, Entities, Extraction, Settings, Rules) MUST adopt the new design language via the shared components and tokens without a layout redesign.

**Connector (数据源接入)**

- **FR-010**: The Connector page MUST list configured connectors grouped by type, each showing name, type, connection status, endpoint/summary, and last-sync/last-status.
- **FR-011**: Authorized users MUST be able to create, edit, and remove a connector and test its connection, with clear success/failure feedback.
- **FR-012**: Connection status MUST be conveyed with distinct, semantically-colored indicators for connected / connecting / failed / not-connected states.

**Data Mapping (数据映射)**

- **FR-013**: The Data Mapping page MUST let users select an ontology class from a class tree and view that class's source binding and property-binding table.
- **FR-014**: For each property binding, the page MUST display source field, field type, transform rule, identifier/label flags, and mapping status (mapped / unmapped).
- **FR-015**: The page MUST communicate mapping coverage (mapped vs. unmapped) for a class and MUST allow running a mapping test.
- **FR-016**: Authorized edits to bindings MUST apply the existing save, validation, versioning, and concurrent-edit-conflict behavior.

**Approval (审批)**

- **FR-017**: The Approval page MUST present a queue of pending items grouped/filterable by type and status.
- **FR-018**: Selecting a queue item MUST show its subject, applicant, submission time, and associated attachments.
- **FR-019**: Authorized reviewers MUST be able to approve or reject an item with a reason, recorded through the existing approval workflow.
- **FR-020**: The page MUST show the selected item's approval history/timeline.

**Report Center (报告中心)**

- **FR-021**: The Report Center page MUST let users browse both platform-generated reports and user-managed documents organized by category, with each item showing title, type, date, and size.
- **FR-022**: Users MUST be able to preview and download an item; authorized users MUST be able to upload documents and delete items.
- **FR-023**: The page MUST provide an upload affordance (button and drag-and-drop area) and MUST show a defined empty state for categories with no items.

**Report Detail (报告详情)**

- **FR-024**: The Report Detail page MUST render, in a main reading pane with a breadcrumb back to Report Center, either a generated report's content or a preview of an uploaded document file.
- **FR-025**: The page MUST provide document structure/outline navigation that moves the reading pane to the selected section.
- **FR-026**: The page MUST provide a related-information panel showing the item's linked ontology entities/metadata, and MUST offer download and share actions.

**Cross-cutting**

- **FR-027**: The new pages MUST surface existing backend capabilities only; this feature MUST NOT introduce new business logic, data models, or backend contract changes.
- **FR-028**: The entire UI (including all fonts and icons) MUST render and function correctly with no public-internet access, consistent with the platform's offline-first posture.
- **FR-029**: The primary UI language MUST be Simplified Chinese (zh-CN), matching `design.pen`.

### Key Entities *(surfaced in the UI; owned by existing backend)*

- **Connector (数据源)**: A configured source of data — name, type (REST API / database / document source), connection status, endpoint/config reference, last-sync/last-status.
- **Class/Property Binding (映射)**: The mapping of an ontology class and its properties to source fields — ontology class, ontology property, source field, field type, transform rule, identifier/label flags, mapping status, version.
- **Report / Document (报告 / 文档)**: A platform-generated risk/analysis report **or** a user-managed document — title, category, kind (generated report | uploaded document), type/format, date, size, content (rendered report content or file preview), document structure/outline, linked ontology entities/metadata.
- **Approval Item (审批项)**: A pending review — type, subject, applicant, submission time, status, attachments, decision (with reason), history/timeline.
- **Design Token (设计令牌)**: A named semantic style value (color/spacing/typography/radius) that UI components consume instead of raw values.
- **Shared UI Component (共享组件)**: A reusable building block (button, card, table, badge, dialog, sidebar item, etc.) from which pages are composed.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 100% of existing user-accessible features remain available and behave identically after the restyle (zero functional regression), verified by exercising every existing page's primary actions.
- **SC-002**: For every page that has a `design.pen` frame, a design review confirms ≥95% conformance to that frame across color, typography, spacing, and component usage.
- **SC-003**: All five new pages (Connector, Data Mapping, Report Center, Report Detail, Approval) are reachable from navigation and each completes its primary task end-to-end.
- **SC-004**: 100% of UI surfaces consume shared components and semantic tokens, with zero raw-palette hardcoding, verified against the componentization-convention checklist.
- **SC-005**: A first-time reviewer can locate the Approval workspace and reach a pending item's decision controls in under 30 seconds.
- **SC-006**: A user can create and successfully test a new connector in under 2 minutes.
- **SC-007**: A user can locate and open a specific report from Report Center in under 30 seconds.
- **SC-008**: Every data-backed view demonstrably renders correct loading, empty, and error states.
- **SC-009**: The application is fully usable — all pages render, navigate, and complete their primary tasks — with no public-internet access.
- **SC-010**: No page exhibits broken, collapsed, or overflowing layout under long-content and small-data (empty) conditions.

## Assumptions

- **Frontend-only scope**: This feature changes presentation and page composition only. Backend APIs, data models, ontology authority, and business logic are unchanged; the new pages surface capabilities that already exist (source-to-ontology connectors and property bindings from feature 014, risk reports from feature 010, and the existing approval workflow).
- **`design.pen` is the visual authority**: Where `design.pen` does not specify a state (loading/empty/error) or a page without a dedicated frame, reasonable defaults consistent with its design language and the componentization convention are used.
- **Componentization convention**: "符合前端组件化开发规约" refers to the platform's established frontend convention (feature 005 shadcn-ui refactor: shared `components/ui` primitives + semantic design tokens, no raw-palette hardcoding, documented accessibility guarantees). The new design system extends/updates that convention's tokens and component set to match `design.pen`; the convention's contracts are updated to stay in sync.
- **Information architecture**: The sidebar/top-bar IA is reorganized to match `design.pen` (e.g., grouped navigation with new entries for the five pages). Every destination reachable today remains reachable; the existing single "integration" entry is superseded by the dedicated Connector and Data Mapping pages, and the existing "approvals" entry by the Approval workspace, in both cases preserving the underlying functionality (old paths redirect).
- **Page-to-capability mapping**: Connector and Data Mapping present feature-014 connector and binding capabilities; Report Center and Report Detail present a **unified surface** over feature-010/013 generated reports **and** the existing document ingestion/annotation capability (feature 007) — no new backend logic; Approval presents the existing approval workflow. Home/Overview and Ontology frames in `design.pen` re-skin their existing pages.
- **Role model unchanged**: The senior_analyst / operator / qa roles and their existing gating are retained; pages respect current permissions.
- **Language & theme**: Simplified Chinese (zh-CN) is the primary UI language. A light theme is in scope as shown in `design.pen`; dark mode is out of scope unless `design.pen` provides it.
- **Offline delivery**: All fonts and icons used by `design.pen` are bundled for offline (air-gapped) operation; no runtime dependency on public-internet asset or font CDNs.
- **No data migration**: No changes to stored data are required; the redesign consumes existing data through existing interfaces.

## Out of Scope

- Any change to backend business logic, data models, ontology content, or API contracts (beyond keeping them working as-is).
- New analytical/reporting capabilities beyond presenting reports that the platform already generates.
- Dark mode (unless explicitly provided by `design.pen`).
- Mobile/responsive layouts beyond what `design.pen` specifies.
- Authentication/SSO changes.
- Building `design.pen` design-system components not used by any in-scope page (the shared library grows as future pages require).
