"""CMCReport「推导 PDE vs 原文 PDE」冲突检测 —— 接活 :func:`derive_facts` 休眠管线。

关系图谱里 CMCReport 的「共线评估数据」端点，其 ``object_data_properties`` 同时携带原文断言的
**PDE**（``pde_mg_per_day``，如 1.8 mg/日）与推导所需的毒理参数（NOAEL/种属/周期）。本模块：

1. 从这些数据属性解析原文 PDE，归一化到 µg/日，经 ``OEL = PDE / V`` 反推其 **OEB 带**（复用
   :func:`app.services.reasoning.derivation._band_from_oel`，与推导侧同一套 cutoffs）。
2. 优先用原文抽取的 NOAEL/种属/周期构造 :class:`ToxStudy`；缺失/不可解析→回退 mock 毒理研究源
   (:func:`app.services.extraction.tox_study_source.get_tox_study_source`)。经 :func:`derive_facts`
   得到**推导 OEB 带** + PDE/OEL + 可复现 provenance。
3. 推导带 ≠ 原文带 → 返回一条冲突记录（供关系图谱端点展示 + 人工裁决）；一致 → ``None``。

纯确定性、无 LLM、离线安全：任何解析失败/源不可用 → ``None``（调用方跳过，绝不打断标注主路径）。
"""

from __future__ import annotations

import logging
import re
from typing import Any

from app.services.reasoning.derivation import (
    DEFAULT_METHOD,
    DerivationMethod,
    ToxStudy,
    _band_from_oel,
    derive_facts,
)

logger = logging.getLogger(__name__)

# 冲突键：一份文档的共线评估 PDE 冲突唯一。人工决策记录以 (job_id, conflict_key) 为主键。
CONFLICT_KEY = "shared_line_pde"

_NUM_RE = re.compile(r"[-+]?\d*\.?\d+")

# 剂量数值：数字须紧邻质量单位（mg/µg/ng）。用于把 NOAEL 里的占位符与括注周期数区分开——
# "XXmg/kg/天（28天重复给药）" 既无真实数值剂量、"28" 又是天数（单位为"天"非质量），应判为"未给出"，
# 而不能被 _num() 的裸数字正则误取为 NOAEL=28。仅取剂量本身，忽略 /kg、/天 等分母。
_DOSE_MG_RE = re.compile(r"([-+]?\d*\.?\d+)\s*(mg|µg|μg|ug|mcg|ng)\b", re.IGNORECASE)

# 中文/英文种属 → derive_facts 的 species key（derivation._SPECIES_FACTOR）。子串匹配，
# 先特异（小鼠/大鼠）后宽泛（犬/兔/猴），避免「小鼠」被「鼠」误归。
_SPECIES_CN: tuple[tuple[str, str], ...] = (
    ("小鼠", "mouse"), ("大鼠", "rat"), ("sd", "rat"), ("wistar", "rat"),
    ("比格", "dog"), ("beagle", "dog"), ("犬", "dog"),
    ("家兔", "rabbit"), ("兔", "rabbit"),
    ("猕猴", "monkey"), ("食蟹猴", "monkey"), ("猴", "monkey"),
    ("人", "human"),
)


def _num(text: Any) -> float | None:
    """从含单位的字符串取首个数值（"0.5mg/kg/天" → 0.5，"28天" → 28.0）。"""
    if text is None:
        return None
    m = _NUM_RE.search(str(text))
    return float(m.group()) if m else None


def _dose_mg(value: Any) -> float | None:
    """把剂量字符串解析为 **mg**，数字须紧邻质量单位；无此结构 → ``None``（判为未给出）。

    与 :func:`_num` 的关键区别：NOAEL 这类剂量必须是"数字+质量单位"。占位符 "XXmg/kg/天…" 无数值、
    "28天重复给药" 的 28 单位是"天"，两者都返回 ``None``，从而回退 mock 而非用括注里的周期数臆造 NOAEL。
    µg/ng 归一到 mg。
    """
    if value is None:
        return None
    m = _DOSE_MG_RE.search(str(value))
    if not m:
        return None
    n = float(m.group(1))
    unit = m.group(2).lower()
    if unit in ("µg", "μg", "ug", "mcg"):
        return n / 1000.0
    if unit == "ng":
        return n / 1_000_000.0
    return n  # mg


