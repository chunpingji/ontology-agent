# Research: AST Template Management & LLM Pipeline Enhancement

**Date**: 2026-07-01 | **Feature**: 012-ast-template-llm-pipeline

## R1: Template Storage — DB vs Filesystem

**Decision**: Database-first (PostgreSQL `ast_templates` table) with filesystem fallback for the original JSON template.

**Rationale**: Templates are user-managed artifacts that need versioning, metadata (name, doc_no, created_by), and relational linking (document type mappings). DB storage enables CRUD via the existing FastAPI/SQLAlchemy stack without inventing a file-management layer. The original `qs_a_020f05.json` is seeded into the DB via Alembic migration but preserved on disk as the ultimate fallback.

**Alternatives considered**:
- Filesystem-only (JSON files in a directory): No versioning, no metadata, no relational queries. Rejected.
- Hybrid (metadata in DB, schema JSON on filesystem): Added complexity for file management. Rejected.

## R2: Template Versioning Strategy

**Decision**: Immutable versions — updates create a new row with incremented version string; old versions are preserved and linked to historical reports.

**Rationale**: Historical reports snapshot their `template_id` at generation time. If templates were mutable, historical coverage manifests would reference a changed schema, violating traceability (Constitution III). Immutable versions ensure reports remain reproducible.

**Alternatives considered**:
- Mutable with soft-delete: Loses history. Rejected.
- Git-based versioning: Over-engineered for the use case. Rejected.

## R3: Template Resolution Strategy

**Decision**: Three-tier fallback: (1) `DocumentTypeMapping` match by `doc_class_iri` pattern + priority, (2) `is_default=True` template, (3) original filesystem template via `load_default_template()`.

**Rationale**: Layered fallback ensures zero regression — existing workflows hit tier 3 (unchanged behavior) until templates and mappings are explicitly configured. Tier 1 enables automatic multi-format support; tier 2 provides a sensible default.

**Alternatives considered**:
- Single-tier (DB only): Breaks if DB is empty or migration fails. Rejected.
- Two-tier (DB + default, no file fallback): Riskier during migration. Rejected.

## R4: LLM Client Architecture

**Decision**: OpenAI-compatible client via the `openai` Python package, configured with `local_llm_base_url` pointing to a local endpoint (e.g., Ollama at `http://localhost:11434/v1`).

**Rationale**: OpenAI-compatible API is the de facto standard for local LLM deployments (Ollama, vLLM, llama.cpp server). Using the `openai` package avoids introducing a new dependency — it's well-maintained, has structured output support, and the pattern matches the existing `anthropic` client usage in the codebase. Constitution VI (offline-first) is satisfied because the endpoint is local.

**Alternatives considered**:
- Direct HTTP via `httpx`: More code, no structured output helpers. Rejected.
- Anthropic API for local models: Not compatible with Ollama/vLLM. Rejected.
- LangChain: Heavy dependency, violates Constitution V. Rejected.

## R5: Gap-Filling Prompt Design

**Decision**: Structured JSON extraction prompt with slot schema injection and `source_span` requirement.

**Rationale**: The prompt provides the LLM with (1) document sections as context, (2) a precise schema of missing slots (slot_id, label, description, expected_type, object_class), and (3) strict output format (JSON array). Low temperature (0.1) minimizes hallucination. Requiring `source_span` enables traceability — every extracted value can be traced to a specific passage.

**Alternatives considered**:
- Free-form extraction then mapping: Harder to validate, more hallucination risk. Rejected.
- Function calling / tool use: Not reliably supported by all local LLM runtimes. Rejected — but could be revisited when local model support matures.

## R6: Visual Slot Editor Approach

**Decision**: In-app editor using a form-based UI (not a raw JSON editor). Users can add/remove/reorder slots within sections and groups, toggle required/optional status, and edit slot metadata. Saving creates a new template version.

**Rationale**: User explicitly chose visual editor over upload-only (clarification session 2026-07-01). A form-based approach is more accessible to domain experts (quality analysts) than raw JSON editing. The slot structure (Section → Group → Slot) maps naturally to a nested accordion/tree UI pattern already used in the AST tree view.

**Alternatives considered**:
- Monaco/CodeMirror JSON editor: Too technical for quality analysts. Rejected.
- Upload-only: Rejected by user.

## R7: Ontology Expansion — Runtime vs Stored

**Decision**: Runtime-only expansion. `template_expander.py` generates an expanded `ReportTemplate` in-memory by querying ontology data properties; the DB template is never modified.

**Rationale**: Ontology is the authority for data properties (Constitution II). If expansion results were persisted, they could drift from the ontology. Runtime expansion ensures the expanded view always reflects the current ontology state. Performance is acceptable — `get_data_properties_by_domain()` is a fast in-memory query.

**Alternatives considered**:
- Store expanded templates periodically: Drift risk, staleness. Rejected.
- Store as a "computed" template variant: Added complexity. Rejected.

## R8: Edge Source Attribution

**Decision**: Edges carry `source: "rule"` (existing) or `source: "llm"` (new). LLM edges also carry `source_span` (original text snippet). The `SlotSource` discriminated union gets a new `LLMExtractionSource` variant with `kind: "llm_extraction"`.

**Rationale**: Constitution III requires traceability. Audit logs distinguish rule vs LLM origin. The analyst UI shows a subtle visual indicator (badge/icon) per clarification Q2 decision. `source_span` enables one-click verification — the analyst can read the exact passage the value was extracted from.

**Alternatives considered**:
- Single "auto" source type: Loses traceability. Rejected (Constitution III violation).
- Detailed provenance (model name, prompt hash): Over-engineered for current needs. Deferred.

## R9: New Dependency — `openai` Package

**Decision**: Add `openai` as an optional dependency under a new `llm` extras group (`uv sync --extra llm`).

**Rationale**: Constitution V requires minimal new dependencies. The `openai` package is lightweight (~2MB), widely used, and provides the exact client interface needed. Making it optional (like `semantic` and `gliner`) ensures the base install remains unchanged. When `local_llm_enabled=False` (default), the package is never imported — zero runtime impact.

**Alternatives considered**:
- Bundle `openai` as a core dependency: Unnecessary for users who don't use LLM features. Rejected.
- Use raw `httpx` (already in deps): More boilerplate, no structured output helpers. Rejected.
