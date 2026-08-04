"""风险评估矩阵（1.3 风险评估过程）单元格换行渲染回归。

规则文案经前端富文本编辑常以 HTML ``<br>`` 承载换行，直接写入 DOCX 单元格会原样显示字面
「<br>」。渲染须把 ``<br>``/``<br/>``/``<br />`` 归一为 Word 软换行（python-docx 读回为 ``\\n``）。"""

from __future__ import annotations

import io

from docx import Document

from app.services.reporting.risk_report_generator import RiskReport, RiskRow
from app.services.reporting.docx_renderer import render_risk_report


def _matrix_cell_texts(docx_bytes: bytes) -> list[str]:
    """回读 DOCX，定位 7 列风险评估矩阵（表头首格以 ``HazID`` 起），返回其全部单元格文本。"""
    doc = Document(io.BytesIO(docx_bytes))
    for t in doc.tables:
        hdr = t.rows[0].cells
        if len(hdr) == 7 and hdr[0].text.startswith("HazID"):
            return [c.text for row in t.rows for c in row.cells]
    raise AssertionError("未找到风险评估矩阵表")


def _report_with(row: RiskRow) -> RiskReport:
    return RiskReport(assessment_rows=[row])


class TestMatrixLineBreaks:
    def test_br_variants_become_word_breaks(self):
        row = RiskRow(
            hazid="人员",
            contributing_factors="1、甲<br>2、乙<BR>3、丙",
            pre_control_level="中",
            post_control_level="低",
            control_measures="措施一<br/>措施二<br />措施三",
            traceability="记录A<br>记录B",
            status="可以接受",
        )
        cells = _matrix_cell_texts(render_risk_report(_report_with(row)))
        joined = "\n".join(cells)
        # 字面 <br>（任意大小写/写法）不得残留
        assert "<br>" not in joined.lower()
        # 归一为真实换行：python-docx 读回为 \n
        assert "1、甲\n2、乙\n3、丙" in cells
        assert "措施一\n措施二\n措施三" in cells
        assert "记录A\n记录B" in cells

    def test_plain_newlines_unaffected(self):
        """已用 \\n 的文案幂等——不受影响，仍是真实换行。"""
        row = RiskRow(
            hazid="文件",
            contributing_factors="1、甲\n2、乙",
            pre_control_level="中",
            post_control_level="低",
            control_measures="仅一行",
            traceability="记录",
            status="可以接受",
        )
        cells = _matrix_cell_texts(render_risk_report(_report_with(row)))
        assert "1、甲\n2、乙" in cells
        assert "仅一行" in cells
