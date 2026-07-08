"""detect_pde_conflict：CMCReport「推导 vs 原文」PDE 冲突检测的确定性单测。"""

from app.services.extraction.relation_extractor import DP
from app.services.reasoning.pde_conflict import (
    _dose_mg,
    _species_key,
    detect_pde_conflict,
)


def _pde_dp(value: str) -> dict:
    return {"iri": DP["pde_mg_per_day"], "label": "PDE", "value": value}


def _mk(iri: str, label: str, value: str) -> dict:
    return {"iri": iri, "label": label, "value": value}


def test_conflict_mock_tox_study_band5_vs_asserted_band2():
    # 原文 PDE 1.8mg（band 2）；无毒理参数 → 回退 mock 毒理研究（rat 0.5/28d → 推荐 band 5）。
    c = detect_pde_conflict([_pde_dp("1.8mg")])
    assert c is not None
    assert c["conflict_key"] == "shared_line_pde"
    assert c["asserted"]["band"] == 2
    assert c["asserted"]["pde_ug_day"] == 1800.0
    assert c["asserted"]["pde_mg_day"] == 1.8
    assert c["derived"]["band"] == 5
    assert c["derived"]["pde_ug_day"] == 50.0
    assert c["derived"]["input_source"] == "mock-tox-study"
    assert c["derived"]["provisional"] is True
    assert c["delta_bands"] == 3
    assert "provenance" in c["derived"]


def test_unit_parsing_mg_and_ug_equivalent():
    c_mg = detect_pde_conflict([_pde_dp("1.8mg")])
    c_ug = detect_pde_conflict([_pde_dp("1800 µg/day")])
    assert c_mg["asserted"]["pde_ug_day"] == c_ug["asserted"]["pde_ug_day"] == 1800.0
    assert c_mg["asserted"]["band"] == c_ug["asserted"]["band"] == 2


def test_extracted_tox_params_used_over_mock():
    # 端点带 NOAEL/种属/周期 → 用抽取值构造 ToxStudy（input_source=extracted），不回退 mock。
    dps = [
        _pde_dp("1.8mg"),
        _mk(DP["noael_mg_per_kg_per_day"], "NOAEL", "0.5mg/kg/天"),
        _mk(DP["noaelSpecies"], "种属", "SD大鼠"),
        _mk(DP["noaelDuration"], "给药周期", "28天"),
    ]
    c = detect_pde_conflict(dps)
    assert c is not None
    assert c["derived"]["input_source"] == "extracted"
    assert c["derived"]["band"] == 5  # 与 mock 同参 → 同结果
    assert c["delta_bands"] == 3


def test_dose_mg_requires_mass_unit_adjacent_to_number():
    # 剂量数值须紧邻质量单位；占位符/纯周期数不得被当作剂量。
    assert _dose_mg("0.5mg/kg/天") == 0.5
    assert _dose_mg("0.2 mg/kg/天") == 0.2
    assert _dose_mg("200µg/kg") == 0.2  # µg 归一到 mg
    assert _dose_mg("500 ng") == 0.0005
    assert _dose_mg("XXmg/kg/天（28天重复给药）") is None  # 占位符，无数值剂量
    assert _dose_mg("28天重复给药") is None  # 28 的单位是"天"，非质量
    assert _dose_mg("≥10（参照ICHS9指南）") is None  # 无质量单位
    assert _dose_mg(None) is None


def test_placeholder_noael_falls_back_to_mock_not_fabricated_band3():
    # 回归：NOAEL 为占位符"XX"、括注含周期数"28天" → 不得把 28 误读为 NOAEL（→PDE 2800 →band 3）；
    # 判为未给出 → 回退 mock 毒理研究（band 5），且 input_source 诚实标注为 mock。
    dps = [
        _pde_dp("1.8mg"),
        _mk(DP["noael_mg_per_kg_per_day"], "NOAEL（大鼠）", "XXmg/kg/天（28天重复给药）"),
        _mk(DP["noaelSpecies"], "动物种属", "SD大鼠/Beagle犬"),
        _mk(DP["noaelDuration"], "给药周期", "7-14天为一个治疗周期"),
    ]
    c = detect_pde_conflict(dps)
    assert c is not None
    assert c["derived"]["input_source"] == "mock-tox-study"  # 未从原文取到 NOAEL
    assert c["derived"]["band"] == 5  # 非臆造的 band 3
    assert c["delta_bands"] == 3


def test_chinese_species_mapping():
    assert _species_key("SD大鼠") == "rat"
    assert _species_key("Beagle犬") == "dog"
    assert _species_key("比格犬") == "dog"
    assert _species_key("小鼠") == "mouse"
    assert _species_key("新西兰兔") == "rabbit"
    assert _species_key("食蟹猴") == "monkey"
    assert _species_key("人") == "human"


def test_no_conflict_when_bands_match():
    # 原文 PDE 0.005mg = 5 µg/日 → OEL 0.5 → band 5 == 推导 band 5 → 无冲突。
    assert detect_pde_conflict([_pde_dp("0.005mg")]) is None


def test_none_when_no_asserted_pde():
    # 端点无 PDE → 无从比较 → None（即便有毒理参数）。
    assert detect_pde_conflict([_mk(DP["noaelSpecies"], "种属", "大鼠")]) is None
    assert detect_pde_conflict([]) is None
    assert detect_pde_conflict(None) is None
