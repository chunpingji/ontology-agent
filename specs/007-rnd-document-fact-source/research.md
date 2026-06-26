# Phase 0 Research: 研发文档事实源（按研发阶段）

**Feature**: `007-rnd-document-fact-source` | **Date**: 2026-06-25 | **Plan**: [plan.md](./plan.md)

规范澄清已闭合（Q1–Q4 见 [spec.md](./spec.md) §Clarifications），**无遗留 NEEDS CLARIFICATION**。本文记录把规范落到既有代码所需的 7 项技术决策，每项含**决策 / 理由 / 备选与否决**，并锚定既有代码事实（行号为本次勘察所见）。

---

## R1 — 连接器工厂：按 `system_type` 分发（替换硬编码 `APSConnector`）

**决策**：在 `services/integration/connector_factory.py` 新增 `connector_for(connector) -> ExternalSystemConnector`，按 `IntegrationConnector.system_type` 分发：`'aps'`（及未知/缺省）→ `APSConnector`；`'doc_repo'` → `DocumentRepositoryConnector`。`materializer.py:50`（`run_sync` 内硬编码 `APSConnector(...)`）与 `api/integration.py`（`test_connector` 行 107、`connector_webhook` 行 155 经 `run_sync`）改为经工厂取连接器。工厂**默认回退 `APSConnector`**。

**理由**：这是设计文档点名的「主要结构改动」，是接入第二类连接器的最小必要解耦。`run_sync` 主流程（幂等去重 60–72、留痕 41–86、先提交后发事件 94–104）与 `FactMaterializationRun` 语义**完全不变**，文档源只是另一种产出 `IncrementalPull.changes` 的连接器，天然复用整条物化/留痕/事件链。

**备选与否决**：(a) 为文档源另写一个并行 materializer——否决，违反宪章 V（并行框架）且重复幂等/留痕/事件逻辑；(b) 在 `run_sync` 内 `if system_type==...` 内联分支——否决，污染主流程、零回归面更大。工厂默认回退保证 APS 既有契约测试为零回归基线。

---

## R2 — 记录层物化：`facts#` 个体，类型由托管 T-Box 类承载（A-Box / T-Box 边界）

**决策**：扩展 `FactMaterializer._materialize`（materializer.py:106）。文档变更的 `IndividualInfo`：
- `iri = f"{_FACT_BASE_IRI}{eid}"`（即 `http://slpra.org/facts#doc-…`）——**个体留 A-Box**；
- `class_iris = [<managed>/slpra/document/<DocSubclass>]`——**类型指向已发布的 T-Box 托管类**（如 `…/slpra/document/TechTransferReport`），而非现状的 `facts#<entity_type>`；
- `properties` 携 `hasDevelopmentPhase`/`documentVersion`/`approvalStatus`/`sourceSystem`/`contentHash`。

映射 `entity_type → DocSubclass IRI` 由连接器 `field_mapping` 或工厂内置默认表提供。`KGStore._detect_module`（kg_store.py:98）增 `"document": "/slpra/document/"`，使文档影子行归 `document` 模块（否则落 `integration`）。

**理由**：守住 [[ttl-tbox-only-abox-in-db]]——个体是事实（DB + `facts#`），类是 T-Box（TTL）。现 `_materialize` 把 `class_iris` 也指向 `facts#`（materializer.py:112），对运营事实无碍；但文档要挂**已发布**的 `RegulatoryDocument` 类层次与 `DevelopmentPhase`，类 IRI 必须指向托管命名空间（前端 `MANAGED_PREFIX = https://ontology.pharma-gmp.cn/slpra/core/` 印证托管前缀；文档模块取 `…/slpra/document/`）。`sync_individual_to_shadow` 以 `class_iris[0]` 落 `class_iri`，故指向托管类即让影子表与图谱按 `RegulatoryDocument` 可检索。

**备选与否决**：把文档类也写 `facts#`——否决，类即进入 A-Box 命名空间，无法经能力一发布/审核/对齐，破坏 T-Box 权威性（宪章 II）。

