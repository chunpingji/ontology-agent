"""detect_pde_conflict：CMCReport「推导 vs 原文」PDE 冲突检测的确定性单测。

推导侧**仅用原文真实抽取的毒理参数**（NOAEL/种属/周期）；NOAEL 单位可取自值或列标签、种属可从
端点标题回退。参数缺失/不可解析 → 跳过（``None``），**绝不回退 mock**，``input_source`` 恒为
``"extracted"``。
"""

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


def _tox_dps() -> list[dict]:
    """一组真实毒理参数（SD大鼠 0.5mg/kg/28天 → 推导 band 5），供构造冲突场景复用。"""
    return [
        _mk(DP["noael_mg_per_kg_per_day"], "NOAEL（mg/kg/天）", "0.5mg/kg/天"),
        _mk(DP["noaelSpecies"], "种属", "SD大鼠"),
        _mk(DP["noaelDuration"], "给药周期", "28天"),
    ]


def test_skip_when_no_tox_params_no_mock():
    # 原文 PDE 但无任何毒理参数 → 无从推导 → 跳过（None），绝不回退 mock 毒理研究源。
    assert detect_pde_conflict([_pde_dp("1.8mg")]) is None


def test_conflict_derived_band5_vs_asserted_band2():
    # 原文 PDE 1.8mg（band 2）+ 真实毒理参数（SD大鼠 0.5/28d → band 5）→ Δ3 冲突。
    c = detect_pde_conflict([_pde_dp("1.8mg"), *_tox_dps()])
    assert c is not None
    assert c["conflict_key"] == "shared_line_pde"
    assert c["asserted"]["band"] == 2
    assert c["asserted"]["pde_ug_day"] == 1800.0
    assert c["asserted"]["pde_mg_day"] == 1.8
    assert c["derived"]["band"] == 5
    assert c["derived"]["pde_ug_day"] == 50.0
    assert c["derived"]["input_source"] == "extracted"
    assert c["derived"]["provisional"] is True
    assert c["delta_bands"] == 3
    assert "provenance" in c["derived"]


def test_unit_parsing_mg_and_ug_equivalent():
    # 原文 PDE 归一：1.8mg 与 1800µg/day 应得同一 asserted band（band 2）。
    c_mg = detect_pde_conflict([_pde_dp("1.8mg"), *_tox_dps()])
    c_ug = detect_pde_conflict([_pde_dp("1800 µg/day"), *_tox_dps()])
    assert c_mg["asserted"]["pde_ug_day"] == c_ug["asserted"]["pde_ug_day"] == 1800.0
    assert c_mg["asserted"]["band"] == c_ug["asserted"]["band"] == 2


def test_extracted_tox_params_used():
    # 端点带 NOAEL/种属/周期 → 用抽取值构造 ToxStudy（input_source=extracted）。
    dps = [
        _pde_dp("1.8mg"),
        _mk(DP["noael_mg_per_kg_per_day"], "NOAEL", "0.5mg/kg/天"),
        _mk(DP["noaelSpecies"], "种属", "SD大鼠"),
        _mk(DP["noaelDuration"], "给药周期", "28天"),
    ]
    c = detect_pde_conflict(dps)
    assert c is not None
    assert c["derived"]["input_source"] == "extracted"
    assert c["derived"]["band"] == 5
    assert c["delta_bands"] == 3


def test_dose_mg_requires_mass_unit_adjacent_to_number():
    # 值内解析：剂量数值须紧邻质量单位；占位符/纯周期数不得被当作剂量（label 缺省，走值内路径）。
    assert _dose_mg("0.5mg/kg/天") == 0.5
    assert _dose_mg("0.2 mg/kg/天") == 0.2
    assert _dose_mg("200µg/kg") == 0.2  # µg 归一到 mg
    assert _dose_mg("500 ng") == 0.0005
    assert _dose_mg("XXmg/kg/天（28天重复给药）") is None  # 占位符，无数值剂量
    assert _dose_mg("28天重复给药") is None  # 28 的单位是"天"，非质量
    assert _dose_mg("≥10（参照ICHS9指南）") is None  # 无质量单位
    assert _dose_mg(None) is None


def test_dose_mg_bare_number_unit_from_label():
    # 值为裸数字、单位在列标签 → 以标签单位解析（NOAEL（mg/kg/天） 值 "30" → 30 mg/kg）。
    assert _dose_mg("30", "NOAEL（mg/kg/天）") == 30.0
    assert _dose_mg("200", "NOAEL（µg/kg/day）") == 0.2  # µg/kg → mg
    assert _dose_mg("30", "NOAEL") is None  # 标签无质量/千克单位
    assert _dose_mg("30天", "NOAEL（mg/kg/天）") is None  # 值非纯数字（含"天"）
    assert _dose_mg("30", "") is None  # 无标签兜底 → 未给出


def test_placeholder_noael_skips_not_fabricated_band3():
    # 回归：NOAEL 为占位符"XX"、括注含周期数"28天" → 不得把 28 误读为 NOAEL（→PDE 2800 →band 3）；
    # 判为未给出 → 跳过（None），既不臆造 band 3，也不回退 mock。
    dps = [
        _pde_dp("1.8mg"),
        _mk(DP["noael_mg_per_kg_per_day"], "NOAEL（大鼠）", "XXmg/kg/天（28天重复给药）"),
        _mk(DP["noaelSpecies"], "动物种属", "SD大鼠/Beagle犬"),
        _mk(DP["noaelDuration"], "给药周期", "7-14天为一个治疗周期"),
    ]
    assert detect_pde_conflict(dps) is None


