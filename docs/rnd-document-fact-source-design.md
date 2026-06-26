# 研发文档事实源（按研发阶段）设计文档

**状态**: 已立项并实现 —— 特性 `007-rnd-document-fact-source`，US1–US4 全部交付；本文保留为**设计评审与架构权衡记录**
**日期**: 2026-06-25（设计）· 2026-06-26（与实现核对）
**作者**: 本体团队
**关联**: 能力三事实源（`backend/app/services/integration/`）、能力二抽取（`backend/app/services/extraction/`）、`docs/gap-analysis.md` §3/§6（N1/N2）、`docs/术语.md` §12

> 本文起初是**设计文档**（非实现计划）；现已据此立为 spec-kit 特性 `007-rnd-document-fact-source` 并走完 specify→clarify→plan→tasks→implement。文末「待决问题」（§10）即当时 `/speckit-clarify` 的候选输入，现已逐条裁定并标注结论。
>
> **本文回答的核心问题**：研发文档作为外部事实源接入时，**文档本身是否要建成 A-Box 个体**（供溯源/合规链）？——答案：**要，但仅文档"作为记录"这一层物化为事实；文档"内部的实体"仍走能力二抽取 + 人工复核。** 详见 §4。

> **实现状态与制品交叉引用（2026-06-26 核对）**
>
> 本设计已落地为 `007`，规范与契约见 `specs/007-rnd-document-fact-source/`：
> - 需求/验收：[`spec.md`](../specs/007-rnd-document-fact-source/spec.md)（US1–US4 + Clarifications）
> - 技术落点：[`plan.md`](../specs/007-rnd-document-fact-source/plan.md)、[`data-model.md`](../specs/007-rnd-document-fact-source/data-model.md)、[`research.md`](../specs/007-rnd-document-fact-source/research.md)
> - 契约：`contracts/` 四件 —— [`doc-repo-connector.md`](../specs/007-rnd-document-fact-source/contracts/doc-repo-connector.md)（C1 工厂 / C2 三模 / C3 凭据）、[`record-materialization-invariants.md`](../specs/007-rnd-document-fact-source/contracts/record-materialization-invariants.md)、[`content-extraction-orchestration.md`](../specs/007-rnd-document-fact-source/contracts/content-extraction-orchestration.md)、[`provenance-and-phase-query.md`](../specs/007-rnd-document-fact-source/contracts/provenance-and-phase-query.md)
> - 端到端校验：[`quickstart.md`](../specs/007-rnd-document-fact-source/quickstart.md)
>
> 实现与本文设计的三处偏离（已就地标注「实现：…」）：(1) 接入模式由设计时的 inline/http **双模**扩为 **inline/upload/http 三模**——`upload` 为真实系统就位前的过渡入口（Clarifications Q4）；(2) T-Box 模块文件名为 `ontology/slpra/slpra-document.ttl`、命名空间 `https://ontology.pharma-gmp.cn/slpra/document/`（前缀仍 `slpra-doc:`）；(3) 连接器工厂落为独立模块 `services/integration/connector_factory.py`（非就地改 `materializer.py`）。

---

## 1. 背景与问题

### 1.1 缺口

现有 5 个外部事实源（APS/ERP/MES/LIMS/CTMS）全是**运营/生产事务系统**，隐含前提：药品已进入（临床或商业）**制造**阶段。但本平台面向**新药研制**，其早期阶段——药物发现、临床前、临床Ⅰ期首批样品——**根本没有 ERP/MES 数据流**。这些阶段药物、备样批次、质量标准的权威来源**只能是研发文档**。

研发阶段、关键产出与质量体系重点关注构成一条**生命周期主干**：

| 研发阶段 | 关键产出 | 质量体系重点关注 |
|---|---|---|
| 药物发现 | 候选化合物 | GLP（毒理）、实验室数据完整性 |
| 临床前 | IND 申报资料 | GMP 起始点判断、分析方法预验证 |
| **临床Ⅰ期（技术转移）** | **首批临床样品** | **共线生产风险评估、清洁确认、技术转移报告** |
| 临床Ⅱ/Ⅲ期 | 关键临床数据 | 工艺验证、方法验证、稳定性研究 |
| NDA/BLA | 上市申请 | 商业化工艺验证、GMP 符合性检查 |
| 上市后 | 真实世界数据 | PV（药物警戒）、变更管理、再验证 |

