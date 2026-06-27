"""契约测试：NER Schema 派生（_schema_from_class）。

覆盖 contracts/ner-schema-derivation.md S1–S6：只读 / label 优先 name 回退 /
回填正确 / 空类安全 / 本体演进自适应 / 唯一标签。以假引擎注入 data_properties
桩，不加载真实本体——风格对齐 conftest 的 FakeOntologyEngine。
"""

from __future__ import annotations

from types import SimpleNamespace

from app.services.extraction.pipeline import _schema_from_class

CLS = "https://ontology.pharma-gmp.cn/slpra/drug/DrugProduct"


class _ReadOnlyFakeEngine:
    """只暴露 get_class_detail；任何其他属性访问即视为写路径 → 断言失败（S1 只读）。"""

    def __init__(self, data_properties):
        self._dp = data_properties

    def get_class_detail(self, iri):
        return SimpleNamespace(data_properties=list(self._dp))

    def __getattr__(self, name):  # 仅当属性缺失时触发（get_class_detail/_dp 不会）
        raise AssertionError(f"_schema_from_class 不得触发非只读引擎方法：{name}")


def _dp(iri, name, label=None):
    return {"iri": iri, "name": name, "label": label, "range": ["string"]}


def test_readonly_no_write_path():
    """S1：派生仅调用 get_class_detail，不触发任何写路径。"""
    eng = _ReadOnlyFakeEngine([_dp(f"{CLS}#activeIngredient", "activeIngredient", "活性成分")])
    schema = _schema_from_class(eng, CLS)  # 若触发非只读方法，fake 会 AssertionError
    assert schema["labels"] == ["活性成分"]


def test_label_preferred_name_fallback():
    """S2：label 非空用 label，否则用 name 作 GLiNER 标签。"""
    eng = _ReadOnlyFakeEngine([
        _dp(f"{CLS}#activeIngredient", "activeIngredient", "活性成分"),
        _dp(f"{CLS}#dosageForm", "dosageForm", None),  # 缺 label → 回退 name
    ])
    schema = _schema_from_class(eng, CLS)
    assert schema["labels"] == ["活性成分", "dosageForm"]


def test_label_to_iri_roundfill():
    """S3：label → 属性 IRI 映射准确（供回填抽取结果到 IRI 键）。"""
    eng = _ReadOnlyFakeEngine([
        _dp(f"{CLS}#activeIngredient", "activeIngredient", "活性成分"),
        _dp(f"{CLS}#dosageForm", "dosageForm", "剂型"),
    ])
    schema = _schema_from_class(eng, CLS)
    assert schema["label_to_iri"]["活性成分"] == f"{CLS}#activeIngredient"
    assert schema["label_to_iri"]["剂型"] == f"{CLS}#dosageForm"


def test_empty_class_safe():
    """S4：类无 data_properties → 空 schema（NER 跳过、不报错）。"""
    assert _schema_from_class(_ReadOnlyFakeEngine([]), CLS) == {"labels": [], "label_to_iri": {}}


def test_missing_class_safe():
    """S4 推广：类不存在（get_class_detail 返回 None）→ 空 schema。"""

    class _NoneEngine:
        def get_class_detail(self, iri):
            return None

    assert _schema_from_class(_NoneEngine(), CLS) == {"labels": [], "label_to_iri": {}}


def test_ontology_evolution_adaptive():
    """S5：类属性增减后，下次派生标签集随之变化（无需改配置）。"""
    eng = _ReadOnlyFakeEngine([_dp(f"{CLS}#a", "a", "活性成分")])
    assert _schema_from_class(eng, CLS)["labels"] == ["活性成分"]
    eng._dp = [_dp(f"{CLS}#a", "a", "活性成分"), _dp(f"{CLS}#b", "b", "剂型")]
    assert _schema_from_class(eng, CLS)["labels"] == ["活性成分", "剂型"]


def test_duplicate_label_keeps_first_deterministic():
    """S6：同 label 多属性 → 确定性保留首个（不随机）。"""
    eng = _ReadOnlyFakeEngine([
        _dp(f"{CLS}#first", "first", "规格"),
        _dp(f"{CLS}#second", "second", "规格"),  # 同 label
    ])
    schema = _schema_from_class(eng, CLS)
    assert schema["labels"] == ["规格"]  # 仅一次
    assert schema["label_to_iri"]["规格"] == f"{CLS}#first"  # 保留首个，稳定
