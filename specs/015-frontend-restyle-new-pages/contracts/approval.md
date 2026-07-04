# UI Contract: Approval (审批工作台)

**Feature**: 015 | Route: `/approval` (supersedes `/approvals`) | User Story 4 | research R3

## Backend surface (existing compliance workflow — no new endpoint)

Queue `getPendingSignatures` (`/api/compliance/signatures/pending`) · approve `signConclusion` (`/api/compliance/signatures`) · reject `rejectConclusion` (`/api/compliance/reject`) · timeline `getComplianceAudit` (`/api/compliance/audit`) · detail `getConclusionTrace`. Reuse existing `QaSignatureDialog` / `RejectDialog` logic, re-skinned.

## Behavior (FR-017–020) — three-pane: queue / detail / timeline

1. **Given** pending approvals, **When** the page loads, **Then** a queue (`getPendingSignatures`) renders, grouped/filterable by type (`execution_type`) and status/`risk_level` (FR-017).
2. **Given** a selected queue item, **When** it loads, **Then** the detail pane shows subject, applicant, submission time, and attachments where the backend provides them (conclusion fields + `getConclusionTrace`); absent fields are omitted, not fabricated (FR-018).
3. **Given** an authorized reviewer, **When** they approve (`signConclusion`) or reject with a reason (`rejectConclusion`), **Then** the decision records through the existing compliance audit chain and the timeline updates (FR-019).
4. **Given** the selected item, **When** viewed, **Then** its approval history/timeline (`getComplianceAudit` for that entity) is shown (FR-020).
5. **Given** an item the user is not authorized to decide, **When** viewed, **Then** decision controls are unavailable (FR-006; QA-gated sign as today).

## Invariants

- Part-11 e-signature + rejection semantics unchanged; audit chain append-only/read-only (Constitution III).
- Rejection requires a non-empty reason (existing dialog contract).
- Role gating identical to current `/approvals` (QA to sign).

## States

Loading (Skeleton) for queue/detail/timeline; empty ("无待审批项"); error (Alert); post-decision timeline refresh.

## Verification

- Filter queue → select item → read detail + attachments → approve or reject-with-reason → timeline reflects outcome (quickstart).
- Unauthorized role sees disabled decision controls; reach decision controls in <30s (SC-005).