注意**临床Ⅰ期那一行**：质量重点 = 共线生产风险评估 + 清洁确认 + 技术转移报告——这正是本平台 SLPRA 的**中心用例本身**。所以研发文档不是旁支，而是平台所做风险评估的**证据/溯源主干**。

### 1.2 一句话定位

> 研发文档事实源 = 把"研发各阶段文档的**生命周期**"接成能力三事实源；文档**作为记录**直接物化为 A-Box 事实（带阶段/版本/状态/SHA），文档**内部的实体**编排能力二抽取入库；同时引入一个横切的**研发阶段（DevelopmentPhase）**维度作溯源与风险上下文。

它**不是**第六个 APS 式"结构化事实直接物化"连接器，也**不**新造一套抽取引擎。

---

## 2. 设计目标与非目标

### 2.1 目标

- **G1**：为早期研发阶段（发现/临床前/临床Ⅰ）补上**目前无源可接**的实体来源。
- **G2**：文档**作为记录**成为一等 A-Box 个体，使每一条抽取出的事实/样品都能**指回其源文档**（阶段、版本、状态、来源系统、SHA），坐实 ALCOA+ 溯源链（宪章 III）。
- **G3**：文档**内部实体**（药物、备样、质量标准）经能力二抽取 + 人工对齐复核入库，**复核门禁零削弱**（宪章 II）。
- **G4**：引入 `DevelopmentPhase` 维度，既作溯源元数据，又作**风险/合规上下文**（逐阶段质量画像）。
- **G5**：复用能力三既有 `materializer`/事件总线/`FactMaterializationRun` 留痕与能力二既有抽取/对齐管线，**零并行框架**（宪章 V）。

### 2.2 非目标

- **不**把文档**内部实体**自动物化为权威事实（必经能力二复核）。
- **不**把文档个体写入权威 T-Box TTL——文档个体是 **A-Box**，落 `facts#` 命名空间 + DB 影子表（见 [[ttl-tbox-only-abox-in-db]]）；**仅** `RegulatoryDocument` 类 / `DevelopmentPhase` 枚举 / 溯源属性这些 **T-Box 结构**经能力一发布路径外科式合并入 TTL。
- **不**做文档全文管理/版本控制系统（那是 EDMS 的职责）；本平台只接其**元数据 + 内容引用**。
- **不**改既有 5 个连接器的语义，也**不**改 `AssessmentResult` 对外形状。
- **不**引入并行推理框架（宪章 V）。

---

## 3. 核心原则

### P1 — "记录是事实，内容是候选"（本设计的核心切分）

一份文档承载两类信息，**信任级别根本不同**，必须分流：

| 信息层 | 例 | 机器可信度 | 入库路径 |
|---|---|---|---|
| **文档作为记录**（生命周期元数据） | 技术转移报告 v2，临床Ⅰ期，已批准，来自 Veeva，SHA=… | 高（来自受控 EDMS，确定性） | **能力三 materializer → 直接物化为 A-Box 文档个体**（无须复核） |
| **文档内部实体**（业务事实） | 该报告里写的原料药杂质谱、备样批号、质量标准限度 | 低（NL 抽取，可能误读） | **能力二抽取 → 人工对齐复核 → A-Box**（复核门禁不可绕） |

这条切分让我们既能**立刻拿到溯源锚点**（记录层），又**不破坏抽取复核纪律**（内容层）。

### P2 — 文档个体是 A-Box，类与维度是 T-Box

- **A-Box（落 DB + `http://slpra.org/facts#`）**：每份文档 = 一个 `RegulatoryDocument` 个体，经现有 `FactMaterializer._materialize` 写影子表，**不**入权威 TTL。
- **T-Box（落 `ontology/slpra/*.ttl`，经能力一发布）**：`RegulatoryDocument` 类层次、`DevelopmentPhase` 枚举、`extractedFrom`/`hasDevelopmentPhase`/`documentStatus` 等属性——经 `surgical_merge` + 三元组 diff 正常发布。

