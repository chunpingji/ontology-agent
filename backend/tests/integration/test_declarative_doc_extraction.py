"""Historical doc_pattern metadata stays readable but cannot execute."""

from app.models.ontology_meta import OntologyClass, OntologyClassMapping
from tests.fixtures.feature014 import ANALYST, DRUG_PRODUCT


def test_historical_doc_pattern_cannot_create_an_extraction_job(drug_client, db):
    cls = db.query(OntologyClass).filter_by(slpra_iri=DRUG_PRODUCT).one()
    row = OntologyClassMapping(
        class_id=cls.id,
        mapping_type="doc_pattern",
        target='{"identity":{"pattern":".*"}}',
        source_system="old",
    )
    db.add(row)
    db.commit()
    for files in [None, {"file": ("old.docx", b"invalid", "application/octet-stream")}]:
        response = drug_client.post(
            "/api/extraction/jobs",
            headers=ANALYST,
            data={"source_type": "word", "class_mapping_id": str(row.id)},
            files=files,
        )
        assert response.status_code == 422, response.text
        assert "EXECUTABLE_PATTERN_RETIRED" in response.text
    current = drug_client.get(f"/api/ontology/classes/{DRUG_PRODUCT}/mappings").json()
    assert next(item for item in current if item["id"] == str(row.id))["target"] == row.target
