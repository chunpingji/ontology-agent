# API Contract: Slot Dismissal

**Date**: 2026-07-01 | **Spec**: [spec.md](../spec.md)

## 1. POST /api/extraction/jobs/{job_id}/ast-coverage/dismiss

Mark a slot as "不适用" (not applicable). Persists a `SlotDismissal` record, logs an audit entry, and returns the updated `ASTCoverageResponse`.

### Request

```
POST /api/extraction/jobs/{job_id}/ast-coverage/dismiss
Content-Type: application/json
```

```json
{
  "slot_id": "subject.pde"
}
```

### Response 200

Returns the full updated `ASTCoverageResponse` (same shape as `GET /ast-coverage`) with the dismissed slot now showing `status: "dismissed"`. This enables the frontend to refresh coverage counts and tree state from a single response without a second request.

### Response 404

```json
{"detail": "作业不存在"}
```

### Response 409

```json
{"detail": "该槽位已标记为不适用"}
```

### Response 422

```json
{"detail": "文档未分类，无法操作覆盖"}
```

### Audit Log Entry

```json
{
  "action": "slot.dismiss",
  "actor": "analyst01",
  "entity_iri": "job_id",
  "details": {
    "slot_id": "subject.pde",
    "job_id": "uuid"
  }
}
```

---

## 2. DELETE /api/extraction/jobs/{job_id}/ast-coverage/dismiss/{slot_id}

Undo a previous dismissal. Deletes the `SlotDismissal` record, logs an audit entry, and returns the updated `ASTCoverageResponse`.

### Request

```
DELETE /api/extraction/jobs/{job_id}/ast-coverage/dismiss/{slot_id}
```

`slot_id` is URL-encoded if it contains special characters (e.g., `assessment.pre_control_level%5BR-RA2%5D`).

### Response 200

Returns the full updated `ASTCoverageResponse` with the slot restored to its computed status (typically `missing_required`).

### Response 404

```json
{"detail": "该槽位未被标记为不适用"}
```

Or:

```json
{"detail": "作业不存在"}
```

### Audit Log Entry

```json
{
  "action": "slot.undismiss",
  "actor": "analyst01",
  "entity_iri": "job_id",
  "details": {
    "slot_id": "subject.pde",
    "job_id": "uuid"
  }
}
```
