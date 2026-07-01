# Research: AST 提取前端 UI

**Date**: 2026-07-01 | **Spec**: [spec.md](spec.md) | **Plan**: [plan.md](plan.md)

## R1. Coverage response shape — flat list vs. tree

**Decision**: Augment the `GET /ast-coverage` response to include both the flat `slots` array (from `CoverageManifest.to_dict()`) and a `sections` array mirroring the AST template tree (Section → Group metadata), so the frontend can render the three-level tree without a separate template endpoint.

**Rationale**: `CoverageManifest.to_dict()` returns `{template_id, total_slots, filled, ..., slots: [{slot_id, status, ...}]}` — a flat list with no Section/Group hierarchy. The frontend needs the tree structure for `ASTTreeView`. Two options:
1. Separate `GET /ast-template` endpoint to fetch the template tree, then merge client-side with coverage.
2. Enrich the coverage response with the template tree + slot coverage merged.

Option 2 avoids a second round-trip and keeps the API self-contained. The `ReportTemplate` Pydantic model already has `sections[].groups[].slots[]` — we emit a nested JSON where each slot entry carries its `SlotCoverage` alongside the template metadata.

**Alternatives considered**: Separate template endpoint (rejected — extra round-trip for data that's always consumed together).

## R2. Dismissed slot integration into `validate_coverage`

**Decision**: Add an optional `dismissed_slot_ids: set[str]` parameter to `validate_coverage()`. When a slot would resolve to `missing_required` but its `slot_id` is in the dismissed set, emit `status="dismissed"` instead. Add `DISMISSED = "dismissed"` constant alongside the existing status constants.

**Rationale**: The dismissal is application-layer state (stored in `SlotDismissal` DB table), not ontology state. Passing dismissed IDs as a parameter keeps `validate_coverage` pure (no DB dependency) while letting the API layer inject dismissals from the DB. The `CoverageManifest` counter properties gain a `dismissed` counter. The `.docx` renderer treats `dismissed` slots as "N/A（不适用）" per spec clarification.

**Alternatives considered**: Modifying the `Slot` model to carry a `dismissed` flag (rejected — template is static, dismissal is per-job); post-processing the manifest after `validate_coverage` returns (viable but more error-prone since the status string would be overwritten after creation).

## R3. WordViewer `highlightRef` reuse in AST page

**Decision**: Reuse the existing `WordViewer` component and its `highlightRef` prop on the AST page. When a user clicks a filled slot with a non-null `source_ref`, pass that value as `highlightRef` to the `WordViewer` instance embedded in the AST page's document preview pane.

**Rationale**: `WordViewer` already implements `parseSourceRef()` (splits on " / ", strips section/table prefixes) and `applyHighlight()` (searches headings and table cells, scrolls into view). The `SlotCoverage.source_ref` field uses the same format produced by the extraction pipeline. No modification to `WordViewer` needed.

**Alternatives considered**: Building a new highlighting mechanism (rejected — unnecessary duplication).

## R4. "查看 AST" entry point in extraction job list

**Decision**: Add a "查看 AST" link in the extraction page's job table (alongside existing "查看标注" and "重新标注" links). The link renders as a Next.js `<Link>` to `/entities/extraction/{jobId}/ast`. Visibility condition: `job.status === "done" && isCMCReport(job)`. The CMC check requires the annotated document cache; to avoid fetching it for every row, add a lightweight `doc_class_iri` field to the `ExtractionJobResponse` schema (sourced from the annotation cache at job completion time).

**Rationale**: The spec requires the AST entry to appear only for CMCReport-classified, done jobs. The current job list already conditionally renders "查看标注" for jobs with `document_path`. Adding a similar conditional link follows the established pattern. The `doc_class_iri` field avoids N+1 fetches of annotation caches.

**Alternatives considered**: Fetching annotation cache per job on the list page (rejected — N+1); always showing the link and handling non-CMC on the AST page (rejected — spec explicitly says "入口不出现").

## R5. Frontend state management pattern

**Decision**: Use React Query (`useQuery` / `useMutation`) for all AST page data fetching, consistent with the existing frontend patterns in `lib/api.ts`. The coverage query key includes `jobId`; dismiss/undismiss mutations invalidate the coverage query to trigger auto-refresh.

**Rationale**: The existing codebase uses direct `fetch()` + `useState` in the extraction page, but React Query is listed as a primary dependency in the constitution and plan. For the AST page — which has multiple data sources (coverage, reports, document content) and mutation-driven refresh flows — React Query's cache invalidation provides cleaner auto-refresh semantics than manual `setState` + `useEffect` chains.

**Alternatives considered**: Manual fetch + state (viable but more boilerplate for the multi-source refresh flows).

## R6. SlotDismissal scope: per-job or per-template

**Decision**: Per-job. `SlotDismissal` is keyed by `(job_id, slot_id)`. Dismissing a slot for one job does not affect other jobs.

**Rationale**: Different CMC documents for different drug products may have different applicable slots. A slot that's "not applicable" for drug A might be essential for drug B. Per-job scoping also aligns with the existing `GeneratedReport` → `ExtractionJob` relationship.

**Alternatives considered**: Per-template global dismissal (rejected — would incorrectly suppress slots across unrelated jobs).