def test_species_from_title_fallback_and_bare_noael():
    # 种属 DP 抽脏（"30"）+ NOAEL 裸数字（单位在标签）→ 种属从端点标题回退、NOAEL 用标签单位；
    # 与 test_extracted_tox_params_used 同参（大鼠 0.5/28d → band5），验证真实数据推导等价。
    dps = [
        _pde_dp("1.8mg"),
        _mk(DP["noael_mg_per_kg_per_day"], "NOAEL（mg/kg/天）", "0.5"),  # 裸数字 + 标签单位
        _mk(DP["noaelSpecies"], "NOAEL 动物种属", "30"),                 # 抽脏，_species_key 失败
        _mk(DP["noaelDuration"], "NOAEL 试验周期", "28"),
    ]
    c = detect_pde_conflict(dps, title="大鼠14天亚急毒试验")
    assert c is not None
    assert c["derived"]["input_source"] == "extracted"
    assert c["derived"]["band"] == 5
    assert c["delta_bands"] == 3
    # 无标题 → 种属仍拿不到（"30" 脏）→ 跳过，不 mock
    assert detect_pde_conflict(dps) is None


def test_title_human_overmatch_does_not_fabricate_conflict():
    # 标题回退防误配：种属 DP 抽脏("30")、NOAEL 裸数字，标题含"人"却无动物 token（如"成人给药耐受性"）→
    # 不得把"人"当种属臆造 human 研究 → 跳过（None），绝不伪造冲突卡。
    dps = [
        _pde_dp("1.8mg"),
        _mk(DP["noael_mg_per_kg_per_day"], "NOAEL（mg/kg/天）", "0.5"),
        _mk(DP["noaelSpecies"], "NOAEL 动物种属", "30"),
    ]
    assert detect_pde_conflict(dps, title="成人给药耐受性研究") is None
    assert detect_pde_conflict(dps, title="人用药品共线评估") is None
    # 对照：标题带真实动物 token → 正常回退推导。
    assert detect_pde_conflict(dps, title="大鼠14天亚急毒试验") is not None


def test_chinese_species_mapping():
    assert _species_key("SD大鼠") == "rat"
    assert _species_key("Beagle犬") == "dog"
    assert _species_key("比格犬") == "dog"
    assert _species_key("小鼠") == "mouse"
    assert _species_key("新西兰兔") == "rabbit"
    assert _species_key("食蟹猴") == "monkey"
    assert _species_key("人") == "human"
    assert _species_key("犬14天亚急毒") == "dog"  # 从端点标题识别


def test_f4_equals_1_clears_provisional():
    # F4=1 明确标注无严重危害端点 → genotoxic/carcinogenic/reproductive_toxicant=False →
    # provisional=False → recommended=point_band（不 +1）。
    # SD大鼠 0.5/28d: PDE=50µg → OEL=5 → point band 4 → (without F4) provisional → band 5。
    # 加 F4=1 后 → recommended=4（非 5）。PDE 1.8mg → asserted band 2 → Δ2 冲突（非 Δ3）。
    _F4_IRI = "https://ontology.pharma-gmp.cn/slpra/drug-development/f4"
    dps = [_pde_dp("1.8mg"), *_tox_dps(), _mk(_F4_IRI, "F4", "1")]
    c = detect_pde_conflict(dps)
    assert c is not None
    assert c["derived"]["provisional"] is False
    assert c["derived"]["band"] == 4
    assert c["delta_bands"] == 2


def test_f4_not_1_stays_provisional():
    # F4=10 → 严重危害端点阳性 → 不清除 provisional（因遗传毒性仍未知）。
    _F4_IRI = "https://ontology.pharma-gmp.cn/slpra/drug-development/f4"
    dps = [_pde_dp("1.8mg"), *_tox_dps(), _mk(_F4_IRI, "F4", "10")]
    c = detect_pde_conflict(dps)
    assert c is not None
    assert c["derived"]["provisional"] is True


def test_same_band_pde_ratio_conflict():
    # 原文 PDE=100mg = 100,000 µg/日 + NOAEL=200 rat 28d F4=1 → 推导 PDE=20,000 µg/日。
    # 同属 band 1（>10,000 µg/日），但 PDE 差 5 倍 → 仍触发冲突卡（计算差异）。
    _F4_IRI = "https://ontology.pharma-gmp.cn/slpra/drug-development/f4"
    dps = [
        _pde_dp("100mg"),
        _mk(DP["noael_mg_per_kg_per_day"], "NOAEL（mg/kg/天）", "200"),
        _mk(DP["noaelSpecies"], "种属", "SD大鼠"),
        _mk(DP["noaelDuration"], "给药周期", "28天"),
        _mk(_F4_IRI, "F4", "1"),
    ]
    c = detect_pde_conflict(dps)
    assert c is not None
    assert c["asserted"]["band"] == 1
    assert c["derived"]["band"] == 1
    assert c["delta_bands"] == 0
    assert c["pde_ratio"] == 5.0
    assert "5.0 倍" in c["summary"]
    assert c["derived"]["provisional"] is False


def test_no_conflict_when_bands_and_pde_close():
    # 原文 PDE 0.05mg = 50 µg/日；推导 PDE=50 µg/日，F4=1 → 同 band 4 且 PDE 一致 → 无冲突。
    _F4_IRI = "https://ontology.pharma-gmp.cn/slpra/drug-development/f4"
    assert detect_pde_conflict([_pde_dp("0.05mg"), *_tox_dps(), _mk(_F4_IRI, "F4", "1")]) is None


def test_none_when_no_asserted_pde():
    # 端点无 PDE → 无从比较 → None（即便有毒理参数）。
    assert detect_pde_conflict([*_tox_dps()]) is None
    assert detect_pde_conflict([]) is None
    assert detect_pde_conflict(None) is None
