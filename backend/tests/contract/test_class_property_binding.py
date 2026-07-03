"""T011 — contract test: class/property binding CRUD + validation (US1).

Contract: ``contracts/class-property-binding.md``. Phase 2 (Foundational) already
implements the declaration model; this contract locks its behaviour:

- create class binding (``db_table``) + property bindings → 201
- ``(class, source_system)`` uniqueness → 409
- credential safety: raw DSN ``source_system`` → 422 (FR-006)
- validation: domain gate (V1), single identifier (V4), object shape (V3),
  transform config (V5) → 422 with the offending code
- role gate (senior_analyst) → 403 for operator
- cascade delete removes owned property bindings
"""

from __future__ import annotations

import uuid

from app.models.ontology_meta import OntologyPropertyBinding
from tests.fixtures.feature014 import (
    ANALYST,
    APPROVAL_NUMBER,
    DRUG_NAME,
    DRUG_PRODUCT,
    MANUFACTURED_BY,
    MANUFACTURER,
    MFR_NAME,
    OPERATOR,
    RISK_LEVEL,
    add_property_binding,
    declare_db_binding,
)

CLASSES = "/api/ontology/classes"
MAPPINGS = "/api/ontology/mappings"


def _error_codes(resp) -> set[str]:
    detail = resp.json()["detail"]
    errors = detail["errors"] if isinstance(detail, dict) else []
    return {e["code"] for e in errors}


# --- CRUD happy path -------------------------------------------------------
def test_create_class_binding_and_property_binding(drug_client):
    mid = declare_db_binding(drug_client)

    pb = add_property_binding(
        drug_client, mid, property_iri=APPROVAL_NUMBER,
        source_path="approval_no", is_identifier=True,
    )
    assert pb.status_code == 201, pb.text
    body = pb.json()
    assert body["property_iri"] == APPROVAL_NUMBER
    assert body["is_identifier"] is True
    assert body["class_mapping_id"] == mid

    listing = drug_client.get(f"{MAPPINGS}/{mid}/property-bindings")
    assert listing.status_code == 200
    assert any(b["source_path"] == "approval_no" for b in listing.json())


def test_controlled_vocab_and_object_bindings_ok(drug_client):
    mid = declare_db_binding(drug_client)
    vocab = add_property_binding(
        drug_client, mid, property_iri=RISK_LEVEL, source_path="risk",
        transform_type="controlled_vocab",
        transform_config={"map": {"高": "HighRisk", "中": "MediumRisk", "低": "LowRisk"}},
    )
    assert vocab.status_code == 201, vocab.text

    fk = add_property_binding(
        drug_client, mid, property_iri=MANUFACTURED_BY, property_kind="object",
        source_path="mfr_code", object_resolution="id_reference",
        target_class_iri=MANUFACTURER, target_id_path="mfr_code",
    )
    assert fk.status_code == 201, fk.text
    assert fk.json()["object_resolution"] == "id_reference"


# --- (class, source) uniqueness (C1/FR-024) --------------------------------
def test_duplicate_class_source_binding_409(drug_client):
    declare_db_binding(drug_client, source="DRUG_DB_DSN")
    dup = drug_client.post(
        f"{CLASSES}/{DRUG_PRODUCT}/mappings",
        json={"mapping_type": "db_table", "target": "drug_product2",
              "source_system": "DRUG_DB_DSN"},
        headers=ANALYST,
    )
    assert dup.status_code == 409, dup.text


# --- credential safety (C3/FR-006) -----------------------------------------
def test_raw_dsn_source_system_422(drug_client):
    r = drug_client.post(
        f"{CLASSES}/{DRUG_PRODUCT}/mappings",
        json={"mapping_type": "db_table", "target": "drug_product",
              "source_system": "postgresql://u:p@db.intranet/registry"},
        headers=ANALYST,
    )
    assert r.status_code == 422, r.text


def test_empty_target_source_entity_422(drug_client):
    r = drug_client.post(
        f"{CLASSES}/{DRUG_PRODUCT}/mappings",
        json={"mapping_type": "db_table", "target": "", "source_system": "DRUG_DB_DSN"},
        headers=ANALYST,
    )
    assert r.status_code == 422, r.text


