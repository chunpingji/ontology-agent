# API Contract: AST Coverage & Report History

**Date**: 2026-07-01 | **Spec**: [spec.md](../spec.md)

## 1. GET /api/extraction/jobs/{job_id}/ast-coverage

Runs coverage validation (without generating a report) and returns the full AST tree with per-slot coverage status. Considers persisted `SlotDismissal` records.

### Request

```
GET /api/extraction/jobs/{job_id}/ast-coverage
Authorization: X-User / X-Role headers (same as extraction endpoints)
```

### Response 200

```json
{
  "template_id": "QS-A-020F05@v1",
  "total_slots": 18,
  "filled": 13,
  "inferred": 3,
  "missing_required": 1,
  "blank_optional": 0,
  "manual": 1,
  "dismissed": 0,
  "sections": [
    {
      "section_id": "section-1",
      "title": "SECTION I  风险评估",
      "groups": [
        {
          "group_id": "subject",
          "title": "1. 风险评估对象 Subject Description",
          "kind": "fields",
          "slots": [
            {
              "slot_id": "subject.name",
              "label": "产品名称",
              "status": "filled",
              "source_kind": "extraction",
              "value": "HRS-1234",
              "source_ref": "§1.1 产品基本信息",
              "rule_key": null,
              "hazid": null,
              "note": null
            }
          ]
        }
      ]
    }
  ]
}
```

### Response 404

```json
{"detail": "作业不存在"}
```

### Response 422

```json
{"detail": "文档未分类，无法生成覆盖预览"}
```

Or:

```json
{"detail": "仅支持 CMCReport 类型文档"}
```

### Notes

- Same CMCReport + edges guards as `POST /risk-report`.
- The `sections` array mirrors the AST template tree structure with `SlotCoverage` data merged into each slot position.
- Assessment table slots are expanded per-rule (e.g., `assessment.pre_control_level[R-RA1]`).

---

## 2. GET /api/extraction/jobs/{job_id}/reports

Lists all historical `GeneratedReport` records for the given job.

### Request

```
GET /api/extraction/jobs/{job_id}/reports
```

### Response 200

```json
[
  {
    "id": "uuid",
    "job_id": "uuid",
    "report_type": "risk_assessment",
    "file_path": "data/reports/uuid_20260701120000.docx",
    "file_size": 45678,
    "rules_fired_count": 3,
    "rules_summary": {
      "rows": [...],
      "coverage": {
        "template_id": "QS-A-020F05@v1",
        "total_slots": 18,
        "filled": 13,
        "slots": [...]
      }
    },
    "actor": "analyst01",
    "created_at": "2026-07-01T12:00:00Z"
  }
]
```

Sorted by `created_at` descending (newest first).

### Response 404

```json
{"detail": "作业不存在"}
```
