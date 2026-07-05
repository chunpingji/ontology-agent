"""012 T035: End-to-end multi-template validation.

Verifies that the full pipeline (upload template → set iri_pattern →
template resolution → coverage manifest) works for a second document type
without code changes and with zero regression on the existing CMCReport
pipeline. (015: iri_pattern replaces the retired DocumentTypeMapping.)
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from app.services.reporting.ast_template import (
    OntologyRelationBinding,
    ReportTemplate,
    Section,
    load_template_file,
    resolve_template,
)

from tests.fixtures.ontology import (
    DRUG_PRODUCT,
    MANUFACTURED_BY,
    MANUFACTURER,
    MFR_NAME,
    build_drug_ontology,
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
        """Seed DB with both CMC and Stability templates keyed by iri_pattern."""
        from app.models.extraction import AstTemplate
        from app.services.reporting.ast_template import load_default_template

        cmc_tpl = load_default_template()
        cmc_row = AstTemplate(
            id=uuid.uuid4(),
            name="CMC Risk Assessment",
            version="v1",
            schema_json=cmc_tpl.model_dump(),
            is_default=True,
            iri_pattern="CMCReport",
            status="published",
        )
        db_session.add(cmc_row)

        stab_tpl = load_template_file(STABILITY_TEMPLATE_PATH)
        stab_row = AstTemplate(
            id=uuid.uuid4(),
            name="Stability Evaluation",
            version="v1",
            schema_json=stab_tpl.model_dump(),
            is_default=False,
            iri_pattern="StabilityEvaluation",
            status="published",
        )
        db_session.add(stab_row)

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
        assert source == "iri_pattern"
        assert db_id == cmc_row.id
        assert template.template_id == "QS-A-020F05@v1"

    def test_stability_resolves_to_stability_template(self, db_session):
        _, stab_row = self._seed_db(db_session)
        template, source, db_id = resolve_template(
            "http://example.org/slpra#StabilityEvaluation", db_session,
        )
        assert source == "iri_pattern"
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

    def test_deleted_template_no_longer_resolves_by_iri_pattern(self, db_session):
        """Deleting a template removes it from iri_pattern resolution (015).

        The retired DocumentTypeMapping cascade is gone; iri_pattern lives on the
        template row itself, so deletion inherently drops the resolution key.
        """
        from app.models.extraction import AstTemplate

        tpl = AstTemplate(
            id=uuid.uuid4(),
            name="ToDelete",
            version="v1",
            schema_json=load_template_file(STABILITY_TEMPLATE_PATH).model_dump(),
            is_default=False,
            iri_pattern="TestDoc",
            status="published",
        )
        db_session.add(tpl)
        db_session.commit()

        _, source, db_id = resolve_template(
            "http://example.org/slpra#TestDoc", db_session,
        )
        assert source == "iri_pattern"
        assert db_id == tpl.id

        db_session.delete(tpl)
        db_session.commit()

        # No templates left → resolution falls through to filesystem fallback.
        _, source, db_id = resolve_template(
            "http://example.org/slpra#TestDoc", db_session,
        )
        assert source == "fallback"
        assert db_id is None

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


# --------------------------------------------------------------------------- #
# 016 US2 (T011): DB-authored section coverage drives generation E2E
# --------------------------------------------------------------------------- #

_REL_ID = "coverage.manufacturedBy__Manufacturer"


def _mfr_edge(name: str = "Acme 制药") -> dict:
    """An edge whose object is a Manufacturer individual (the declared range type)."""
    return {
        "predicate_iri": MANUFACTURED_BY,
        "object_class_iri": MANUFACTURER,
        "object_text": None,
        "object_data_properties": [{"iri": MFR_NAME, "label": "企业名称", "value": name}],
        "source_ref": "§ 生产信息",
    }


def _drug_coverage_template() -> ReportTemplate:
    """A drug template whose two sections BOTH declare the same required
    manufacturedBy → Manufacturer relationship (exercises cross-section dedup)."""
    rel = OntologyRelationBinding(
        doc_class_iri=DRUG_PRODUCT,
        predicate_iri=MANUFACTURED_BY,
        range_class_iri=MANUFACTURER,
        required=True,
    )
    return ReportTemplate(
        template_id="DRUG-COV@v1",
        sections=[
            Section(section_id="s1", title="概述", groups=[], coverage=[rel]),
            Section(section_id="s2", title="生产信息", groups=[], coverage=[rel]),
        ],
    )


class TestCoverageDrivesGeneration:
    """016 US2 / SC-003 / D5: DB-authored coverage survives ``resolve_template``
    and drives the omission signal; legacy templates do not regress (SC-006)."""

    @pytest.fixture()
    def db_session(self):
        from sqlalchemy import create_engine
        from sqlalchemy.orm import Session

        from app.db import Base

        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        with Session(engine) as session:
            yield session

    def _seed_drug_template(self, db_session):
        from app.models.extraction import AstTemplate

        row = AstTemplate(
            id=uuid.uuid4(),
            name="Drug Coverage",
            version="v1",
            schema_json=_drug_coverage_template().model_dump(),
            is_default=False,
            iri_pattern="DrugProduct",
            status="published",
        )
        db_session.add(row)
        db_session.commit()
        return row

    def test_coverage_survives_db_round_trip(self, db_session):
        """model_dump → schema_json → model_validate must preserve section coverage
        (the declared-field invariant, C2 / T003)."""
        self._seed_drug_template(db_session)
        template, source, _ = resolve_template(DRUG_PRODUCT, db_session)
        assert source == "iri_pattern"
        # both sections carry the ontology_relation binding after the DB round-trip
        covered = [s for s in template.sections if getattr(s, "coverage", [])]
        assert len(covered) == 2
        binding = covered[0].coverage[0]
        assert binding.kind == "ontology_relation"
        assert binding.predicate_iri == MANUFACTURED_BY
        assert binding.range_class_iri == MANUFACTURER

    def test_coverage_drives_generation(self, db_session):
        """The resolved coverage produces a deduped omission when the required
        relationship's target type is absent, and none when present."""
        from app.services.reporting.coverage_validator import validate_coverage

        self._seed_drug_template(db_session)
        template, _, _ = resolve_template(DRUG_PRODUCT, db_session)
        engine = build_drug_ontology()

        # target type absent → exactly one omission though TWO sections declare it (D3)
        m_absent = validate_coverage(template, [], [], engine=engine)
        assert m_absent.missing_required == 1
        rel_positions = [s for s in m_absent.slots if s.slot_id == _REL_ID]
        assert len(rel_positions) == 2  # both sections surface the position

        # target type present → coverage contributes zero omissions
        m_present = validate_coverage(template, [_mfr_edge()], [], engine=engine)
        assert m_present.missing_required == 0

    def test_legacy_missing_required_unchanged_with_engine(self, db_session):
        """SC-006 regression anchor: passing an engine must NOT perturb a
        coverage-free template — the stability template stays at 11."""
        from app.services.reporting.coverage_validator import validate_coverage

        template = load_template_file(STABILITY_TEMPLATE_PATH)
        engine = build_drug_ontology()
        base = validate_coverage(template, [], [], None)
        witheng = validate_coverage(template, [], [], engine=engine)
        assert base.missing_required == 11
        assert witheng.missing_required == 11
        assert witheng.total_slots == base.total_slots
        # a coverage-free template emits no synthetic coverage positions
        assert not any(s.slot_id.startswith("coverage.") for s in witheng.slots)