---

## R3 — `extractedFrom` 溯源回链：经候选 `source_ref` 在 `_commit_candidate` 注入

**决策**：内容层抽取作业（`source_type='doc_repo'`）为每个候选设 `ExtractionCandidate.source_ref = <文档个体 IRI>`（如 `http://slpra.org/facts#doc-TTR-001`）。`_commit_candidate`（extraction.py:191）在确认入库时，把 `extractedFrom = source_ref` 注入提交个体的 `properties`（连同既有 `committed_iri`/`project_entities` 投影），从而每条经文档抽取确认的业务实体携带指回源文档个体的 `extractedFrom`（含经文档继承的 `hasDevelopmentPhase`）。

**理由**：`source_ref`（String(200)，extraction.py:77）正是为「这条候选来自哪个源」而设，现承载 `source_filename`/`config.source_type`（pipeline.py:54）。文档源把它收敛为**文档个体 IRI**，是最小改动即坐实 FR-004（一键溯源）与 SC-002（100% 可溯源）。复用既有复核闭环（`review_candidate` 仅 `confirmed` 走 `_commit_candidate`，extraction.py:217），复核门禁零削弱。

**备选与否决**：新增候选表字段存 `extractedFrom`——否决，YAGNI，`source_ref` 已足够；在管线 Stage 3 直接写图谱——否决，绕过复核门禁，违反宪章 II。

---

## R4 — `DevelopmentPhase`：受控词表枚举（T-Box 例外），本期仅作溯源标注

**决策**：`DevelopmentPhase` 建为 T-Box 枚举类 + 6 个阶段个体（药物发现/临床前/临床Ⅰ期/临床Ⅱ-Ⅲ期/NDA-BLA/上市后），**体例同 `RiskLevel` 枚举个体**——作为「受控词表常量」例外地留在权威 TTL（`slpra-document.ttl`），与「实例事实入 DB」不冲突（[[ttl-tbox-only-abox-in-db]] 备注）。文档/实体经 `hasDevelopmentPhase` 指向这些个体。**本期阶段仅作评估结论的溯源上下文标注**（写入 `rules_fired`/溯源），**不**进 006 声明式判据/决策规则前件（澄清 Q3）。

**理由**：阶段取值是有限、受控、可版本化的词表，与已落地的 `RiskLevel` 同性质，复用其先例最稳。Q3 选「仅溯源标注」使本特性与 006 规则层**零耦合**，范围可控；前件接入留作后续可选升级（FR-011）。

**备选与否决**：阶段个体落 `facts#`/DB——否决，词表常量入 A-Box 不利发布/复用，且与 `RiskLevel` 体例不一致；本期即接规则前件——否决，超出 Q3 决策范围，增 006 规则数据模型与测试面。

---

## R5 — 内容抽取触发：手动发起（事件入队，分析师启动）

**决策**：文档生命周期事件（批准/新版本）**不自动起**抽取作业；`fact_event_bus` 事件触发**编排入「待抽取队列」**（创建 `status='pending'` 的 `ExtractionJob(source_type='doc_repo', source_config={doc_ref, content_ref})` 或等价待办标记），由分析师经一个**手动发起端点**启动管线（澄清 Q1）。记录层物化与事件发布顺序仍遵 materializer.py:94 的「先提交后发事件」不变式。

**理由**：合规上人工发起更稳、节流可控（避免文档洪峰击穿复核队列）；且与既有抽取「先建作业、人工驱动复核」节奏一致。这条决策是「记录是事实、内容是候选」在**触发层**的体现——记录层自动物化（机器可信），内容层人工把关（NL 不确定）。

**备选与否决**：批准即自动起抽取——否决（本期），与 Q1 相悖，需更强限流/去重护栏；完全无事件、纯轮询发起——否决，丢失「文档→抽取」编排联动（FR-007）。

---

## R6 — 过渡接入路径 = 既有「上传」导入；内容存储 = 仅元数据 + 外部引用

