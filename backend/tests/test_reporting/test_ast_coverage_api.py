"""API tests for AST coverage, dismiss, undismiss, and reports endpoints (011, T030)."""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from unittest.mock import patch

import pytest

from app.models.extraction import ExtractionJob, GeneratedReport, SlotDismissal
from app.services import audit as audit_mod

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
        "doc_class": {"doc_class_iri": doc_class_iri, "label": "CMC变更报告"} if doc_class else None,
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
            {"iri": "https://ontology.pharma-gmp.cn/slpra/drug/pde_mg_per_day",
             "label": "PDE", "value": "1.80"},
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


class TestGetAstCoverage:
    def test_success(self, client, db):
        job = _create_job(db)
        _write_cache(job.id, edges=[_drug_edge(), _equipment_edge()])
        r = client.get(f"/api/extraction/jobs/{job.id}/ast-coverage", headers=HEADERS)
        assert r.status_code == 200
        body = r.json()
        assert "template_id" in body
        assert "sections" in body
        assert body["total_slots"] > 0
        assert body["filled"] >= 0
        _remove_cache(job.id)

    def test_422_when_cache_missing(self, client, db):
        job = _create_job(db)
        _remove_cache(job.id)
        r = client.get(f"/api/extraction/jobs/{job.id}/ast-coverage", headers=HEADERS)
        assert r.status_code == 422

    def test_non_cmc_report_resolves_via_fallback(self, client, db):
        """012: any doc type now resolves via template fallback instead of 422."""
        job = _create_job(db)
        _write_cache(job.id, doc_class_iri="https://example.org/OtherDoc")
        r = client.get(f"/api/extraction/jobs/{job.id}/ast-coverage", headers=HEADERS)
        assert r.status_code == 200
        assert r.json()["total_slots"] > 0
        _remove_cache(job.id)

    def test_422_when_doc_class_null(self, client, db):
        job = _create_job(db)
        _write_cache(job.id, doc_class=False)
        r = client.get(f"/api/extraction/jobs/{job.id}/ast-coverage", headers=HEADERS)
        assert r.status_code == 422
        _remove_cache(job.id)

    def test_404_when_job_missing(self, client, db):
        r = client.get(f"/api/extraction/jobs/{uuid.uuid4()}/ast-coverage", headers=HEADERS)
        assert r.status_code == 404

    def test_template_id_query_param(self, client, db):
        """012: explicit template_id overrides auto-resolution."""
        from app.models.extraction import AstTemplate
        from app.services.reporting.ast_template import load_default_template

        tpl_json = load_default_template().model_dump()
        row = AstTemplate(
            name="Custom", version="v1", doc_no="CUSTOM",
            schema_json=tpl_json, is_default=False, created_by="test",
        )
        db.add(row)
        db.commit()
        db.refresh(row)

        job = _create_job(db)
        _write_cache(job.id, edges=[_drug_edge()])
        r = client.get(
            f"/api/extraction/jobs/{job.id}/ast-coverage?template_id={row.id}",
            headers=HEADERS,
        )
        assert r.status_code == 200
        body = r.json()
        assert body["template_name"] == "Custom"
        assert body["template_version"] == "v1"
        _remove_cache(job.id)

    def test_template_id_not_found(self, client, db):
        """012: non-existent template_id returns 404."""
        job = _create_job(db)
        _write_cache(job.id, edges=[_drug_edge()])
        r = client.get(
            f"/api/extraction/jobs/{job.id}/ast-coverage?template_id={uuid.uuid4()}",
            headers=HEADERS,
        )
        assert r.status_code == 404
        _remove_cache(job.id)

    def test_coverage_counts_consistent(self, client, db):
        job = _create_job(db)
        _write_cache(job.id, edges=[_drug_edge()])
        r = client.get(f"/api/extraction/jobs/{job.id}/ast-coverage", headers=HEADERS)
        body = r.json()
        computed_total = (
            body["filled"] + body["inferred"] + body["missing_required"]
            + body["blank_optional"] + body["manual"] + body["dismissed"]
        )
        assert body["total_slots"] == computed_total
        _remove_cache(job.id)


# --------------------------------------------------------------------------- #
# POST /ast-coverage/dismiss + DELETE /ast-coverage/dismiss/{slot_id}
# --------------------------------------------------------------------------- #


