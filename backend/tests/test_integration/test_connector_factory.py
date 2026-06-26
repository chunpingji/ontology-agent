"""连接器工厂 APS 零回归基线（007 Foundational，T005）。

契约：[doc-repo-connector C1.1/C1.3/C1.4](../../../specs/007-rnd-document-fact-source/contracts/doc-repo-connector.md)。
红线：默认回退 APS——既有 5 类事实源与未知取值零回归（SC-007）。
"""

from __future__ import annotations

from app.models.integration import IntegrationConnector
from app.services.integration.aps_connector import APSConnector
from app.services.integration.connector_factory import connector_for


def _connector(system_type, *, poll=2) -> IntegrationConnector:
    return IntegrationConnector(
        system_type=system_type,
        connection_config={"source_mode": "inline", "inline_changes": []},
        field_mapping={"prod_code": "product"},
        poll_interval_seconds=poll,
    )


def test_aps_dispatch_params_identical_to_materializer_line50():
    """C1.1：aps → APSConnector，config/field_mapping/timeout 与现 materializer.py:50 完全一致。"""
    c = _connector("aps", poll=7)
    conn = connector_for(c)
    assert isinstance(conn, APSConnector)
    assert conn.config is c.connection_config
    assert conn.field_mapping is c.field_mapping
    assert conn.timeout == float(7) + 3.0


def test_unknown_system_type_falls_back_to_aps():
    """C1.3：未知/其余既有取值 → 回退 APSConnector（不破坏既有连接器）。"""
    for st in ("erp", "mes", "lims", "ctms", "weird", ""):
        assert isinstance(connector_for(_connector(st)), APSConnector)


def test_none_system_type_falls_back_to_aps():
    """C1.3：None system_type → 回退 APSConnector。"""
    c = _connector("aps")
    c.system_type = None
    assert isinstance(connector_for(c), APSConnector)


def test_default_timeout_formula_when_poll_missing():
    """缺省 poll_interval_seconds → 超时公式回退 float(2)+3.0（与现状一致）。"""
    c = _connector("aps")
    c.poll_interval_seconds = None
    assert connector_for(c).timeout == float(2) + 3.0


def test_uppercase_system_type_falls_back_to_aps():
    """大小写无关：'APS'（既有测试以大写建连接器）→ APSConnector。"""
    assert isinstance(connector_for(_connector("APS")), APSConnector)