与既有"E5 限制存 TTL、个体存 DB"完全同构，守住 [[ttl-tbox-only-abox-in-db]]。

### P3 — 文档生命周期事件编排，而非直接驱动结论

连接器发出"文档 X 新批准/新版本"事件 → 触发：(a) 一个能力二抽取作业（抽内部实体）；(b) 受影响子图的推理重算。沿用 `fact_event_bus` 的"先提交后发事件"顺序不变式（`materializer.py:94`）。

### P4 — 优雅降级 / 阶段而非实时

文档事实源的节奏是**版本/阶段门控**（低频），不是 2 秒轮询。复用 `ingest_mode`（现支持非 `poll`）走事件/拉取混合；EDMS 不可达时连接器置 `last_status`，不推进水位（同 `materializer._fail`），既有手工上传抽取路径不受影响。

---

## 4. 核心决策：文档是否建成 A-Box 个体？——**是**

### 4.1 结论

**把"文档作为记录"物化为 A-Box 个体（`RegulatoryDocument` 实例），落 `facts#` 命名空间与 DB 影子表；不入权威 TTL。**

### 4.2 三条理由

1. **合规链需要一等锚点（宪章 III / ALCOA+）**：制药 GxP 要求每个数据点可溯到源记录。若文档只是抽取作业的一个 `source_filename` 字符串（现状），则"这条杂质限度来自哪份文档的哪一版、谁批的、对应哪个 SHA"无法在图谱里查询。建成个体后，样品/药物个体可经 `extractedFrom` 指向文档个体，文档个体再带 `developmentPhase`/`documentVersion`/`approvalStatus`/`sourceSystem`/`contentHash`——审计链在 A-Box 内闭合、可 SPARQL。

2. **记录层是结构化、机器可信的**：文档的生命周期元数据来自受控 EDMS 的字段（类型、版本、状态、阶段、批准人、生效日），与 APS 的排产记录同性质——**确定性、可直接物化**，无须经过为"NL 抽取不确定性"而设的复核门禁。把它挡在复核后面反而是过度设计。

3. **使"阶段"可挂载**：`DevelopmentPhase` 必须挂在某个一等个体上才能驱动风险上下文。文档个体是阶段最自然的载体（IND 资料属临床前、技术转移报告属临床Ⅰ）。备样/药物个体再经文档继承阶段。

### 4.3 边界（防止越界写权威）

| 落点 | 内容 | 路径 | 守宪 |
|---|---|---|---|
| **A-Box** `facts#` + DB 影子 | `RegulatoryDocument` **个体** + 生命周期属性值 | 能力三 `materializer`，无复核 | 个体不入 TTL（[[ttl-tbox-only-abox-in-db]]） |
| **A-Box** `facts#` + DB 影子 | 文档**内部**药物/备样**个体** | 能力二抽取 **→ 人工复核** | 复核门禁不可绕（宪章 II） |
| **T-Box** `ontology/slpra/*.ttl` | `RegulatoryDocument` **类** / `DevelopmentPhase` 枚举 / 溯源属性 | 能力一 `surgical_merge` + 三元组 diff | 外科式合并 + 角色门禁（宪章 II/安全） |

> **一句话边界**：文档**个体**是事实（直接物化）；文档**类型与阶段词表**是 T-Box（经发布）；文档**内部业务实体**是候选（经复核）。三者各走各路、互不越权。

---

## 5. 总体架构

