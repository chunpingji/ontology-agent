# Contract: 记录层物化不变量（文档作为事实）

**Feature**: `007-rnd-document-fact-source` | **Covers**: FR-002/006/008/009/014、US1/US3、SC-004/005/006/008 | **Refs**: research R2/R4/R7, data-model §2.3/§5/§7

> 本契约锁定「文档作为记录直接物化为 A-Box 个体」的不变量——尤其是 **A-Box/T-Box 边界**（SC-004 = 0 越界）与**幂等**（SC-005 = 0 重复）。这是 US1 的 MVP 验收骨架，也是宪章 II 的核心门禁。

---

## C1. `_materialize` 文档分支（`materializer.py:106`）

文档变更物化为 `IndividualInfo`：

| 字段 | 取值 | 不变量 |
|---|---|---|
| `iri` | `f"{_FACT_BASE_IRI}{entity_id}"` | **MUST** 在 `http://slpra.org/facts#`（A-Box） |
| `class_iris[0]` | `field_mapping.doc_type_to_class[entity_type]`（缺省走工厂默认表） | **MUST** 指向托管 `…/slpra/document/<Subclass>`（**非** `facts#`） |
| `label_zh` | `change.label` | — |
| `properties` | `{**fields, "_version": version}` | 含 `hasDevelopmentPhase`/`documentVersion`/`approvalStatus`/`sourceSystem`/`contentHash`/`externalRef` |

| # | 断言 | 测试意图 |
|---|---|---|
| C1.1 | 文档物化后 `entity_shadow.iri` 落 `facts#`，`class_iri` 落托管 `/slpra/document/` | A-Box 个体 + T-Box 类型（FR-002/006） |
| C1.2 | `sync_individual_to_shadow` 经 `_detect_module(class_iris)` 归 `module='document'`（`kg_store.py:98` **与** `api/kg.py:96` 两处一致） | data-model §2.5 双处增补 |
| C1.3 | 文档个体 100% 携 `hasDevelopmentPhase`，可按阶段检索（`search_entities`/SPARQL 过滤） | SC-008 |
| C1.4 | 非文档变更（entity_type 不在文档映射表）仍走原 `facts#<entity_type>` 类 IRI 分支 | 既有运营事实零回归 |

---

## C2. A-Box / T-Box 边界门禁（SC-004，**关键**）

| # | 断言 | 测试意图 |
|---|---|---|
| C2.1 | 物化任意批文档后，扫描 `ontology/slpra/*.ttl`：**无任何** `facts#` 个体三元组 | 文档个体不入 TTL（SC-004 = 0） |
| C2.2 | `slpra-document.ttl` 仅含**类/枚举/属性**（T-Box），不含文档**个体**（`RegulatoryDocument` 的具名实例为 0） | T-Box 纯净 |
| C2.3 | T-Box 增补经 `surgical_merge` + `export_diff` round-trip：重复发布幂等、未建模三元组逐字保留、外部命名 IRI（BFO/OBO）不被改写 | 宪章 II 保真 |

> C2.1 是 **SC-004 的直接量化门禁**——必须作为独立测试存在，文档物化路径任何回归都应令其失败。

---

## C3. 幂等与水位（FR-008/009，SC-005/006）

复用 `run_sync` 既有幂等机制（`materializer.py:59–72`，`versions[eid]` 仅推进最高版本）：

| # | 断言 | 测试意图 |
|---|---|---|
| C3.1 | 同一 `entity_id` 先后到 v1、v2 → 仅保留/更新至 v2；重复 v2 或乱序 v1 被跳过（不产生第二个个体） | SC-005 = 0 重复 |
| C3.2 | 成功同步：`cursor_to` 推进、`change_count` 准确、`changes` 留痕、`event_ids` 在**提交后**回填 | FR-008、顺序不变式（`materializer.py:94`） |
| C3.3 | 超时/不可达：`_fail` 置 `cursor_to=None`、`last_status∈{timeout,error}`、无半成品 `entity_shadow` 行写入；上一良好水位保留 | FR-009/SC-006 |
| C3.4 | `upload` 路径物化的幂等/留痕/水位行为与 `inline`/`http` 一致 | FR-015 |

---

## C4. 生命周期状态（FR-014，不物理删除）

| # | 断言 | 测试意图 |
|---|---|---|
| C4.1 | 文档被取代：旧版本个体置 `approvalStatus='superseded'` 并**保留**；新版本个体可查 | FR-014 / Edge「撤回/作废」 |
| C4.2 | 文档作废：置 `approvalStatus='withdrawn'`，溯源链（指向它的 `extractedFrom`）仍可追溯 | FR-014 |
| C4.3 | 任何路径**不** `DELETE` 文档个体影子行（状态变更表达，非物理删除） | 宪章 III |

---

## C5. 审计与留痕（宪章 III）

| # | 断言 | 测试意图 |
|---|---|---|
| C5.1 | 每次文档同步 `audit.append("integration.materialize", actor="system", details={run_id,change_count})` | FR-008、US1 AS#5 |
| C5.2 | `FactMaterializationRun` 可追溯来源/变更数/水位/时间/状态 | US1 AS#5 |
