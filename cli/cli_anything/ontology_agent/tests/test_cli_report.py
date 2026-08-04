"""End-to-end CLI tests for the report commands (stubbed transport)."""

from __future__ import annotations

import json

from cli_anything.ontology_agent.core.client import FileOrJson
from cli_anything.ontology_agent.oa_cli import cli


def _data(result):
    return json.loads(result.output)["data"]


def _error_text(result):
    """Return the error envelope text, tolerant of Click's stderr handling."""
    try:
        return result.stderr
    except (ValueError, AttributeError):
        return result.output


# --------------------------------------------------------------------------- #
# generate — the primary flow
# --------------------------------------------------------------------------- #


def test_generate_sync_saves_file(runner, stub, install_client, tmp_path):
    stub.file_or_json = FileOrJson(is_file=True, content=b"DOCX", filename="r.docx")
    install_client(stub)
    out = tmp_path / "gen.docx"
    res = runner.invoke(cli, ["--json", "report", "generate", "--job-id", "J", "-o", str(out)])
    assert res.exit_code == 0, res.output
    data = _data(res)
    assert data["mode"] == "sync"
    assert data["job_id"] == "J"
    assert data["sha256"]
    assert out.read_bytes() == b"DOCX"


def test_generate_requires_exactly_one_target(runner, stub, install_client):
    install_client(stub)
    assert runner.invoke(cli, ["report", "generate"]).exit_code == 2
    assert runner.invoke(cli, ["report", "generate", "doc:1", "--job-id", "J"]).exit_code == 2


def test_generate_doc_without_job_is_precondition(runner, stub, install_client):
    stub.get_map["/api/entities"] = {"items": [{"iri": "doc:1", "properties_json": {}}]}
    install_client(stub)
    res = runner.invoke(cli, ["report", "generate", "doc:1"])
    assert res.exit_code == 7


def test_generate_from_doc_iri_resolves_job(runner, stub, install_client, tmp_path):
    stub.get_map["/api/entities"] = {"items": [{"iri": "doc:1", "properties_json": {"job_id": "J9"}}]}
    stub.file_or_json = FileOrJson(is_file=True, content=b"X", filename="r.docx")
    install_client(stub)
    out = tmp_path / "d.docx"
    res = runner.invoke(cli, ["--json", "report", "generate", "doc:1", "-o", str(out)])
    assert res.exit_code == 0, res.output
    assert _data(res)["job_id"] == "J9"
    assert out.read_bytes() == b"X"


def test_generate_async_completed_downloads(runner, stub, install_client, tmp_path):
    stub.file_or_json = FileOrJson(is_file=False, json={"report_id": "R", "status": "pending"})
    stub.status_responses = [{"report_status": "completed"}]
    stub.file_download = (b"DOCX", "r.docx")
    install_client(stub)
    out = tmp_path / "a.docx"
    res = runner.invoke(
        cli, ["--json", "report", "generate", "--job-id", "J", "--poll-interval", "0.01", "-o", str(out)]
    )
    assert res.exit_code == 0, res.output
    data = _data(res)
    assert data["mode"] == "async-completed"
    assert data["report_id"] == "R"
    assert out.read_bytes() == b"DOCX"


def test_generate_async_failed_exit_10(runner, stub, install_client):
    stub.file_or_json = FileOrJson(is_file=False, json={"report_id": "R", "status": "pending"})
    stub.status_responses = [{"report_status": "failed", "report_error": "boom"}]
    install_client(stub)
    res = runner.invoke(cli, ["--json", "report", "generate", "--job-id", "J", "--poll-interval", "0.01"])
    assert res.exit_code == 10
    env = json.loads(_error_text(res))
    assert env["ok"] is False
    assert env["error"]["code"] == "report_failed"


def test_generate_no_wait_returns_pending(runner, stub, install_client):
    stub.file_or_json = FileOrJson(is_file=False, json={"report_id": "R", "status": "pending"})
    install_client(stub)
    res = runner.invoke(cli, ["--json", "report", "generate", "--job-id", "J", "--no-wait"])
    assert res.exit_code == 0, res.output
    data = _data(res)
    assert data["mode"] == "async-pending"
    assert data["report_id"] == "R"