```
┌─────────────── 外部（受控文档系统）────────────────┐
│  EDMS(Veeva/Documentum) · eTMF · ELN/LIMS · PLM-R&D │
└───────────────────────┬─────────────────────────────┘
                        │  文档元数据 + 内容引用（增量/事件）
┌───────────────────────▼─────────────────────────────────────────────┐
│  能力三  DocumentRepositoryConnector（新增 system_type='doc_repo'）    │
│   ├─ ExternalSystemConnector 子类（同 APSConnector 形态）              │
│   ├─ fetch_incremental(cursor) → 文档生命周期变更                      │
│   └─ inline/upload/http 三模（inline 确定性测试 · upload 过渡 · http 生产）│
└───────────────────────┬─────────────────────────────────────────────┘
                        │
        ┌────────────────┴─────────────────────────────┐
        ▼（记录层，直接物化）                            ▼（内容层，编排抽取）
┌──────────────────────────────┐        ┌──────────────────────────────────┐
│ FactMaterializer（复用扩展）   │        │ fact_event_bus → 触发能力二抽取作业 │
│  _materialize 文档为          │        │  ExtractionJob(source_type=        │
│  RegulatoryDocument 个体       │        │   'doc_repo', source_config=文档ref)│
│  → facts# + KGStore 影子表     │        │  → 候选 → 对齐复核 → A-Box           │
│  → FactMaterializationRun 留痕 │        │  → extractedFrom 指回文档个体        │
└───────────────┬───────────────┘        └────────────────┬─────────────────┘
                └───────────── 事实变更事件 ───────────────┘
                                  │  触发受影响子图推理重算
┌─────────────────────────────────▼───────────────────────────────────┐
│  PostgreSQL 影子表 + facts# A-Box   │  权威 TTL（仅 T-Box：类/枚举/属性）│
└──────────────────────────────────────────────────────────────────────┘
```

**核心架构判断**：连接器只新增一个 `ExternalSystemConnector` 子类；记录层物化**复用** `FactMaterializer`（仅需把 `materializer.py:50` 硬编码的 `APSConnector` 改为**按 `system_type` 分发的连接器工厂**——这是落地的主要结构改动）；内容层物化**复用**能力二整条管线（仅 `source_type='doc_repo'` 一个新枚举值）。

---

## 6. 本体增补（T-Box，经能力一发布）

### 6.1 文档类（BFO 挂位）

文档是**信息内容实体**，BFO 上位 `obo:BFO_0000031`（generically dependent continuant）——与既有 `slpra-risk:RiskAssessmentReport` 同挂位（`slpra-risk.ttl:92`），有先例。

```turtle
# 实现：ontology/slpra/slpra-document.ttl（前缀 slpra-doc: = https://ontology.pharma-gmp.cn/slpra/document/）
slpra-doc:RegulatoryDocument a owl:Class ;
    rdfs:subClassOf obo:BFO_0000031 ;
    rdfs:label "Regulatory Document"@en ;
    rdfs:label "法规文档"@zh .
# 子类（实现 6 个，对应 §1.1 关键产出）：INDDossier / TechTransferReport /
#   ProcessValidationReport / StabilityReport / NDA_BLADossier / PVReport
```

### 6.2 研发阶段枚举（同 RiskLevel 枚举体例）

```turtle
# 实现：DevelopmentPhase ⊑ obo:BFO_0000015（process）；6 个体经 skos:notation 1–6 定序
slpra-doc:DevelopmentPhase a owl:Class ;
    rdfs:subClassOf obo:BFO_0000015 .   # 枚举载体（个体见下，A-Box 同 RiskLevel 个体例外）
# 个体（skos:notation 1–6）：Phase_DrugDiscovery / Phase_Preclinical / Phase_ClinicalI /
#   Phase_ClinicalII_III / Phase_NDA_BLA / Phase_PostMarket
```

> 注：`DevelopmentPhase` 取值个体属"受控词表常量"，可同 `RiskLevel` 枚举个体一样**例外地**留在 T-Box（见 [[ttl-tbox-only-abox-in-db]] 备注），与"实例事实入 DB"不冲突。各个体经 `skos:notation "1"–"6"` 定序，使阶段可比较/可排序。

### 6.3 溯源与上下文属性

