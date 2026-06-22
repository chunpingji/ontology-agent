"""US4 测试：风险评估报告 JSON + PDF 双产物（FR-024, SC-007）。"""

from __future__ import annotations

from app.models.reasoning import ReasoningExecution


def _conclusion(db, **overrides):
    e = ReasoningExecution(
        execution_type="assessment",
        input_params={"equipment_iris": ["EQ-2001"], "drug_iri": "D-1"},
        rules_fired=[{"rule_id": "DED-003", "regulation_ref": "GMP附录一"}],
        results={
            "requires_dedication": True,
            "category": "高活性",
            "contamination_scores": {"airborne": 0.8},
            "cfdi_scenarios": ["HormonalSharedLineScenario"],
            "pde": 10.0,
        },
        risk_level="HighRisk",
        maco_value=1.23,
        maco_method="PDE",
        effective=True,
    )
    for k, v in overrides.items():
        setattr(e, k, v)
    db.add(e)
    db.commit()
    db.refresh(e)
    return e


def test_report_json_has_all_sections(client, db):
    c = _conclusion(db)
    r = client.get(f"/api/reports/{c.id}")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["classification"]["risk_level"] == "HighRisk"
    assert body["dedication_decision"] == "required"
    assert body["contamination_scores"]["airborne"] == 0.8
    assert "HormonalSharedLineScenario" in body["cfdi_scenarios"]
    assert body["maco"]["value"] == 1.23
    assert body["maco"]["method"] == "PDE"
    assert body["rule_chain"][0]["rule_id"] == "DED-003"
    assert body["pdf_url"].endswith(f"/api/reports/{c.id}/pdf")


def test_report_pdf_renders(client, db):
    c = _conclusion(db)
    r = client.get(f"/api/reports/{c.id}/pdf")
    assert r.status_code == 200, r.text
    assert r.headers["content-type"] == "application/pdf"
    assert r.content[:4] == b"%PDF"


def test_unsigned_high_risk_report_marked_pending(client, db):
    c = _conclusion(db, effective=False, requires_signature=True)
    body = client.get(f"/api/reports/{c.id}").json()
    assert body["effective"] is False
    assert body["signature"] is None