**决策**（Q4 + Q2）：真实 EDMS/eTMF 连接器（US4）就位前，文档经平台**既有「上传」方式**导入——即 `DocumentRepositoryConnector` 的 **`upload` 模式**：上传产出一条**归一化文档生命周期变更**（携类型/版本/状态/阶段/来源/SHA 元数据 + 外部内容引用），喂入**同一条** `FactMaterializer` 记录层物化路径，**物化与溯源行为与连接器拉取完全一致**（FR-015）。平台**只存文档元数据 + 外部引用**（URL/DocID/上传文件句柄），抽取时按需拉取正文，**不做平台内全文库**（Q2，避免成为第二个 EDMS）。三种接入模式：`upload`（过渡生产）/ `inline`（确定性测试）/ `http`（真实端点，US4）。

**理由**：Q4 明确以上传为过渡入口而非纯内联；三模共用归一化变更 + 单一物化路径，使 US1 记录层、US2 内容层在真实系统就位前即可端到端验证（含 SC-001 阶段从 0→3）。Q2 的「仅引用」与 `ExtractionConfig`/`source_config` 既有「按引用取源」模式一致（如 `db_source.dsn_ref`），存储/合规面最小。

**备选与否决**：过渡期仅用 `inline` 测试数据——否决，无法支撑真实文档导入这一过渡生产诉求；缓存正文入库——否决（本期），与 Q2 相悖，引入正文留存/合规负担。

---

## R7 — 受影响子图重算扩展 + 凭据注入

**决策**：`events.resolve_affected_subgraph`（events.py:19）增 `'document'` 维（及由文档继承的 `sample`/`product` 关联），使文档事实变更可触发**受影响子图**的推理重算（FR-007 下半句），复用既有事件→重算编排，不改 `AssessmentResult` 对外形状（FR-012）。真实端点（`http` 模式）凭据经 **env 注入**（同 `APSConnector` R7 说明、`DBSourceSpec.dsn_ref` 模式），`connection_config` 不含明文凭据（FR-010）。

**理由**：现子图解析仅覆盖 equipment/product/area；文档/阶段是新的受影响维度，最小扩展即让「新样品/阶段→共线风险评估重算」联动成立。凭据 env 注入复用既有约定，零新机制。

**备选与否决**：文档变更不触发重算——否决，丢 FR-007；为文档另建事件总线——否决，违宪章 V。

---

## 决策汇总

| # | 决策 | 触及代码 | 守宪/对应 |
|---|---|---|---|
| R1 | 连接器工厂按 `system_type` 分发，默认回退 APS | `connector_factory.py`(新)、`materializer.py:50`、`api/integration.py` | V（复用/零回归）、FR-001/012 |
| R2 | 文档个体留 `facts#`，`class_iris` 指托管 T-Box 类 | `materializer.py:106`、`kg_store.py:98` | II、FR-002/006、[[ttl-tbox-only-abox-in-db]] |
| R3 | `extractedFrom` 经 `source_ref` 于 `_commit_candidate` 注入 | `extraction.py:191`、`pipeline.py` | III、FR-004、SC-002 |
| R4 | `DevelopmentPhase` 枚举（同 RiskLevel）；仅溯源标注 | `slpra-document.ttl`(新) | II、FR-005/011、Q3 |
| R5 | 内容抽取手动发起（事件入队） | `events.py`、`api/extraction.py`(新端点) | II、FR-007、Q1 |
| R6 | 过渡=上传导入；仅存元数据+引用 | `doc_repo_connector.py`(新, `upload`/`inline`/`http`) | FR-015、Q2/Q4 |
| R7 | 重算子图增 `document` 维；凭据 env 注入 | `events.py:19`、`doc_repo_connector.py` | III/安全、FR-007/010/012 |

**Phase 0 结论**：所有未知已解析，关键风险（绕复核 / facts# 入 TTL / 工厂波及 APS）均有对应门禁测试设计，进入 Phase 1。
