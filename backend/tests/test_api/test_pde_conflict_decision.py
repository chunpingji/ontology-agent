"""PDE 冲突人工决策端点：GET 回显 + POST CAS upsert（乐观并发 409）。"""

import uuid

from app.models.extraction import ExtractionJob


def _url(job_id: str) -> str:
    return f"/api/extraction/jobs/{job_id}/pde-conflict/decision"


def _job(db) -> str:
    row = ExtractionJob(id=uuid.uuid4(), source_type="excel", status="completed")
    db.add(row)
    db.commit()
    return str(row.id)


def test_decision_get_default_pending(client, db, analyst_headers):
    job_id = _job(db)
    r = client.get(_url(job_id), headers=analyst_headers)
    assert r.status_code == 200
    body = r.json()
    assert body["chosen"] == "pending"
    assert body["version"] == 0
    assert body["actor"] == ""
    assert body["conflict_key"] == "shared_line_pde"


def test_decision_post_creates_then_cas_updates(client, db, analyst_headers):
    job_id = _job(db)
    url = _url(job_id)

    # 首次：expected_version 0 → v1
    r = client.post(url, headers=analyst_headers,
                    json={"chosen": "derived", "note": "采纳推导 band 5", "expected_version": 0})
    assert r.status_code == 200
    body = r.json()
    assert body["chosen"] == "derived"
    assert body["note"] == "采纳推导 band 5"
    assert body["version"] == 1
    assert body["actor"] == "analyst"

    # GET 回显 v1
    got = client.get(url, headers=analyst_headers).json()
    assert got["version"] == 1 and got["chosen"] == "derived"

    # 陈旧 expected_version 0 → 409
    stale = client.post(url, headers=analyst_headers,
                        json={"chosen": "asserted", "expected_version": 0})
    assert stale.status_code == 409

    # 正确 expected_version 1 → v2
    r2 = client.post(url, headers=analyst_headers,
                     json={"chosen": "asserted", "expected_version": 1})
    assert r2.status_code == 200
    assert r2.json()["version"] == 2
    assert r2.json()["chosen"] == "asserted"


def test_decision_invalid_choice_422(client, db, analyst_headers):
    job_id = _job(db)
    r = client.post(_url(job_id), headers=analyst_headers,
                    json={"chosen": "bogus", "expected_version": 0})
    assert r.status_code == 422


def test_decision_requires_identity(client, db):
    job_id = _job(db)
    r = client.post(_url(job_id), json={"chosen": "derived", "expected_version": 0})
    assert r.status_code == 403
