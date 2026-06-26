# Implementation Plan: 研发文档事实源（按研发阶段）

**Branch**: `007-rnd-document-fact-source` | **Date**: 2026-06-25 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/007-rnd-document-fact-source/spec.md`

## Summary

把**研发各阶段文档**（IND 申报资料 / 技术转移报告 / 工艺验证 / 稳定性 / NDA-BLA / PV…）接成能力三外部事实源，面向新药研制场景补上早期阶段（药物发现 / 临床前 / 临床Ⅰ期）**目前无源可接**的药物与备样实体来源。核心切分**「记录是事实，内容是候选」**：文档**作为记录**（生命周期元数据：类型/版本/状态/研发阶段/来源系统/SHA）经能力三 `FactMaterializer` 直接物化为**事实层（A-Box）文档个体**（IRI 落 `http://slpra.org/facts#`，**类型由发布到 T-Box 的 `RegulatoryDocument` 类承载**），无须复核，作一等溯源/合规锚点（ALCOA+，宪章 III）；文档**内部业务实体**仍走能力二抽取 + 人工对齐复核，复核门禁零削弱（宪章 II）。

**技术取向（关键决策，详见 [research.md](./research.md)）**：在**不引入并行框架**（宪章 V）前提下，仅做三处复用式接缝改造——(1) 把 `materializer.py:50` 硬编码的 `APSConnector` 改为**按 `system_type` 分发的连接器工厂**（默认回退 APS，零回归）；(2) 新增 `DocumentRepositoryConnector`（`upload`/`inline`/`http` 三模），其 `fetch_incremental` 产出**归一化文档生命周期变更**，复用 `FactMaterializer.run_sync` 既有幂等/留痕/事件链；(3) 记录层 `_materialize` 小扩展——文档个体 IRI 仍在 `facts#`，但 `class_iris` 指向**已发布的 T-Box 托管类**（`…/slpra/document/RegulatoryDocument` 子类），守住「个体在 A-Box、类在 T-Box」边界（[[ttl-tbox-only-abox-in-db]]）。内容层复用能力二整条抽取/对齐/复核管线（仅 `source_type='doc_repo'` 一个新枚举值 + 一条 `doc_repo` 抽取分支）；确认入库时经候选 `source_ref` 注入 `extractedFrom` 溯源回链（`_commit_candidate`）。`DevelopmentPhase` 为横切受控词表（同 `RiskLevel` 枚举体例，例外地留 T-Box），本期仅作评估**溯源标注**（澄清 Q3）。T-Box 增补（`RegulatoryDocument` 类层次、`DevelopmentPhase` 枚举、`extractedFrom`/`hasDevelopmentPhase` 等溯源属性）经能力一 `surgical_merge` + 三元组 diff 发布。

> 完整设计与架构权衡见 [`docs/rnd-document-fact-source-design.md`](../../docs/rnd-document-fact-source-design.md)。

## Technical Context

**Language/Version**: Python 3.11（后端）/ TypeScript（前端事实源面板最小增补，Next.js）

**Primary Dependencies**: FastAPI（`APIRouter`+`Depends`）、SQLAlchemy 2.0（`Mapped`/`mapped_column`）、Alembic、Owlready2、rdflib（TTL 解析/序列化与外科式合并）、pytest。**不新增**第三方依赖、不新增连接器/抽取/推理框架（宪章 V）。

**Storage**: PostgreSQL（A-Box 影子表 `entity_shadow`、连接器 `integration_connectors`、物化留痕 `fact_materialization_run`、抽取 `extraction_*`，**复用现表**，新增仅枚举取值与可选 JSON 字段约定，零建表）+ A-Box 命名空间 `http://slpra.org/facts#`（文档个体）+ 权威 TTL `ontology/slpra/*.ttl`（**仅** T-Box：新模块 `slpra-document.ttl` 类/枚举/属性）+ Owlready2 World（发布期物化）。

**Testing**: pytest（契约/集成）；新增连接器 `upload`/`inline` 双模确定性契约测试、记录层物化幂等用例、连接器工厂 APS 零回归基线、内容层 `doc_repo` 抽取→复核→`extractedFrom` 回链集成测试、T-Box 增补 `surgical_merge` round-trip 与「facts# 个体不入 TTL」边界门禁测试。

**Target Platform**: 内网 Linux 服务器（小并发、长生命周期）。

**Project Type**: Web（backend FastAPI + frontend Next.js）。本特性以后端为主；前端仅在既有事实源面板加 `doc_repo` 连接器卡 + 文档溯源视图（小改，复用 shadcn）。

**Performance Goals**: 文档事实源节奏为**版本/阶段门控（低频）**，非 2 秒轮询；记录层一次同步在秒级；不引入 JVM/外部推理机，无额外启动成本。既有 5 类事实源与 `AssessmentResult` 对外形状 p95 不劣于现状（零回归）。