def test_generate_refuses_overwrite_without_force(runner, stub, install_client, tmp_path):
    stub.file_or_json = FileOrJson(is_file=True, content=b"DOCX", filename="r.docx")
    install_client(stub)
    out = tmp_path / "exists.docx"
    out.write_bytes(b"OLD")
    res = runner.invoke(cli, ["report", "generate", "--job-id", "J", "-o", str(out)])
    assert res.exit_code == 2  # UsageError: file exists
    assert out.read_bytes() == b"OLD"


# --------------------------------------------------------------------------- #
# status / download / delete / list / coverage
# --------------------------------------------------------------------------- #


def test_status(runner, stub, install_client):
    stub.status_responses = [{"report_status": "completed", "report_id": "R"}]
    install_client(stub)
    res = runner.invoke(cli, ["--json", "report", "status", "J", "R"])
    assert res.exit_code == 0, res.output
    assert _data(res)["report_status"] == "completed"


def test_download(runner, stub, install_client, tmp_path):
    stub.file_download = (b"DOCX", "r.docx")
    install_client(stub)
    out = tmp_path / "dl.docx"
    res = runner.invoke(cli, ["--json", "report", "download", "J", "R", "-o", str(out)])
    assert res.exit_code == 0, res.output
    assert _data(res)["report_id"] == "R"
    assert out.read_bytes() == b"DOCX"


def test_download_latest_when_no_report_id(runner, stub, install_client, tmp_path):
    stub.file_download = (b"LATEST", "latest.docx")
    install_client(stub)
    out = tmp_path / "latest.docx"
    res = runner.invoke(cli, ["--json", "report", "download", "J", "-o", str(out)])
    assert res.exit_code == 0, res.output
    data = _data(res)
    assert data["report_id"] is None
    assert out.read_bytes() == b"LATEST"
    # must hit the latest-report endpoint, not a by-id path
    assert any(c[0] == "GETFILE" and c[1].endswith("/risk-report") for c in stub.calls)


def test_delete_with_yes(runner, stub, install_client):
    install_client(stub)
    res = runner.invoke(cli, ["--json", "report", "delete", "J", "R", "--yes"])
    assert res.exit_code == 0, res.output
    assert _data(res)["deleted"] is True


def test_list_for_job(runner, stub, install_client):
    stub.get_map["/api/extraction/jobs/J/reports"] = [{"report_id": "R", "report_status": "completed"}]
    install_client(stub)
    res = runner.invoke(cli, ["--json", "report", "list", "--job-id", "J"])
    assert res.exit_code == 0, res.output
    assert _data(res)[0]["report_id"] == "R"


def test_list_fanout_across_documents(runner, stub, install_client):
    stub.get_map["/api/entities"] = {"items": [{"iri": "doc:1", "properties_json": {"job_id": "J1"}}]}
    stub.get_map["/api/extraction/jobs/J1/reports"] = [{"report_id": "R1", "report_status": "completed"}]
    install_client(stub)
    res = runner.invoke(cli, ["--json", "report", "list"])
    assert res.exit_code == 0, res.output
    row = _data(res)[0]
    assert row["job_id"] == "J1"
    assert row["document_iri"] == "doc:1"


def test_coverage_json_returns_full_payload(runner, stub, install_client):
    payload = {"template_id": "T", "total_slots": 5, "filled": 3, "missing_required": 0, "sections": [{"x": 1}]}
    stub.get_map["/api/extraction/jobs/J/ast-coverage"] = payload
    install_client(stub)
    res = runner.invoke(cli, ["--json", "report", "coverage", "J"])
    assert res.exit_code == 0, res.output
    assert _data(res)["sections"] == [{"x": 1}]


def test_coverage_fail_on_missing_exit_7(runner, stub, install_client):
    payload = {"template_id": "T", "total_slots": 5, "filled": 3, "missing_required": 2, "sections": []}
    stub.get_map["/api/extraction/jobs/J/ast-coverage"] = payload
    install_client(stub)
    res = runner.invoke(cli, ["--json", "report", "coverage", "J", "--fail-on-missing"])
    assert res.exit_code == 7


def test_docs_list_projection(runner, stub, install_client):
    stub.get_map["/api/entities"] = {
        "items": [{"iri": "doc:1", "label_zh": "报告A", "class_iri": "C", "properties_json": {"job_id": "J1"}}]
    }
    install_client(stub)
    res = runner.invoke(cli, ["--json", "docs", "list"])
    assert res.exit_code == 0, res.output
    assert _data(res)[0] == {"iri": "doc:1", "label": "报告A", "class_iri": "C", "job_id": "J1"}
