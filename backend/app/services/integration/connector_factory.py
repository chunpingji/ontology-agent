"""连接器工厂（007 Foundational，FR-001/012；doc-repo-connector C1）。

统一连接器获取入口：按 `IntegrationConnector.system_type` 分发到具体连接器实现。
**默认回退 `APSConnector`**——既有 5 类事实源（APS/ERP/MES/LIMS/CTMS）与未知/缺省/
`None` 取值均零回归（C1.1/C1.3）。`doc_repo` 分发在 T016 叠加（依赖 T015 连接器存在）。

工厂是纯插入层：不改 `run_sync` 主流程（`materializer.py:38–104`）。所有连接器入口
（`materializer.run_sync` + `api/integration.py` test/sync/webhook）均经 `connector_for`
取连接器，不再直引 `APSConnector`（C1.4）。
"""

from __future__ import annotations

from app.models.integration import IntegrationConnector
from app.services.integration.aps_connector import APSConnector
from app.services.integration.base import ExternalSystemConnector

# 托管文档模块命名空间（与 fixture/slpra-document.ttl 单一来源一致）。
DOCUMENT_NS = "https://ontology.pharma-gmp.cn/slpra/document/"

# 默认 entity_type → 托管 T-Box 文档类 IRI 映射（local-name 即类名；field_mapping 可覆盖）。
_DOC_TYPES = (
    "RegulatoryDocument",
    "INDDossier",
    "TechTransferReport",
    "ProcessValidationReport",
    "StabilityReport",
    "NDA_BLADossier",
    "PVReport",
)
DEFAULT_DOC_TYPE_TO_CLASS: dict[str, str] = {dt: f"{DOCUMENT_NS}{dt}" for dt in _DOC_TYPES}


def _timeout_for(connector: IntegrationConnector) -> float:
    """与现 `materializer.py:50` / `api/integration.py:107` 完全一致的超时公式。"""
    return float(connector.poll_interval_seconds or 2) + 3.0


def doc_type_to_class_map(connector: IntegrationConnector) -> dict[str, str]:
    """doc_repo：entity_type → 托管文档类 IRI 映射（默认表 ∪ field_mapping 覆盖）。

    非 doc_repo 连接器返回空 dict → `_materialize` 走原 `facts#<entity_type>` 分支（零回归）。
    凭此一张表把"记录"挂到 **托管 T-Box** 子类（而非 `facts#` 类），A-Box 个体仍落 `facts#`。
    """
    if (connector.system_type or "").lower() != "doc_repo":
        return {}
    override = (connector.field_mapping or {}).get("doc_type_to_class") or {}
    return {**DEFAULT_DOC_TYPE_TO_CLASS, **override}


def connector_for(connector: IntegrationConnector) -> ExternalSystemConnector:
    """按 `system_type` 取连接器实例。

    `doc_repo` → `DocumentRepositoryConnector`（T016 叠加）；其余一切（`aps`/未知/缺省/
    `None`）→ **回退 `APSConnector`**，参数与现 `materializer.py:50` 完全一致（C1.1/C1.3）。
    """
    system_type = (connector.system_type or "").lower()
    timeout = _timeout_for(connector)

    if system_type == "doc_repo":
        # 延迟导入：避免 Foundational 阶段对 US1 连接器模块的硬依赖；T016 起生效。
        from app.services.integration.doc_repo_connector import DocumentRepositoryConnector

        return DocumentRepositoryConnector(
            connector.connection_config, connector.field_mapping, timeout=timeout
        )

    # 'aps' 及任何未知/缺省/None → APSConnector（默认回退，零回归红线 C1.1/C1.3）。
    return APSConnector(
        connector.connection_config, connector.field_mapping, timeout=timeout
    )
