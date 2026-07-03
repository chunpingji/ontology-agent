"""T023 — integration: declare ``api_endpoint`` binding → run → traceable candidates,
``nested_object`` sub-candidate, unreachable-intranet degradation (US2 AS 1–4).

Written *before* the API reader / REST connector exist (T024–T031) — MUST FAIL until
the declarative pipeline dispatches ``api_endpoint`` to the REST reader. The
declaration layer (class binding + property bindings, Foundational/US1) already
exists, so the RED signal is purely in the *run* assertions.

Offline-deterministic: the connector row carries an ``inline_pages`` seam (served by
``RestConnector`` with no network); ``simulate="unreachable"`` drives the
graceful-degradation scenario (R12/FR-019/SC-008).
"""

from __future__ import annotations

import json
import os

from app.models.integration import IntegrationConnector
from tests.fixtures.feature014 import (
    ANALYST,
    APPROVAL_NUMBER,
    DRUG_NAME,
    DRUG_PRODUCT,
    MANUFACTURED_BY,
    MANUFACTURER,
    MFR_NAME,
    RISK_LEVEL,
    make_pages,
)

JOBS = "/api/extraction/jobs"
CLASSES = "/api/ontology/classes"
MAPPINGS = "/api/ontology/mappings"

CONNECTOR_NAME = "internal-drug-registry"
TOKEN_ENV = "DRUG_API_TOKEN"

_ITEMS = [
    {"drugName": "阿司匹林", "approvalNo": "国药准字H20003007",
     "manufacturer": {"name": "华北制药"}},
    {"drugName": "布洛芬", "approvalNo": "国药准字H20050001",
     "manufacturer": {"name": "石药集团"}},
    {"drugName": "对乙酰氨基酚", "approvalNo": "国药准字H20090001",
     "manufacturer": {"name": "华北制药"}},
]


def _connector_config(**over) -> dict:
    cfg = {
        "base_url": "http://drug-registry.intranet.local",
        "endpoint": "/api/drugs",
        "auth": {"scheme": "bearer", "token_env": TOKEN_ENV},
        "pagination": {"style": "cursor", "cursor_path": "$.next", "page_size": 2},
        "inline_pages": make_pages(_ITEMS, 2),          # offline transport seam
    }
    cfg.update(over)
    return cfg


def _seed_connector(client, **cfg_over) -> IntegrationConnector:
    """Insert a rest_api connector row directly (connector *validation* is covered
    by the T022 contract; here we exercise the reader/pipeline)."""
    c = IntegrationConnector(
        name=CONNECTOR_NAME, system_type="rest_api",
        connection_config=_connector_config(**cfg_over), is_active=True,
    )
    client.db.add(c)
    client.db.commit()
    return c


