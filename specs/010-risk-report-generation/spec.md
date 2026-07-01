# Feature Specification: Risk Assessment Report Generation

**Feature Branch**: `010-risk-report-generation`

**Created**: 2026-06-29

**Status**: Draft

**Input**: User description: "从文档抽取到结构化报告的技术实现——在对原料药 CMC 文档提取关系图谱后，基于声明式规则自动生成符合 QS-A-020F05 格式的结构化风险评估报告"

## Clarifications

### Session 2026-06-29

- Q: Should report generation be restricted to specific roles? → A: Available to any role that can view the extraction job (same permission as viewing extraction results).
- Q: Should the system log an audit record when a report is generated? → A: Yes — log audit record on each generation (actor, job_id, timestamp, rules fired count).
- Q: Should the generated .docx be persisted server-side or only streamed ephemerally? → A: Persist .docx server-side (retrievable via API) and also stream to browser on generation.
- Q: Is report generation scoped to CMCReport only or any classified document type? → A: CMCReport only — button hidden for other document types (explicit type guard).

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Generate Risk Assessment Report from Reviewed Extraction (Priority: P1)

As a QA/regulatory analyst, after reviewing the relation graph extracted from a CMC document (e.g., 原料药 HRS-1234 临床备样生产信息.docx), I want to generate a structured risk assessment report (QS-A-020F05 format) so that I can proceed with compliance archival and sign-off without manually compiling HazID tables.

**Why this priority**: This is the core value delivery — transforming verified extraction results into a regulatory-compliant document that would otherwise require hours of manual compilation across five HazID dimensions.

**Independent Test**: Can be fully tested by uploading a CMC document, completing extraction, reviewing the relation panel, clicking "生成风险评估报告", and receiving a valid .docx file with correct equipment tables and assessment rows.

**Acceptance Scenarios**:

1. **Given** a CMC document has been extracted with relationships (DrugProduct, Equipment, SynthesisRoute, SafetyRisk, SharedLineAssessmentData), **When** the user clicks "生成风险评估报告" in the ExtractionDrawer status bar, **Then** a .docx file is downloaded containing SECTION I (subject description, equipment tables by workshop), Assessment table (5 HazID rows with pre/post risk levels), and SECTION II placeholders.
2. **Given** the document has 26 equipment edges split across two workshops (642 and 646), **When** the report is generated, **Then** the equipment tables correctly group entries by workshop with sequential numbering, equipment ID, name, spec, and material columns.
3. **Given** the extraction includes SharedLineAssessmentData with PDE = 1.80mg (non-high-activity), **When** risk levels are computed, **Then** the pre-control level for 生产设备 is "高" (due to shared-line presence) and post-control level is "低" (after control measures applied).

---

### User Story 2 - Conditional Button Visibility (Priority: P1)

As a user navigating the ExtractionDrawer, I should only see the "生成风险评估报告" button when the document has been classified as CMCReport and relationships have been extracted, so that I cannot generate a report prematurely from incomplete data or for irrelevant document types.

**Why this priority**: Prevents users from generating invalid/empty reports, which would erode trust in the system and could enter compliance archives incorrectly.

**Independent Test**: Can be tested by opening ExtractionDrawer for documents at different extraction stages and verifying button visibility.

**Acceptance Scenarios**:

1. **Given** a document is loaded but has no doc_class assigned, **When** the ExtractionDrawer opens, **Then** the "生成风险评估报告" button is not visible.
2. **Given** a document is classified as CMCReport but has zero extracted relationships, **When** the ExtractionDrawer opens, **Then** the button is not visible.
3. **Given** a document is classified as CMCReport and has at least one relationship edge, **When** the ExtractionDrawer opens, **Then** the button is visible and enabled.
5. **Given** a document is classified as a non-CMCReport type (e.g., ValidationProtocol), **When** the ExtractionDrawer opens, **Then** the "生成风险评估报告" button is not visible regardless of relationship count.
4. **Given** a re-annotation is in progress (rerunning = true), **When** the user views the status bar, **Then** the button is hidden until re-annotation completes.

---

### User Story 3 - Risk Rule Evaluation with Pre/Post Control Levels (Priority: P1)

