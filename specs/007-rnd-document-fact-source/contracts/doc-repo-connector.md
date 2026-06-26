# Contract: 文档库连接器 + 连接器工厂

**Feature**: `007-rnd-document-fact-source` | **Covers**: FR-001/010/012/015、US1/US4 | **Refs**: research R1/R6/R7, data-model §2.1/§3

> 契约先行（宪章 IV）。本契约定义 (a) 连接器工厂分发协议、(b) `DocumentRepositoryConnector` 接口与三种接入模式行为、(c) 凭据注入约束。实现 MUST 满足以下断言，关键断言均附测试意图。

---

## C1. 连接器工厂（`services/integration/connector_factory.py`，新）

```python
def connector_for(connector: IntegrationConnector) -> ExternalSystemConnector: ...
```

| # | 断言 | 测试意图 |
|---|---|---|
| C1.1 | `system_type == 'aps'` → 返回 `APSConnector`（参数 `connection_config`/`field_mapping`/`timeout` 与现 `materializer.py:50` 完全一致） | APS 零回归基线 |
| C1.2 | `system_type == 'doc_repo'` → 返回 `DocumentRepositoryConnector` | doc_repo 分发 |
| C1.3 | `system_type` 未知/缺省/`None` → **回退 `APSConnector`** | 默认回退不破坏既有连接器 |
| C1.4 | `materializer.run_sync` 与 `api/integration.py`（test/sync/webhook）经 `connector_for` 取连接器，**不再直引 `APSConnector`** | 所有入口统一经工厂 |

> **零回归红线**：C1.1 必须使既有 APS 契约/集成测试在改造后**全部仍通过**——工厂是纯插入层，不改 `run_sync` 主流程（`materializer.py:38–104`）。

---

## C2. `DocumentRepositoryConnector`（`services/integration/doc_repo_connector.py`，新）

实现 `ExternalSystemConnector`（同 `APSConnector` 形态）。核心方法：

```python
async def test_connection(self) -> bool: ...
async def fetch_incremental(self, cursor: dict | None) -> IncrementalPull: ...
# ExternalSystemConnector 其余抽象方法（fetch_production_schedule 等）→ 最小实现（返回 []）
```

`fetch_incremental` 返回 `IncrementalPull(changes=[…归一化文档变更…], cursor_to={"version": N})`，变更形状见 [data-model.md §3](../data-model.md#3-归一化文档生命周期变更进程内形状)。

### 接入模式（`connection_config.access_mode`）

| 模式 | 用途 | 行为契约 |
|---|---|---|
| `inline` | 确定性测试 | 从 `connection_config.inline_changes` 读增量；`simulate=='timeout'` 时 `sleep(timeout+1)` 触发超时（同 APS 接缝） |
| `upload` | **过渡生产**（Q4/FR-015） | 经平台既有「上传」路径导入的文档转为**一条/多条归一化文档变更**喂入同一 `fetch_incremental` 出口；物化/溯源/幂等行为 MUST 与 `inline`/`http` 一致 |
| `http` | 真实端点（US4/FR-001） | 经 `base_url` + env 注入凭据探活/增量拉取（实现位留出）；`cursor` 按文档系统增量机制推进 |

| # | 断言 | 测试意图 |
|---|---|---|
| C2.1 | `inline` 模式：给定 `inline_changes`，`fetch_incremental` 返回 `version > cursor.version` 的变更，`cursor_to.version = max(version)` | 确定性增量 + 幂等水位 |
| C2.2 | `upload` 模式：一次上传产出的变更经 `run_sync` 物化后，文档个体的 `iri`/`class_iri`/`module`/`properties_json` 与等价 `inline` 变更**逐字节一致** | FR-015 parity |
| C2.3 | `simulate=='timeout'`：`fetch_incremental` 抛 `asyncio.TimeoutError`，`run_sync` 经 `_fail` 置 `cursor_to=None`、`last_status='timeout'` | FR-009/SC-006 |
| C2.4 | 三模 `fetch_incremental` 产出**同一变更骨架**（entity_id/entity_type/version/label/fields），下游物化路径无分支差异 | 单一物化路径 |

---

## C3. 凭据注入（FR-010，宪章 安全）

| # | 断言 | 测试意图 |
|---|---|---|
| C3.1 | `http` 模式凭据经 **env 变量名引用**（如 `connection_config.token_ref="EDMS_TOKEN"`），运行时 `os.environ` 解析 | 凭据不入库 |
| C3.2 | 持久化后检视 `integration_connectors.connection_config`：**不含明文 token/password/密钥** | SC（FR-010）、US4 AS#2 |
| C3.3 | 凭据 MUST NOT 出现在 `FactMaterializationRun.changes`/审计/日志 | 最小暴露 |

---

## C4. 与既有契约的关系

- **不新增** REST 端点形状：doc_repo 连接器 CRUD 复用 `api/integration.py` 既有连接器端点（仅 `system_type` 取值扩展）。
- **不改** `IncrementalPull` 数据类（复用 `aps_connector.py:30`，或上提至 `base.py` 供两连接器共享——实现细节，不改字段）。
- **不改** `FactMaterializationRun`/`fact_event_bus` 事件信封。
