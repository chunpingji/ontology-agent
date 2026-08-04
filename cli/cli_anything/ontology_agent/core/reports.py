"""Risk-assessment report domain layer — the heart of the CLI.

Mirrors ``frontend/src/lib/api.ts::generateRiskReportBlob`` (the exact call the
Report Center's "生成风险评估报告" action makes), hardened for a headless client:

* ``POST /jobs/{id}/risk-report`` returns **either** a ready ``.docx`` (sync, when
  the backend's LLM flags are off) **or** ``{report_id,status}`` (async, the
  default) — distinguished by ``Content-Type``.
* async → poll ``GET /jobs/{id}/reports/{report_id}`` until ``completed`` (then
  download **by that exact id**) or ``failed`` (surface ``report_error``); a
  monotonic deadline bounds the wait.

The frontend polls 2000 ms × 60 (~120 s). We keep 2 s / 120 s as defaults but
expose both so agents/humans can tune or opt out (``wait=False``).
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Callable

from cli_anything.ontology_agent.core.client import HttpClient
from cli_anything.ontology_agent.core.errors import ApiError, ReportFailedError, WaitTimeoutError

DEFAULT_POLL_INTERVAL = 2.0
DEFAULT_WAIT_TIMEOUT = 120.0


@dataclass
class GenerateResult:
    """Outcome of :func:`generate_report`.

    ``mode`` ∈ {"sync", "async-completed", "async-pending"}. For the two
    terminal modes ``content``/``filename`` hold the ``.docx``; for
    "async-pending" (``wait=False``) only ``report_id``/``status`` are set.
    """

    mode: str
    content: bytes | None = None
    filename: str | None = None
    report_id: str | None = None
    status: str | None = None


def generate_report(
    client: HttpClient,
    job_id: str,
    *,
    wait: bool = True,
    poll_interval: float = DEFAULT_POLL_INTERVAL,
    wait_timeout: float = DEFAULT_WAIT_TIMEOUT,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> GenerateResult:
    """Generate a risk-assessment report for ``job_id``.

    Server preconditions (job exists, doc classified as ``CMCReport`` with
    relationships) surface as typed errors (404 / 422) from the client.
    """
    resp = client.request_file_or_json("POST", f"/api/extraction/jobs/{job_id}/risk-report")

    if resp.is_file:
        return GenerateResult(mode="sync", content=resp.content, filename=resp.filename)

    data = resp.json or {}
    report_id = data.get("report_id")
    if not report_id:
        raise ApiError("生成响应缺少 report_id", status_code=200, detail=data)

    if not wait:
        return GenerateResult(
            mode="async-pending", report_id=report_id, status=data.get("status", "pending")
        )

    wait_for_report(
        client,
        job_id,
        report_id,
        poll_interval=poll_interval,
        wait_timeout=wait_timeout,
        sleep=sleep,
        clock=clock,
    )
    content, filename = download_report(client, job_id, report_id)
    return GenerateResult(
        mode="async-completed",
        content=content,
        filename=filename,
        report_id=report_id,
        status="completed",
    )


def wait_for_report(
    client: HttpClient,
    job_id: str,
    report_id: str,
    *,
    poll_interval: float = DEFAULT_POLL_INTERVAL,
    wait_timeout: float = DEFAULT_WAIT_TIMEOUT,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> dict[str, Any]:
    """Poll until the report is ``completed`` (return its status dict), raising
    :class:`ReportFailedError` on ``failed`` or :class:`WaitTimeoutError` once
    the monotonic deadline passes."""
    deadline = clock() + wait_timeout
    last_status: str | None = None
    while True:
        status = get_report_status(client, job_id, report_id)
        report_status = status.get("report_status")
        last_status = report_status
        if report_status == "completed":
            return status
        if report_status == "failed":
            raise ReportFailedError(
                status.get("report_error") or "报告生成失败",
                report_id=report_id,
                detail=status,
            )
        if clock() >= deadline:
            raise WaitTimeoutError(
                f"报告生成超时（超过 {wait_timeout:.0f}s）；可稍后用 report download 取件，report_id={report_id}",
                report_id=report_id,
                detail={"report_id": report_id, "last_status": last_status},
            )
        sleep(poll_interval)


def get_report_status(client: HttpClient, job_id: str, report_id: str) -> dict[str, Any]:
    """``GET /jobs/{id}/reports/{report_id}`` — status poll (report_status/report_error/…)."""
    return client.get(f"/api/extraction/jobs/{job_id}/reports/{report_id}")


def download_report(client: HttpClient, job_id: str, report_id: str) -> tuple[bytes, str | None]:
    """Download a specific report's ``.docx`` by exact id (409 if not completed)."""
    return client.get_file(f"/api/extraction/jobs/{job_id}/reports/{report_id}/download")


def download_latest(client: HttpClient, job_id: str) -> tuple[bytes, str | None]:
    """Download the job's latest completed report ``.docx`` (404 if none)."""
    return client.get_file(f"/api/extraction/jobs/{job_id}/risk-report")


def list_reports(client: HttpClient, job_id: str) -> list[dict[str, Any]]:
    """``GET /jobs/{id}/reports`` — completed, non-deleted reports for the job."""
    return client.get(f"/api/extraction/jobs/{job_id}/reports")


def delete_report(client: HttpClient, job_id: str, report_id: str) -> None:
    """``DELETE /jobs/{id}/reports/{report_id}`` — soft delete (needs senior_analyst)."""
    client.delete(f"/api/extraction/jobs/{job_id}/reports/{report_id}")


def get_coverage(client: HttpClient, job_id: str, *, template_id: str | None = None) -> dict[str, Any]:
    """``GET /jobs/{id}/ast-coverage`` — the no-omission coverage manifest."""
    params = {"template_id": template_id} if template_id else None
    return client.get(f"/api/extraction/jobs/{job_id}/ast-coverage", params=params)