**Constraints**: 文档**个体**MUST 落 `facts#`/DB，MUST NOT 入权威 TTL；**仅**类/枚举/溯源属性经 `surgical_merge` + 逐字保留未建模三元组 + 写前三元组级 diff 发布（宪章 II / FR-006）；文档内部实体 MUST 经能力二复核门禁（宪章 II / FR-003）；记录层物化幂等、留痕、失败不推进水位（FR-008/009）；变更可追溯（宪章 III / FR-004/014）；EDMS 凭据 env 注入不入库（宪章 安全 / FR-010）；阶段本期仅溯源标注、不进规则前件（FR-011 / Q3）。

**Scale/Scope**: 研发阶段词表 6 项；文档类层次约 6 子类（对应 §1.1 关键产出）；溯源/上下文属性约 6 个；连接器接入模式 3 种（upload/inline/http）。单位数并发用户。

## Constitution Check

*GATE: Phase 0 前与 Phase 1 后各评估一次。*

| 原则 | 适用门禁 | 本计划落点 | 结论 |
|---|---|---|---|
| **I 规范驱动** | 规范为唯一真理；实现细节不渗入规范 | 已完成 specify→clarify（4 决策 Q1–Q4）→本 plan；规范只含 WHAT/WHY，技术落点（连接器形态/命名空间/字段）只在本 plan 与设计制品 | ✅ PASS |
| **II 本体权威性与保真 (NON-NEGOTIABLE)** | 外科式合并回写、逐字保留未建模三元组、维持 BFO/外部对齐、双存储写后一致、写前三元组级 diff | 文档**个体**只落 `facts#`/影子表（复用 `FactMaterializer`，不触 TTL）；文档**内部实体**强制经能力二复核；**仅**`RegulatoryDocument` 类层次/`DevelopmentPhase` 枚举/溯源属性经既有 `surgical_merge`+`export_diff` 发布；`RegulatoryDocument` 挂 BFO `BFO_0000031`（与 `RiskAssessmentReport` 同挂位，有先例） | ✅ PASS（边界由 Phase 1「facts# 不入 TTL」门禁测试坐实） |
| **III 可追溯与审计** | 版本号 + 乐观并发；批次发布（TTL 导出 + Git 提交 + SHA）；审计日志；已发布不可篡改 | 文档个体 = 一等溯源锚点（`extractedFrom`/`contentHash`/`documentVersion`/`hasDevelopmentPhase`）；记录层复用 `FactMaterializationRun`+`audit.append("integration.materialize")`，内容层复用抽取复核审计（`extraction.candidate.commit`）；文档作废/取代以**状态变更**表达不物理删除（FR-014） | ✅ PASS（本特性核心收益） |
| **IV 测试纪律与契约优先** | 对外接口先契约后实现；关键路径契约/集成测试；quickstart 可执行；发布前一致性门禁 | `contracts/` 先行（连接器/记录层/内容层/溯源查询）；连接器工厂 APS 零回归基线；记录层幂等用例；内容层 `doc_repo` parity；T-Box 增补一致性/边界门禁 | ✅ PASS |
| **V 最小复杂度与复用** | 复用既有栈/模式；新依赖最小化并论证；YAGNI；不引入并行框架 | 复用 `FactMaterializer`/`fact_event_bus`/`FactMaterializationRun` + 能力二整条抽取/对齐/复核管线 + 既有 `surgical_merge` 发布；新增仅一连接器 + 一 T-Box 模块 + 若干枚举取值；**零新依赖、零并行框架**；前端仅最小增补 | ✅ PASS |
| **安全与合规** | 写/发布角色门禁（`senior_analyst`）；凭据不入库；最小暴露 | T-Box 发布与文档内部实体复核入库复用 `require_role(senior_analyst)`；EDMS 凭据经 env 注入（同 APS R7），`connection_config` 不含明文凭据（FR-010）；内网最小暴露 | ✅ PASS |

**初评结论**：无违例，无需 Complexity Tracking。**关键风险**集中于 II 边界——「文档内部实体被误当记录层直接物化（绕过复核）」与「facts# 个体误入权威 TTL」，二者均在 Phase 0（R2/R5）显性化并由 Phase 1 门禁测试坐实。

**Phase 1 后复评**：见本文件末「Post-Design Constitution Re-Check」。

## Project Structure

### Documentation (this feature)

```text
specs/007-rnd-document-fact-source/
├── plan.md              # 本文件
├── research.md          # Phase 0：7 项决策（连接器工厂/物化类映射/extractedFrom 回链/阶段词表/触发策略/上传过渡/重算子图）
├── data-model.md        # Phase 1：现表枚举/字段约定 + 归一化文档变更 + T-Box 公理 + 状态机
├── quickstart.md        # Phase 1：端到端验证场景（映射 US1–US4 验收与 SC）
├── contracts/           # Phase 1：连接器/记录层/内容层/溯源查询契约 + 不变量
│   ├── doc-repo-connector.md
│   ├── record-materialization-invariants.md
│   ├── content-extraction-orchestration.md
│   └── provenance-and-phase-query.md
├── checklists/
│   └── requirements.md  # /speckit-specify 产出（已校验）
└── tasks.md             # /speckit-tasks 产出（本命令不产）
```

### Source Code (repository root)

