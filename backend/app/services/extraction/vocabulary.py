"""Explicit controlled vocabularies; conditional evidence uses the semantic runner."""

CONTROLLED_VOCAB: dict[str, list[str]] = {
    "oeb": ["OEB1", "OEB2", "OEB3", "OEB4", "OEB5", "OEB6"],
    "cleanliness_grade": ["A级", "B级", "C级", "D级", "CNC"],
    "material": ["316L不锈钢", "304不锈钢", "哈氏合金", "玻璃", "PTFE", "硅胶"],
    "pde_unit": ["µg/day", "mg/day", "ng/day"],
}


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
