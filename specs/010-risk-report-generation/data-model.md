# Data Model: Risk Assessment Report Generation

**Date**: 2026-06-29 | **Feature**: 010-risk-report-generation

## §1 New Entities

### 1.1 GeneratedReport (DB table: `generated_reports`)

Persisted record of a generated risk assessment report, enabling later retrieval and audit.

| Field | Type | Constraints | Description |
|-------|------|-------------|-------------|
| `id` | UUID | PK, default uuid4 | Unique report identifier |
| `job_id` | UUID | FK → `extraction_jobs.id`, NOT NULL, indexed | Source extraction job |
| `report_type` | String(50) | NOT NULL, default `"risk_assessment"` | Report type discriminator |
| `file_path` | String(500) | NOT NULL | Server-side path to the .docx file |
| `file_size` | Integer | NULL | File size in bytes |
| `rules_fired_count` | Integer | NOT NULL, default 0 | Number of DecisionRules that fired |
| `rules_summary` | JSON | NULL | Summary of fired rules (keys + risk levels) |
| `actor` | String(100) | NOT NULL | User who triggered generation |
| `created_at` | DateTime(tz) | NOT NULL, default utcnow | Generation timestamp |

**Relationships**: Many-to-one with `ExtractionJob` (a job can have multiple generated reports over time).

**Lifecycle**: Append-only. Reports are never updated or deleted — a new generation creates a new row.

### 1.2 RiskReport (in-memory dataclass, not persisted)

Structured representation of a QS-A-020F05 risk assessment, built from extraction edges and rule evaluation. Used as the intermediate format between the generator and the renderer.

| Field | Type | Description |
|-------|------|-------------|
| `doc_no` | str | Document number (default "QS-A-020F05") |
| `revision` | str | Revision number |
| `effective_date` | str | Effective date |
| `subject_description` | str | SECTION I — risk assessment subject description |
| `equipment_tables` | dict[str, list[EquipmentEntry]] | Equipment by workshop |
| `equipment_notes` | list[str] | Equipment footnotes |
| `team_members` | list[dict] | Assessment team (placeholder) |
| `assessment_rows` | list[RiskRow] | Assessment table rows |
| `qa_comments` | str | QA comments (placeholder) |
| `approvers` | list[dict] | Approval chain (placeholder) |
| `risk_review` | str | SECTION II — risk review (placeholder) |
| `conclusion` | str | Conclusion (placeholder) |

### 1.3 RiskRow (in-memory dataclass)

Single row in the Assessment evaluation table.

| Field | Type | Description |
|-------|------|-------------|
| `hazid` | str | HazID dimension (人员/生产设备/物料管理/文件/三废处理) |
| `contributing_factors` | str | Risk factor description |
| `pre_control_level` | str | Pre-control risk level (高/中/低) |
| `post_control_level` | str | Post-control risk level |
| `control_measures` | str | Risk control measures |
| `traceability` | str | Control measure traceability references |
| `status` | str | Risk status (可以接受/不可接受) |

### 1.4 EquipmentEntry (in-memory dataclass)

Single row in an equipment table.

| Field | Type | Description |
|-------|------|-------------|
| `seq` | int | Sequential number |
| `equipment_id` | str | Equipment ID (e.g., "RE64202") |
| `name` | str | Equipment name |
| `spec` | str | Specification |
| `material` | str | Material |

## §2 Modified Entities

### 2.1 RULE_GROUPS Extension

Add `"risk_assessment"` to `RULE_GROUPS` in `backend/app/models/ontology_meta.py`:

```python
RULE_GROUPS = ("equipment_dedication", "scenario_identification", "contamination_risk", "risk_assessment")
```

Frontend `DECISION_RULE_GROUPS` in `frontend/src/lib/api.ts` must add the corresponding type and label.

### 2.2 Risk Assessment Rule Consequent Schema

Rules in the `"risk_assessment"` group use an extended `consequent` dict:

```json
{
  "risk_level": "HighRisk | MediumRisk | LowRisk",
  "category": "人员 | 生产设备 | 物料管理 | 文件 | 三废处理",
  "description": "Contributing factors text",
  "control_measure": "Control measures text",
  "traceability_docs": "Traceability references text",
  "postconditions": {"key": "value"}
}
```

This is purely a data convention — no schema migration needed for `ontology_decision_rule`.

## §3 Validation Rules

- `GeneratedReport.job_id` must reference an existing `ExtractionJob`
- `GeneratedReport.file_path` must point to a readable file on disk
- `GeneratedReport.actor` must be a non-empty string (from `X-User` header)
- Risk assessment rules: `consequent.category` must be one of the 5 HazID dimensions
- Risk assessment rules: `consequent.risk_level` must be one of `HighRisk`, `MediumRisk`, `LowRisk`

## §4 Migration

One Alembic migration: `add_generated_reports` — creates the `generated_reports` table. No existing tables are modified (RULE_GROUPS is a Python constant, not a DB constraint).
