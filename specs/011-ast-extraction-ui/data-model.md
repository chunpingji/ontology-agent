# Data Model: AST 提取前端 UI

**Date**: 2026-07-01 | **Spec**: [spec.md](spec.md) | **Plan**: [plan.md](plan.md)

## 1. New Entity: SlotDismissal

Persists a user's "标记为不适用" decision on a specific slot within a specific extraction job.

### 1.1 Schema

| Field | Type | Constraints | Notes |
|-------|------|-------------|-------|
| `id` | UUID | PK, auto | |
| `job_id` | UUID | FK → `extraction_jobs.id` ON DELETE CASCADE, NOT NULL, indexed | |
| `slot_id` | VARCHAR(200) | NOT NULL | AST slot identifier, e.g. `subject.pde` or `assessment.pre_control_level[R-RA2]` |
| `dismissed_by` | VARCHAR(100) | NOT NULL | Actor username from identity |
| `dismissed_at` | TIMESTAMP WITH TZ | NOT NULL, default=now() | |

**Unique constraint**: `(job_id, slot_id)` — a slot can only be dismissed once per job.

**Table name**: `slot_dismissals`

### 1.2 Lifecycle

```
                   dismiss API
    (not exists) ──────────────► SlotDismissal row created
                                     │
                               undismiss API
                                     │
                                     ▼
                              SlotDismissal row DELETED
```

No soft-delete — undismiss physically removes the row. The audit log (`audit_log` table) provides the historical trail per Constitution III.

### 1.3 SQLAlchemy Model

```python
class SlotDismissal(Base):
    __tablename__ = "slot_dismissals"
    __table_args__ = (
        UniqueConstraint("job_id", "slot_id", name="uq_slot_dismissal_job_slot"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("extraction_jobs.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    slot_id: Mapped[str] = mapped_column(String(200), nullable=False)
    dismissed_by: Mapped[str] = mapped_column(String(100), nullable=False)
    dismissed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
```

## 2. Extended Entity: CoverageManifest (in-memory, no DB change)

### 2.1 New status constant

Add `DISMISSED = "dismissed"` to `coverage_validator.py` alongside `FILLED`, `INFERRED`, etc.

### 2.2 Extended `validate_coverage` signature

```python
def validate_coverage(
    template: ReportTemplate,
    edges: Sequence[dict],
    rules: Sequence[Any],
    facts: Any | None = None,
    dismissed_slot_ids: set[str] | None = None,  # NEW
) -> CoverageManifest:
```

When `dismissed_slot_ids` is provided and a slot resolves to `missing_required`, check if `slot.slot_id` is in the set. If so, emit `status="dismissed"` instead. Assessment table slots with instance keys like `assessment.pre_control_level[R-RA2]` are checked by base slot_id (`assessment.pre_control_level`) against the dismissed set.

### 2.3 New counter property

```python
@property
def dismissed(self) -> int:
    return self._counts[DISMISSED]
```

## 3. New Pydantic Schemas

### 3.1 Coverage Response (for `GET /ast-coverage`)

```python
class SlotCoverageResponse(BaseModel):
    slot_id: str
    label: str
    status: str  # filled | inferred | missing_required | blank_optional | manual | dismissed
    source_kind: str
    value: str | None = None
    source_ref: str | None = None
    rule_key: str | None = None
    hazid: str | None = None
    note: str | None = None

class GroupCoverageResponse(BaseModel):
    group_id: str
    title: str
    kind: str  # fields | manual | equipment_table | assessment_table
    slots: list[SlotCoverageResponse]

class SectionCoverageResponse(BaseModel):
    section_id: str
    title: str
    groups: list[GroupCoverageResponse]

class ASTCoverageResponse(BaseModel):
    template_id: str
    total_slots: int
    filled: int
    inferred: int
    missing_required: int
    blank_optional: int
    manual: int
    dismissed: int
    sections: list[SectionCoverageResponse]
```

### 3.2 Dismissal Request/Response

```python
class SlotDismissRequest(BaseModel):
    slot_id: str

class SlotDismissalResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    job_id: UUID
    slot_id: str
    dismissed_by: str
    dismissed_at: datetime
```

## 4. Existing Entities (unchanged)

- **ExtractionJob**: No schema change. The `doc_class_iri` for AST entry visibility is read from the annotation cache (no column added — avoids migration for a UI convenience).
- **GeneratedReport**: No change. `rules_summary.coverage` already stores the full manifest snapshot.
- **ReportTemplate / Slot / CoverageManifest**: No structural changes beyond the `dismissed_slot_ids` parameter and `DISMISSED` constant.
