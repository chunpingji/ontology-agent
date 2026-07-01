# Quickstart: Risk Assessment Report Generation

**Date**: 2026-06-29 | **Feature**: 010-risk-report-generation

## Prerequisites

- Backend running: `cd backend && uv run uvicorn app.main:app --reload`
- Frontend running: `cd frontend && npm run dev`
- PostgreSQL with migrations applied: `cd backend && uv run alembic upgrade head`
- At least one CMC document uploaded and extracted (e.g., `原料药 HRS-1234 临床备样生产信息.docx`)
- Risk assessment DecisionRules seeded in `ontology_decision_rule` table (rule_group = `"risk_assessment"`)

## Scenario 1: End-to-End Report Generation (UI)

**Validates**: US1 (report generation), US2 (button visibility), US4 (download)

1. Open the extraction page in the browser
2. Click on a completed CMCReport extraction job to open `ExtractionDrawer`
3. Verify the status bar shows a "生成风险评估报告" button (next to "重新标注")
4. Click "生成风险评估报告"
5. **Expected**: Button shows "生成中...", then browser downloads `风险评估表_{filename}.docx`
6. Open the downloaded file in Word/LibreOffice
7. **Expected**: Document contains:
   - SECTION I with subject description and equipment tables by workshop
   - Assessment table with 5 HazID rows (人员/生产设备/物料管理/文件/三废处理)
   - Pre-control and post-control risk levels per row
   - Control measures and traceability references
   - SECTION II placeholders

## Scenario 2: Button Visibility Guards (UI)

**Validates**: US2 (conditional visibility), clarification Q4 (CMCReport only)

1. Open ExtractionDrawer for a job with no `doc_class` → **Expected**: No report button visible
2. Open ExtractionDrawer for a CMCReport job with zero relationships → **Expected**: No button
3. Open ExtractionDrawer for a non-CMCReport classified job → **Expected**: No button
4. Open ExtractionDrawer for a CMCReport job with relationships → **Expected**: Button visible
5. Click "重新标注" → **Expected**: Report button disappears during re-annotation

## Scenario 3: API Direct Validation

**Validates**: US5 (error handling), FR-006, FR-014

```bash
# Generate report (should return .docx file)
curl -X POST http://localhost:8000/api/extraction/jobs/{JOB_ID}/risk-report \
  -H "X-User: testuser" -H "X-Role: senior_analyst" \
  -o test_report.docx

# Verify file is valid
file test_report.docx
# Expected: Microsoft Word 2007+

# Retrieve previously generated report
curl http://localhost:8000/api/extraction/jobs/{JOB_ID}/risk-report \
  -H "X-User: testuser" -H "X-Role: senior_analyst" \
  -o retrieved_report.docx

# Verify same content
diff <(md5sum test_report.docx) <(md5sum retrieved_report.docx)
# Note: files may differ if retrieved_report is from a different generation

# Error case: non-CMCReport job
curl -X POST http://localhost:8000/api/extraction/jobs/{NON_CMC_JOB_ID}/risk-report \
  -H "X-User: testuser" -H "X-Role: operator"
# Expected: 422 {"detail": "仅支持 CMCReport 类型文档生成风险评估报告"}
```

## Scenario 4: Bridge Layer Unit Test

**Validates**: US3 (rule evaluation), FR-001, FR-002, FR-003

```bash
cd backend
uv run pytest tests/test_reporting/test_fact_bridge.py -v
```

**Expected**: All tests pass, covering:
- `edges_to_facts()` correctly maps equipment edges to `relations`
- `edges_to_facts()` correctly extracts PDE from data properties into `scalars`
- Drug class extraction from DrugProduct edges
- Empty edges produce empty Facts (no crash)

## Scenario 5: Risk Report Generator Unit Test

**Validates**: US3 (pre/post control levels), FR-002, FR-003, FR-004

```bash
cd backend
uv run pytest tests/test_reporting/test_risk_report_generator.py -v
```

**Expected**: All tests pass, covering:
- Pre-control level evaluation for each HazID dimension
- Post-control level drops to "低" after postconditions applied
- Equipment grouping by workshop
- Subject description assembly from DrugProduct edges

## Scenario 6: Audit Trail Verification

**Validates**: FR-012, SC-007, clarification Q2

1. Generate a report via API (Scenario 3)
2. Check audit log:
```bash
curl http://localhost:8000/api/compliance/audit/verify \
  -H "X-User: testuser" -H "X-Role: qa"
# Expected: {"ok": true, ...}
```
3. Verify the last audit entry has action=`"report.generate"` with the correct job_id and actor

## Pass Criteria

| Scenario | Criterion |
|----------|-----------|
| 1 | .docx downloads successfully, opens in Word, contains all sections |
| 2 | Button shows/hides correctly for all 5 conditions |
| 3 | POST returns .docx; GET retrieves it; error cases return 422 |
| 4 | All bridge layer unit tests pass |
| 5 | All generator unit tests pass |
| 6 | Audit chain intact, report.generate entry present |
