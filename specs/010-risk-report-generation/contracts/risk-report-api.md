# Contract: Risk Report API

**Date**: 2026-06-29 | **Feature**: 010-risk-report-generation

## §1 Generate Risk Report

### POST /api/extraction/jobs/{job_id}/risk-report

Generate and persist a risk assessment report for the given extraction job.

**Authorization**: Any authenticated user who can view the extraction job (FR-011).

**Path parameters**:
| Param | Type | Description |
|-------|------|-------------|
| `job_id` | UUID | Extraction job identifier |

**Request body**: None required.

**Success response**: `200 OK`
- `Content-Type: application/vnd.openxmlformats-officedocument.wordprocessingml.document`
- `Content-Disposition: attachment; filename="风险评估表_{source_filename}.docx"`
- Body: .docx file bytes

**Error responses**:
| Status | Condition | Body |
|--------|-----------|------|
| 404 | Job not found | `{"detail": "作业不存在"}` |
| 422 | Job has no doc_class | `{"detail": "文档未分类，无法生成风险评估报告"}` |
| 422 | doc_class is not CMCReport | `{"detail": "仅支持 CMCReport 类型文档生成风险评估报告"}` |
| 422 | No relationships extracted | `{"detail": "未检测到关系数据，无法生成风险评估报告"}` |

**Side effects**:
1. Generated .docx file persisted at `data/reports/{job_id}_{timestamp}.docx`
2. `generated_reports` row inserted (job_id, file_path, actor, rules_fired_count)
3. Audit log entry appended: action=`"report.generate"`, actor=X-User, entity_iri=job_id, details={rules_fired_count, report_id}

## §2 Retrieve Generated Report

### GET /api/extraction/jobs/{job_id}/risk-report

Retrieve the most recently generated risk assessment report for the given job.

**Authorization**: Any authenticated user who can view the extraction job.

**Path parameters**:
| Param | Type | Description |
|-------|------|-------------|
| `job_id` | UUID | Extraction job identifier |

**Success response**: `200 OK`
- `Content-Type: application/vnd.openxmlformats-officedocument.wordprocessingml.document`
- `Content-Disposition: attachment; filename="风险评估表_{source_filename}.docx"`
- Body: .docx file bytes (from persisted file)

**Error responses**:
| Status | Condition | Body |
|--------|-----------|------|
| 404 | Job not found | `{"detail": "作业不存在"}` |
| 404 | No report generated yet | `{"detail": "该作业尚未生成风险评估报告"}` |

## §3 Frontend Integration

### ExtractionDrawer Button

**Visibility conditions** (all must be true):
- `doc` is loaded (not null)
- `!rerunning` (not re-annotating)
- `doc.doc_class?.doc_class_iri` contains `"CMCReport"`
- `doc.relationships?.length > 0`

**Click handler**:
1. Set `reportGenerating = true`, disable button, show "生成中..."
2. `POST /api/extraction/jobs/{jobId}/risk-report` with identity headers
3. On success: receive blob → create object URL → trigger download → revoke URL
4. On error: show error message in ExtractionDrawer error area
5. Set `reportGenerating = false`

**Download filename**: `风险评估表_{doc.filename without .docx extension}.docx`

## §4 Audit Record Schema

```json
{
  "action": "report.generate",
  "actor": "<X-User>",
  "entity_iri": "<job_id as string>",
  "details": {
    "report_id": "<generated_report UUID>",
    "rules_fired_count": 5,
    "report_type": "risk_assessment"
  }
}
```
