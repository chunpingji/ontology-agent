# Quickstart: Ontology-Guided Word Extraction

**Feature**: 017-ontology-doc-extraction

## Prerequisites

- Run commands from backend with the uv-managed environment.
- No public network or model service is required.

## 1. Focused Test Suite

    cd backend
    uv run pytest tests/test_extraction/test_docx_structure.py
    uv run pytest tests/test_extraction/test_document_profile.py
    uv run pytest tests/test_extraction/test_relation_extraction.py
    uv run pytest tests/contract/test_extraction_job_declarative.py
    uv run pytest tests/integration/test_declarative_doc_extraction.py

Expected: all tests pass.

## 2. Word IR Acceptance

Run:

    uv run pytest tests/test_extraction/test_docx_structure.py -q

Verify:

- toc 2 is a heading.
- 4.2.3产品毒性信息 in a short bold Normal paragraph is a heading.
- a long bold prose sentence is not a heading.
- original filename is used instead of a UUID when no level-1 heading exists.
- multi-row table headers are canonical and retain raw row indices.

## 3. DrugProduct Acceptance

Run the DrugProduct profile regression:

    uv run pytest tests/test_extraction/test_relation_extraction.py -q -k drug_product

Verify:

- endpoint text is HRS-1597;
- dosage form and appearance are nonempty;
- toxicity booleans map to ontology property IRIs;
- the “是否是” variants normalize;
- no DrugProduct class-specific method dispatch is required.

## 4. Header Alias Acceptance

Run:

    uv run pytest tests/test_extraction/test_document_profile.py -q -k alias

Verify both signatures match:

- 设备规格 + 匹配设备
- 设备名称 + 设备编号

Verify residue identity accepts:

- 名称
- 中间体及成品
- 中间体/成品

## 5. Wide Toxicology Acceptance

Run:

    uv run pytest tests/test_extraction/test_document_profile.py -q -k toxicology

Verify two table rows produce two candidates. For each candidate, inspect:

- active ingredient and study type form the identifier;
- NOAEL, F1-F5, PDE, OEB, and source all come from the same row;
- source_ref identifies table, row, and column;
- “PDE：允许日暴露量” never appears as a valid decimal PDE.

## 6. doc_pattern Job Acceptance

Run:

    uv run pytest tests/integration/test_declarative_doc_extraction.py -q

Verify:

- missing file is rejected before job creation;
- a valid E6/E6b declaration produces pending review candidates;
- an absent alias marks the mapping drift but preserves other values;
- a corrupt file ends with a nonempty degraded reason.

## 7. Regression Suite

    uv run pytest tests/test_relation_schema.py
    uv run pytest tests/test_extraction/test_document_annotator.py
    uv run pytest tests/integration/test_declarative_db_extraction.py
    uv run pytest tests/integration/test_api_extraction_degradation.py

Expected: all tests pass.

## 8. Static Checks

    uv run ruff check app/services/extraction/docx_structure.py
    uv run ruff check app/services/extraction/document_profile.py
    uv run ruff check app/services/extraction/relation_extractor.py
    uv run ruff check app/services/extraction/pipeline.py
    uv run ruff check app/api/extraction.py

    git diff --check

Expected: no errors.

## 9. Validation Evidence

Validated on 2026-07-19:

- Focused Word IR, Profile, and relationship suites: 45 passed.
- Declarative contract and doc_pattern integration suites: 8 passed.
- Relation schema and document annotator regressions: 32 passed.
- Existing DB/API declarative regressions: 9 passed.
- Drug, equipment, drug-development, and integration Turtle files parsed successfully.
- Real OntologyEngine load plus synthetic DOCX end-to-end Profile extraction passed.
- Ruff passed for all changed Python files.
- git diff --check passed.
- The full backend run reached 1079 passed and 5 pre-existing risk-report failures;
  all five fail on the unrelated background SessionLocal test fixture with
  no such table: generated_reports and reproduce when run alone.