| 属性 | 域 → 值域 | 语义 |
|---|---|---|
| `hasDevelopmentPhase` | （无 domain，横切）→ DevelopmentPhase | 文档/样品所属研发阶段 |
| `extractedFrom` | （无 domain，横切）→ RegulatoryDocument | 溯源：该事实抽自哪份文档 |
| `documentVersion` | RegulatoryDocument → xsd:string | 版本号 |
| `approvalStatus` | RegulatoryDocument → xsd:string | draft/approved/superseded |
| `sourceSystem` | RegulatoryDocument → xsd:string | Veeva/eTMF/… |
| `contentHash` | RegulatoryDocument → xsd:string | 内容 SHA-256（完整性） |

> **实现注（域的取舍）**：`hasDevelopmentPhase` 与 `extractedFrom` 两个横切属性**刻意不声明 `rdfs:domain`**，仅约束 `rdfs:range`——它们同时挂在文档与业务实体上，若给 `RegulatoryDocument` 域会令 OWL 把携带该属性的业务实体误推为文档（殃及 US2 抽取实体）。其余四个生命周期数据属性域为 `RegulatoryDocument`。

### 6.4 阶段作为风险/合规上下文（与 006 协同）

§1.1"质量体系重点关注"列本质是**逐阶段合规画像**。落地形态曾有二（待决 §10 Q3）：(a) 仅作溯源标注；(b) 进 006 声明式判据/决策规则的前件。

> **实现裁定（Q3 → (a)，红线）**：本期**仅作溯源标注**，**绝不进任何规则前件**。阶段经 `phase_provenance_note()`（`services/reasoning/phase_context.py`）映射为一条人类可读的「质量体系侧重」文案，由 `AssessmentResult.phase_context`（附加面，缺省 `None`，不进 golden-master 投影）承载。阶段 IRI **永不进入 `interpreter.Facts`**，故 006 任何判据/决策规则都无法引用它（FR-011 硬红线；负向门禁 `test_phase_not_in_rule_antecedent` + 终局零回归门禁 `test_assessment_external_shape_unchanged_across_full_matrix` 守护）。升 (b) 留作后续可选项，须经 006 规则层显式接线。

---

## 7. 一次完整数据流（技术转移报告为例）

```
1. EDMS 中"技术转移报告 v2"批准 → 连接器 fetch_incremental 拉到该生命周期变更
2. 记录层：FactMaterializer 物化 RegulatoryDocument 个体（个体 IRI 落 facts#，class_iri 指向托管 slpra-doc 子类）
     facts#doc-TTR-001 a slpra-doc:TechTransferReport ;
       hasDevelopmentPhase slpra-doc:Phase_ClinicalI ; documentVersion "2" ;
       approvalStatus "approved" ; sourceSystem "Veeva" ; contentHash "…"
   → 写 KGStore 影子表 + FactMaterializationRun 留痕（同 APS 路径）
3. 内容层：发事件 → 编排 ExtractionJob(source_type='doc_repo', source_config={doc_ref})
     → LLM 抽取首批样品/原料药/质量标准 → 候选 → 人工对齐复核
     → 确认入 A-Box，每个个体带 extractedFrom = facts#doc-TTR-001
4. 推理：事实变更事件触发受影响子图重算（共线风险评估随新样品/阶段更新）
5. 审计：integration.materialize（记录层）+ 抽取复核审计（内容层）全程留痕
```

---

## 8. 落地改动清单（复用为主）

| 改动 | 文件 | 性质 |
|---|---|---|
| 连接器工厂（按 `system_type` 分发，默认回退 APS） | `services/integration/connector_factory.py`（**新独立模块** `connector_for`/`doc_type_to_class_map`）；`materializer.run_sync` + `api/integration.py` 入口改经工厂取连接器 | **主要结构改动** |
| `DocumentRepositoryConnector` | `services/integration/doc_repo_connector.py`（新，inline/upload/http 三模） | 新增（同 APSConnector 形态） |
| 记录层物化文档个体 | `FactMaterializer._materialize`（小扩展：doc_repo 经 `doc_type_to_class_map` 挂托管文档类，个体仍落 `facts#`） | 小改 |
| 抽取源新枚举 | 能力二 `source_type='doc_repo'` + `source_config` 取文档引用 | 小改 |
| T-Box 增补 | `ontology/slpra/slpra-document.ttl`（新模块 8）+ E1 类元数据 | 经能力一发布 |
| `DevelopmentPhase` 维度 | T-Box 枚举（6 个体 skos:notation 1–6）+ 溯源属性 | 经能力一发布 |
| 前端事实源面板 | `integration/` 加 doc_repo 连接器卡（upload/inline/http）+ 文档溯源视图（T040/T041） | 小改（复用 shadcn） |

