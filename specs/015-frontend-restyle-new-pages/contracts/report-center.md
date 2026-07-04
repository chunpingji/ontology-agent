# UI Contract: Report Center (报告中心) + Report Detail (报告详情)

**Feature**: 015 | Routes: `/reports`, `/reports/[reportId]` | User Stories 5 & 6 | Clarify Q1 (unified center)

## Backend surface (existing — composed client-side, **no new endpoint**, research R2)

Documents `listDocuments` (`/api/entities?module=document`) · generated reports `listReports(jobId)` aggregated over `listExtractionJobs` · content `getAnnotatedDocument(jobId)` · download `downloadReportById` · linked entities `listExtractedFrom(docIri)`.

## Report Center behavior (FR-021–023)

1. **Given** reports+documents organized by category, **When** the page loads, **Then** a category tree (report types ∪ `DOC_TYPE_LABELS` ∪ `DEVELOPMENT_PHASES`) and an item list show each item's title, type, date, and size.
2. **Given** an item, **When** its actions open (DropdownMenu), **Then** preview, download, and (if authorized) delete are available (FR-022).
3. **Given** an upload affordance (button + drag-and-drop), **When** used, **Then** the document is added (existing doc-repo `upload` / entity ingestion) and appears in the list (FR-023).
4. **Given** an empty category, **When** opened, **Then** a defined empty state renders (FR-023).

## Report Detail behavior (FR-024–026)

1. **Given** a selected item, **When** Detail opens, **Then** a main reading pane renders — a document via tiptap (`getAnnotatedDocument`) or a generated report's preview + download — with a **breadcrumb** back to Report Center (FR-024).
2. **Given** a long item, **When** the outline is used, **Then** selecting an entry scrolls the reading pane to that section (tiptap headings / report sections) (FR-025).
3. **Given** a related-info panel, **When** opened, **Then** linked ontology entities/metadata (`listExtractedFrom` / item metadata) are shown (FR-026).
4. **Given** an open item, **When** download or share is chosen, **Then** the corresponding action is available (FR-026).

## Invariants

- **Presentation-only**: no new backend logic (clarify Q1, FR-027). Cross-job report aggregation is a bounded client fan-out; if bounded for responsiveness the UI shows a count / "load more" (no silent truncation, research R2).
- Full in-browser DOCX fidelity is **not** required — preview is best-effort; the authoritative file is always downloadable (FR-024).
- Delete/upload gated by role (FR-022).

## Verification

- Browse categories → open item preview/download → upload a document → view empty-category state (quickstart).
- Detail: outline navigation moves the pane; related-info shows linked entities; breadcrumb returns to center.
