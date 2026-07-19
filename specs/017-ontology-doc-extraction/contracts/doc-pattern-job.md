# Contract: Declaration-Driven doc_pattern Job

**Feature**: 017-ontology-doc-extraction

## 1. Create Job

Endpoint remains POST /api/extraction/jobs as multipart form data.

Required fields:

- source_type = word
- class_mapping_id = UUID of an E6 mapping whose mapping_type is doc_pattern
- file = uploaded .docx

Optional:

- config_id for backward-compatible metadata only

Success:

- HTTP 202
- ExtractionJob.source_filename is the original filename.
- ExtractionJob.document_path is a persisted upload path.
- source_config.class_mapping_id identifies the E6 binding.
- Background pipeline receives the persisted path.

Validation:

- missing binding: 404
- non-source mapping: 422
- doc_pattern without file: 422
- doc_pattern with unsupported suffix: 422
- DB/API declaration jobs remain valid without a file.

## 2. Runtime Dispatch

The declarative pipeline dispatch table becomes:

- db_table → read_source_rows
- api_endpoint → read_api_items
- doc_pattern → read_doc_pattern
- unknown source entity type → existing unsupported/degraded result

read_doc_pattern compiles binding.target and the binding's E6b rows into the common Document Profile interpreter.

## 3. Candidate Output

Each document candidate becomes one existing ExtractionCandidate:

- candidate_kind = instance
- target_class_iri from the E6 class
- extracted_properties maps ontology property IRI to converted value
- group_key is the declared identifier/composite identifier
- source_ref is JSON containing original filename, persisted document identity, and section/table position
- degraded_reason holds nonfatal transform notes
- review_status = pending

No candidate is auto-published.

## 4. Health and Completion

- Any drifted_paths sets OntologyClassMapping.health to drift.
- Partial candidates still complete in reviewing state.
- Invalid Profile/corrupt document returns zero candidates and job status degraded with a nonempty reason.
- Valid document with zero matches completes reviewing with zero candidates and drift health; it is not a transport degradation.

## 5. Compatibility

- Existing DB/API class-binding jobs retain their request and result behavior.
- Existing config-driven Word upload remains unchanged.
- Existing upload cleanup and annotation scheduling behavior remains unchanged unless the doc_pattern job explicitly schedules annotation later.

## 6. Contract Tests

- create doc_pattern job without file returns 422.
- create with DOCX returns 202 and stores original filename/path.
- background execution produces declared properties and structured source_ref.
- one missing binding produces drift and partial candidates.
- corrupt document degrades without an unhandled exception.
- existing DB declarative contract tests remain green.
