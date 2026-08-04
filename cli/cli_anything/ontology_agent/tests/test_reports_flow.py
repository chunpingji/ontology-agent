"""The generate/poll/download state machine in core/reports.py."""

from __future__ import annotations

import pytest

from cli_anything.ontology_agent.core import reports as reports_mod
from cli_anything.ontology_agent.core.client import FileOrJson
from cli_anything.ontology_agent.core.errors import ApiError, ReportFailedError, WaitTimeoutError

_NO_SLEEP = lambda _seconds: None  # noqa: E731
_ZERO_CLOCK = lambda: 0.0  # noqa: E731


class FakeClock:
    """Return each value once, then repeat the last — for deadline tests."""

    def __init__(self, values):
        self._values = list(values)
        self._i = 0

    def __call__(self) -> float:
        v = self._values[min(self._i, len(self._values) - 1)]
        self._i += 1
        return v


def test_generate_sync_returns_file(stub):
    stub.file_or_json = FileOrJson(is_file=True, content=b"DOCX", filename="r.docx")
    result = reports_mod.generate_report(stub, "J", sleep=_NO_SLEEP, clock=_ZERO_CLOCK)
    assert result.mode == "sync"
    assert result.content == b"DOCX"
    assert result.filename == "r.docx"


def test_generate_async_completed_downloads_by_exact_id(stub):
    stub.file_or_json = FileOrJson(is_file=False, json={"report_id": "R1", "status": "pending"})
    stub.status_responses = [
        {"report_status": "pending"},
        {"report_status": "running"},
        {"report_status": "completed"},
    ]
    stub.file_download = (b"DOCX", "r.docx")
    result = reports_mod.generate_report(stub, "J", sleep=_NO_SLEEP, clock=_ZERO_CLOCK)
    assert result.mode == "async-completed"
    assert result.report_id == "R1"
    assert result.content == b"DOCX"
    # download must target the exact report id
    assert any(c[0] == "GETFILE" and "/reports/R1/download" in c[1] for c in stub.calls)


def test_generate_async_failed_raises(stub):
    stub.file_or_json = FileOrJson(is_file=False, json={"report_id": "R2", "status": "pending"})
    stub.status_responses = [
        {"report_status": "pending"},
        {"report_status": "failed", "report_error": "boom"},
    ]
    with pytest.raises(ReportFailedError) as exc:
        reports_mod.generate_report(stub, "J", sleep=_NO_SLEEP, clock=_ZERO_CLOCK)
    assert exc.value.report_id == "R2"
    assert "boom" in exc.value.message


def test_generate_async_timeout_reports_id(stub):
    stub.file_or_json = FileOrJson(is_file=False, json={"report_id": "R3", "status": "pending"})
    stub.status_responses = [{"report_status": "pending"}] * 5
    clock = FakeClock([0.0, 5.0, 15.0])  # deadline = 0 + 10
    with pytest.raises(WaitTimeoutError) as exc:
        reports_mod.generate_report(
            stub, "J", wait_timeout=10.0, sleep=_NO_SLEEP, clock=clock
        )
    assert exc.value.report_id == "R3"


def test_generate_no_wait_returns_pending(stub):
    stub.file_or_json = FileOrJson(is_file=False, json={"report_id": "R4", "status": "pending"})
    result = reports_mod.generate_report(stub, "J", wait=False)
    assert result.mode == "async-pending"
    assert result.report_id == "R4"
    assert result.status == "pending"


def test_generate_missing_report_id_raises_api_error(stub):
    stub.file_or_json = FileOrJson(is_file=False, json={"status": "pending"})
    with pytest.raises(ApiError):
        reports_mod.generate_report(stub, "J", sleep=_NO_SLEEP, clock=_ZERO_CLOCK)
