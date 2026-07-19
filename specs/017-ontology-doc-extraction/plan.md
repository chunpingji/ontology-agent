# Implementation Plan: Ontology-Guided Word Extraction

**Branch**: 017-ontology-doc-extraction | **Date**: 2026-07-19 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from /specs/017-ontology-doc-extraction/spec.md

## Summary

Replace the first group of template-specific relationship finders with one ontology-guided document interpreter. The implementation has four connected slices:

1. Make docx_structure the shared Word structure authority for classification, preview, and relation extraction. It recognizes Heading, TOC, outline level, font-size fallback, and conservative numbered/bold semantic headings; normalizes multi-row table headers; and keeps block-level provenance.
2. Add a constrained Document Extraction Profile read surface to the integration ontology and a generic interpreter for section_kv, table_rows, and table_singleton. Profiles express anchor boolean groups, aliases, identity, transforms, grouping, and applicability; the interpreter never dispatches on a concrete target-class IRI.
3. Migrate DrugProduct, Equipment, Residue, and SharedLineAssessmentData profiles to that interpreter. DrugProduct uses program-code identity and property aliases; tables use OR-grouped headers; wide toxicology produces one endpoint per API/study row and rejects nonnumeric PDE definitions.
4. Complete the E6/E6b doc_pattern runtime reader and upload path, returning the same candidate/provenance shape as DB/API readers. Existing composite synthesis-route, external resolver, inference/conflict, and legacy source paths remain isolated and compatible.

## Technical Context

**Language/Version**: Python 3.11+

**Primary Dependencies**: FastAPI, SQLAlchemy 2.0, Pydantic v2, python-docx, Owlready2, rdflib; all already present.

**Storage**: Existing PostgreSQL E6/E6b metadata and ExtractionJob/ExtractionCandidate tables; authoritative ontology/slpra TTL. No database migration is planned. E6 target carries the locator profile and E6b source_path/transform_config carry property aliases and transforms.

**Testing**: pytest through uv run pytest. Contract and integration tests cover doc_pattern upload/runtime; unit tests cover Word IR, classifier full scan, profile selectors/transforms, P0 relation profiles, and compatibility.

**Target Platform**: Linux service in an internal air-gapped GMP environment.

**Project Type**: Backend-weighted web application; no frontend behavior change is required for the P0 runtime.

**Performance Goals**: One DOCX parse per relationship run; profile execution linear in document blocks and declared bindings. No model call or network call is introduced.

**Constraints**: Offline by default; ontology reads are local and read-only at runtime; source values remain review candidates; explicit document type controls the root Schema; all values remain traceable; no new dependency.

**Scale/Scope**: Initial migration of four range classes and three generic locator modes, plus one E6 reader. Composite route/resolver/reasoning logic is excluded.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-checked after Phase 1 design.*

| Principle | Status | Evidence |
|---|---|---|
| I. Spec-Driven Development | PASS | spec.md contains resolved clarifications, four independently testable stories, FR-001 through FR-021, and measurable outcomes. Plan, research, data model, contracts, quickstart, and tasks precede implementation. |
| II. Ontology Authority & Fidelity | PASS | Extraction semantics are added to the integration ontology, not hidden in Python. Domain properties added for missing extraction facts are surgical TTL additions. Runtime only reads the ontology; no A-Box or automatic publish path is introduced. TTL tests and a focused diff protect existing triples. |
| III. Traceability & Auditability | PASS | Every DocumentValue and candidate carries section/paragraph or table/row/column source data. E6/E6b versioning and existing job audit paths are reused. |
| IV. Test Discipline & Contract-First | PASS | contracts/document-profile.md and contracts/doc-pattern-job.md define the interpreter and API before code. Quickstart provides executable acceptance cases; pytest covers critical paths and regressions. |
| V. Minimal Complexity & Reuse | PASS | Reuses DocStructure, get_relation_schema, get_extraction_hints, E6/E6b, RowReadResult, apply_transform, and existing edge/candidate shapes. No new framework, database table, or dependency. |
| VI. Offline-First & Graceful Degradation | PASS | Parsing and interpretation are deterministic and local. A corrupt DOCX or invalid Profile yields a recorded degraded reason; normally offline operation is not degraded. |

**Pre-Phase-0 result**: PASS. No constitutional violation requires a complexity exception.

### Post-Design Re-evaluation

- Profile semantics are constrained JSON and data classes, not executable code. PASS.
- Versioned template aliases remain in extraction metadata and do not pollute stable domain labels. PASS.
- The design does not replace existing DB/API readers or the legacy Word/Excel config path. PASS.
- TTL changes only add missing properties/domains and integration extraction annotations; no existing alignment or axiom is removed. PASS.
- Invalid decimal/unit values are rejected from valid property output but retained as notes and provenance. PASS.

**Post-Phase-1 result**: PASS.

## Project Structure

### Documentation

    specs/017-ontology-doc-extraction/
    ├── spec.md
    ├── plan.md
    ├── research.md
    ├── data-model.md
    ├── quickstart.md
    ├── contracts/
    │   ├── document-profile.md
    │   └── doc-pattern-job.md
    ├── checklists/requirements.md
    └── tasks.md

### Source Code

    backend/
    ├── app/
    │   ├── api/extraction.py
    │   └── services/
    │       ├── ontology_engine.py
    │       └── extraction/
    │           ├── docx_structure.py
    │           ├── document_annotator.py
    │           ├── document_classifier.py
    │           ├── document_profile.py
    │           ├── relation_extractor.py
    │           └── pipeline.py
    └── tests/
        ├── contract/test_extraction_job_declarative.py
        ├── integration/test_declarative_doc_extraction.py
        └── test_extraction/
            ├── test_docx_structure.py
            ├── test_document_profile.py
            └── test_relation_extraction.py

    ontology/slpra/
    ├── slpra-drug.ttl
    ├── slpra-drug-development.ttl
    ├── slpra-equipment.ttl
    └── slpra-integration.ttl

**Structure Decision**: Existing backend web application structure. A single new document_profile service hosts the constrained profile parser/interpreter; existing services remain the integration points. No new top-level project.

## Implementation Phases

### Phase 0 - Research

- Confirm Word heading/table failure modes and current shared seams.
- Define constrained Profile semantics and fallback rules.
- Decide how TTL profiles and E6/E6b bindings compile into one runtime model.
- Define strict value validation and compatibility boundary.

Output: research.md.

### Phase 1 - Design and Contracts

- Define Word IR provenance additions and runtime entities.
- Define Profile JSON, E6 target/source_path conventions, interpreter output, drift/degradation semantics, and doc_pattern upload contract.
- Define quickstart acceptance matrix.

Output: data-model.md, contracts/, quickstart.md.

### Phase 2 - Tasks

- Generate story-oriented tasks with tests before implementation and exact paths.

Output: tasks.md.

### Phase 3 - Implementation

- US1: Word IR and classification/explicit type.
- US2: generic Profile interpreter and DrugProduct migration.
- US3: OR tables and wide toxicology migration.
- US4: E6/E6b doc_pattern upload and runtime reader.
- Regression, TTL parsing, quickstart verification.

## Complexity Tracking

No Constitution Check violations.

| Violation | Why Needed | Simpler Alternative Rejected Because |
|---|---|---|
| — | — | — |
