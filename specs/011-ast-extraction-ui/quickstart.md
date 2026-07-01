# Quickstart: AST 提取前端 UI

**Date**: 2026-07-01 | **Spec**: [spec.md](spec.md) | **Plan**: [plan.md](plan.md)

End-to-end validation scenarios for the 011 feature. Each scenario is independently runnable.

## Prerequisites

- Backend running (`uv run uvicorn app.main:app`)
- Frontend running (`npm run dev` in `frontend/`)
- PostgreSQL with migrations applied (`uv run alembic upgrade head`)
- At least one CMC .docx test document available
- At least one risk assessment rule configured in `ontology_decision_rules` (rule_group = "risk_assessment")

## Scenario 1: AST Page Load & Coverage Dashboard

**Validates**: US-1, US-2, FR-UI-001, FR-UI-002, FR-UI-003, FR-API-001, SC-001, SC-002

1. Upload a CMC .docx document on `/entities/extraction` and wait for extraction to complete (status = done).
2. In the job table, verify a "查看 AST" link appears for the completed CMCReport job.
3. Click "查看 AST" — browser navigates to `/entities/extraction/{jobId}/ast`.
4. **Verify**: Page loads in < 2 seconds (SC-001).
5. **Verify**: AST tree displays Section → Group → Slot hierarchy with color-coded status badges.
6. **Verify**: Coverage summary card shows correct counts matching the API response:
   ```bash
   curl -s http://localhost:8000/api/extraction/jobs/{JOB_ID}/ast-coverage | python -m json.tool
   ```
   Compare `total_slots`, `filled`, `inferred`, `missing_required`, `manual` counts.
7. **Verify**: Non-CMCReport jobs do NOT show "查看 AST" link (SC-006).

## Scenario 2: Slot Detail & Source Document Jump

**Validates**: US-3, FR-UI-004, FR-UI-005, SC-003

1. On the AST page, click a `filled` (green) slot with `source_kind=extraction`.
2. **Verify**: Detail panel shows slot_id, label, value, source_kind, source_ref.
3. Click the `source_ref` link in the detail panel.
4. **Verify**: Document preview (WordViewer) scrolls to and highlights the referenced section/table.
5. Click an `inferred` (blue) slot with `source_kind=rule`.
6. **Verify**: Detail panel shows rule_key, hazid, evaluation result.
7. Click a `manual` (yellow) slot.
8. **Verify**: Detail panel shows "预留手工填写" label.

## Scenario 3: Coverage Pre-check & Report Generation

**Validates**: US-4, FR-UI-006, FR-UI-007, SC-008

1. On the AST page with `missing_required > 0`, click "生成报告".
2. **Verify**: AlertDialog appears listing the missing slot IDs and labels.
3. Click "取消" — dialog closes, no report generated.
4. Click "生成报告" again, then "仍然生成" in the dialog.
5. **Verify**: Button shows "生成中..." while API call is in progress.
6. **Verify**: .docx file downloads with correct naming (`风险评估表_{source_name}.docx`).
7. **Verify**: Coverage summary and report history list refresh after generation.

## Scenario 4: Slot Dismissal & Auto-Refresh

**Validates**: US-6, FR-API-003, FR-API-004, FR-API-005, FR-API-006, SC-005

1. On the AST page, find a `missing_required` (red) slot.
2. Click the slot, then click "标记为不适用" in the action bar.
3. **Verify**: Slot status changes to `dismissed` (strikethrough + gray).
4. **Verify**: Coverage summary card updates — `missing_required` count decreases by 1, `dismissed` count increases by 1.
5. Refresh the page (F5).
6. **Verify**: Dismissed state persists (SC-005).
7. Click the dismissed slot, then click "撤销标记".
8. **Verify**: Slot restores to `missing_required` status, counts update.
9. **Verify audit trail**:
   ```bash
   curl -s "http://localhost:8000/api/audit?action=slot.dismiss" | python -m json.tool
   curl -s "http://localhost:8000/api/audit?action=slot.undismiss" | python -m json.tool
   ```
   Both entries present with correct actor, job_id, slot_id.

## Scenario 5: Report History

**Validates**: US-5, FR-UI-008, FR-API-002, SC-004

1. Generate 2+ reports for the same job (via Scenario 3).
2. On the AST page, scroll to the report history section.
3. **Verify**: All generated reports appear, newest first.
4. **Verify**: Each row shows: timestamp (to second), actor, rules_fired_count, coverage summary.
5. Click "下载" on a historical report.
6. **Verify**: Correct .docx file downloads.
7. Click "查看覆盖" on a historical report.
8. **Verify**: Expands to show the coverage snapshot from that generation (from `rules_summary.coverage`).

## Scenario 6: Dismissed Slots in Generated Report

**Validates**: Edge Case (dismissed slot rendering), FR-API-006

1. Dismiss a `missing_required` slot (via Scenario 4 step 2).
2. Generate a report.
3. Open the downloaded .docx.
4. **Verify**: The dismissed slot's cell contains "N/A（不适用）", NOT "⚠ 待评估（数据缺失）".

## Scenario 7: Offline Verification

**Validates**: SC-007, Constitution VI

1. Disconnect the machine from the network.
2. Navigate to the AST page.
3. **Verify**: All UI components render correctly (no broken icons, fonts, or CDN resources).
4. Perform dismiss/undismiss, generate report.
5. **Verify**: All operations succeed without network dependency.
