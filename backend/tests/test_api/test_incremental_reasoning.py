"""US3 集成测试：事实变更仅触发受影响子图增量重算（非全量）、≤5s（FR-017/SC-005）。"""

from __future__ import annotations

import time

from app.models.reasoning import ReasoningExecution


def _seed(db, equipment_id):
    e = ReasoningExecution(
        execution_type="assessment",
        input_params={"equipment_iris": [equipment_id], "drug_iri": f"D-{equipment_id}"},
        results={"risk_level": "HighRisk"},
        risk_level="HighRisk",
        affected_subgraph={"equipment": [equipment_id]},
        effective=True,
    )
    db.add(e)
    db.commit()
    db.refresh(e)
    return e


def test_incremental_recompute_only_affected(client, db, analyst_headers):
    e1 = _seed(db, "EQ-1")
    e2 = _seed(db, "EQ-2")

    t0 = time.time()
    r = client.post(
        "/api/reasoning/incremental",
        json={"affected_subgraph": {"equipment": ["EQ-1"]}},
        headers=analyst_headers,
    )
    elapsed = time.time() - t0
    assert r.status_code == 200, r.text
    assert elapsed < 5.0  # ≤5s（SC-005）

    refreshed = r.json()["refreshed"]
    assert refreshed, "expected at least one refreshed conclusion"
    assert all("EQ-1" in str(c["affected_subgraph"]) for c in refreshed)

    db.refresh(e1)
    db.refresh(e2)
    assert e1.superseded_by is not None, "affected conclusion must be superseded"
    assert e1.effective is False
    assert e2.superseded_by is None, "non-affected conclusion must NOT be recomputed"
    assert e2.effective is True