class TestDismissUndismiss:
    def test_dismiss_success(self, client, db):
        job = _create_job(db)
        _write_cache(job.id, edges=[_drug_edge()])
        r_cov = client.get(f"/api/extraction/jobs/{job.id}/ast-coverage", headers=HEADERS)
        missing_before = r_cov.json()["missing_required"]

        missing_slots = []
        for sec in r_cov.json()["sections"]:
            for grp in sec["groups"]:
                for slot in grp["slots"]:
                    if slot["status"] == "missing_required":
                        missing_slots.append(slot["slot_id"])
        if not missing_slots:
            _remove_cache(job.id)
            pytest.skip("No missing slots to dismiss")

        slot_id = missing_slots[0]
        r = client.post(
            f"/api/extraction/jobs/{job.id}/ast-coverage/dismiss",
            json={"slot_id": slot_id},
            headers=HEADERS,
        )
        assert r.status_code == 200
        body = r.json()
        assert body["dismissed"] >= 1
        assert body["missing_required"] < missing_before

        row = db.query(SlotDismissal).filter_by(job_id=job.id, slot_id=slot_id).first()
        assert row is not None
        assert row.dismissed_by == "analyst"

        _remove_cache(job.id)

    def test_dismiss_duplicate_409(self, client, db):
        job = _create_job(db)
        _write_cache(job.id, edges=[_drug_edge()])
        r_cov = client.get(f"/api/extraction/jobs/{job.id}/ast-coverage", headers=HEADERS)
        missing_slots = []
        for sec in r_cov.json()["sections"]:
            for grp in sec["groups"]:
                for slot in grp["slots"]:
                    if slot["status"] == "missing_required":
                        missing_slots.append(slot["slot_id"])
        if not missing_slots:
            _remove_cache(job.id)
            pytest.skip("No missing slots to dismiss")

        slot_id = missing_slots[0]
        client.post(
            f"/api/extraction/jobs/{job.id}/ast-coverage/dismiss",
            json={"slot_id": slot_id},
            headers=HEADERS,
        )
        r2 = client.post(
            f"/api/extraction/jobs/{job.id}/ast-coverage/dismiss",
            json={"slot_id": slot_id},
            headers=HEADERS,
        )
        assert r2.status_code == 409
        _remove_cache(job.id)

    def test_undismiss_success(self, client, db):
        job = _create_job(db)
        _write_cache(job.id, edges=[_drug_edge()])
        r_cov = client.get(f"/api/extraction/jobs/{job.id}/ast-coverage", headers=HEADERS)
        missing_slots = []
        for sec in r_cov.json()["sections"]:
            for grp in sec["groups"]:
                for slot in grp["slots"]:
                    if slot["status"] == "missing_required":
                        missing_slots.append(slot["slot_id"])
        if not missing_slots:
            _remove_cache(job.id)
            pytest.skip("No missing slots")

        slot_id = missing_slots[0]
        client.post(
            f"/api/extraction/jobs/{job.id}/ast-coverage/dismiss",
            json={"slot_id": slot_id},
            headers=HEADERS,
        )

        r = client.delete(
            f"/api/extraction/jobs/{job.id}/ast-coverage/dismiss/{slot_id}",
            headers=HEADERS,
        )
        assert r.status_code == 200
        body = r.json()
        dismissed_slot = None
        for sec in body["sections"]:
            for grp in sec["groups"]:
                for slot in grp["slots"]:
                    if slot["slot_id"] == slot_id:
                        dismissed_slot = slot
        assert dismissed_slot is not None
        assert dismissed_slot["status"] == "missing_required"

        row = db.query(SlotDismissal).filter_by(job_id=job.id, slot_id=slot_id).first()
        assert row is None

        _remove_cache(job.id)

    def test_undismiss_404(self, client, db):
        job = _create_job(db)
        _write_cache(job.id, edges=[_drug_edge()])
        r = client.delete(
            f"/api/extraction/jobs/{job.id}/ast-coverage/dismiss/nonexistent.slot",
            headers=HEADERS,
        )
        assert r.status_code == 404
        _remove_cache(job.id)

    def test_dismiss_audit_logged(self, client, db):
        job = _create_job(db)
        _write_cache(job.id, edges=[_drug_edge()])
        r_cov = client.get(f"/api/extraction/jobs/{job.id}/ast-coverage", headers=HEADERS)
        missing_slots = []
        for sec in r_cov.json()["sections"]:
            for grp in sec["groups"]:
                for slot in grp["slots"]:
                    if slot["status"] == "missing_required":
                        missing_slots.append(slot["slot_id"])
        if not missing_slots:
            _remove_cache(job.id)
            pytest.skip("No missing slots")

        slot_id = missing_slots[0]
        client.post(
            f"/api/extraction/jobs/{job.id}/ast-coverage/dismiss",
            json={"slot_id": slot_id},
            headers=HEADERS,
        )

        from app.models.reasoning import AuditLog
        logs = (
            db.query(AuditLog)
            .filter(AuditLog.action == "slot.dismiss")
            .all()
        )
        assert len(logs) >= 1
        log = logs[-1]
        assert log.actor == "analyst"
        assert slot_id in str(log.details)

        _remove_cache(job.id)


