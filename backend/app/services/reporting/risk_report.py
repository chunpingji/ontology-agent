"""风险评估报告渲染（能力四, R12, FR-024, SC-007）。

组装 JSON（分类/专用化决策/污染评分/CFDI 情景/MACO·PDE/规则链与法规依据 + 签批
信息），并经 `reportlab` 渲染归档 PDF。未签名的高风险结论报告标注「未生效/待 QA
签批」。
"""

from __future__ import annotations

import io
import logging

from sqlalchemy.orm import Session

from app.models.reasoning import ElectronicSignature, ReasoningExecution

logger = logging.getLogger(__name__)


def _signature(db: Session, conclusion: ReasoningExecution) -> ElectronicSignature | None:
    return (
        db.query(ElectronicSignature)
        .filter(ElectronicSignature.conclusion_id == conclusion.id)
        .order_by(ElectronicSignature.signed_at.desc())
        .first()
    )


def build_report_json(db: Session, conclusion: ReasoningExecution) -> dict:
    """组装报告 JSON（contracts/action-report-api §4）。"""
    results = conclusion.results or {}
    sig = _signature(db, conclusion)
    maco = {
        "value": float(conclusion.maco_value) if conclusion.maco_value is not None else None,
        "method": conclusion.maco_method,
        "pde": results.get("pde"),
    }
    return {
        "conclusion_id": str(conclusion.id),
        "effective": bool(conclusion.effective),
        "classification": {
            "risk_level": conclusion.risk_level,
            "category": results.get("category"),
        },
        "dedication_decision": "required" if results.get("requires_dedication") else "not_required",
        "contamination_scores": results.get("contamination_scores", {}) or {},
        "cfdi_scenarios": list(results.get("cfdi_scenarios", []) or []),
        "maco": maco,
        "rule_chain": list(conclusion.rules_fired or []),
        "signature": {
            "signer": sig.signer,
            "meaning": sig.meaning,
            "signed_at": sig.signed_at.isoformat(),
        } if sig else None,
        "pdf_url": f"/api/reports/{conclusion.id}/pdf",
    }


def render_report_pdf(db: Session, conclusion: ReasoningExecution) -> bytes:
    """渲染报告 PDF 字节流（含 QA 签批信息位）。"""
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.pdfgen import canvas

    data = build_report_json(db, conclusion)
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    width, height = A4
    y = height - 25 * mm

    def line(text: str, *, size: int = 10, dy: float = 6 * mm) -> None:
        nonlocal y
        c.setFont("Helvetica", size)
        c.drawString(20 * mm, y, text)
        y -= dy

    line("Cross-Contamination Risk Assessment Report", size=15, dy=10 * mm)
    line(f"Conclusion ID: {data['conclusion_id']}")
    if not data["effective"]:
        line("** NOT EFFECTIVE / PENDING QA SIGNATURE **", size=11)
    line(f"Risk Level: {data['classification']['risk_level']}  "
         f"Category: {data['classification'].get('category')}")
    line(f"Dedication Decision: {data['dedication_decision']}")
    line(f"Contamination Scores: {data['contamination_scores']}")
    line(f"CFDI Scenarios: {', '.join(data['cfdi_scenarios']) or '-'}")
    m = data["maco"]
    line(f"MACO: {m['value']} (method={m['method']}, PDE={m['pde']})")
    line("Rule Chain:")
    for r in data["rule_chain"]:
        line(f"  - {r.get('rule_id')}  ({r.get('regulation_ref', '')})")
    sig = data["signature"]
    line("QA Signature: " + (f"{sig['signer']} — {sig['meaning']} @ {sig['signed_at']}"
                             if sig else "(unsigned)"))

    c.showPage()
    c.save()
    return buf.getvalue()
