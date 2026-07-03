"""Shared test fixtures for feature 014 (ontology dynamic object mapping).

Two reusable doubles consumed by US1/US2/US3/US4:

- ``ontology`` — a lightweight fake ``OntologyEngine`` (hierarchy / domain /
  alignment read surface) seeded with a ``DrugProduct`` test T-Box, so
  extraction + fact-building tests get deterministic hierarchy/domain answers
  without loading a real Owlready2 World (T001).
- ``extraction_doubles`` — a temp SQLite source table and a paginated JSON
  REST stub (injected via the connector's ``http_fetcher`` seam) (T002).
"""
