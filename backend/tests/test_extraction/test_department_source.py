"""Mock 外部部门主数据事实源单测（Department A-Box 尚未对接的 interim 替身）。

契约：按部门代码解析为带合成 A-Box IRI / 名称 / 简介 / 属性的 Department 事实；
未知部门代码 → ``None``（优雅降级）。``list_all`` 列出全部已知部门。
"""

from __future__ import annotations

from app.services.extraction.department_source import (
    DepartmentFact,
    MockDepartmentSource,
    get_department_source,
)


# --- resolve by department code ---------------------------------------------

def test_resolve_qa():
    fact = get_department_source().resolve("QA")
    assert isinstance(fact, DepartmentFact)
    assert fact.code == "QA"
    assert fact.label == "质量部"
    assert fact.iri == "http://slpra.org/facts#department-QA"
    assert "质量保证" in fact.description


def test_resolve_wh():
    fact = get_department_source().resolve("WH")
    assert fact.label == "仓储部"


def test_resolve_ehs():
    fact = get_department_source().resolve("EHS")
    assert fact.label == "EHS"
    labels = {d["label"]: d["value"] for d in fact.data_properties}
    assert labels["部门全称"] == "环境、职业健康与安全部"


def test_resolve_eng():
    fact = get_department_source().resolve("ENG")
    assert fact.label == "设备部"


def test_resolve_ws():
    fact = get_department_source().resolve("WS")
    assert fact.label == "生产车间"


def test_resolve_pd1():
    fact = get_department_source().resolve("PD1")
    assert fact.label == "生产一部"


def test_resolve_pd2():
    fact = get_department_source().resolve("PD2")
    assert fact.label == "生产二部"


def test_resolve_data_properties_structure():
    fact = get_department_source().resolve("QA")
    labels = {d["label"]: d["value"] for d in fact.data_properties}
    assert labels["部门代码"] == "QA"
    assert labels["部门名称"] == "质量部"
    assert labels["部门全称"] == "质量管理部"
    assert "核心职责" in labels
    assert "关联GxP角色" in labels
    assert "qa" in labels["关联GxP角色"]


def test_resolve_case_insensitive():
    source = get_department_source()
    assert source.resolve("qa").label == "质量部"
    assert source.resolve("Qa").label == "质量部"
    assert source.resolve("ehs").label == "EHS"


def test_resolve_strips_whitespace():
    assert get_department_source().resolve("  QA ").label == "质量部"


def test_resolve_unknown_returns_none():
    assert get_department_source().resolve("UNKNOWN") is None


def test_resolve_empty_returns_none():
    source = get_department_source()
    assert source.resolve("") is None
    assert source.resolve("   ") is None


# --- list_all ---------------------------------------------------------------

def test_list_all_count():
    items = get_department_source().list_all()
    assert len(items) == 7


def test_list_all_contains_all_departments():
    labels = {f.label for f in get_department_source().list_all()}
    assert labels == {"质量部", "仓储部", "EHS", "设备部", "生产车间", "生产一部", "生产二部"}


def test_all_iris_unique():
    items = get_department_source().list_all()
    iris = [f.iri for f in items]
    assert len(iris) == len(set(iris))


def test_all_codes_unique():
    items = get_department_source().list_all()
    codes = [f.code for f in items]
    assert len(codes) == len(set(codes))


# --- factory ----------------------------------------------------------------

def test_factory_returns_mock():
    assert isinstance(get_department_source(), MockDepartmentSource)
