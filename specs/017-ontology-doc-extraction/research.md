# Research: Ontology-Guided Word Extraction

**Feature**: 017-ontology-doc-extraction

**Date**: 2026-07-19

## D1. One shared Word heading inference

**Decision**: docx_structure owns heading inference. It combines style name, TOC style, OOXML outline level, font size, and a conservative numbered-plus-bold heuristic. document_annotator calls the same function.

**Rationale**: The current preview has a font-size fallback while relation parsing only checks style names. Keeping independent rules recreates drift. A shared pure helper makes the same paragraph a heading in classification, preview, and relationship extraction.

**Rejected**:

- Only add toc 2 to the style list: does not cover Normal + bold or outline metadata.
- Treat every bold paragraph as a heading: produces false sections from emphasized prose.
- Infer headings with an LLM: violates deterministic/offline requirements and adds latency.

## D2. Conservative semantic-heading heuristic

**Decision**: A Normal paragraph is inferred as a semantic heading only when it is short, predominantly bold, does not end with prose punctuation, and either carries a recognized hierarchical number or is a compact heading phrase. Decimal numbering determines level where possible.

**Rationale**: HRS-1597 uses 4.2.3产品毒性信息. The additional guards avoid converting long bold warnings and sentence emphasis into sections.

## D3. Table header normalization in the IR

**Decision**: Detect the header region, combine nonempty unique header fragments by column, start data rows after the entire header region, and retain raw cells plus table/row indices.

**Rationale**: Profile selectors require stable column names. Reusing only the first row fails merged and multi-row headers; flattening without raw cells loses auditability.

## D4. Classifier uses controlled full-document signals

**Decision**: Build the classifier haystack from title, headings, and all nonempty paragraphs. Keep the existing deterministic signal list, threshold, raw score, and matched signals.

**Rationale**: Documents are bounded files and substring scanning is linear. The first-12-paragraph limit caused the observed score 6 versus 22 and provides no meaningful performance benefit.

## D5. Explicit document type is authoritative

**Decision**: When source_config contains a valid doc_class_iri, construct an explicit classification result and use it both for NER scope and relation-schema selection. Automatic classification is retained for missing explicit type and optional mismatch diagnostics.

**Rationale**: The analyst already selected CMCReport. Ignoring it for relationships can discard the graph even when the correct type is known.

## D6. Constrained Document Extraction Profile

**Decision**: Use a JSON-compatible, validated Profile compiled to data classes. Supported concepts:

- sources: one or more locator declarations;
- locator: section_kv, table_rows, table_singleton;
- section anchors: any_of;
- table headers: all_of groups containing any_of aliases;
- property aliases keyed by ontology property local name or IRI;
- endpoint mode and identity aliases/pattern/composite fields;
- transforms and reject_invalid;
- subclass_by for ontology-label classification;
- applicability metadata and source label.

No arbitrary Python expressions, imports, or callbacks are allowed.

**Rationale**: It is expressive enough for the P0 templates, diffable, editable, testable, and safe. Explicit all_of/any_of fixes the current ambiguous flat anchor list.

**Rejected**:

- Add more extractionAnchor values: current table semantics interpret all values as AND.
- Put every alias in skos:altLabel: enterprise layout vocabulary is not stable domain semantics.
- General-purpose XPath/JMESPath over OOXML: too powerful, hard to validate, and exposes layout implementation detail.

## D7. One interpreter for TTL and E6/E6b

**Decision**: The integration ontology may attach extractionProfile JSON to a range class. OntologyEngine returns it with existing hints. For declaration-driven jobs, E6 target compiles to the same Profile and E6b bindings compile to the same property-binding runtime type.

**Rationale**: Relationship preview and extraction jobs then share locators, aliases, transforms, provenance, and drift logic. It completes feature 014's missing doc_pattern reader instead of building a parallel Word adapter.

## D8. Automatic ontology-property bindings with explicit overrides

**Decision**: For TTL profiles, the compiler starts with data properties declared on the range class, using ontology label/local name as aliases and datatype as the default transform. Profile property_aliases and transforms add template-specific variants. E6/E6b jobs use only the analyst's explicit property bindings.

**Rationale**: New ordinary properties become extractable without Python edits while E6 jobs remain explicitly controlled. Normalization removes field numbering, units for matching, whitespace, and the extra character in “是否是”.

## D9. Program-code identity beats noisy NER

**Decision**: DrugProduct Profile declares a document identity pattern for program codes. A matching program code wins over generic NER words; NER remains a fallback only when no declared identity is available.

**Rationale**: HRS-1597 was incorrectly renamed to “药品” because the first typed span was trusted over a stronger document identifier.

## D10. Wide toxicology is row-scoped

**Decision**: Wide tables run table_rows mode and use active ingredient plus study type as a composite identifier. Every row becomes a distinct SharedLineAssessmentData endpoint for this feature. Narrow parameter/value tables remain table_singleton mode and can merge into a singleton endpoint.

**Rationale**: Row scope preserves NOAEL, F1-F5, PDE, OEB, and source pairing. A future ToxicologyStudy class can replace the endpoint class through Profile/schema evolution without changing the interpreter.

**Rejected**:

- Flatten repeated values onto one endpoint: loses row identity and creates false combinations.
- Introduce the final ToxicologyStudy ontology shape in this feature: requires a separate ontology design/publish review beyond the P0 extraction repair.

## D11. Strict PDE validation

**Decision**: A binding may declare numeric extraction, accepted units, and reject_invalid. The interpreter excludes an invalid value from extracted_properties and records a note with its source. “允许日暴露量” therefore remains auditable but is not a numeric PDE.

**Rationale**: Existing generic transforms are deliberately nonfatal and retain invalid raw values. That is suitable for review candidates generally, but not sufficient to prevent a known false numeric property. Strict rejection is opt-in per binding.

## D12. No database migration

**Decision**: Reuse E6 target for the constrained locator profile and E6b source_path for aliases. source_path accepts a JSON list or pipe-delimited aliases; transform_config carries strict numeric/unit options.

**Rationale**: Existing fields express the P0. Avoiding selector_config storage expansion is the smallest viable implementation. A future editor can add a structured field if profiles outgrow the 500-character E6 target limit.

## D13. Compatibility boundary

**Decision**:

- Profile strategy runs before legacy class overrides when an extractionProfile exists.
- DrugProduct, Equipment, Residue, and SharedLineAssessmentData TTL profiles migrate those targets.
- SynthesisRoute and ClinicalSampleProductionPlan remain explicit composite/resolver strategies.
- Existing generic anchor fallback remains for classes without a Profile.
- DB/API/Excel/Word legacy paths remain unchanged.

**Rationale**: This creates a safe incremental migration and a measurable no-new-class-branch rule without rewriting composite business logic as a giant generic function.

## Requirement Traceability

| Requirements | Decisions |
|---|---|
| FR-001–FR-006 | D1–D5 |
| FR-007–FR-011 | D6–D8, D11 |
| FR-012 | D8–D9 |
| FR-013 | D3, D6–D8 |
| FR-014–FR-015 | D10–D11 |
| FR-016–FR-018 | D7, D11–D12 |
| FR-019–FR-021 | D13 |