# --- validation V1/V3/V4/V5 ------------------------------------------------
def test_domain_gate_rejects_foreign_property_422(drug_client):
    """manufacturerName's domain is Manufacturer → cannot bind on DrugProduct."""
    mid = declare_db_binding(drug_client)
    r = add_property_binding(
        drug_client, mid, property_iri=MFR_NAME, source_path="mfr_name",
    )
    assert r.status_code == 422, r.text
    assert "binding_domain" in _error_codes(r)


def test_second_identifier_422(drug_client):
    mid = declare_db_binding(drug_client)
    first = add_property_binding(
        drug_client, mid, property_iri=APPROVAL_NUMBER, source_path="approval_no",
        is_identifier=True,
    )
    assert first.status_code == 201, first.text
    second = add_property_binding(
        drug_client, mid, property_iri=DRUG_NAME, source_path="drug_name",
        is_identifier=True,
    )
    assert second.status_code == 422, second.text
    assert "binding_identifier" in _error_codes(second)


def test_object_id_reference_requires_target_id_path_422(drug_client):
    mid = declare_db_binding(drug_client)
    r = add_property_binding(
        drug_client, mid, property_iri=MANUFACTURED_BY, property_kind="object",
        source_path="mfr_code", object_resolution="id_reference",
        target_class_iri=MANUFACTURER,  # missing target_id_path
    )
    assert r.status_code == 422, r.text
    assert "binding_object_shape" in _error_codes(r)


def test_bad_transform_config_422(drug_client):
    mid = declare_db_binding(drug_client)
    r = add_property_binding(
        drug_client, mid, property_iri=APPROVAL_NUMBER, source_path="approval_no",
        transform_type="cast", transform_config={"to": "nonsense_type"},
    )
    assert r.status_code == 422, r.text
    assert "binding_transform" in _error_codes(r)


# --- role gate (FR-006) ----------------------------------------------------
def test_operator_forbidden_403(drug_client):
    r = drug_client.post(
        f"{CLASSES}/{DRUG_PRODUCT}/mappings",
        json={"mapping_type": "db_table", "target": "drug_product",
              "source_system": "DRUG_DB_DSN"},
        headers=OPERATOR,
    )
    assert r.status_code == 403, r.text


def test_property_binding_operator_forbidden_403(drug_client):
    mid = declare_db_binding(drug_client)
    r = drug_client.post(
        f"{MAPPINGS}/{mid}/property-bindings",
        json={"property_iri": APPROVAL_NUMBER, "source_path": "approval_no"},
        headers=OPERATOR,
    )
    assert r.status_code == 403, r.text


# --- validate endpoint (FR-005/FR-021) -------------------------------------
def test_validate_binding_unmapped_then_ok(drug_client):
    mid = declare_db_binding(drug_client)
    empty = drug_client.post(f"{MAPPINGS}/{mid}/validate")
    assert empty.status_code == 200, empty.text
    assert empty.json()["health"] == "unmapped"

    add_property_binding(
        drug_client, mid, property_iri=APPROVAL_NUMBER, source_path="approval_no",
        is_identifier=True,
    )
    ok = drug_client.post(f"{MAPPINGS}/{mid}/validate")
    assert ok.status_code == 200, ok.text
    assert ok.json()["health"] == "ok"


# --- optimistic concurrency + cascade delete -------------------------------
def test_update_property_binding_optimistic(drug_client):
    mid = declare_db_binding(drug_client)
    created = add_property_binding(
        drug_client, mid, property_iri=RISK_LEVEL, source_path="risk",
    ).json()
    pid = created["id"]
    upd = drug_client.put(
        f"/api/ontology/property-bindings/{pid}",
        json={"property_iri": RISK_LEVEL, "source_path": "risk_level",
              "expected_version": created["version"]},
        headers=ANALYST,
    )
    assert upd.status_code == 200, upd.text
    assert upd.json()["source_path"] == "risk_level"


def test_cascade_delete_removes_property_bindings(drug_client):
    mid = declare_db_binding(drug_client)
    add_property_binding(
        drug_client, mid, property_iri=APPROVAL_NUMBER, source_path="approval_no",
    )
    m = drug_client.get(f"{CLASSES}/{DRUG_PRODUCT}/mappings").json()
    version = next(x["version"] for x in m if x["id"] == mid)

    dele = drug_client.delete(
        f"{MAPPINGS}/{mid}?expected_version={version}", headers=ANALYST
    )
    assert dele.status_code == 204, dele.text

    remaining = (
        drug_client.db.query(OntologyPropertyBinding)
        .filter_by(class_mapping_id=uuid.UUID(mid))
        .count()
    )
    assert remaining == 0
