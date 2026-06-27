"""契约测试：Excel 自由文本暂存与富化合并（parse_excel ner_columns / _merge_ner）。

覆盖 [contracts/parser-and-enrichment.md](../../../specs/008-gliner-ner-extraction/contracts/parser-and-enrichment.md)
P5–P9、P11：白名单列原文暂存 `__freetext__`（不污染属性）/ 向后兼容 /
`_merge_ner` 结构化权威·仅补空缺·清除暂存。以真实 openpyxl 构造样本，纯本地、零云端。
"""

from __future__ import annotations

import openpyxl

from app.services.extraction.parser import parse_excel
from app.services.extraction.pipeline import _merge_ner

EQUIP = "http://slpra.org/equipment#Equipment"
MAPPING = {"设备编号": "equipmentID", "设备名称": "equipmentName", "材质": "material"}
NOTE_1 = "额定功率15kW，操作温度80℃。"
NOTE_2 = "备用包衣设备。"


def _xlsx(path):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["设备编号", "设备名称", "材质", "备注"])
    ws.append(["CT64201", "压片机A", "316L不锈钢", NOTE_1])
    ws.append(["DE64203", "包衣机B", "304不锈钢", NOTE_2])
    wb.save(path)
    return path


# --- P5/P6 白名单暂存、不污染属性 -------------------------------------------
def test_ner_columns_stashed_to_freetext(tmp_path):
    """P5：仅 ner_columns 命中列原文进入 __freetext__，键集 = ner_columns ∩ 实际表头。"""
    rows = parse_excel(_xlsx(tmp_path / "a.xlsx"), MAPPING, ner_columns=["备注"])
    assert len(rows) == 2
    for row in rows:
        assert set(row["__freetext__"].keys()) == {"备注"}
    assert rows[0]["__freetext__"]["备注"] == NOTE_1


def test_freetext_not_polluting_properties(tmp_path):
    """P6：自由文本原文不直接作属性值落 IRI 键（仅暂存于 __freetext__）。"""
    rows = parse_excel(_xlsx(tmp_path / "a.xlsx"), MAPPING, ner_columns=["备注"])
    row0 = rows[0]
    iri_values = [v for k, v in row0.items() if k != "__freetext__"]
    assert NOTE_1 not in iri_values                      # 原文不作属性值
    assert row0["equipmentID"] == "CT64201"              # 结构化列照常落 IRI 键


# --- P7 向后兼容 -------------------------------------------------------------
def test_no_ner_columns_backward_compatible(tmp_path):
    """P7：ner_columns=None → 无 __freetext__ 键、行为与改造前一致。"""
    rows = parse_excel(_xlsx(tmp_path / "a.xlsx"), MAPPING)
    assert all("__freetext__" not in r for r in rows)
    assert rows[0] == {"equipmentID": "CT64201", "equipmentName": "压片机A",
                       "material": "316L不锈钢"}


# --- P8/P9/P11 _merge_ner --------------------------------------------------
def test_merge_only_fills_missing_keys():
    """P9：NER 仅写 row 中不存在/为空的 IRI 键。"""
    row = {"equipmentID": "CT64201", "__freetext__": {"备注": NOTE_1}}
    _merge_ner(row, {f"{EQUIP}#ratedPower": "15kW"})
    assert row[f"{EQUIP}#ratedPower"] == "15kW"


def test_merge_structured_authority_no_overwrite():
    """P8：结构化列已有非空值 → NER 不得覆盖。"""
    row = {"equipmentName": "压片机A", "__freetext__": {"备注": NOTE_1}}
    _merge_ner(row, {"equipmentName": "正文误识别名", f"{EQUIP}#ratedPower": "15kW"})
    assert row["equipmentName"] == "压片机A"             # 保留结构化权威值
    assert row[f"{EQUIP}#ratedPower"] == "15kW"          # 空缺仍被补


def test_merge_fills_empty_string_value():
    """P9 边界：结构化列为空串 → 视为空缺，可被 NER 补。"""
    row = {"equipmentName": "  ", "__freetext__": {"备注": NOTE_2}}
    _merge_ner(row, {"equipmentName": "包衣机B"})
    assert row["equipmentName"] == "包衣机B"


def test_merge_clears_freetext_stash():
    """P11：合并后 row 不含 __freetext__ 临时键。"""
    row = {"equipmentID": "CT64201", "__freetext__": {"备注": NOTE_1}}
    _merge_ner(row, {})
    assert "__freetext__" not in row                     # 即便无 NER 命中也清除暂存
