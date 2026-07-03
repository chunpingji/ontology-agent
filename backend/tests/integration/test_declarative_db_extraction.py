"""T013 + T021 — integration: declare → run DB job → traceable candidates (US1).

Written before the pipeline rewire (T014–T019) — MUST FAIL until the declarative
branch produces candidates. Exercises US1 acceptance scenarios 1–6:

- declared identifiers + transformed (controlled-vocab) values on each candidate
- identifier-based alignment merges an existing individual (AS-5)
- object ``id_reference`` links to a Manufacturer individual, else a ``link``
  review candidate (AS-6, FR-023a)
- 100% resolvable ``source_ref`` provenance (SC-003)

T021 asserts the legacy ``column_mapping`` path still runs unchanged (FR-018).
"""

from __future__ import annotations

import io
import json

import openpyxl

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

JOBS = "/api/extraction/jobs"
CONFIGS = "/api/extraction/configs"


def _declare_full_binding(client):
    mid = declare_db_binding(client)
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


def _run_and_fetch(client, mid):
    r = client.post(JOBS, data={"source_type": "database", "class_mapping_id": mid},
                    headers=ANALYST)
    assert r.status_code == 202, r.text
    job_id = r.json()["id"]
    body = client.get(f"{JOBS}/{job_id}/candidates").json()
    members = body["ungrouped"] + [c for g in body["groups"] for c in g["candidates"]]
    return job_id, members


def test_candidates_carry_declared_and_transformed_values(drug_client, source_table):
    source_table()
    mid = _declare_full_binding(drug_client)
    _job_id, members = _run_and_fetch(drug_client, mid)

    instances = [c for c in members if c["candidate_kind"] == "instance"]
    assert len(instances) == 3
    by_approval = {c["extracted_properties"][APPROVAL_NUMBER]: c for c in instances}
    assert set(by_approval) == {
        "国药准字H20003007", "国药准字H20050001", "国药准字H20090001"
    }
    # controlled-vocab normalization across all three rows.
    assert by_approval["国药准字H20003007"]["extracted_properties"][RISK_LEVEL] == "HighRisk"
    assert by_approval["国药准字H20050001"]["extracted_properties"][RISK_LEVEL] == "MediumRisk"
    assert by_approval["国药准字H20090001"]["extracted_properties"][RISK_LEVEL] == "LowRisk"


def test_identifier_alignment_merges_existing(drug_client, source_table):
    source_table()
    # seed an existing DrugProduct individual carrying row 1's identifier.
    existing = drug_client.drug_engine.add_individual(
        DRUG_PRODUCT, "DrugAspirin",
        properties={APPROVAL_NUMBER: "国药准字H20003007"},
    )
    mid = _declare_full_binding(drug_client)
    _job_id, members = _run_and_fetch(drug_client, mid)

    instances = [c for c in members if c["candidate_kind"] == "instance"]
    matched = [c for c in instances
               if c["extracted_properties"][APPROVAL_NUMBER] == "国药准字H20003007"]
    assert len(matched) == 1
    assert matched[0]["alignment_result"] == "merge"
    assert matched[0]["aligned_iri"] == existing.iri
    assert matched[0]["match_score"] == 1.0
    # the other two rows have no prior individual → new.
    others = [c for c in instances
              if c["extracted_properties"][APPROVAL_NUMBER] != "国药准字H20003007"]
    assert all(c["alignment_result"] == "new" for c in others)


def test_object_id_reference_links_or_link_candidate(drug_client, source_table):
    source_table()
    mfr = drug_client.drug_engine.add_individual(
        MANUFACTURER, "MfrM001", properties={"mfr_code": "M001"},
    )
    mid = _declare_full_binding(drug_client)
    _job_id, members = _run_and_fetch(drug_client, mid)

    instances = [c for c in members if c["candidate_kind"] == "instance"]
    # rows 1 & 3 (mfr_code M001) resolve manufacturedBy to the seeded individual.
    resolved = [c for c in instances
                if c["extracted_properties"].get(MANUFACTURED_BY) == mfr.iri]
    assert len(resolved) == 2

    # row 2 (mfr_code M002) has no manufacturer individual → a link review candidate.
    links = [c for c in members if c["candidate_kind"] == "link"]
    assert any(c["extracted_properties"].get(MANUFACTURED_BY) == "M002" for c in links)


def test_provenance_every_candidate_resolvable(drug_client, source_table):
    source_table()
    mid = _declare_full_binding(drug_client)
    _job_id, members = _run_and_fetch(drug_client, mid)

    assert members
    for c in members:
        ref = json.loads(c["source_ref"])
        assert ref["system"] == "DRUG_DB_DSN"
        assert ref["entity"] == "drug_product"
        assert ref["record"]


# --- T021 backward-compat --------------------------------------------------
def _xlsx_bytes() -> bytes:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["设备编号", "设备名称", "材质"])
    ws.append(["CT64201", "压片机A", "316L不锈钢"])
    ws.append(["DE64203", "包衣机B", "304不锈钢"])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def test_backward_compat_legacy_column_mapping_job(drug_client):
    """A legacy config/``column_mapping`` job (no binding) runs unchanged (FR-018)."""
    cfg = drug_client.post(CONFIGS, json={
        "name": "设备台账",
        "target_class_iri": "http://slpra.org/equipment#Equipment",
        "source_type": "excel",
        "column_mapping": {"设备编号": "equipmentID", "设备名称": "equipmentName",
                           "材质": "material"},
    })
    assert cfg.status_code == 201, cfg.text
    cfg_id = cfg.json()["id"]

    r = drug_client.post(
        JOBS,
        data={"source_type": "excel", "config_id": cfg_id},
        files={"file": ("台账.xlsx", _xlsx_bytes(), "application/octet-stream")},
        headers=ANALYST,
    )
    assert r.status_code == 202, r.text
    job_id = r.json()["id"]
    final = drug_client.get(f"{JOBS}/{job_id}").json()
    assert final["status"] == "reviewing"
    assert final["total_candidates"] >= 2
