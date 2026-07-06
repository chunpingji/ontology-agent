"""Mock 外部角色主数据事实源单测（GxPRole A-Box 尚未对接的 interim 替身）。

契约：按角色代码解析为带合成 A-Box IRI / 名称 / 本体角色类 IRI / 属性的 Role 事实；
未知角色代码 → ``None``（优雅降级）。``list_all`` 列出全部已知角色。
"""

from __future__ import annotations

from app.services.extraction.role_source import (
    MockRoleSource,
    RoleFact,
    get_role_source,
)

_PERS_NS = "https://ontology.pharma-gmp.cn/slpra/personnel/"


# --- resolve by role code ---------------------------------------------------

def test_resolve_qa():
    fact = get_role_source().resolve("QA")
    assert isinstance(fact, RoleFact)
    assert fact.code == "QA"
    assert fact.label == "QA"
    assert fact.iri == "http://slpra.org/facts#role-QA"
    assert fact.role_class_iri == f"{_PERS_NS}QualityAssuranceRole"


def test_resolve_wh():
    fact = get_role_source().resolve("WH")
    assert fact.label == "仓储管理"
    assert fact.role_class_iri == f"{_PERS_NS}WarehouseManagementRole"


def test_resolve_ehs():
    fact = get_role_source().resolve("EHS")
    assert fact.label == "EHS评估"
    assert fact.role_class_iri == f"{_PERS_NS}EHSRole"


def test_resolve_ppa():
    fact = get_role_source().resolve("PPA")
    assert fact.label == "生产工艺评估"
    assert fact.role_class_iri == f"{_PERS_NS}ProductionProcessAssessmentRole"


def test_resolve_eng():
    fact = get_role_source().resolve("ENG")
    assert fact.label == "设备评估"
    assert fact.role_class_iri == f"{_PERS_NS}EquipmentEngineeringRole"


def test_resolve_data_properties():
    fact = get_role_source().resolve("QA")
    labels = {d["label"]: d["value"] for d in fact.data_properties}
    assert labels["角色代码"] == "QA"
    assert labels["角色名称"] == "QA"
    assert "核心职责" in labels
    assert labels["归属部门"] == "质量部"


def test_resolve_case_insensitive():
    source = get_role_source()
    assert source.resolve("qa").label == "QA"
    assert source.resolve("ppa").label == "生产工艺评估"
    assert source.resolve("Eng").label == "设备评估"


def test_resolve_strips_whitespace():
    assert get_role_source().resolve("  QA ").label == "QA"


def test_resolve_unknown_returns_none():
    assert get_role_source().resolve("UNKNOWN") is None


def test_resolve_empty_returns_none():
    source = get_role_source()
    assert source.resolve("") is None
    assert source.resolve("   ") is None


# --- list_all ---------------------------------------------------------------

def test_list_all_count():
    assert len(get_role_source().list_all()) == 5


def test_list_all_contains_all_roles():
    labels = {f.label for f in get_role_source().list_all()}
    assert labels == {"QA", "仓储管理", "EHS评估", "生产工艺评估", "设备评估"}


def test_all_iris_unique():
    items = get_role_source().list_all()
    iris = [f.iri for f in items]
    assert len(iris) == len(set(iris))


def test_all_role_class_iris_unique():
    items = get_role_source().list_all()
    class_iris = [f.role_class_iri for f in items]
    assert len(class_iris) == len(set(class_iris))


# --- factory ----------------------------------------------------------------

def test_factory_returns_mock():
    assert isinstance(get_role_source(), MockRoleSource)
