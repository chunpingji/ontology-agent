"""受控词表与 Word 正文 Action 抽取（FR-005/006, R3）。

- ``CONTROLLED_VOCAB``：OEB/PDE/材质/洁净级别等受控取值，抽取时归一化注入。
- ``parse_action_from_text``：识别 SOP 正文「若…则…必须…」条件式 → Action 候选的
  前置/后置条件结构（写入 ``ExtractionCandidate.action_conditions``）。
"""

from __future__ import annotations

import re

CONTROLLED_VOCAB: dict[str, list[str]] = {
    "oeb": ["OEB1", "OEB2", "OEB3", "OEB4", "OEB5", "OEB6"],
    "cleanliness_grade": ["A级", "B级", "C级", "D级", "CNC"],
    "material": ["316L不锈钢", "304不锈钢", "哈氏合金", "玻璃", "PTFE", "硅胶"],
    "pde_unit": ["µg/day", "mg/day", "ng/day"],
}

# 条件式触发词。
_COND_RE = re.compile(r"(若|如果|当)(?P<cond>.+?)(则|，则|时)(?P<act>.+)")
_MUST_RE = re.compile(r"(必须|应当|应|需|不得|禁止)")


def normalize_vocab(value: str) -> str | None:
    """将文本归一化到受控词表取值（大小写/全角容错），命中返回规范值。"""
    if not value:
        return None
    v = value.strip().upper().replace(" ", "")
    for terms in CONTROLLED_VOCAB.values():
        for term in terms:
            if term.upper().replace(" ", "") == v:
                return term
    return None


def tag_controlled_vocab(properties: dict) -> dict:
    """在属性字典上附加命中的受控词表归一化结果（不破坏原值）。"""
    tags: dict[str, str] = {}
    for key, val in properties.items():
        if not isinstance(val, str):
            continue
        canon = normalize_vocab(val)
        if canon:
            tags[key] = canon
    if tags:
        properties = {**properties, "_controlled_vocab": tags}
    return properties


def parse_action_from_text(text: str) -> dict | None:
    """从一段正文识别条件-动作（前置/后置条件），命中返回 action_conditions 结构。"""
    text = (text or "").strip()
    if not text:
        return None
    m = _COND_RE.search(text)
    if not m:
        return None
    action_part = m.group("act").strip()
    # 仅当动作段含强制性措辞才视为可执行 Action（区别于一般陈述）。
    if not _MUST_RE.search(action_part):
        return None
    return {
        "precondition": m.group("cond").strip(),
        "action": action_part,
        "obligation": _MUST_RE.search(action_part).group(0),
        "source_text": text,
    }
