"""resolve_document_job_id — the doc-IRI → extraction-job resolution."""

from __future__ import annotations

from cli_anything.ontology_agent.core import documents as documents_mod


def _entities(*shadows):
    return {"items": list(shadows), "total": len(shadows)}


def test_resolve_prefers_primary_job_id_key(stub):
    stub.get_map["/api/entities"] = _entities(
        {"iri": "doc:1", "properties_json": {"job_id": "J-primary", "sourceJob": "J-alias"}}
    )
    assert documents_mod.resolve_document_job_id(stub, "doc:1") == "J-primary"


def test_resolve_falls_back_to_alias_key(stub):
    stub.get_map["/api/entities"] = _entities(
        {"iri": "doc:1", "properties_json": {"sourceJob": "J-alias"}}
    )
    assert documents_mod.resolve_document_job_id(stub, "doc:1") == "J-alias"


def test_resolve_none_when_doc_has_no_job(stub):
    stub.get_map["/api/entities"] = _entities({"iri": "doc:1", "properties_json": {"foo": "bar"}})
    assert documents_mod.resolve_document_job_id(stub, "doc:1") is None


def test_resolve_none_when_doc_absent(stub):
    stub.get_map["/api/entities"] = _entities({"iri": "doc:other", "properties_json": {"job_id": "J"}})
    assert documents_mod.resolve_document_job_id(stub, "doc:missing") is None


def test_resolve_ignores_non_string_job_value(stub):
    stub.get_map["/api/entities"] = _entities(
        {"iri": "doc:1", "properties_json": {"job_id": 123, "jobId": "J-str"}}
    )
    assert documents_mod.resolve_document_job_id(stub, "doc:1") == "J-str"