As the system, when generating a report I must evaluate each HazID dimension's risk level before and after control measures, using the existing DecisionRule engine, so that the assessment table reflects deterministic, auditable reasoning.

**Why this priority**: The assessment table is the regulatory core of the report — incorrect risk levels could have compliance consequences.

**Independent Test**: Can be tested by providing known edges/facts to the bridge layer and verifying that evaluate() produces correct pre-control and post-control levels for each rule.

**Acceptance Scenarios**:

1. **Given** edges contain SharedLineAssessmentData and Equipment entries, **When** the "hazid_equipment_shared_line" rule evaluates against the original Facts, **Then** pre-control level = "高".
2. **Given** the same rule with postconditions {equipment_qualified: true, shared_line_assessed: true, cleaning_validated: true} applied, **When** re-evaluated, **Then** post-control level = "低".
3. **Given** edges indicate a non-cytotoxic, non-high-activity compound (PDE > 10μg), **When** the "hazid_waste_treatment" rule evaluates, **Then** pre-control level = "低" and status = "可以接受".

---

### User Story 4 - Report Download as .docx (Priority: P2)

As a user, when I click "生成风险评估报告", I want the report to download as a .docx file named after the source document, so I can immediately archive it or circulate for sign-off.

**Why this priority**: The downstream consumption of risk reports is print/sign/archive — a .docx file matches the existing compliance workflow.

**Independent Test**: Can be tested by clicking the button and verifying browser download behavior, file naming, and that the file opens correctly in Word.

**Acceptance Scenarios**:

1. **Given** the source document is "原料药 HRS-1234 临床备样生产信息.docx", **When** report generation succeeds, **Then** the downloaded file is named "风险评估表_原料药 HRS-1234 临床备样生产信息.docx".
2. **Given** the user clicks the button, **When** the backend returns successfully, **Then** the browser downloads the file without requiring a second click or navigation.
3. **Given** the user clicks the button, **When** generation is in progress, **Then** the button shows "生成中..." and is disabled until complete.

---

### User Story 5 - Error Handling for Incomplete Data (Priority: P2)

As a user, if I somehow trigger report generation when prerequisites are not met (e.g., API called directly), I want a clear error message explaining what is missing.

**Why this priority**: Robustness for edge cases and API consumers beyond the UI.

**Independent Test**: Can be tested by calling the API endpoint directly with missing doc_class or empty relationships.

**Acceptance Scenarios**:

1. **Given** a job whose document has no doc_class, **When** POST /api/extraction/jobs/{job_id}/risk-report is called, **Then** HTTP 422 is returned with message "文档未分类，无法生成风险评估报告".
2. **Given** a job with zero extracted relationships, **When** the endpoint is called, **Then** HTTP 422 is returned with message "未检测到关系数据，无法生成风险评估报告".

---

### Edge Cases

- What happens when a DecisionRule references a property not present in the extracted edges? The system evaluates to UNKNOWN (open world) and maps to "低" risk (absence of evidence is not evidence of risk).
- What happens when equipment edges lack workshop grouping information? All equipment entries appear in a single ungrouped table with a note indicating the workshop could not be determined.
- What happens when the document is classified but not as CMCReport? The button is not displayed — report generation is scoped exclusively to CMCReport-classified documents. If the API endpoint is called directly for a non-CMCReport job, HTTP 422 is returned with message "仅支持 CMCReport 类型文档生成风险评估报告".

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST provide a bridge layer that converts relationship extraction edges into a Facts object consumable by the existing reasoning engine, without persisting data to the database.
- **FR-002**: System MUST evaluate all DecisionRules in the "risk_assessment" group against the bridged Facts to determine pre-control risk levels for each HazID dimension.
- **FR-003**: System MUST apply each rule's postconditions to produce augmented Facts and re-evaluate to determine post-control risk levels.
- **FR-004**: System MUST map evaluation results to a structured report conforming to QS-A-020F05 format with: SECTION I (subject description, equipment tables by workshop, team member placeholders), Assessment table (HazID, contributing factors, pre/post levels, control measures, traceability, status), and SECTION II (placeholders for risk review and conclusion).
- **FR-005**: System MUST render the structured report as a .docx file using python-docx, preserving table formatting, section headings, and bilingual column headers.
- **FR-006**: System MUST expose a POST endpoint at /api/extraction/jobs/{job_id}/risk-report that returns the generated .docx as a file stream.
- **FR-007**: Frontend MUST display a "生成风险评估报告" button in the ExtractionDrawer status bar, visible only when doc_class is CMCReport and at least one relationship edge exists.
- **FR-008**: Frontend MUST handle the download flow: show loading state, receive blob response, trigger browser download with appropriate filename.
- **FR-009**: System MUST propagate source_ref from extraction edges into the report's traceability column for auditability.
- **FR-010**: System MUST operate fully offline — no external API calls, no network dependencies during report generation.
- **FR-011**: Report generation MUST be accessible to any authenticated user who has permission to view the extraction job — no additional role restriction beyond extraction view access.
- **FR-012**: System MUST log an audit record on each report generation event, capturing: actor (user identity), job_id, timestamp, count of rules fired, and generation outcome (success/failure).
- **FR-013**: System MUST persist the generated .docx file server-side, associated with the extraction job, so that it can be retrieved later via API without re-generation.
- **FR-014**: System MUST expose a GET endpoint to retrieve a previously generated report for a given job, returning the persisted .docx file.

