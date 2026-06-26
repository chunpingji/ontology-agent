# Contract: 溯源查询 + 研发阶段维度

**Feature**: `007-rnd-document-fact-source` | **Covers**: FR-004/005/011、US3、SC-002/008 | **Refs**: research R3/R4, data-model §6/§7

> 本契约定义对外可观测的**溯源查询**与**研发阶段维度**行为：阶段作为受控词表的检索/标注，以及阶段作为评估结论的**溯源上下文**（本期**仅标注**，不进规则前件——Q3/FR-011）。

---

## C1. 研发阶段词表（FR-005，T-Box）

| # | 断言 | 测试意图 |
|---|---|---|
| C1.1 | `DevelopmentPhase` 枚举含 6 个体：DrugDiscovery/Preclinical/ClinicalI/ClinicalII_III/NDA_BLA/PostMarket | FR-005、US3 AS#3 |
| C1.2 | 阶段取值受控、可版本化；新增/调整经 `surgical_merge` 发布 + 审计 | US3 AS#3 |
| C1.3 | 每阶段个体携 `skos:notation`（次序）+ `rdfs:comment`（质量体系侧重） | US3 AS#2 标注来源 |

---

## C2. 按阶段检索（SC-008，US3 AS#1）

| # | 断言 | 测试意图 |
|---|---|---|
| C2.1 | 按 `hasDevelopmentPhase` 过滤可返回该阶段下的**文档**集合 | US3 AS#1 |
| C2.2 | 按 `hasDevelopmentPhase` 过滤可返回该阶段下的**派生实体**（备样/药物）集合 | US3 AS#1、SC-001 |
| C2.3 | 文档事实个体 100% 携阶段标注（无「未标注」文档个体） | SC-008 |

> 检索复用既有 `search_entities`（`kg_store.py:54`）/ SPARQL 端点（`api/kg.py:35`）；按 `properties_json.hasDevelopmentPhase` 或图谱属性过滤——**不新增检索框架**。

---

## C3. 一键溯源（FR-004，SC-002）

| # | 断言 | 测试意图 |
|---|---|---|
| C3.1 | 给定经文档抽取入库的业务实体，可经其 `extractedFrom` 解析到**源文档个体** | FR-004 |
| C3.2 | 由文档个体的 `documentVersion` 可得「抽自哪一版」 | SC-002 |
| C3.3 | 100% 经文档抽取确认入库实体可溯源（无断链候选入图） | SC-002 |

---

## C4. 阶段作为评估溯源上下文（FR-011 / Q3 — **本期仅标注**）

| # | 断言 | 测试意图 |
|---|---|---|
| C4.1 | 至少一条评估结论的**溯源**（如 `rules_fired`/溯源标注）体现对应阶段质量侧重（如临床Ⅰ期→共线风险/清洁确认） | US3 AS#2 |
| C4.2 | 阶段 **MUST NOT** 出现在 006 声明式判据/决策规则的**前件**中（本期不参与推断） | FR-011 红线、Q3 |
| C4.3 | 接入阶段标注后，`AssessmentResult` 对外形状不变（阶段标注为附加溯源上下文，非新结论字段） | FR-012/SC-007 |

> C4.2 是 **FR-011 的负向门禁**：测试应断言阶段 IRI 不被任何 006 规则的 antecedent 引用——防止本期越界把阶段织入推断逻辑（避免与 006 规则数据模型耦合）。

---

## C5. 零回归（FR-012 / SC-007）

| # | 断言 | 测试意图 |
|---|---|---|
| C5.1 | 接入文档事实源后，既有 5 类事实源（APS/ERP/MES/LIMS/CTMS）回归用例非预期变化数 = 0 | SC-007 |
| C5.2 | 既有评估结论基线对外形状（`AssessmentResult`）不变 | FR-012 |