---

## 9. 宪章对齐（仿 plan.md 体例自检）

| 原则 | 落点 | 结论 |
|---|---|---|
| **I 规范驱动** | 本设计先行；立 007 走 specify→clarify→plan | ✅ |
| **II 本体权威性与保真（NON-NEGOTIABLE）** | 文档**个体**走 A-Box（不入 TTL）；文档**内部实体**经能力二复核门禁；**仅**类/枚举/属性经 `surgical_merge` + 三元组 diff 入 TTL；BFO 挂位 `BFO_0000031` 有先例 | ✅ |
| **III 可追溯与审计** | 文档个体 = 一等溯源锚点（`extractedFrom`/SHA/版本/阶段）；记录层 `integration.materialize` + 内容层抽取复核双审计 | ✅（本设计核心收益） |
| **IV 测试纪律与契约优先** | 连接器 inline 模式确定性契约测试（同 APS R4）；记录层物化幂等用例；内容层抽取复核 parity；`contracts/` 先行 | ✅ |
| **V 最小复杂度与复用** | 复用 `materializer`/事件总线/留痕 + 能力二整条抽取管线；新增仅一连接器 + 一 T-Box 模块；无并行框架 | ✅ |
| **安全与合规** | EDMS 凭据经 env 注入不入库（同 R7）；T-Box 发布受 `senior_analyst` 门禁；内网最小暴露 | ✅ |

---

## 10. 关键风险与待决问题

### 待决问题裁定（已于 `/speckit-clarify` 逐条解决，见 [`spec.md` Clarifications](../specs/007-rnd-document-fact-source/spec.md)）

- **Q1 — 文档个体 vs 记录层粒度** → **采纳设计建议**：文档个体为主溯源锚点；业务实体（备样/药物）经内容层抽取后以 `extractedFrom` 回链文档个体（US2 落地）。
- **Q2 — 内容抽取的触发** → **手动发起**：文档入「待抽取队列」，由分析师手动发起抽取（合规更稳、节流可控）。
- **Q3 — 阶段作为风险上下文的深度** → **仅溯源标注**：阶段只作评估结论的溯源上下文，**不进** 006 判据/决策规则前件（FR-011 红线，见 §6.4）；升级保留为后续可选。
- **Q4 — 接入的真实系统范围** → **过渡 + 末位生产接入**：真实系统就位前经既有「上传」方式（`upload` 模式）导入文档作过渡入口；真实 EDMS/eTMF 端点（`http` 模式）置于 US4，与 N1/N2 协同排序。
- **Q5 — 文档内容存储** → **仅元数据 + 外部引用**：抽取时按需拉取正文，不在平台内做全文库（避免成第二个 EDMS）。

### 风险登记

| 风险 | 等级 | 缓解 |
|---|---|---|
| 把文档内部实体误当"记录层"直接物化，绕过复核 | 高 | P1 切分写死在两条路径；内容层强制经能力二复核（宪章 II） |
| 文档个体误入权威 TTL | 中 | P2 边界：个体落 `facts#`/DB；CI 校验 TTL 不含 facts# 个体（同既有 T-Box-only 门禁） |
| 连接器工厂改造波及现有 APS 路径 | 中 | 工厂默认回退 APSConnector；APS 既有契约测试零回归基线 |
| EDMS 异构、元数据字段不齐 | 中 | `field_mapping` 吸收差异（同现有连接器）；inline 模式先打通 |
| 阶段维度与现有连接器语义重叠（如 LIMS 属发现期） | 低 | 阶段是横切元数据，可同时标注现有连接器事实，不互斥 |

