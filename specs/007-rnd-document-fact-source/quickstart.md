# Quickstart: 研发文档事实源（按研发阶段）

**Feature**: `007-rnd-document-fact-source` | **Plan**: [plan.md](./plan.md) | **Contracts**: [contracts/](./contracts/)

> 端到端验证指南——每个场景对应一组用户故事验收与成功标准（SC），用确定性 `inline`/`upload` 数据即可全程跑通，无需真实 EDMS（真实端点为 US4）。具体断言见 [contracts/](./contracts/)，数据形状见 [data-model.md](./data-model.md)。

## 前置

```bash
cd backend && pytest -q                 # 改造前：既有套件全绿（零回归基线）
```
- T-Box：`ontology/slpra/slpra-document.ttl` 经能力一发布路径合入（`surgical_merge` + 三元组 diff），含 `RegulatoryDocument` 类层次 + `DevelopmentPhase` 6 阶段枚举 + `extractedFrom`/`hasDevelopmentPhase` 等属性（[data-model.md §7](./data-model.md#7-t-box-公理增补slprasprasdocumentttl-新模块经-surgical_merge-发布)）。
- 凭据（仅 `http` 模式）：经环境变量注入（如 `export EDMS_TOKEN=…`），`connection_config` 仅存变量名引用（FR-010 / [doc-repo-connector C3](./contracts/doc-repo-connector.md)）。

---

## 场景 1 — 文档作为一等溯源锚点入图（US1 / P1，MVP）

**目标**：SC-004（边界 0 越界）、SC-005（幂等 0 重复）、SC-006（失败 0 半成品）、SC-008（100% 阶段标注）。

1. 建 `system_type='doc_repo'` 连接器，`connection_config.access_mode='inline'`，`inline_changes` 放一条「技术转移报告 v2 / 临床Ⅰ期 / 已批准」变更（形状见 [data-model §3](./data-model.md#3-归一化文档生命周期变更进程内形状)）。
2. 运行同步（经连接器工厂 → `run_sync`）。

**预期**：
- 生成 `facts#` 文档个体，`class_iri` = 托管 `…/slpra/document/TechTransferReport`，`module='document'`，携阶段/版本/状态/来源/指纹，可检索（US1 AS#1）。
- 再喂同一文档 v1（乱序）与重复 v2 → 仅保留 v2，无重复个体（US1 AS#2 / **SC-005**）。
- 扫描 `ontology/slpra/*.ttl`：**无** `facts#` 个体三元组（US1 AS#3 / **SC-004**，[record-materialization C2](./contracts/record-materialization-invariants.md)）。
- 注入 `simulate='timeout'` → `last_status='timeout'`、`cursor_to=None`、无半成品、上一良好水位保留，上传抽取路径不受影响（US1 AS#4 / **SC-006**）。
- `FactMaterializationRun` + `integration.materialize` 审计可追溯来源/变更数/时间/执行者（US1 AS#5）。

```bash
pytest -q tests/test_integration/ -k "doc_repo and (materialize or idempotent or boundary or timeout)"
```

---

## 场景 2 — 从文档抽取业务实体并溯源回文档（US2 / P2）

**目标**：SC-002（100% 可溯源）、SC-003（0% 自动入库）。

1. 取场景 1 的文档个体；模拟其 `approved` 事件 → 编排**入待抽取队列**（`ExtractionJob source_type='doc_repo' status='pending'`，**不自动发起**，Q1）。
2. 分析师手动发起抽取 → doc_repo 分支产出**待复核候选**（药物/备样/质量标准），`source_ref=<文档个体 IRI>`。
3. 确认一个候选、拒绝另一个。

**预期**：
- 候选一律 `pending`，不自动入库（US2 AS#1 / **SC-003**，[content-extraction C2/C3](./contracts/content-extraction-orchestration.md)）。
- 确认的候选入事实层，携 `extractedFrom=<文档个体 IRI>` + 继承 `hasDevelopmentPhase`（US2 AS#2 / **SC-002**）。
- 拒绝的候选不入库且决定可追溯（US2 AS#3）。
- 新版本文档入队与旧版本溯源可区分（US2 AS#4）。

```bash
pytest -q tests/test_extraction/ -k "doc_repo and (provenance or review or extractedFrom)"
```

---

## 场景 3 — 研发阶段作为溯源与风险/合规上下文（US3 / P3）

**目标**：SC-001（覆盖阶段 0→3）、SC-008（按阶段检索）。

1. 物化分属不同阶段的文档/实体。
2. 按 `hasDevelopmentPhase` 过滤检索；查看一条临床Ⅰ期共线风险评估结论的溯源。

**预期**：
- 按阶段正确返回文档与实体集合（US3 AS#1 / **SC-008**，[provenance-and-phase C2](./contracts/provenance-and-phase-query.md)）。
- 评估结论溯源体现该阶段质量侧重（共线风险/清洁确认）——**仅标注**（US3 AS#2 / FR-011）。
- 阶段词表受控、可版本化、发布经审计（US3 AS#3）。
- **负向门禁**：阶段 IRI 不出现在任何 006 规则前件中（**FR-011 红线**，[provenance-and-phase C4.2](./contracts/provenance-and-phase-query.md)）。
- 早期阶段（发现/临床前/临床Ⅰ）药物/备样经文档进入图谱——从「无源」变「有源」（**SC-001**）。

```bash
pytest -q -k "phase and (filter or provenance or not_in_rule_antecedent)"
```

---

## 场景 4 — 上传过渡入口（US1 扩展 / FR-015）与真实端点（US4 / P4）

1. **上传过渡（FR-015）**：经平台既有「上传」路径导入一份文档 → 产生归一化变更 → 经同一 `run_sync` 物化。
   - **预期**：记录层个体的 `iri`/`class_iri`/`module`/`properties` 与等价 `inline` 变更**逐字节一致**（**FR-015 parity**，[doc-repo-connector C2.2](./contracts/doc-repo-connector.md)）。
2. **真实端点（US4）**：配 `access_mode='http'` + env 注入凭据 → 探活 + 增量拉取。
   - **预期**：复用 US1 记录层 + US2 内容编排，行为与 inline 一致（US4 AS#1）；`connection_config` 不含明文凭据（US4 AS#2 / **FR-010**）。

```bash
pytest -q tests/test_integration/ -k "doc_repo and (upload_parity or http or credentials)"
```

---

## 零回归门禁（贯穿 / SC-007）

```bash
cd backend && pytest -q     # 全套绿：APS/ERP/MES/LIMS/CTMS 与 AssessmentResult 对外形状不变
```
连接器工厂默认回退 APS（[doc-repo-connector C1.1/C1.3](./contracts/doc-repo-connector.md)）→ 既有 5 类事实源与评估结论**零回归**（FR-012 / **SC-007**）。

---

## 验收映射速查

| 场景 | 用户故事 | 关键 SC | 关键契约 |
|---|---|---|---|
| 1 | US1（P1，MVP） | SC-004/005/006/008 | record-materialization-invariants |
| 2 | US2（P2） | SC-002/003 | content-extraction-orchestration |
| 3 | US3（P3） | SC-001/008、FR-011 | provenance-and-phase-query |
| 4 | US1-ext/US4（P4） | FR-015/010 | doc-repo-connector |
| 全程 | — | SC-007 | doc-repo-connector C1 |
