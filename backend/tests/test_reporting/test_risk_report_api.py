"""Error handling + audit tests for risk-report API endpoints (010, T020/T021)."""

from __future__ import annotations

import json
import uuid
from unittest.mock import patch

from app.models.extraction import ExtractionJob, GeneratedReport

HEADERS = {"X-User": "analyst", "X-Role": "senior_analyst"}

CMC_IRI = "https://ontology.pharma-gmp.cn/slpra/drug-development/CMCReport"


def _create_job(
    db,
    *,
    document_path: str | None = "/tmp/test.docx",
    source_filename: str = "HRS-1234.docx",
) -> ExtractionJob:
    job = ExtractionJob(
        source_type="upload",
        source_filename=source_filename,
        document_path=document_path,
        status="completed",
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


def _write_cache(job_id, data: dict, tmp_path=None) -> None:
    """Write an annotation cache file for the given job_id."""
    from pathlib import Path

    cache_path = Path("data/uploads") / f"{job_id}.annotated.json"
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(data), encoding="utf-8")


def _remove_cache(job_id) -> None:
    from pathlib import Path

    cache_path = Path("data/uploads") / f"{job_id}.annotated.json"
    cache_path.unlink(missing_ok=True)


class TestRiskReportEndpointErrors:
    def test_422_when_cache_missing(self, client, db):
        job = _create_job(db)
        _remove_cache(job.id)
        r = client.post(
            f"/api/extraction/jobs/{job.id}/risk-report", headers=HEADERS,
        )
        assert r.status_code == 422
        assert "文档未分类" in r.text

    def test_422_when_doc_class_missing(self, client, db):
        job = _create_job(db)
        _write_cache(job.id, {"doc_class": None, "relationships": []})
        r = client.post(
            f"/api/extraction/jobs/{job.id}/risk-report", headers=HEADERS,
        )
        assert r.status_code == 422
        assert "文档未分类" in r.text
        _remove_cache(job.id)

    def test_422_when_not_cmc_report(self, client, db):
        job = _create_job(db)
        _write_cache(job.id, {
            "doc_class": {"doc_class_iri": "https://example.org/OtherDoc"},
            "relationships": [{"some": "edge"}],
        })
        r = client.post(
            f"/api/extraction/jobs/{job.id}/risk-report", headers=HEADERS,
        )
        assert r.status_code == 422
        assert "CMCReport" in r.text
        _remove_cache(job.id)

    def test_422_when_zero_relationships(self, client, db):
        job = _create_job(db)
        _write_cache(job.id, {
            "doc_class": {
                "doc_class_iri": "https://ontology.pharma-gmp.cn/slpra/drug-development/CMCReport"
            },
            "relationships": [],
        })
        r = client.post(
            f"/api/extraction/jobs/{job.id}/risk-report", headers=HEADERS,
        )
        assert r.status_code == 422
        assert "未检测到关系数据" in r.text
        _remove_cache(job.id)

    def test_404_when_job_not_found(self, client, db):
        fake_id = uuid.uuid4()
        r = client.post(
            f"/api/extraction/jobs/{fake_id}/risk-report", headers=HEADERS,
        )
        assert r.status_code == 404

    def test_get_404_when_no_report_generated(self, client, db):
        job = _create_job(db)
        r = client.get(
            f"/api/extraction/jobs/{job.id}/risk-report", headers=HEADERS,
        )
        assert r.status_code == 404
        assert "尚未生成" in r.text


class TestAuditChainAfterReport:
    def test_audit_entry_present_and_chain_valid(self, client, db):
        from app.services import audit

        job = _create_job(db)
        _write_cache(job.id, {
            "doc_class": {"doc_class_iri": CMC_IRI},
            "relationships": [
                {
                    "subject_class_iri": CMC_IRI,
                    "predicate_iri": "https://ontology.pharma-gmp.cn/slpra/drug-development/usesEquipment",
                    "object_class_iri": "https://ontology.pharma-gmp.cn/slpra/equipment/Equipment",
                    "object_text": "RE001",
                    "object_data_properties": [
                        {"iri": None, "label": "设备名称", "value": "搅拌釜"},
                    ],
                    "source_ref": "642车间",
                },
            ],
        })
        r = client.post(
            f"/api/extraction/jobs/{job.id}/risk-report", headers=HEADERS,
        )
        assert r.status_code == 200

        result = audit.verify(db)
        assert result["ok"] is True
        assert result["verified_count"] >= 1

        from app.models.reasoning import AuditLog

        entry = (
            db.query(AuditLog)
            .filter(AuditLog.action == "report.generate")
            .first()
        )
        assert entry is not None
        assert entry.actor == "analyst"
        assert entry.entity_iri == str(job.id)
        assert entry.details["report_type"] == "risk_assessment"
        assert entry.details["source_document_name"] == "HRS-1234.docx"

        generated = db.query(GeneratedReport).filter_by(job_id=job.id).one()
        source_edge = generated.rules_summary["source_document_edge"]
        assert source_edge["predicate_iri"].endswith("/basedOnSourceDocument")
        assert source_edge["object_text"] == "HRS-1234.docx"
        _remove_cache(job.id)


class TestSourceDocumentProvenance:
    def test_generated_docx_and_snapshot_reference_source_cmc_report(self, client, db):
        import io
        from pathlib import Path

        from docx import Document

        from app.services.reporting.product_report_edges import (
            BASED_ON_SOURCE_DOCUMENT_IRI,
            DOCUMENT_NAME_IRI,
        )

        source_filename = "HRS-1597 CMCReport.docx"
        job = _create_job(db, source_filename=source_filename)
        _write_cache(job.id, {
            "doc_class": {"doc_class_iri": CMC_IRI},
            "relationships": [{
                "subject_class_iri": CMC_IRI,
                "predicate_iri": (
                    "https://ontology.pharma-gmp.cn/slpra/drug-development/usesEquipment"
                ),
                "object_class_iri": (
                    "https://ontology.pharma-gmp.cn/slpra/equipment/Equipment"
                ),
                "object_text": "RE001",
                "object_data_properties": [],
                "source_ref": "642车间",
            }],
        })

        with patch("app.api.extraction._llm_report_flags_active", return_value=False):
            response = client.post(
                f"/api/extraction/jobs/{job.id}/risk-report", headers=HEADERS,
            )

        assert response.status_code == 200
        document = Document(io.BytesIO(response.content))
        text = "\n".join(paragraph.text for paragraph in document.paragraphs)
        assert text.count(f"分析文档：CMCReport《{source_filename}》") == 1

        generated = (
            db.query(GeneratedReport)
            .filter(GeneratedReport.job_id == job.id)
            .one()
        )
        edge = generated.rules_summary["source_document_edge"]
        assert edge["predicate_iri"] == BASED_ON_SOURCE_DOCUMENT_IRI
        assert edge["source_ref"] == f"urn:slpra:extraction-job:{job.id}"
        assert edge["object_data_properties"] == [{
            "iri": DOCUMENT_NAME_IRI,
            "label": "文档名称",
            "value": source_filename,
        }]

        Path(generated.file_path).unlink(missing_ok=True)
        _remove_cache(job.id)