```text
backend/
├── app/
│   ├── services/
│   │   └── integration/
│   │       ├── materializer.py          # 改：APSConnector 硬编码 → connector_for(system_type) 工厂（默认回退 APS）；
│   │       │                            #     _materialize 文档分支：facts# 个体 + class_iris=托管 RegulatoryDocument 子类
│   │       ├── connector_factory.py     # 新：按 system_type 分发（'aps'→APSConnector, 'doc_repo'→DocumentRepositoryConnector）
│   │       ├── doc_repo_connector.py     # 新：DocumentRepositoryConnector（upload/inline/http 三模，形态同 APSConnector）
│   │       └── events.py                 # 改：resolve_affected_subgraph 增 'document' 维（供内容层/阶段重算编排）
│   ├── services/
│   │   ├── extraction/
│   │   │   └── pipeline.py               # 改：增 source_type=='doc_repo' 抽取分支（按 source_config 文档引用取内容；source_ref=文档个体 IRI）
│   │   └── kg_store.py                   # 改：_detect_module 增 'document': '/slpra/document/'（文档个体归 document 模块）
│   ├── api/
│   │   ├── integration.py                # 改：test/sync/webhook 经连接器工厂（不再直引 APSConnector）；doc_repo 连接器 CRUD 复用
│   │   └── extraction.py                 # 改：_commit_candidate 注入 extractedFrom = candidate.source_ref（文档溯源回链）；
│   │   │                                 #     新增「文档批准事件 → 入待抽取队列」编排端点（手动发起, Q1）
│   └── alembic/versions/                 # 视需要：仅当字段约定需落库索引时新增轻量迁移（默认复用现有 JSON 列, 零建表）
└── tests/
    ├── test_integration/                 # +doc_repo upload/inline 契约 + 记录层物化幂等 + 连接器工厂 APS 零回归 + facts#-不入-TTL 门禁
    └── test_extraction/                  # +doc_repo 抽取→复核→extractedFrom 回链集成

ontology/slpra/
└── slpra-document.ttl                    # 新模块（经 surgical_merge 发布，非手改）：
                                          #   RegulatoryDocument ⊑ obo:BFO_0000031 + 6 子类；
                                          #   DevelopmentPhase 枚举 + 6 阶段个体（同 RiskLevel 体例）；
                                          #   extractedFrom / hasDevelopmentPhase / documentVersion /
                                          #   approvalStatus / sourceSystem / contentHash

frontend/src/
├── app/(dashboard)/integration/          # +doc_repo 连接器卡（上传/内联/端点配置）+ 文档溯源视图（小改, 复用 shadcn）
└── lib/api.ts                            # +doc_repo 连接器与文档溯源查询客户端
```

**Structure Decision**: 沿用既有 Web（backend/frontend）结构与能力三/能力二既有栈。后端为主体：能力三侧新增**一个连接器 + 一个工厂**并对 `materializer`/`events` 做**点状复用式改造**（不改 `run_sync` 主流程与 `FactMaterializationRun` 留痕语义，保护 APS 零回归）；能力二侧**仅加一个 `source_type` 分支 + 在既有 `_commit_candidate` 注入溯源回链**（不改对齐/复核 UI 主流程，保护复核门禁与既有抽取零回归）。T-Box 增补独立成 `slpra-document.ttl` 新模块，经能力一既有发布路径入库。前端仅最小增补，不新建路由架构。

## Complexity Tracking

> 无宪章违例，无需填写。

唯一的「主要结构改动」（连接器工厂）由「接入第二类连接器」这一既定范围必然要求，且以**默认回退 APS** 保证现路径零回归；其余均为既有机制的枚举取值/分支/字段复用，非额外复杂度来源。**零新建表、零新依赖、零并行框架**。

## Post-Design Constitution Re-Check

Phase 1 设计完成后复评（详见 [data-model.md](./data-model.md) / [contracts/](./contracts/)）：

- **II 保真**：记录层经 `_materialize` 写 `facts#` 个体（`class_iris` 指向托管 T-Box 类，个体三元组永不入 TTL），由 `contracts/record-materialization-invariants.md` 的「TTL 不含 facts# 个体」门禁与 round-trip 测试坐实；文档内部实体经 `contracts/content-extraction-orchestration.md` 强制走复核（`review_status` 仅 `confirmed` 入库）；T-Box 增补经既有 `surgical_merge`+`export_diff`，命名 IRI 外部对齐逐字保留。✅
- **III 审计**：记录层 `FactMaterializationRun` + `integration.materialize` 审计；内容层 `extraction.candidate.commit` 审计携 `extractedFrom`；文档作废以 `approvalStatus`/状态变更表达、不物理删除（data-model 状态机）。✅
- **IV 测试**：`contracts/` 四份先行；连接器工厂 APS 零回归基线 + 记录层幂等 + 内容层回链 + 边界门禁覆盖关键路径；`quickstart.md` 场景可执行。✅
- **V 复用**：未引入新依赖/框架；连接器与抽取分支为薄接缝层，记录层与内容层分别完整复用能力三/能力二既有管线。✅

**复评结论**：设计未引入新违例，门禁全部 PASS，可进入 `/speckit-tasks`。
