"""Mock 外部车间主数据事实源单测（ProductionArea 尚未对接的 interim 替身）。

契约：按车间号解析为带合成 A-Box IRI / 名称 / 简介 / 设施属性的 ProductionArea 事实；
未知车间号 → ``None``（优雅降级，调用方跳过）。真实内网设施 API 接入后仅替换工厂返回的实现。
"""

from __future__ import annotations

from app.services.extraction.production_area_source import (
    MockProductionAreaSource,
    ProductionAreaFact,
    get_production_area_source,
)


def test_resolve_known_workshop_returns_fact():
    fact = get_production_area_source().resolve("642")
    assert isinstance(fact, ProductionAreaFact)
    assert fact.code == "642"
    assert fact.label == "642车间"
    assert fact.iri == "http://slpra.org/facts#production-area-642"
    labels = {d["label"]: d["value"] for d in fact.data_properties}
    assert labels["车间编号"] == "642"
    assert labels["洁净区设置"] == "一般区 / D级洁净区"
    assert labels["所属厂房"] == "原料药一厂房"
    assert labels["部门代码"] == "222"
    assert labels["车间用途"] == "非细胞毒临床产品备样专用车间"
    assert labels["产品类型"] == "非无菌原料药"
    assert labels["厂房设施确认年份"] == "2020"
    assert "适用品种" in labels


def test_resolve_642_has_description():
    fact = get_production_area_source().resolve("642")
    assert "642 车间" in fact.description
    assert "D 级洁净区" in fact.description
    assert "连云港" in fact.description


def test_resolve_646_has_description():
    fact = get_production_area_source().resolve("646")
    assert "646 车间" in fact.description
    assert "仅设有一般区" in fact.description
    assert fact.description  # non-empty


def test_resolve_646_no_d_class():
    fact = get_production_area_source().resolve("646")
    labels = {d["label"]: d["value"] for d in fact.data_properties}
    assert labels["洁净区设置"] == "一般区"
    assert labels["部门代码"] == "226"


def test_resolve_both_split_workshops():
    source = get_production_area_source()
    assert source.resolve("642").label == "642车间"
    assert source.resolve("646").label == "646车间"
    assert source.resolve("642").iri != source.resolve("646").iri


def test_resolve_unknown_workshop_returns_none():
    assert get_production_area_source().resolve("999") is None


def test_resolve_strips_whitespace():
    assert get_production_area_source().resolve("  642 ").label == "642车间"


def test_resolve_empty_code_returns_none():
    source = get_production_area_source()
    assert source.resolve("") is None
    assert source.resolve("   ") is None


def test_resolve_644_has_no_description():
    fact = get_production_area_source().resolve("644")
    assert fact is not None
    assert fact.description == ""


def test_factory_returns_mock_implementation():
    assert isinstance(get_production_area_source(), MockProductionAreaSource)