---

## 11. 分阶段落地建议（立 007 后的用户故事雏形）

> 每阶段独立可用、可端到端验证，与 006 增量交付同纪律。**实现：US1–US4 全部交付**（任务清单见 [`tasks.md`](../specs/007-rnd-document-fact-source/tasks.md)）。

- **US1（MVP，记录层）✅**：`DocumentRepositoryConnector`（inline 模式）+ 记录层物化文档为 A-Box 个体 + T-Box `RegulatoryDocument`/`DevelopmentPhase` 增补。端到端验证"文档作为一等溯源锚点可入图、可查"。**无内容抽取、无复核**——纯记录层，零写权威风险。
- **US2（内容层接线）✅**：文档事件 → 编排能力二抽取（`source_type='doc_repo'`，**手动发起**，Q2）→ 复核 → `extractedFrom` 回链。打通"从文档抽出备样/药物并溯源"。
- **US3（阶段作上下文）✅**：`hasDevelopmentPhase` 作**溯源标注**（`AssessmentResult.phase_context`）+ 按阶段检索；**阶段不进 006 规则前件**（Q3 红线，见 §6.4）。
- **US4（生产接入 + 打磨）✅**：`http` 模式接真实 EDMS/eTMF（凭据 env 注入，`token_ref`/`api_key_ref`）；inline/upload/http 三模经同一归一化产出逐字节同骨架（单一物化路径）；前端文档溯源视图（T040/T041）。与 gap-analysis N1/N2 协同排序。

---

## 附：复用资产索引（实现时按图索骥）

> 设计时为「复用资产索引」；下表已补入实现新增制品（标 **新**）。

| 资产 | 位置 | 用途 |
|---|---|---|
| `FactMaterializer` + `_materialize` | `backend/app/services/integration/materializer.py` | 记录层物化文档个体（小扩展：经工厂取连接器 + `doc_type_to_class_map` 挂类） |
| `connector_for` / `doc_type_to_class_map` **新** | `backend/app/services/integration/connector_factory.py` | 按 `system_type` 分发连接器（默认回退 APS）+ doc_repo 文档类映射 |
| `DocumentRepositoryConnector` **新** | `backend/app/services/integration/doc_repo_connector.py` | doc_repo 连接器（inline/upload/http 三模 + env 凭据注入 + 增量水位） |
| `phase_provenance_note` **新** | `backend/app/services/reasoning/phase_context.py` | 阶段 → 「质量体系侧重」溯源标注（仅标注，不进规则前件） |
| `slpra-document.ttl` **新** | `ontology/slpra/slpra-document.ttl` | 模块 8 T-Box：文档类层次 + DevelopmentPhase 枚举 + 溯源属性 |
| `APSConnector`（形态范本） | `backend/app/services/integration/aps_connector.py` | 新连接器 inline/http 双模 + 增量拉取范本 |
| `ExternalSystemConnector` ABC | `backend/app/services/integration/base.py` | 连接器抽象基类 |
| `fact_event_bus` | `backend/app/services/integration/events.py` | 文档事件 → 触发抽取/重算 |
| `FactMaterializationRun` | `backend/app/models/integration.py` | 记录层物化留痕 |
| 能力二抽取管线 | `backend/app/services/extraction/pipeline.py`、`llm_extractor.py` | 内容层抽取 + 对齐复核 |
| `ExtractionJob`/`source_config` | `backend/app/models/extraction.py` | 内容层作业（新 `source_type='doc_repo'`） |
| `surgical_merge` / `export_diff` | `backend/app/services/ttl_merge.py` | T-Box 增补发布 + 三元组 diff |
| `RiskAssessmentReport`（BFO 挂位先例） | `ontology/slpra/slpra-risk.ttl:92` | `RegulatoryDocument` 挂 `BFO_0000031` 参照 |
| `RiskLevel` 枚举个体（体例先例） | `ontology/slpra/slpra-risk.ttl` | `DevelopmentPhase` 枚举体例参照 |
