# Contract: Document Extraction Profile and Interpreter

**Feature**: 017-ontology-doc-extraction

## 1. Profile Input

The compiler accepts a JSON object with version 1 and the fields defined in data-model.md. At least one source is required.

Minimal section example:

    {
      "version": 1,
      "sources": [
        {
          "locator": "section_kv",
          "anchors": {"any_of": ["产品的基本性质", "基本性质"]},
          "endpoint_mode": "singleton",
          "source_label": "产品基本性质"
        }
      ],
      "identity": {
        "pattern": "[A-Z]{2,4}-[0-9]{3,5}",
        "fallback": "药物产品"
      },
      "property_aliases": {
        "dosageForm": ["制剂剂型", "剂型"],
        "isHighlyActive": ["是否高活性药物", "是否是高毒高活药物"]
      }
    }

Minimal table example:

    {
      "version": 1,
      "sources": [
        {
          "locator": "table_rows",
          "headers": {
            "all_of": [
              {"any_of": ["设备规格", "设备名称"]},
              {"any_of": ["匹配设备", "设备编号"]}
            ]
          },
          "endpoint_mode": "row"
        }
      ],
      "identity": {"aliases": ["设备编号", "匹配设备"], "split": "/"}
    }

## 2. Public Service Seams

    parse_profile(raw: string or map) -> DocumentExtractionProfile

    compile_ontology_bindings(
        engine,
        target_class_iri,
        profile
    ) -> list[DocumentPropertyBinding]

    compile_e6_bindings(
        property_bindings
    ) -> list[DocumentPropertyBinding]

    read_document_profile(
        structure,
        target_class_iri,
        profile,
        property_bindings
    ) -> DocumentReadResult

These functions are deterministic and perform no network I/O.

## 3. Matching Rules

- Section anchors are OR by default.
- Table HeaderSelector requires every all_of group and one alias from each any_of group.
- Matching uses normalized text and allows an alias to match a canonical multi-row header fragment.
- Property aliases are ordered; the first nonempty matching key/column wins.
- Duplicate aliases never emit the same property twice for one candidate.
- section_kv reads key/value paragraphs until the next inferred section boundary.
- table_rows emits one candidate for every nonempty row.
- table_singleton with orientation kv reads parameter/value rows into one candidate.

## 4. Identity Rules

Priority:

1. document identity regex match;
2. declared identity alias;
3. declared composite identity fields;
4. declared fallback label;
5. target class label.

A generic word such as “药品” is never allowed to replace an already matched program code.

## 5. Transform and Validation

- Standard transform behavior reuses apply_transform.
- Boolean accepts existing Chinese/English true/false tokens.
- For numeric document values, number_pattern may extract the numeric token before cast.
- accepted_units validates a present unit.
- reject_invalid=true excludes an invalid value from extracted properties and records a note.
- Property-level failure is nonfatal; sibling properties and rows remain.

## 6. Provenance

Section value source_ref:

    {
      "kind": "paragraph",
      "section": "产品的基本性质",
      "heading_index": 20,
      "paragraph_index": 21,
      "key": "制剂剂型"
    }

Table value source_ref:

    {
      "kind": "table_cell",
      "table": 3,
      "row": 2,
      "column": 8,
      "header": "PDE (mg/天)"
    }

Every candidate has an aggregate source_ref and every emitted value has a more specific reference.

## 7. Error Semantics

- Invalid Profile: degraded_reason, zero candidates.
- Readable document with an unmatched selector: no degraded reason; declared aliases absent everywhere appear in drifted_paths.
- One invalid value: candidate retained with note.
- Corrupt/non-DOCX: degraded_reason, zero candidates.
- Unknown target property key in a TTL profile: warning and skip; E6 binding already validates property existence through existing CRUD.

## 8. Required Tests

- OR header groups do not become one impossible AND list.
- New profile-only range class extracts without class-specific dispatch.
- DrugProduct identifier pattern wins over generic typed span.
- Key normalization maps “是否是高致敏药物” to the declared property.
- Wide rows preserve composite identity and provenance.
- PDE definition text is rejected under strict numeric binding.
- Narrow parameter/value table still extracts.
