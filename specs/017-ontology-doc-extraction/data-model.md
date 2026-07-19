# Data Model: Ontology-Guided Word Extraction

**Feature**: 017-ontology-doc-extraction

**Date**: 2026-07-19

This feature adds runtime structures and ontology extraction annotations. It does not add a database table or column.

## 1. Word Document IR

### DocStructure

Existing aggregate, extended without breaking current callers:

| Field | Type | Meaning |
|---|---|---|
| title | string | Business title inferred from visual heading or original filename |
| sections | list of DocSection | Semantic sections in document order |
| tables | list of DocTable | Normalized tables |
| paragraphs | list of string | All nonempty paragraphs |
| headings | list of string | All inferred headings |
| source_filename | optional string | Original upload name used as fallback |
| warnings | list of string | Nonfatal parse/profile diagnostics |

### DocSection

| Field | Type | Meaning |
|---|---|---|
| heading | string | Heading text |
| level | integer 1–6 | Shared inferred heading level |
| paras | list of string | Paragraph text until the next heading |
| heading_index | optional integer | Original paragraph index |
| para_indices | list of integer | Original paragraph indices, aligned with paras |

Source identity is section plus heading_index; an individual property uses the matching paragraph index.

### DocTable

| Field | Type | Meaning |
|---|---|---|
| headers | list of string | Canonical column headers after multi-row normalization |
| rows | list of map | Canonical-header to cell value for data rows |
| cells | list of list of string | Raw rectangular cell text |
| table_index | integer | Original document table index |
| header_row_count | integer | Number of rows consumed as the header region |
| row_indices | list of integer | Raw row index for every rows entry |

Invariants:

- headers length equals the maximum data width.
- data rows begin after header_row_count.
- repeated/blank fragments do not overwrite a prior nonempty header.
- raw cells are retained for audit.

## 2. Document Extraction Profile

Runtime immutable value compiled from an ontology extractionProfile annotation or E6 target.

| Field | Type | Rules |
|---|---|---|
| version | integer | Current schema version 1 |
| sources | list of LocatorProfile | At least one |
| property_aliases | map property key to aliases | Property key is full IRI or local name |
| transforms | map property key to TransformProfile | Optional |
| identity | IdentityProfile | Optional; document pattern or field aliases |
| subclass_by | optional property key | Value is classified through ontology subclass labels |
| label | optional string | Fallback endpoint/source label |
| applicability | optional map | Document class/template/source/version metadata |

Unknown keys are rejected in strict parsing for E6 jobs and ignored with warning for published TTL profiles, allowing a bad profile to degrade without breaking unrelated ranges.

### LocatorProfile

| Field | Type | Rules |
|---|---|---|
| locator | section_kv, table_rows, or table_singleton | Required |
| anchors | AnchorSelector | Required for section locators |
| headers | HeaderSelector | Required for table locators |
| endpoint_mode | singleton, row, or group_by | Defaults from locator |
| identity | optional IdentityProfile | Overrides profile identity for this source |
| orientation | columns or kv | table_singleton may use kv |
| key_aliases | list of string | Required for kv orientation |
| value_aliases | list of string | Required for kv orientation |
| source_label | string | Human-readable provenance label |

### AnchorSelector

- any_of: at least one alias must match.
- all_of: every nested group must match.
- HeaderSelector represents all_of as a list of any_of groups.

Example semantics:

    all_of:
      - any_of: [设备规格, 设备名称]
      - any_of: [匹配设备, 设备编号]

### IdentityProfile

| Field | Type | Meaning |
|---|---|---|
| aliases | list of string | Identity field aliases |
| fields | list of property keys | Composite identity fields |
| pattern | optional regex string | Search entire document |
| split | optional string | Keep first segment for equipment alternatives |
| fallback | optional string | Stable singleton label |

## 3. Runtime Property Binding

Common value compiled from ontology properties/Profile aliases or an E6b OntologyPropertyBinding.

| Field | Type |
|---|---|
| property_iri | string |
| label | string |
| aliases | ordered list of string |
| transform_type | none, controlled_vocab, pattern, cast |
| transform_config | optional map |
| reject_invalid | boolean |
| is_identifier | boolean |
| is_label | boolean |

Alias normalization removes leading section/item numbering, normalizes whitespace and punctuation, strips trailing unit text for matching, and maps “是否是X” to “是否X”.

For E6b:

- source_path may be a JSON string array, pipe-delimited aliases, or a single alias.
- transform_type and transform_config retain feature 014 semantics.
- transform_config may additionally contain number_pattern, accepted_units, and reject_invalid for document values.

## 4. DocumentValue

| Field | Type | Meaning |
|---|---|---|
| property_iri | string | Ontology data-property IRI |
| label | string | Display label |
| value | scalar | Converted value |
| raw_value | string | Source text before conversion |
| source_ref | map | Section/paragraph or table/row/column address |
| note | optional string | Nonfatal transform/validation explanation |

When reject_invalid is true and conversion fails, no valid DocumentValue is emitted; the note is retained on the candidate result with the same source reference.

## 5. DocumentCandidate and DocumentReadResult

### DocumentCandidate

| Field | Type |
|---|---|
| target_class_iri | string |
| values | list of DocumentValue |
| identifier | optional string |
| group_key | optional string |
| source_ref | map |
| notes | list of string |
| subclass_value | optional string |

### DocumentReadResult

| Field | Type | Meaning |
|---|---|---|
| candidates | list of DocumentCandidate | Partial success allowed |
| drifted_paths | list of string | Bindings absent from all selected blocks |
| degraded_reason | optional string | Invalid profile or unreadable/corrupt document |

Adapters:

- relation_extractor converts values to existing object_data_properties and source_ref fields.
- pipeline converts candidates to existing RowCandidate/ExtractionCandidate shapes.

## 6. Persistent E6/E6b Reuse

No model change:

- OntologyClassMapping.mapping_type = doc_pattern.
- OntologyClassMapping.target = constrained locator Profile JSON or compact locator string.
- OntologyClassMapping.source_system = optional template/source identifier, never a credential.
- OntologyPropertyBinding.source_path = aliases.
- Existing version/status/timestamps and CRUD audit continue unchanged.

## 7. Ontology Annotations

The integration module adds extractionProfile as an annotation property. It carries JSON Profile data on a range class and is read by OntologyEngine.get_extraction_hints.

Domain ontology additions are limited to:

- missing DrugProduct properties required by the report, such as appearance, solubility, oncology flag, and inactivation method;
- missing domain declarations for residue properties;
- SharedLineAssessmentData row fields required to preserve wide toxicology values.

No existing triples are removed.
