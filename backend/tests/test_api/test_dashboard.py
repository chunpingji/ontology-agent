"""US5 测试：实时看板（相容性矩阵 + 排期风险）+ 结论规则链溯源（FR-025/027）。"""

from __future__ import annotations

from app.models.reasoning import ReasoningExecution


def _seed(db, *, equipment, product, results=None, effective=True):
    e = ReasoningExecution(
        execution_type="assessment",
        input_params={"equipment_iris": [equipment], "drug_iri": product},
        rules_fired=[{"rule_id": "DED-003", "group": "equipment_dedication",
                      "regulation_ref": "GMP附录一"}],
        results=results or {"product": product},
        risk_level="HighRisk",
        affected_subgraph={"equipment": [equipment], "product": [product]},
        effective=effective,
    )
    db.add(e)
    db.commit()
    db.refresh(e)
    return e


def test_dashboard_matrix_and_schedule_risks(client, db):
    _seed(db, equipment="EQ-2001", product="P-A",
          results={"product": "P-A", "schedule_conflict": True})
    body = client.get("/api/integration/dashboard").json()
    cell = next(c for c in body["compatibility_matrix"] if c["equipment"] == "EQ-2001")
    assert cell["product"] == "P-A"
    assert cell["risk_level"] == "HighRisk"
    assert cell["conclusion_id"]
    assert any(r["equipment"] == "EQ-2001" and r["conflict"] for r in body["schedule_risks"])
    assert body["updated_at"]


def test_conclusion_trace_returns_rule_chain(client, db):
    e = _seed(db, equipment="EQ-3", product="P-B")
    body = client.get(f"/api/reasoning/conclusions/{e.id}/trace").json()
    assert body["rules_fired"][0]["rule_id"] == "DED-003"
    assert body["rules_fired"][0]["regulation_ref"] == "GMP附录一"
