# Research: Risk Assessment Report Generation

**Date**: 2026-06-29 | **Feature**: 010-risk-report-generation

## R1: DecisionRule Schema Alignment

**Decision**: Encode risk assessment rule metadata in the existing `consequent` JSON dict rather than adding new DB columns.

**Rationale**: The existing `OntologyDecisionRule` model stores `antecedent` (pattern AST) and `consequent` (JSON dict). The design doc's risk assessment rules need additional fields (`category`, `control_measure`, `traceability_docs`, `postconditions`) not present in the current schema. Since `consequent` is already a freeform JSON column, these fields fit naturally as keys within it. This avoids an Alembic migration on `ontology_decision_rule` and keeps the generic rule infrastructure unchanged.

**Alternatives considered**:
- Add new columns to `OntologyDecisionRule`: Rejected — would couple the generic rule infrastructure to a domain-specific report format; future rule groups would need different columns.
- Create a separate `RiskAssessmentRule` table: Rejected — violates Principle V (minimal complexity & reuse); the existing rule infrastructure already has CRUD, versioning, and publishing.

**Consequent schema for risk_assessment rules**:
```json
{
  "risk_level": "HighRisk",
  "category": "生产设备",
  "description": "642/646 车间生产使用的设备...",
  "control_measure": "1、生产使用的设备均按照...",
  "traceability_docs": "1、批记录中记录设备...",
  "postconditions": {"equipment_qualified": true, "shared_line_assessed": true}
}
```

## R2: Rule Group Extension

**Decision**: Add `"risk_assessment"` to `RULE_GROUPS` tuple in `ontology_meta.py`.

**Rationale**: The existing groups are `("equipment_dedication", "scenario_identification", "contamination_risk")` — all from the 006 declarative rule layer for cross-contamination assessment. Risk assessment reporting is a distinct domain (QS-A-020F05 HazID dimensions). Adding it to the tuple is a one-line change; the frontend `DECISION_RULE_GROUPS` array and `GROUP_LABEL` map need corresponding updates.

**Alternatives considered**:
- Hardcode risk assessment rules in Python (like `defaults.py`): Rejected — spec says rules are a configuration concern; should be manageable via existing DecisionRule CRUD UI.

## R3: Report File Storage Strategy

**Decision**: Store generated .docx files in the existing `data/reports/` directory (alongside uploads in `data/uploads/`), referenced by a new `generated_reports` DB table.

**Rationale**: The project already uses file-system storage for uploaded documents (`ExtractionJob.document_path` → `data/uploads/`). Using the same pattern for generated reports keeps the architecture consistent. The DB record stores the path, not the file content (avoids blob-in-DB anti-pattern). File naming uses `{job_id}_{timestamp}.docx` for uniqueness.

**Alternatives considered**:
- Store .docx as a BLOB in PostgreSQL: Rejected — project convention is file-system storage; BLOBs complicate backups and add DB size.
- Store in a temp directory: Rejected — FR-013 requires persistence for later retrieval.

## R4: Bridge Layer — Field Mapping from Edges to Facts

**Decision**: The bridge layer (`fact_bridge.py`) maps edge fields to `Facts` fields using the existing field names from `interpreter.py`.

**Rationale**: The `Facts` dataclass expects `relations` (object property → class IRIs), `data_values` (data property IRI → value), `scalars` (label → value), `drug_classes`, and `alignments`. The edge dict from `extract_relationships()` provides `predicate_iri`, `object_class_iri`, and `object_data_properties` — a direct mapping. The bridge converts IRI keys to short-names by stripping the namespace prefix, since `evaluate()` uses short-name lookups.

**Key mapping**:
| Edge field | Facts field | Transform |
|---|---|---|
| `predicate_iri` | `relations[short_name]` | Strip namespace, append `object_class_iri` |
| `object_data_properties[].iri` | `data_values[short_name]` | Strip namespace |
| `object_data_properties[].label` | `scalars[label]` | Direct |
| DrugProduct class markers | `drug_classes` | Scan for 分类/类别 labels |

## R5: Postcondition Injection for Post-Control Risk

**Decision**: `_apply_postconditions()` creates a shallow copy of `Facts` with postcondition keys injected into `scalars` (for boolean/literal predicates) and a sentinel class added to `relations` (for `some_values_from` predicates).

**Rationale**: The interpreter's `evaluate()` is a pure function over `Facts` — it doesn't mutate state. The postconditions dict from the rule's consequent contains boolean flags like `{"training_completed": true}`. Injecting these as scalars makes them available to `literal_eq`/`boolean_has_value` predicates. For the pre-control evaluation, these keys are absent → UNKNOWN → the risk-triggering pattern fires. For post-control, the keys are present and true → the pattern's negation/absence condition holds → risk drops.

**Alternatives considered**:
- Modify the rule pattern itself for post-control: Rejected — would require two copies of each rule (one for pre, one for post), doubling configuration burden.

## R6: python-docx Availability

**Decision**: Confirmed — python-docx is already a project dependency.

**Rationale**: `pyproject.toml` includes `python-docx` in the main dependency group (used by `docx_structure.py` for parsing). No new dependency needed for rendering.