### Key Entities

- **Facts**: In-memory representation of extracted relationships and data values, consumed by the reasoning engine. Contains relations (predicate → object classes), data_values (data property IRI → value), scalars (label → value), and drug_classes.
- **RiskReport**: Complete structured representation of a QS-A-020F05 risk assessment, containing subject description, equipment tables (grouped by workshop), assessment rows, and approval placeholders.
- **RiskRow**: Single row in the Assessment table — one per HazID dimension (人员/生产设备/物料管理/文件/三废处理), containing pre/post risk levels, control measures, and traceability references.
- **EquipmentEntry**: Single row in an equipment table — equipment ID, name, specification, and material.
- **DecisionRule**: Existing entity — a declarative rule with pattern (logical expression), conclusion (risk level), control_measure, traceability_docs, and postconditions.
- **GeneratedReport**: Persisted record of a generated risk assessment report — links to extraction job, stores the .docx file path, generation timestamp, actor, and rules-fired summary. Enables later retrieval without re-generation.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Users can generate a compliant risk assessment report within 10 seconds of clicking the button (end-to-end including rendering).
- **SC-002**: Generated reports contain all five HazID dimensions with correct pre-control and post-control risk levels matching the deterministic rule evaluation.
- **SC-003**: Equipment tables in generated reports accurately reflect 100% of extracted equipment edges, correctly grouped by workshop.
- **SC-004**: The report generation process requires zero manual data entry — all content is derived from extraction results and declarative rules.
- **SC-005**: Generated .docx files open without errors in Microsoft Word and preserve table formatting, bilingual headers, and section structure.
- **SC-006**: Time from document upload to downloadable risk report (including extraction review) is reduced from approximately 4 hours of manual compilation to under 30 minutes (dominated by human review time, not system processing).
- **SC-007**: Every report generation event is logged with actor, job reference, and timestamp — audit trail is queryable for compliance review.
- **SC-008**: Previously generated reports can be retrieved without re-generation, enabling compliance officers to access historical reports on demand.

## Assumptions

- The existing 10 endpoint finders in the relation extraction pipeline already cover all information types needed for risk assessment (DrugProduct, Equipment, SynthesisRoute, SafetyRisk, SharedLineAssessmentData, etc.).
- python-docx is already a project dependency and sufficient for rendering the required table formats.
- Risk assessment rules (5-10 DecisionRules for the five HazID dimensions) will be configured as data — the feature provides the infrastructure but rule content is a configuration concern.
- SECTION II (risk review) and QA sign-off sections are intentionally left as placeholders for manual completion — the system does not automate periodic review conclusions.
- The assessment team composition (评估小组) is either template-preset or derived from organizational ontology classes — not extracted from the source document.
- The existing evaluate() engine and its predicate operations (some_values_from, class_membership, datatype_facet, boolean_has_value, literal_eq) are sufficient for all risk assessment rule patterns without modification.
- Report generation is scoped to CMCReport-classified documents only; other document types are out of scope for this feature.
- Generated reports are persisted server-side alongside the extraction job; storage retention follows the same policy as extraction job data.
