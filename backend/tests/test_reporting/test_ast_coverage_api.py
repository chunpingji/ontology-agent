"""API tests for AST coverage, dismiss, undismiss, and reports endpoints (011, T030)."""

from __future__ import annotations

import json
from pathlib import Path

from app.models.extraction import ExtractionJob, GeneratedReport

HEADERS = {"X-User": "analyst", "X-Role": "senior_analyst"}

CMC_IRI = "https://ontology.pharma-gmp.cn/slpra/drug-development/CMCReport"


def _create_job(db, *, status: str = "done") -> ExtractionJob:
    job = ExtractionJob(
        source_type="upload",
        source_filename="HRS-1234.docx",
        document_path="/tmp/test.docx",
        status=status,
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


def _write_cache(job_id, *, edges=None, doc_class_iri=CMC_IRI, doc_class=True) -> Path:
    cache_path = Path("data/uploads") / f"{job_id}.annotated.json"
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "doc_class": {"doc_class_iri": doc_class_iri, "label": "CMC变更报告"}
        if doc_class
        else None,
        "relationships": edges or [_drug_edge()],
    }
    cache_path.write_text(json.dumps(data), encoding="utf-8")
    return cache_path


def _remove_cache(job_id) -> None:
    path = Path("data/uploads") / f"{job_id}.annotated.json"
    path.unlink(missing_ok=True)


def _drug_edge() -> dict:
    return {
        "predicate_iri": "https://ontology.pharma-gmp.cn/slpra/drug-development/describes",
        "object_class_iri": "https://ontology.pharma-gmp.cn/slpra/drug/DrugProduct",
        "object_text": "HRS-1234",
        "object_data_properties": [
            {
                "iri": "https://ontology.pharma-gmp.cn/slpra/drug/pde_mg_per_day",
                "label": "PDE",
                "value": "1.80",
            },
            {"iri": None, "label": "分类", "value": "化学药品"},
        ],
        "source_ref": "§ 产品信息",
    }


def _equipment_edge(code: str = "RE001") -> dict:
    return {
        "predicate_iri": "https://ontology.pharma-gmp.cn/slpra/drug-development/usesEquipment",
        "object_class_iri": "https://ontology.pharma-gmp.cn/slpra/equipment/Equipment",
        "object_text": code,
        "object_data_properties": [
            {"iri": None, "label": "设备名称", "value": f"设备-{code}"},
            {"iri": None, "label": "设备规格", "value": "搅拌釜 500L"},
            {"iri": None, "label": "材质", "value": "316L"},
        ],
        "source_ref": "642车间 设备需求",
    }


# --------------------------------------------------------------------------- #
# GET /ast-coverage
# --------------------------------------------------------------------------- #


class TestListReports:
    def test_empty_list(self, client, db):
        job = _create_job(db)
        r = client.get(f"/api/extraction/jobs/{job.id}/reports", headers=HEADERS)
        assert r.status_code == 200
        assert r.json() == []

    def test_returns_reports_newest_first(self, client, db):
        from datetime import datetime, timedelta, timezone

        job = _create_job(db)
        now = datetime.now(timezone.utc)
        r1 = GeneratedReport(
            job_id=job.id,
            report_type="risk_assessment",
            file_path="/tmp/r1.docx",
            file_size=100,
            rules_fired_count=5,
            rules_summary={"coverage": {"filled": 10}},
            actor="analyst",
            created_at=now - timedelta(hours=1),
        )
        r2 = GeneratedReport(
            job_id=job.id,
            report_type="risk_assessment",
            file_path="/tmp/r2.docx",
            file_size=200,
            rules_fired_count=7,
            rules_summary={"coverage": {"filled": 12}},
            actor="analyst",
            created_at=now,
        )
        db.add_all([r1, r2])
        db.commit()

        r = client.get(f"/api/extraction/jobs/{job.id}/reports", headers=HEADERS)
        assert r.status_code == 200
        reports = r.json()
        assert len(reports) == 2
        assert reports[0]["file_size"] == 200
        assert reports[1]["file_size"] == 100
