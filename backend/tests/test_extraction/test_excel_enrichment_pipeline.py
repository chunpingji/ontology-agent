"""集成测试：Excel 自由文本列经本地 NER 富化本行属性（US3，端到端）。

覆盖 data-model §3.2 / FR-008/018、contract P8–P12、SC-005：声明 `ner_columns` →
空缺属性被本地 NER 回填、结构化已填值零覆盖、候选数 = 行数、候选不含 `__freetext__`
临时键；NER 不可用时优雅降级（暂存仍清除、结构化零回归）。

注入式（无权重下载）：确定性 GLiNER 桩替换 `get_gliner_extractor`，假本体引擎派生
`label_to_iri`、`get_individuals` 返空——对齐 test_word_prose.py 做法。纯本地、零云端。
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import openpyxl

from app.config import settings
from app.models.extraction import ExtractionCandidate, ExtractionConfig, ExtractionJob
from app.services.extraction import pipeline as pipeline_module
from app.services.extraction.pipeline import run_extraction_pipeline

EQUIP = "http://slpra.org/equipment#Equipment"
IRI_ID = f"{EQUIP}#equipmentID"
IRI_NAME = f"{EQUIP}#equipmentName"
IRI_MATERIAL = f"{EQUIP}#material"
IRI_POWER = f"{EQUIP}#ratedPower"
IRI_TEMP = f"{EQUIP}#operatingTemp"

# 结构化列 → 属性 IRI（生产即以 IRI 为键，故结构化/ NER 同 IRI 可比、可判权威）。
MAPPING = {"设备编号": IRI_ID, "设备名称": IRI_NAME, "材质": IRI_MATERIAL}
NOTE_1 = "额定功率15kW，操作温度80℃，名称压片机甲。"   # 富化 power/temp；name 已填→不覆盖
NOTE_2 = "包衣机B，额定功率8kW。"                        # name 空缺→补；power 富化

# 桩 NER：按自由文本返回 {label: value}（含一个与已填结构化列同标签者，验证权威）。
_NER_BY_TEXT = {
    NOTE_1: {"额定功率": "15kW", "操作温度": "80℃", "设备名称": "正文误识别名"},
    NOTE_2: {"设备名称": "包衣机B", "额定功率": "8kW"},
}


class _FakeExtractor:
    def is_available(self) -> bool:
        return True

    def extract_text(self, text, labels, threshold=None):
        return {k: v for k, v in _NER_BY_TEXT.get(text, {}).items() if k in labels}


class _Unavailable:
    """缺权重/功能关 → is_available()=False，NER 分支须静默跳过（零回归，P12）。"""

    def is_available(self) -> bool:
        return False

    def extract_text(self, *a, **k):  # pragma: no cover - 守卫后不应被调用
        raise AssertionError("NER 不可用时不得调用 extract_text")


class _EquipEngine:
    """假本体引擎：派生设备标签集（含 ratedPower/operatingTemp）；个体返空（对齐→new）。"""

    def get_class_detail(self, iri):
        return SimpleNamespace(data_properties=[
            {"iri": IRI_NAME, "name": "equipmentName", "label": "设备名称", "range": ["string"]},
            {"iri": IRI_POWER, "name": "ratedPower", "label": "额定功率", "range": ["string"]},
            {"iri": IRI_TEMP, "name": "operatingTemp", "label": "操作温度", "range": ["string"]},
        ])

    def get_individuals(self, iri):
        return []

    def __getattr__(self, name):
        def _noop(*a, **k):
            return None
        return _noop


def _xlsx(path):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["设备编号", "设备名称", "材质", "备注"])
    ws.append(["CT64201", "压片机A", "316L不锈钢", NOTE_1])   # name 已填
    ws.append(["DE64203", "", "304不锈钢", NOTE_2])           # name 空缺
    wb.save(path)
    return path


def _config(db, *, ner_columns):
    cfg = ExtractionConfig(
        name="设备台账", target_class_iri=EQUIP, source_type="excel",
        column_mapping=MAPPING, ner_columns=ner_columns,
    )
    db.add(cfg)
    db.commit()
    db.refresh(cfg)
    return cfg


def _job(db):
    job = ExtractionJob(source_type="excel", source_filename="equipment.xlsx",
                        source_config={}, status="pending")
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


def _run(db, monkeypatch, tmp_path, *, ner_columns, extractor_factory=_FakeExtractor):
    monkeypatch.setattr(settings, "llm_cloud_enabled", False)
    monkeypatch.setattr(settings, "anthropic_api_key", "")
    monkeypatch.setattr(pipeline_module, "get_gliner_extractor",
                        lambda: extractor_factory())
    cfg, job = _config(db, ner_columns=ner_columns), _job(db)
    asyncio.run(run_extraction_pipeline(job, cfg, _xlsx(tmp_path / "equipment.xlsx"),
                                        _EquipEngine(), db))
    db.refresh(job)
    return job


def _candidates(db, job):
    return db.query(ExtractionCandidate).filter(
        ExtractionCandidate.job_id == job.id).all()


def _by_id(cands, equip_id):
    return next(c for c in cands
               if c.extracted_properties.get(IRI_ID) == equip_id)


# --- P9/P10 富化空缺、候选数=行数 -------------------------------------------
def test_freetext_enriches_blank_properties(db, monkeypatch, tmp_path):
    """空缺属性经本行自由文本 NER 回填；候选数严格 = 行数（不另生候选，P10）。"""
    job = _run(db, monkeypatch, tmp_path, ner_columns=["备注"])
    assert job.status == "reviewing"
    cands = _candidates(db, job)
    assert len(cands) == 2                              # = 行数，零额外候选

    row1 = _by_id(cands, "CT64201")
    assert row1.extracted_properties[IRI_POWER] == "15kW"    # 空缺被补（P9）
    assert row1.extracted_properties[IRI_TEMP] == "80℃"

    row2 = _by_id(cands, "DE64203")
    assert row2.extracted_properties[IRI_NAME] == "包衣机B"   # 结构化空缺被补
    assert row2.extracted_properties[IRI_POWER] == "8kW"


# --- P8 结构化权威 ----------------------------------------------------------
def test_structured_value_not_overwritten(db, monkeypatch, tmp_path):
    """结构化列已填值 → 同标签 NER 命中不得覆盖（结构化权威）。"""
    job = _run(db, monkeypatch, tmp_path, ner_columns=["备注"])
    row1 = _by_id(_candidates(db, job), "CT64201")
    assert row1.extracted_properties[IRI_NAME] == "压片机A"   # 非 “正文误识别名”


# --- P11 暂存不入候选 -------------------------------------------------------
def test_no_freetext_stash_in_candidates(db, monkeypatch, tmp_path):
    """候选 extracted_properties 不含 __freetext__ 临时键（合并后清除）。"""
    job = _run(db, monkeypatch, tmp_path, ner_columns=["备注"])
    for c in _candidates(db, job):
        assert "__freetext__" not in c.extracted_properties


# --- P12/SC-005 优雅降级 ----------------------------------------------------
def test_ner_unavailable_zero_regression(db, monkeypatch, tmp_path):
    """NER 不可用：富化静默跳过，但暂存仍清除、结构化属性零回归、候选数=行数。"""
    job = _run(db, monkeypatch, tmp_path, ner_columns=["备注"],
               extractor_factory=_Unavailable)
    cands = _candidates(db, job)
    assert len(cands) == 2
    for c in cands:
        assert "__freetext__" not in c.extracted_properties   # 暂存仍清除（P12）
        assert IRI_POWER not in c.extracted_properties          # 未富化（无 NER）
    row1 = _by_id(cands, "CT64201")
    assert row1.extracted_properties[IRI_NAME] == "压片机A"     # 结构化零回归
    assert row1.extracted_properties[IRI_MATERIAL] == "316L不锈钢"


def test_no_ner_columns_backward_compatible(db, monkeypatch, tmp_path):
    """未声明 ner_columns：行为与改造前一致——无富化、无 __freetext__、候选=行数。"""
    job = _run(db, monkeypatch, tmp_path, ner_columns=None)
    cands = _candidates(db, job)
    assert len(cands) == 2
    for c in cands:
        assert "__freetext__" not in c.extracted_properties
        assert IRI_POWER not in c.extracted_properties
    row2 = _by_id(cands, "DE64203")
    assert IRI_NAME not in row2.extracted_properties or \
        row2.extracted_properties.get(IRI_NAME) in (None, "")  # 空名未被补
