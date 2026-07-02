"""012 T035: End-to-end multi-template validation.

Verifies that the full pipeline (upload template → create mapping →
template resolution → coverage manifest) works for a second document type
without code changes and with zero regression on the existing CMCReport
pipeline.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from app.services.reporting.ast_template import (
    ReportTemplate,
    load_template_file,
    resolve_template,
)

STABILITY_TEMPLATE_PATH = (
    Path(__file__).resolve().parents[2]
    / "app"
    / "services"
    / "reporting"
    / "templates"
    / "stability_evaluation.json"
)


class TestStabilityTemplate:
    """Validate the stability evaluation template."""

    def test_loads_and_validates(self):
        t = load_template_file(STABILITY_TEMPLATE_PATH)
        assert t.template_id == "STABILITY-EVAL@v1"
        assert len(t.sections) == 3

    def test_slot_count(self):
        t = load_template_file(STABILITY_TEMPLATE_PATH)
        slots = list(t.iter_slots())
        assert len(slots) == 17

    def test_required_slots(self):
        t = load_template_file(STABILITY_TEMPLATE_PATH)
        required = t.required_slots()
        assert len(required) == 11

    def test_no_duplicate_slot_ids(self):
        t = load_template_file(STABILITY_TEMPLATE_PATH)
        ids = [s.slot_id for _, _, s in t.iter_slots()]
        assert len(ids) == len(set(ids))

    def test_manual_slots_exist(self):
        t = load_template_file(STABILITY_TEMPLATE_PATH)
        manual = [s for _, _, s in t.iter_slots() if s.source.kind == "manual"]
        assert len(manual) == 2


class TestMultiTemplateResolution:
    """Verify resolve_template picks the right template per document type."""

    def _seed_db(self, db_session):
        """Seed DB with both CMC and Stability templates + mappings."""
        from app.models.extraction import AstTemplate, DocumentTypeMapping
        from app.services.reporting.ast_template import load_default_template

        cmc_tpl = load_default_template()
        cmc_row = AstTemplate(
            id=uuid.uuid4(),
            name="CMC Risk Assessment",
            version="v1",
            schema_json=cmc_tpl.model_dump(),
            is_default=True,
        )
        db_session.add(cmc_row)

        stab_tpl = load_template_file(STABILITY_TEMPLATE_PATH)
        stab_row = AstTemplate(
            id=uuid.uuid4(),
            name="Stability Evaluation",
            version="v1",
            schema_json=stab_tpl.model_dump(),
            is_default=False,
        )
        db_session.add(stab_row)

        db_session.add(DocumentTypeMapping(
            id=uuid.uuid4(),
            doc_class_iri_pattern="CMCReport",
            template_id=cmc_row.id,
            priority=10,
        ))
        db_session.add(DocumentTypeMapping(
            id=uuid.uuid4(),
            doc_class_iri_pattern="StabilityEvaluation",
            template_id=stab_row.id,
            priority=10,
        ))

        db_session.commit()
        return cmc_row, stab_row

    @pytest.fixture()
    def db_session(self):
        from sqlalchemy import create_engine
        from sqlalchemy.orm import Session

        from app.db import Base

        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        with Session(engine) as session:
            yield session

    def test_cmc_report_resolves_to_cmc_template(self, db_session):
        cmc_row, _ = self._seed_db(db_session)
        template, source, db_id = resolve_template(
            "http://example.org/slpra#CMCReport", db_session,
        )
        assert source == "mapping"
        assert db_id == cmc_row.id
        assert template.template_id == "QS-A-020F05@v1"

    def test_stability_resolves_to_stability_template(self, db_session):
        _, stab_row = self._seed_db(db_session)
        template, source, db_id = resolve_template(
            "http://example.org/slpra#StabilityEvaluation", db_session,
        )
        assert source == "mapping"
        assert db_id == stab_row.id
        assert template.template_id == "STABILITY-EVAL@v1"

    def test_unknown_doc_type_falls_back_to_default(self, db_session):
        cmc_row, _ = self._seed_db(db_session)
        template, source, db_id = resolve_template(
            "http://example.org/slpra#CleaningValidation", db_session,
        )
        assert source == "default"
        assert db_id == cmc_row.id

    def test_zero_regression_cmc_slot_structure(self, db_session):
        """CMC template retains its original slot structure."""
        self._seed_db(db_session)
        template, _, _ = resolve_template(
            "http://example.org/slpra#CMCReport", db_session,
        )
        slot_ids = {s.slot_id for _, _, s in template.iter_slots()}
        assert "subject.name" in slot_ids
        assert "subject.pde" in slot_ids

    def test_stability_template_slot_structure(self, db_session):
        """Stability template has its own unique slot structure."""
        self._seed_db(db_session)
        template, _, _ = resolve_template(
            "http://example.org/slpra#StabilityEvaluation", db_session,
        )
        slot_ids = {s.slot_id for _, _, s in template.iter_slots()}
        assert "product.name" in slot_ids
        assert "study.type" in slot_ids
        assert "conclusion.shelf_life" in slot_ids
        assert "subject.name" not in slot_ids


class TestTemplateDeletionEdgeCase:
    """T036: Verify template deletion doesn't corrupt historical data."""

    @pytest.fixture()
    def db_session(self):
        from sqlalchemy import create_engine, event
        from sqlalchemy.orm import Session

        from app.db import Base

        engine = create_engine("sqlite:///:memory:")

        @event.listens_for(engine, "connect")
        def _enable_fk(dbapi_conn, _rec):
            dbapi_conn.execute("PRAGMA foreign_keys=ON")

        Base.metadata.create_all(engine)
        with Session(engine) as session:
            yield session

    def test_mapping_cascade_deletes_with_template(self, db_session):
        from app.models.extraction import AstTemplate, DocumentTypeMapping

        tpl = AstTemplate(
            id=uuid.uuid4(),
            name="ToDelete",
            version="v1",
            schema_json=load_template_file(STABILITY_TEMPLATE_PATH).model_dump(),
            is_default=False,
        )
        db_session.add(tpl)
        db_session.flush()

        mapping = DocumentTypeMapping(
            id=uuid.uuid4(),
            doc_class_iri_pattern="TestDoc",
            template_id=tpl.id,
            priority=5,
        )
        db_session.add(mapping)
        db_session.commit()

        assert db_session.query(DocumentTypeMapping).count() == 1

        db_session.delete(tpl)
        db_session.commit()

        assert db_session.query(DocumentTypeMapping).count() == 0

    def test_generated_report_survives_template_deletion(self, db_session):
        """GeneratedReport has no FK to AstTemplate — snapshots are independent."""
        from app.models.extraction import AstTemplate, GeneratedReport, ExtractionJob

        tpl = AstTemplate(
            id=uuid.uuid4(),
            name="Temp",
            version="v1",
            schema_json=load_template_file(STABILITY_TEMPLATE_PATH).model_dump(),
            is_default=False,
        )
        db_session.add(tpl)

        job = ExtractionJob(
            id=uuid.uuid4(),
            source_type="docx",
            source_filename="test.docx",
            status="completed",
        )
        db_session.add(job)
        db_session.flush()

        report = GeneratedReport(
            id=uuid.uuid4(),
            job_id=job.id,
            report_type="risk_assessment",
            file_path="/fake/report.docx",
            rules_fired_count=3,
            rules_summary={"template_name": "Temp", "version": "v1"},
            actor="test_user",
        )
        db_session.add(report)
        db_session.commit()

        db_session.delete(tpl)
        db_session.commit()

        surviving = db_session.get(GeneratedReport, report.id)
        assert surviving is not None
        assert surviving.rules_summary["template_name"] == "Temp"


class TestCoverageWithStabilityTemplate:
    """Validate that coverage validation works with the stability template."""

    def test_coverage_manifest_uses_stability_slots(self):
        from app.services.reporting.coverage_validator import validate_coverage

        template = load_template_file(STABILITY_TEMPLATE_PATH)
        manifest = validate_coverage(template, [], [], MagicMock())
        assert manifest.total_slots > 0
        assert manifest.missing_required == 11
        slot_ids = {s.slot_id for s in manifest.slots}
        assert "product.name" in slot_ids
        assert "study.type" in slot_ids