def _declare_api_binding(client, class_iri, *, target="/api/drugs", source=CONNECTOR_NAME):
    r = client.post(
        f"{CLASSES}/{class_iri}/mappings",
        json={"mapping_type": "api_endpoint", "target": target, "source_system": source},
        headers=ANALYST,
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _add_binding(client, mid, **payload):
    r = client.post(f"{MAPPINGS}/{mid}/property-bindings", json=payload, headers=ANALYST)
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _declare_full(client):
    # child Manufacturer binding — types the nested sub-candidate (not fetched itself).
    child = _declare_api_binding(client, MANUFACTURER, source=CONNECTOR_NAME + "-mfr")
    _add_binding(client, child, property_iri=MFR_NAME, source_path="name", is_label=True)
    # parent DrugProduct binding.
    parent = _declare_api_binding(client, DRUG_PRODUCT)
    _add_binding(client, parent, property_iri=DRUG_NAME,
                 source_path="$.data[*].drugName", is_label=True)
    _add_binding(client, parent, property_iri=APPROVAL_NUMBER,
                 source_path="$.data[*].approvalNo", is_identifier=True)
    _add_binding(client, parent, property_iri=MANUFACTURED_BY, property_kind="object",
                 source_path="$.data[*].manufacturer", object_resolution="nested_object",
                 nested_binding_id=child)
    return parent


def _run(client, mid):
    r = client.post(JOBS, data={"source_type": "api", "class_mapping_id": mid},
                    headers=ANALYST)
    assert r.status_code == 202, r.text
    return r.json()["id"]


def _members(client, job_id):
    body = client.get(f"{JOBS}/{job_id}/candidates").json()
    return body["ungrouped"] + [c for g in body["groups"] for c in g["candidates"]]


def _instances(members, class_iri):
    return [c for c in members
            if c["candidate_kind"] == "instance" and c["target_class_iri"] == class_iri]


# --- AS-1/AS-2: declare → run → candidates + resolvable provenance ----------
def test_api_declare_run_yields_candidates_with_provenance(drug_client):
    os.environ[TOKEN_ENV] = "tkn-abc"
    try:
        _seed_connector(drug_client)
        mid = _declare_full(drug_client)
        members = _members(drug_client, _run(drug_client, mid))
    finally:
        os.environ.pop(TOKEN_ENV, None)

    drugs = _instances(members, DRUG_PRODUCT)
    assert len(drugs) == 3
    by_name = {c["extracted_properties"].get(DRUG_NAME): c for c in drugs}
    assert set(by_name) == {"阿司匹林", "布洛芬", "对乙酰氨基酚"}
    assert by_name["阿司匹林"]["extracted_properties"][APPROVAL_NUMBER] == "国药准字H20003007"
    # provenance resolvable (SC-003): system = connector ref, record set.
    for c in drugs:
        ref = json.loads(c["source_ref"])
        assert ref["system"] == CONNECTOR_NAME
        assert ref["record"]


# --- AS-3: nested_object → linked Manufacturer sub-candidate ----------------
def test_nested_object_yields_linked_manufacturer_subcandidate(drug_client):
    os.environ[TOKEN_ENV] = "tkn-abc"
    try:
        _seed_connector(drug_client)
        mid = _declare_full(drug_client)
        members = _members(drug_client, _run(drug_client, mid))
    finally:
        os.environ.pop(TOKEN_ENV, None)

    mfrs = _instances(members, MANUFACTURER)
    assert len(mfrs) == 3                               # one per source item
    assert {c["extracted_properties"].get(MFR_NAME) for c in mfrs} == {"华北制药", "石药集团"}

    # each Manufacturer sub-candidate shares its parent DrugProduct's group_key.
    drug_keys = {c["group_key"] for c in _instances(members, DRUG_PRODUCT)}
    assert drug_keys and all(m["group_key"] in drug_keys for m in mfrs)


# --- AS-4: unreachable intranet → graceful degradation (never crash) --------
def test_unreachable_intranet_degrades_not_fails(drug_client):
    os.environ[TOKEN_ENV] = "tkn-abc"
    try:
        _seed_connector(drug_client, simulate="unreachable")
        mid = _declare_full(drug_client)
        job_id = _run(drug_client, mid)
        job = drug_client.get(f"{JOBS}/{job_id}").json()
        members = _members(drug_client, job_id)
    finally:
        os.environ.pop(TOKEN_ENV, None)

    assert job["status"] == "degraded"                 # never "failed" (R12/FR-019)
    assert job["total_candidates"] == 0
    assert not _instances(members, DRUG_PRODUCT)
    assert job["error_message"]                        # degraded_reason surfaced
    # degraded because the intranet host was unreachable — NOT the unsupported placeholder.
    assert "暂不支持" not in job["error_message"]


# --- reader drift: missing response path → value skipped, item still a candidate ---
def test_missing_response_path_skipped_item_still_yields_candidate(drug_client):
    os.environ[TOKEN_ENV] = "tkn-abc"
    try:
        _seed_connector(drug_client)
        mid = _declare_full(drug_client)
        # a binding whose JSON path is absent from every item → drift for that binding.
        _add_binding(drug_client, mid, property_iri=RISK_LEVEL,
                     source_path="$.data[*].riskLevel")
        members = _members(drug_client, _run(drug_client, mid))
    finally:
        os.environ.pop(TOKEN_ENV, None)

    drugs = _instances(members, DRUG_PRODUCT)
    assert len(drugs) == 3                              # item still yields a candidate
    assert all(RISK_LEVEL not in c["extracted_properties"] for c in drugs)