# --------------------------------------------------------------------------- #
# 016 T015: section.coverage surfaces as ghost/coverage rows (D13)
# --------------------------------------------------------------------------- #


def _mfr_edge(name: str = "Acme 制药") -> dict:
    from tests.fixtures.ontology import MANUFACTURED_BY, MANUFACTURER, MFR_NAME

    return {
        "predicate_iri": MANUFACTURED_BY,
        "object_class_iri": MANUFACTURER,
        "object_text": None,
        "object_data_properties": [{"iri": MFR_NAME, "label": "企业名称", "value": name}],
        "source_ref": "§ 生产信息",
    }


def _seed_coverage_template(db):
    """A DB template whose section declares a required manufacturedBy→Manufacturer
    coverage binding, keyed by an iri_pattern matching the drug doc class."""
    from app.models.extraction import AstTemplate
    from app.services.reporting.ast_template import (
        OntologyRelationBinding,
        ReportTemplate,
        Section,
    )
    from tests.fixtures.ontology import DRUG_PRODUCT, MANUFACTURED_BY, MANUFACTURER

    rel = OntologyRelationBinding(
        doc_class_iri=DRUG_PRODUCT,
        predicate_iri=MANUFACTURED_BY,
        range_class_iri=MANUFACTURER,
        required=True,
    )
    tpl = ReportTemplate(
        template_id="DRUG-COV@v1",
        sections=[Section(section_id="s1", title="生产信息", groups=[], coverage=[rel])],
    )
    row = AstTemplate(
        id=uuid.uuid4(),
        name="Drug Coverage",
        version="v1",
        schema_json=tpl.model_dump(),
        is_default=False,
        iri_pattern="DrugProduct",
        status="published",
    )
    db.add(row)
    db.commit()
    return row


class TestCoverageEmission:
    """016 US3/D13: section-level coverage declarations render as coverage rows in
    the AST tree, degrading gracefully when the engine is unloaded (substring
    presence fallback)."""

    def test_missing_required_relationship_surfaces(self, client, db):
        from tests.fixtures.ontology import DRUG_PRODUCT

        _seed_coverage_template(db)
        job = _create_job(db)
        # a drug edge with NO Manufacturer target → required relationship absent
        _write_cache(job.id, edges=[_drug_edge()], doc_class_iri=DRUG_PRODUCT)
        r = client.get(f"/api/extraction/jobs/{job.id}/ast-coverage", headers=HEADERS)
        assert r.status_code == 200
        body = r.json()

        cov_slots = [
            slot
            for sec in body["sections"]
            for grp in sec["groups"]
            for slot in grp["slots"]
            if slot["slot_id"].startswith("coverage.")
        ]
        rel = [s for s in cov_slots if s["slot_id"] == "coverage.manufacturedBy__Manufacturer"]
        assert len(rel) == 1  # exactly one — no double-emit via template_expander
        assert rel[0]["status"] == "missing_required"
        assert rel[0]["source_kind"] == "ontology_relation"
        _remove_cache(job.id)

    def test_present_relationship_is_filled(self, client, db):
        from tests.fixtures.ontology import DRUG_PRODUCT

        _seed_coverage_template(db)
        job = _create_job(db)
        _write_cache(
            job.id, edges=[_drug_edge(), _mfr_edge()], doc_class_iri=DRUG_PRODUCT
        )
        r = client.get(f"/api/extraction/jobs/{job.id}/ast-coverage", headers=HEADERS)
        body = r.json()
        rel = [
            slot
            for sec in body["sections"]
            for grp in sec["groups"]
            for slot in grp["slots"]
            if slot["slot_id"] == "coverage.manufacturedBy__Manufacturer"
        ]
        assert len(rel) == 1
        assert rel[0]["status"] == "filled"
        # counts stay internally consistent with the new coverage rows
        computed = (
            body["filled"] + body["inferred"] + body["missing_required"]
            + body["blank_optional"] + body["manual"] + body["dismissed"]
        )
        assert body["total_slots"] == computed
        _remove_cache(job.id)


# --------------------------------------------------------------------------- #
# GET /reports
# --------------------------------------------------------------------------- #


class TestListReports:
    def test_empty_list(self, client, db):
        job = _create_job(db)
        r = client.get(f"/api/extraction/jobs/{job.id}/reports", headers=HEADERS)
        assert r.status_code == 200
        assert r.json() == []

    def test_returns_reports_newest_first(self, client, db):
        from datetime import datetime, timezone, timedelta

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
        assert reports[0]["file_path"] == "/tmp/r2.docx"
        assert reports[1]["file_path"] == "/tmp/r1.docx"