def _find_dp(props: list[dict], iri_suffix: str, label_kw: tuple[str, ...] = ()) -> Any:
    """按 IRI 后缀（首选，前缀无关）取数据属性值；未命中回退 label 关键字子串匹配。"""
    for dp in props or []:
        if iri_suffix and str(dp.get("iri") or "").endswith(iri_suffix):
            return dp.get("value")
    for dp in props or []:
        label = str(dp.get("label") or "")
        if any(k in label for k in label_kw):
            return dp.get("value")
    return None


def _to_ug_day(value: Any) -> float | None:
    """PDE 字符串 → µg/日。识别 mg/µg/ng；无单位按 mg（DP 名即 ``pde_mg_per_day``）。"""
    n = _num(value)
    if n is None:
        return None
    s = str(value).lower()
    if "µg" in s or "μg" in s or "ug" in s or "mcg" in s:
        return n
    if "ng" in s:
        return n / 1000.0
    return n * 1000.0  # 默认 mg → µg


def _species_key(text: Any) -> str | None:
    if not text:
        return None
    low = str(text).lower()
    for kw, key in _SPECIES_CN:
        if kw in low:
            return key
    return None


def _study_from_props(props: list[dict]) -> ToxStudy | None:
    """从共线评估数据属性构造毒理研究事实；NOAEL 或种属缺失/不可解析 → ``None``（回退 mock）。"""
    noael = _dose_mg(_find_dp(props, "noael_mg_per_kg_per_day", ("NOAEL",)))
    species = _species_key(_find_dp(props, "noaelSpecies", ("种属",)))
    duration = _num(_find_dp(props, "noaelDuration", ("周期", "给药周期")))
    if noael is None or species is None:
        return None
    return ToxStudy(
        noael_mg_kg_day=noael,
        species=species,
        study_duration_days=int(duration) if duration else 28,
        notes="从原文共线评估数据抽取的毒理参数",
    )


def detect_pde_conflict(
    object_data_properties: list[dict] | None,
    *,
    method: DerivationMethod = DEFAULT_METHOD,
) -> dict | None:
    """检测原文 PDE 与推导 PDE 的 OEB 带冲突。命中返回冲突记录，一致/不可比较 → ``None``。

    ``object_data_properties`` 为「共线评估数据」端点的数据属性列表（``[{iri,label,value}]``）。
    """
    props = object_data_properties or []

    asserted_ug_day = _to_ug_day(_find_dp(props, "pde_mg_per_day", ("PDE",)))
    if not asserted_ug_day or asserted_ug_day <= 0:
        return None  # 原文无 PDE → 无从比较

    study = _study_from_props(props)
    input_source = "extracted"
    if study is None:
        from app.services.extraction.tox_study_source import get_tox_study_source

        try:
            study = get_tox_study_source().get_study()
        except Exception:  # pragma: no cover - 源不可用绝不抛出
            logger.debug("mock tox-study source unavailable", exc_info=True)
            study = None
        input_source = "mock-tox-study"
    if study is None:
        return None

    result = derive_facts(study, method)
    derived_band = result.oeb_band_recommended
    if derived_band is None:
        return None  # 无法推导 → 不构成冲突

    asserted_band = _band_from_oel(asserted_ug_day / method.breathing_volume_m3, method)
    if derived_band == asserted_band:
        return None  # 一致，无冲突

    asserted_mg = round(asserted_ug_day / 1000.0, 6)
    derived_pde = result.pde_ug_day
    pde_phrase = f"（PDE≈{derived_pde:.3g} µg/日）" if derived_pde else ""
    summary = (
        f"推导 OEB band {derived_band}{pde_phrase}与原文 PDE {asserted_mg:g} mg/日"
        f"（band {asserted_band}）不一致，相差 {abs(derived_band - asserted_band)} 个潜能等级，需人工裁决。"
    )

    return {
        "conflict_key": CONFLICT_KEY,
        "asserted": {
            "pde_mg_day": asserted_mg,
            "pde_ug_day": round(asserted_ug_day, 3),
            "band": asserted_band,
        },
        "derived": {
            "band": derived_band,
            "band_point": result.oeb_band_point,
            "pde_ug_day": round(derived_pde, 3) if derived_pde else None,
            "oel_ug_m3": round(result.oel_ug_m3, 4) if result.oel_ug_m3 else None,
            "provisional": result.provisional,
            "input_source": input_source,
            "provenance": result.provenance,
        },
        "delta_bands": abs(derived_band - asserted_band),
        "summary": summary,
    }
