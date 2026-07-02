"""Tests for 013 LLM gap-fill merge into RiskReport (T018).

Verifies:
  - LLM gap-fill values land in ``report.llm_supplements``
  - Manifest ``is_llm_sourced`` is flipped for filled slots
  - Flags off → output identical to 012 baseline (SC-005)
  - Deterministic fields (rules, risk levels) unchanged (FR-009)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from unittest.mock import MagicMock, patch

import pytest

from app.services.reporting.risk_report_generator import RiskReport


@dataclass
class _FakeSlotCoverage:
    slot_id: str
    label: str
    status: str = "missing_required"
    source_kind: str = "extraction"
    is_llm_sourced: bool = False
    value: str | None = None
    source_ref: str | None = None
    rule_key: str | None = None
    hazid: str | None = None
    note: str | None = None
    source_span: str | None = None


@dataclass
class _FakeManifest:
    slots: list = field(default_factory=list)
    total_slots: int = 0
    filled: int = 0
    missing_required: int = 0
    missing_required_slots: list = field(default_factory=list)
    has_omissions: bool = False
    dismissed: int = 0
    dismissed_slots: list = field(default_factory=list)
    inferred: int = 0
    manual: int = 0

    def to_dict(self):
        return {"total_slots": self.total_slots}

    def summary(self):
        return "test"


class TestLLMMerge:
    def test_llm_supplements_populated_when_flag_on(self):
        report = RiskReport()
        slot = _FakeSlotCoverage(slot_id="subject.name", label="药品名称")
        manifest = _FakeManifest(
            slots=[slot],
            missing_required_slots=[slot],
            missing_required=1,
            has_omissions=True,
        )

        synthetic = [
            {"slot_id": "subject.name", "value": "阿莫西林", "source": "llm"},
        ]

        with (
            patch("app.config.settings") as mock_settings,
            patch(
                "app.services.extraction.llm_gap_filler.fill_coverage_gaps",
                return_value=synthetic,
            ),
        ):
            mock_settings.llm_report_merge_values = True

            from app.services.reporting.risk_report_generator import RiskReportGenerator

            gen = RiskReportGenerator.__new__(RiskReportGenerator)
            gen._template = MagicMock()
            gen._try_llm_merge(report, manifest, "/fake/doc.docx")

        assert "subject.name" in report.llm_supplements
        assert report.llm_supplements["subject.name"] == "阿莫西林"
        assert "subject.name" in report.llm_generated_fields
        assert slot.is_llm_sourced is True

    def test_flags_off_produces_no_supplements(self):
        report = RiskReport()
        slot = _FakeSlotCoverage(slot_id="subject.name", label="药品名称")
        manifest = _FakeManifest(
            slots=[slot],
            missing_required_slots=[slot],
            missing_required=1,
        )

        with patch("app.config.settings") as mock_settings:
            mock_settings.llm_report_merge_values = False

            from app.services.reporting.risk_report_generator import RiskReportGenerator

            gen = RiskReportGenerator.__new__(RiskReportGenerator)
            gen._try_llm_merge(report, manifest, "/fake/doc.docx")

        assert report.llm_supplements == {}
        assert report.llm_generated_fields == set()
        assert slot.is_llm_sourced is False

    def test_no_missing_slots_skips_merge(self):
        report = RiskReport()
        manifest = _FakeManifest(missing_required_slots=[], missing_required=0)

        with patch("app.config.settings") as mock_settings:
            mock_settings.llm_report_merge_values = True

            from app.services.reporting.risk_report_generator import RiskReportGenerator

            gen = RiskReportGenerator.__new__(RiskReportGenerator)
            gen._try_llm_merge(report, manifest, "/fake/doc.docx")

        assert report.llm_supplements == {}

    def test_deterministic_fields_unchanged_fr009(self):
        """FR-009: LLM merge must not alter assessment rows or risk levels."""
        from app.services.reporting.risk_report_generator import RiskRow

        row = RiskRow(
            hazid="人员",
            contributing_factors="test",
            pre_control_level="高",
            post_control_level="低",
            control_measures="measure",
            traceability="trace",
            status="可以接受",
        )
        report = RiskReport(assessment_rows=[row])
        slot = _FakeSlotCoverage(slot_id="some.slot", label="某字段")
        manifest = _FakeManifest(
            slots=[slot],
            missing_required_slots=[slot],
            missing_required=1,
        )

        with (
            patch("app.config.settings") as mock_settings,
            patch(
                "app.services.extraction.llm_gap_filler.fill_coverage_gaps",
                return_value=[{"slot_id": "some.slot", "value": "填充值", "source": "llm"}],
            ),
        ):
            mock_settings.llm_report_merge_values = True

            from app.services.reporting.risk_report_generator import RiskReportGenerator

            gen = RiskReportGenerator.__new__(RiskReportGenerator)
            gen._template = MagicMock()
            gen._try_llm_merge(report, manifest, "/fake/doc.docx")

        assert row.pre_control_level == "高"
        assert row.post_control_level == "低"
        assert row.status == "可以接受"
        assert row.hazid == "人员"
        assert "some.slot" in report.llm_supplements
