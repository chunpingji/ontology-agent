"""T012 — contract test: declaration-driven DB extraction job (US1).

Contract: ``contracts/extraction-job.md``. Written **before** the pipeline rewire
(T014–T019) — MUST FAIL until the declarative branch is wired.

Covers: declaration-mode job creation (202), unknown ``class_mapping_id`` (404),
non-source-entity binding (422), the unified candidate output (one per row with
declared identifiers + controlled-vocab normalization + resolvable provenance),
and drift → partial job with E6 ``health="drift"``.
"""

from __future__ import annotations

import json
import uuid

from tests.fixtures.feature014 import (
    ANALYST,
    APPROVAL_NUMBER,
    DRUG_PRODUCT,
    MANUFACTURED_BY,
    MANUFACTURER,
    RISK_LEVEL,
    add_property_binding,
    declare_db_binding,
)

CLASSES = "/api/ontology/classes"
JOBS = "/api/extraction/jobs"


def _declare_full_binding(client, source="DRUG_DB_DSN", target="drug_product"):
    mid = declare_db_binding(client, source=source, target=target)
    add_property_binding(
        client, mid, property_iri=APPROVAL_NUMBER, source_path="approval_no",
        is_identifier=True,
    )
    add_property_binding(
        client, mid, property_iri=RISK_LEVEL, source_path="risk",
        transform_type="controlled_vocab",
        transform_config={"map": {"高": "HighRisk", "中": "MediumRisk", "低": "LowRisk"}},
    )
    add_property_binding(
        client, mid, property_iri=MANUFACTURED_BY, property_kind="object",
        source_path="mfr_code", object_resolution="id_reference",
        target_class_iri=MANUFACTURER, target_id_path="mfr_code",
    )
    return mid


def _run_job(client, mid, source_type="database"):
    return client.post(
        JOBS, data={"source_type": source_type, "class_mapping_id": mid},
        headers=ANALYST,
    )


def _all_candidates(client, job_id) -> list[dict]:
    body = client.get(f"{JOBS}/{job_id}/candidates").json()
    return body["ungrouped"] + [c for g in body["groups"] for c in g["candidates"]]


def test_declarative_db_job_happy_path(drug_client, source_table):
    source_table()  # publishes DRUG_DB_DSN → drug_product (DEFAULT_ROWS)
    mid = _declare_full_binding(drug_client)

    r = _run_job(drug_client, mid)
    assert r.status_code == 202, r.text
    job_id = r.json()["id"]

    job = drug_client.get(f"{JOBS}/{job_id}").json()
    assert job["status"] in ("reviewing", "done")
    assert job["total_candidates"] >= 3

    instances = [c for c in _all_candidates(drug_client, job_id)
                 if c["candidate_kind"] == "instance"]
    assert len(instances) == 3
    approvals = {c["extracted_properties"].get(APPROVAL_NUMBER) for c in instances}
    assert "国药准字H20003007" in approvals

    # controlled vocab: the 高 row becomes HighRisk (normalized ontology term).
    high = next(c for c in instances
                if c["extracted_properties"].get(APPROVAL_NUMBER) == "国药准字H20003007")
    assert high["extracted_properties"].get(RISK_LEVEL) == "HighRisk"

    # provenance: every candidate carries a resolvable source_ref (SC-003).
    for c in instances:
        ref = json.loads(c["source_ref"])
        assert ref["system"] == "DRUG_DB_DSN"
        assert ref["entity"] == "drug_product"
        assert ref["record"]


def test_unknown_class_mapping_id_404(drug_client, source_table):
    source_table()
    r = _run_job(drug_client, str(uuid.uuid4()))
    assert r.status_code == 404, r.text


def test_non_source_entity_binding_422(drug_client):
    # a legacy bfo mapping is not a source-entity binding → cannot drive a job.
    bfo = drug_client.post(
        f"{CLASSES}/{DRUG_PRODUCT}/mappings",
        json={"mapping_type": "bfo", "target": "BFO_0000040"},
        headers=ANALYST,
    ).json()
    r = _run_job(drug_client, bfo["id"])
    assert r.status_code == 422, r.text


def test_drift_declared_column_absent_partial(drug_client, source_table):
    source_table()
    mid = declare_db_binding(drug_client)
    add_property_binding(
        drug_client, mid, property_iri=APPROVAL_NUMBER, source_path="approval_no",
        is_identifier=True,
    )
    # a declared column absent from the table → drift (skip binding, partial job).
    add_property_binding(
        drug_client, mid, property_iri=RISK_LEVEL, source_path="does_not_exist",
    )

    r = _run_job(drug_client, mid)
    assert r.status_code == 202, r.text
    job_id = r.json()["id"]

    job = drug_client.get(f"{JOBS}/{job_id}").json()
    assert job["status"] in ("reviewing", "done")  # completes, never crashes
    assert job["total_candidates"] >= 3

    mappings = drug_client.get(f"{CLASSES}/{DRUG_PRODUCT}/mappings").json()
    db_map = next(m for m in mappings if m["id"] == mid)
    assert db_map["health"] == "drift"
