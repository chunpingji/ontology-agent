"""Report-center summaries stay small and retain the existing visibility rules."""

from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from sqlalchemy import event

from app.models.extraction import ExtractionJob, GeneratedReport

HEADERS = {"X-User": "analyst", "X-Role": "senior_analyst"}


def report_id(number):
    return UUID(f"aaaaaaaa-0000-4000-8000-{number:012x}")


def job(db, **values):
    row = ExtractionJob(source_type="upload", source_filename="source.docx", **values)
    db.add(row)
    db.flush()
    return row


def report(db, source, number, **values):
    data = {
        "id": report_id(number), "job_id": source.id, "report_type": "risk_assessment",
        "file_path": "/unused/report.docx", "file_size": 100, "actor": "analyst",
        "report_status": "completed", "created_at": datetime(2026, 9, 14, tzinfo=UTC),
    }
    row = GeneratedReport(**(data | values))
    db.add(row)
    db.flush()
    return row


def test_summary_pages_cross_jobs_and_never_select_large_payloads(client, db):
    old = job(db, created_at=datetime(2026, 1, 1, tzinfo=UTC))
    for _ in range(30):
        job(db)
    new = job(db)
    payload = {"body_ast": {"text": "large frozen report " * 2000}, "graph": ["fact"]}
    for number, source in ((1, new), (2, old), (3, old), (4, new)):
        report(db, source, number, rules_summary=payload, narratives={"text": "detail only"})
    db.commit()
    db.expunge_all()
    statements = []

    def capture(_connection, _cursor, sql, _parameters, _context, _many):
        statements.append(sql.lower())

    event.listen(db.get_bind(), "before_cursor_execute", capture)
    try:
        first = client.get("/api/reports?page=1&page_size=2", headers=HEADERS)
        second = client.get("/api/reports?page=2&page_size=2", headers=HEADERS)
    finally:
        event.remove(db.get_bind(), "before_cursor_execute", capture)
    assert first.status_code == second.status_code == 200
    assert first.json()["total"] == second.json()["total"] == 4
    assert first.json()["page"] == 1 and second.json()["page"] == 2
    rows = first.json()["items"] + second.json()["items"]
    assert [item["id"] for item in rows] == [str(report_id(n)) for n in (4, 3, 2, 1)]
    assert all(set(item) == {
        "id", "job_id", "source_filename", "report_type", "file_size", "created_at"
    } for item in rows)
    assert len(first.content) < 2048
    assert len(statements) == 4  # one count and one bounded query per page
    assert all(name not in sql for sql in statements for name in (
        "rules_summary", "narratives", "source_config", "document_path"
    ))
    assert db.get(GeneratedReport, report_id(1)).rules_summary == payload
    assert client.get("/api/reports?page=3&page_size=2", headers=HEADERS).json() == {
        "items": [], "total": 4, "page": 3, "page_size": 2,
    }


def test_summary_visibility_and_totals_match_per_job_lists(client, db):
    source = job(db)
    cases = (
        {},
        {"actor": "other"},
        {"report_type": "batch_record_demo"},
        {"report_type": "batch_record_demo", "actor": "other"},
        {"deleted_at": datetime.now(UTC)},
        {"report_status": "running"},
        {"report_status": "failed"},
        {"report_status": None},
        {"report_status": None, "file_size": 0},
        {"report_status": None, "file_size": None},
    )
    for number, values in enumerate(cases, 1):
        report(db, source, number, **values)
    db.commit()
    for actor, expected in (("analyst", (1, 2, 3, 8)), ("other", (1, 2, 4, 8))):
        headers = {"X-User": actor, "X-Role": "qa"}
        result = client.get("/api/reports", headers=headers)
        assert result.status_code == 200
        assert result.json()["total"] == len(expected)
        ids = {item["id"] for item in result.json()["items"]}
        assert ids == {str(report_id(n)) for n in expected}
        existing = client.get(f"/api/extraction/jobs/{source.id}/reports", headers=headers)
        assert existing.status_code == 200
        assert ids == {item["id"] for item in existing.json()}


def test_newer_reports_sort_before_uuid_ties(client, db):
    source = job(db)
    report(db, source, 10)
    report(db, source, 1, created_at=datetime(2026, 9, 14, tzinfo=UTC) + timedelta(hours=1))
    db.commit()
    result = client.get("/api/reports?page_size=1", headers=HEADERS).json()
    assert result["items"][0]["id"] == str(report_id(1))
    assert result["total"] == 2


@pytest.mark.parametrize("query", ["page=0", "page=-1", "page_size=0", "page_size=101"])
def test_summary_pagination_rejects_invalid_bounds(client, query):
    assert client.get(f"/api/reports?{query}", headers=HEADERS).status_code == 422


def test_summary_requires_authentication_when_enabled(client, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "auth_required", True)
    assert client.get("/api/reports").status_code == 401
