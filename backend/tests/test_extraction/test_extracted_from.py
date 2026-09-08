"""019/020: source provenance cannot fabricate extractedFrom or development-phase facts."""

from __future__ import annotations

import asyncio

from app.models.entity_shadow import EntityShadow
from app.models.extraction import ExtractionCandidate, ExtractionJob
from app.models.integration import IntegrationConnector
from app.services.integration.materializer import FactMaterializer
from tests.test_integration.fixtures.doc_repo_changes import (
    DOC_REPO_CHANGE_TTR_V2,
    DOCUMENT_NS,
    inline_config,
)

FACTS = "http://slpra.org/facts#"
DOC_IRI = f"{FACTS}doc-TTR-001"
PHASE_CLINICAL_I = f"{DOCUMENT_NS}Phase_ClinicalI"
DRUG_CLASS = "https://ontology.pharma-gmp.cn/slpra/drug/DrugProduct"


def _materialize_doc(db, fake_engine):
    """能力三物化文档记录 → facts#doc-TTR-001 影子行（携 hasDevelopmentPhase=Phase_ClinicalI）。"""
    c = IntegrationConnector(
        system_type="doc_repo",
        name="EDMS",
        connection_config=inline_config(changes=[DOC_REPO_CHANGE_TTR_V2]),
        field_mapping={},
        poll_interval_seconds=2,
        is_active=True,
    )
    db.add(c)
    db.commit()
    db.refresh(c)
    asyncio.run(FactMaterializer(db, fake_engine).run_sync(c))
    shadow = db.query(EntityShadow).filter(EntityShadow.iri == DOC_IRI).one()
    assert shadow.properties_json["hasDevelopmentPhase"] == PHASE_CLINICAL_I  # 前置坐实


def _candidate(db, props, *, source_ref=DOC_IRI, source_type="doc_repo"):
    job = ExtractionJob(
        source_type=source_type,
        source_filename=DOC_IRI,
        source_config={"doc_ref": DOC_IRI},
        status="reviewing",
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    cand = ExtractionCandidate(
        job_id=job.id,
        target_class_iri=DRUG_CLASS,
        extracted_properties=props,
        source_ref=source_ref,
        review_status="pending",
        alignment_result="new",
    )
    db.add(cand)
    db.commit()
    db.refresh(cand)
    return cand


def _confirm(client, headers, cand):
    response = client.put(
        f"/api/extraction/candidates/{cand.id}/review",
        json={"status": "confirmed"},
        headers=headers,
    )
    assert response.status_code == 409, response.text
    assert "legacy_unverified" in response.text


def test_source_reference_cannot_inject_an_unverified_business_relationship(
    client, analyst_headers, db, fake_engine
):
    _materialize_doc(db, fake_engine)
    cand = _candidate(db, {"activeIngredient": "化合物 X"})
    _confirm(client, analyst_headers, cand)
    db.refresh(cand)
    assert cand.committed_iri is None
    assert cand.extracted_properties == {"activeIngredient": "化合物 X"}
    assert cand.source_ref == DOC_IRI


def test_document_phase_does_not_become_an_entity_fact(client, analyst_headers, db, fake_engine):
    _materialize_doc(db, fake_engine)
    for properties in [
        {"activeIngredient": "化合物 X"},
        {"activeIngredient": "化合物 Z", "hasDevelopmentPhase": f"{DOCUMENT_NS}Phase_Preclinical"},
    ]:
        cand = _candidate(db, properties)
        _confirm(client, analyst_headers, cand)
        db.refresh(cand)
        assert cand.extracted_properties == properties
        assert cand.committed_iri is None


def test_non_document_candidates_also_require_evidence_validation(client, analyst_headers, db):
    cand = _candidate(
        db, {"equipmentID": "CT64201"}, source_ref="设备台账.xlsx", source_type="excel"
    )
    _confirm(client, analyst_headers, cand)
    db.refresh(cand)
    assert cand.committed_iri is None
    assert cand.extracted_properties == {"equipmentID": "CT64201"}
