# Implementation Plan: 本体指引的文档结构与摘要元数据关系图谱识别

**Branch**: `021-ontology-guided-doc-graph` | **Date**: 2026-09-08 | **Spec**: [spec.md](./spec.md)

**Input**: `specs/021-ontology-guided-doc-graph/spec.md` 与 `docs/基于本体指引的文档结构和摘要元数据的关系图谱识别方案.md`。后者提供技术背景和 R0—R7 拆分，需求与验收以 `spec.md` 为准。

## Summary

以一个持久、owner 隔离的 `DocumentAnalysisRun` 直接替换当前同步、临时的 Word 文档分析路径。用户上传 Word 并显式选择合法根类后，服务端只构建一次 DocumentIR，在识别前冻结 MetadataSnapshot 与 OntologySnapshot；标题和摘要只能为检索排序，不能成为事实证据。识别核心按当前已证明主体的本体直接菜单，在共享全文搜索域中定向召回，只为实际准入候选建立任务及守恒台账，分别验证物理提及、局部指称、类型、身份角色和具体谓词桥接，只让具有当前完整证明的肯定边驱动下一跳。旧冻结运行保留原两阶段全文政策。

2026-09-13 活性修复依据[候选完成契约](../022-semantic-graph-closure/contracts/candidate-completion.md)：新运行冻结 `candidate_planning=sparse-candidates-v1`；候选策略完成与全文穷尽分开。租约续期不随历史事件更新运行 revision，慢提交与失租按 fencing 校验；旧运行不改身份/预算，有限恢复后仍无进展须失败并保留制品。此处描述验收目标，实际测试和部署分别登记。

运行、任务、对象 revision、proof、覆盖、事件水位、不可变图快照、租约和删除墓碑统一以 PostgreSQL 为权威存储。FastAPI 请求只负责验证、持久化和 202 应答；重工作由可恢复的 PostgreSQL dispatcher/worker 领取，lifespan 活跃时 API 只发送唤醒信号，`BackgroundTasks` 仅保留给不运行 lifespan 的测试/嵌入调用作兼容执行。页面保留 `/analysis?tab=document`，显式开始分析；按 2026-09-09 布局调整，分析历史位于左侧，章节树与原文预览共享显示，右侧提供恰好“节点元数据”和“关系图谱”两个结果 Tab。两个 Tab 只读同一运行及水位，不重复解析、摘要或模型调用。新分析产物不进入旧 CandidateStore，不自动审核或提交中央事实。

## Technical Context

**Language/Version**: Python >=3.11；TypeScript 5；React 19 / Next.js 16。

**Primary Dependencies**: 现有 FastAPI、Pydantic 2、SQLAlchemy 2、Alembic、psycopg2、Owlready2/rdflib、python-docx；React Query、Tiptap、d3、Tailwind/shadcn。无新增第三方依赖。

**Storage**: PostgreSQL 为生产运行、队列、lease、事件、revision、proof 和图快照真理来源；运行授权的内容寻址文件保存原件与不可变 artifact；SQLite 仅覆盖 schema、纯领域和基本 CAS 测试。

**Testing**: pytest 契约/领域/API/集成测试，隔离 PostgreSQL 并发与 fencing 测试，Node `node:test`，TypeScript、ESLint、Next production build、外置浏览器流程，以及独立真实模型/专家标注质量评测。

**Target Platform**: 内网/air-gap Linux 服务与现代浏览器；CPU 同步模型调用从事件循环卸载。

**Project Type**: 现有 FastAPI + Next.js Web 应用内的直接替换特性。

**Performance Goals**: 创建接口验证并持久化后返回 202，不等待图谱完成；文件选择、GET、SSE 重连、Tab/筛选切换新增模型调用数为 0；元数据可早于图谱读取；任务、hop、上下文和重试均有界。首期不虚构延迟或吞吐 SLO，真实运行只按冻结配置报告调用数、token、排队、首结果和完成时间。

**Constraints**: 默认不出网；权威 TTL 只允许 FR-087—FR-092 经批准的有限 CMC 扩展，禁止修改其他本体、BFO 或外部对齐；不预展开未到达对象的多跳类型；摘要无事实权限；不建立新旧算法开关、双读/双写或旧 checkpoint 适配；不导入旧候选/审核状态；所有 source/SSE/control 入口同一 owner 鉴权；`candidate_id@revision` 等引用不可静默升级。

**Scale/Scope**: 单次运行处理一个 Word 原件与一个显式根 IRI，线上默认覆盖文档根全部直接谓词并在已证明边上有界递归；终态与 paused 默认保留 7 天且可配置。具体上传、解压、task、hop、token 和并发上限是部署冻结输入，未提供时不得宣称生产容量已验收。

## Constitution Check

### Phase 0 前检查

| Gate | 结论 | 计划约束 |
|---|---|---|
| I. 规范驱动 | PASS | 021 按 `spec → plan/research/data-model/contracts/quickstart → tasks → implement` 推进；设计文档不替代规范。 |
| II. 本体权威与保真 | PASS | 运行时只读构建保留正式 domain/range、声明来源和未解析约束的 OntologySnapshot；权威 TBox 写入仅限 FR-087—FR-092 的有限 CMC 扩展，并须通过 diff、双存储、schema/local menu、匿名 union/属性链往返及发布门；不改其他本体、BFO 或外部对齐。 |
| III. 可追溯与审计 | **CONDITIONAL / G-C03 BLOCKED** | 新运行使用 revision、CAS、追加事件、不可变 proof/快照与审计。现行宪章禁止物理删除已发布内容；生产旧域 manifest 尚未证明物理删除集合只含未发布独占对象，因此破坏性清理和正式切换阻断。 |
| IV. 契约与测试优先 | PASS | 本计划前已有 API contract、data model 与 T01—T31 验收矩阵；实现任务先写失败测试，并要求 PostgreSQL、API、前端和真实质量证据分层报告。 |
| V. 最小复杂度与复用 | PASS | 复用现有 IR、表格记录、摘要、原文回放、本体引擎、模型调度和前端组件；无 Celery/Redis 或第二套解析器。独立运行聚合的必要性见 Complexity Tracking。 |
| VI. 离线优先 | PASS | 运行期不新增网络依赖；本地模型不可用时保留确定性结构并如实标记语义工作未完成，不回退旧 finder 或把正常离线标为 degraded。 |
| 安全与合规 | PASS for design | 所有端点认证且 owner 隔离；创建和 lifecycle 写操作服从部署授权策略，读权限仍须 owner/授权范围检查；prompt、原文片段、token 和凭据不写通用日志。 |

**Phase 0 gate result**: 非破坏性的设计、实现、测试、dry-run 盘点可继续。G-C03 不是例外批准；在 manifest 的已发布/确认/共享/未知集合被排除出物理删除目标，或正式完成宪章修订/治理决定前，生产物理清理和单次切换不得执行。

### Phase 1 后复查

- `research.md` 已记录现有同步入口、复用点、FastAPI 重工作边界、PostgreSQL dispatcher 选择和 G-C03；没有以框架便利替代持久恢复。
- `data-model.md` 将稳定运行 ID 与 execution token 分离，并给出 CAS、追加事件、精确 revision、完整水位快照、共享 artifact 引用检查和最小 tombstone 不变量。
- `contracts/document-analysis-runs-api.md` 对 create/status/metadata/graph/source/SSE/pause/resume/cancel/delete 统一版本、水位、owner 与角色边界；所有 GET 明确零写入/零模型副作用。
- `quickstart.md` 把确定性测试、隔离 PostgreSQL、前端/浏览器、真实质量和清理 dry-run 分开；未执行或 skipped 不得标通过。
- 未新增依赖；权威本体写入仅限 FR-087—FR-092 的有限 CMC 扩展并受 AC-T31 约束；未设计旧协议兼容层，也未授权将属性链推导或运行图自动发布为业务事实。

**Phase 1 gate result**: 任务化工程主体已在当前工作区落地并完成本地机制回归；G-C03、独立专家金标/批准 SLO/保留集、真实 PostgreSQL 和浏览器/真实模型结果仍是发布外部门。任何一个未满足时，开发结果只能保持非生产状态，不能执行旧域破坏性清理或开放正式入口。

## Architecture and Boundaries

### 2026-09-10 报告树形交互适配

用户已确认评估方案，澄清为仅迁移报告目录和共享 `TemplateGraphTree`，不扩展全站其他树。采用 ReUI Radix Tree 的 MIT 源码与 Headless Tree 1.6.3；新增 core/react 两个依赖用于统一键盘、焦点、选择和可见节点管理，复用已安装 Radix Slot、Lucide、主题变量，局部转换 Tailwind 4 样式为现有 Tailwind 3。运行时无外网请求。

目录以 node_id/source_range 适配，图谱保留现有 O(V+E) 生成森林和引用跳转，转换为具有稳定 ID 的实体/谓词/断言/引用节点。非 button 的 treeitem 容器承载独立折叠与证据按钮；同步数据更新后重建可见树并保留有效状态。用户、文档、run、projection 构成重置边界。无需虚拟化、拖放、迁移数据库或变更 API。

宪章检查（设计前/后）：范围和验收已补充 spec；本体、后端和事实提交均无修改；新增依赖直接服务已批准交互；MIT 声明随本地源码保存；离线运行满足要求。实施与检查任务见 `tree-interaction-tasks.md`，可执行验收见 `tree-interaction-quickstart.md`。

2026-09-09 历史入口补充：沿用既有运行表和鉴权，通过 run store 的 owner 过滤与有界分页提供轻量列表，application 复用公开状态映射，路由新增只读集合 GET。前端复用 `api.ts`、现有卡片/按钮及 `documentRun` URL 恢复逻辑；创建后刷新首页，历史列表与当前结果分别取消过期请求。不新增依赖或迁移，不改变模型执行、保留期限和事实提交边界；宪章前后检查均无新增例外。

1. **一份原文与冻结输入**：`analyze_word_core()`、`DocumentIR`、`parse_docx_structure()`、`table_records()` 和 `WordViewer` 继续定义物理来源。运行级 SourceArtifact、RecordIndex 和 MetadataSnapshot 引用同一 `analysis_id`；识别前冻结完整 fingerprint。
2. **在线/评测共用领域核心**：将 evaluation 中已验证的 staged retrieval、citation、instance/resolution 和 reconciliation 机制抽入 `app.services.extraction.ontology_guided`。生产代码不能 import `app.evaluation`；活动评测只做薄适配，历史冻结归档不改。
3. **直接菜单与证明门**：LocalMenu 只包含当前已支持主体的一个谓词步骤；两阶段检索覆盖每个范围内主体—谓词。`SourceMention → LocalReferent → EntityInterpretation → IdentityClaim` 分层，PredicateEvidence 独立证明端点角色、谓词桥接、适用条件和反证。
4. **唯一资格与调度**：EligibilityPolicy 是 scheduler、projection 和评测的唯一资格实现。必要依赖按 AND、独立替代 proof 按 OR；公平轮转根/子主体、属性/关系和记录，技术失败或某提议拒绝不形成文档级否定。
5. **持久运行与原子水位**：PostgreSQL 以 run 为所有权根保存 durable task、lease/fencing、event batch、object/proof head、checkpoint 和完整 GraphSnapshot。每批提交同时检查 execution token、event head、对象 revision 与引用闭包；公开 API 不暴露 checkpoint 或 token。
6. **异步 API 与双 Tab**：multipart create 落库后 202；dispatcher 从数据库领取。metadata 可先 ready，graph 只返回同一 event watermark 的完整快照。React Query cache key 包含 run ID/水位，AbortSignal 和单调水位阻止迟到串运行；筛选只投影现有快照。
7. **直接替换和治理清理**：旧 `/api/document-analysis/word`、前端 `analyzeWordDocument`、旧 Word runner/恢复器最终从生产可达路径删除。清理只按审计 manifest 和依赖闭包处理；已发布内容保留历史并追加撤销/失效，绝不靠 `--force` 物理删除。

## Project Structure

### Documentation

```text
specs/021-ontology-guided-doc-graph/
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   └── document-analysis-runs-api.md
├── tasks.md
├── baseline.json                              # frozen engineering baseline
├── evaluation-baseline.json                   # frozen offline-evidence baseline
├── validation.md                              # engineering evidence and release gates
└── retirement/
    └── old-word-domain-manifest.json          # generated dry-run artifact, proposed
```

### Source Code

```text
backend/app/
├── api/
│   └── document_analysis.py                   # replace synchronous /word with run API
├── schemas/
│   └── document_analysis.py                   # versioned public request/response schemas
├── models/
│   └── document_analysis.py                   # independent run/event/object/artifact heads
├── services/
│   ├── document_analysis/                     # new application boundary
│   │   ├── application.py
│   │   ├── artifact_store.py
│   │   ├── execution.py
│   │   ├── public_projection.py
│   │   ├── retention.py
│   │   └── run_store.py
│   └── extraction/
│       ├── ontology_guided/                   # online/evaluation shared domain core
│       │   ├── contracts.py
│       │   ├── citations.py
│       │   ├── context.py
│       │   ├── executor.py
│       │   ├── ontology_plan.py
│       │   ├── records.py
│       │   ├── metadata.py
│       │   ├── retrieval.py
│       │   ├── mentions.py
│       │   ├── verification.py
│       │   ├── model_adapter.py
│       │   ├── resolution.py
│       │   ├── dependencies.py
│       │   ├── ledger.py
│       │   ├── scheduler.py
│       │   ├── source_citations.py
│       │   └── projection.py
│       ├── word_analysis.py                   # reused DocumentIR entry
│       ├── document_ir.py
│       ├── docx_structure.py
│       ├── table_records.py
│       ├── hierarchical_context.py
│       ├── literal_normalizer.py
│       ├── local_semantic_model.py
│       └── model_protocol.py
└── evaluation/                                # thin active adapters; frozen artifacts unchanged

backend/alembic/versions/
└── 0033_document_analysis_runs.py             # additive schema implemented in workspace

backend/tests/
├── test_extraction/test_ontology_guided_{boundaries,core,evaluation}.py
├── test_extraction/test_document_analysis_*.py
├── test_extraction/test_document_run_execution_postgresql.py
├── test_api/test_document_analysis*.py
└── test_integration/test_word_domain_cleanup.py

frontend/
├── src/app/(dashboard)/analysis/page.tsx
├── src/components/analysis/
│   ├── document-analysis-panel.tsx
│   └── document-relationship-graph.tsx        # graph/evidence/coverage consolidated
├── src/components/extraction/word-viewer.tsx   # reused
├── src/lib/api.ts
└── tests/document-analysis-*.mjs

scripts/
├── audit_word_recognition_retirement.py       # read-only inventory/static boundary audit
└── cleanup_word_recognition.py                 # manifest-bound; dry-run by default
```

**Structure Decision**: 保持一个既有 backend 和一个既有 frontend，不建立新服务或仓库。`services/document_analysis` 负责运行应用生命周期，`services/extraction/ontology_guided` 负责无 HTTP/ORM 副作用的领域核心；现有 Word IR、本体与模型设施仍是共享底层。数据库模型独立于 `ExtractionJob`，但不复制解析器、模型客户端或本体引擎。

## Delivery Sequence (R0—R7)

| Package | Implementation outcome | Exit evidence |
|---|---|---|
| **R0 证据、回归与清理盘点** | 冻结代码/模型/tokenizer/本体/摘要/解析身份；建立确定性正反例；只读枚举旧 Word 域、共享依赖和保护对象。 | baseline 与 manifest 可复算；09/assistant-silver 不改且不作新运行输入；G-C03 分类明确。 |
| **R1 唯一协议与公共执行核心** | 固定 Target/Decision/Proof/Event/RunProgress/原子引用协议，抽取 ContextAssembler 与 TaskExecutor；在线不 import evaluation。 | 契约/引用/边界失败测试通过；无 legacy/new 模式字段；非 Word 共享调用方回归。 |
| **R2 本体、记录与元数据** | 增强只读 OntologySnapshot、动态直接菜单、RecordIndex/FieldGroup、识别前 MetadataSnapshot 和全谓词两阶段计划。 | AC-T01—T04；phase1/phase2 不重叠且并集守恒；摘要只能改排序。R1 稳定后可与 R3 并行。 |
| **R3 证明、身份与依赖** | 四层实体身份、字段角色/值状态、PredicateEvidence、ResolutionEvent、DependencyIndex 和资格投影。 | AC-T05—T19；明确正例可通过，来源号/缺桥接/错 owner/层级反例不进入有效图，兄弟工作继续。 |
| **R4 调度、运行存储与恢复** | 独立 DocumentAnalysisRun、PostgreSQL queue/lease/fencing、事件批次、真实 revision head、checkpoint 和完整 GraphSnapshot。 | AC-T20—T22；并发/重复/故障/失租约无陈旧写入，无旧 CandidateStore 或中央事实副作用。 |
| **R5 上传与运行 API** | 202 create、status/metadata/graph/source/SSE、CAS pause/resume/cancel/delete、owner/RBAC、保留与 tombstone；退役同步协议。 | AC-T23—T28 API 侧；所有 GET 零解析/摘要/模型写入，source/path/跨 owner 全部失败关闭。 |
| **R6 文档工具双 Tab** | 显式开始、URL run 恢复、metadata/graph 并列 Tab、证据/覆盖/状态与原文联动、迟到响应隔离。 | AC-T24—T26 前端/浏览器侧；切换零额外模型调用，graph 失败不清 metadata，只有两个结果 Tab。 |
| **R7 真实验收、清理与单次切换** | 活动评测薄适配、独立专家标注/保留集、至少三次真实新运行、清理演练、旧入口/runner 残留审计与维护窗口切换。 | AC-T29—T31、T01—T31 全追踪、批准质量门、真实 PG/浏览器证据及 G-C03 均通过；任一缺失则保持未发布且不执行破坏性清理。 |

首个端到端纵向切片覆盖：显式创建运行；持久 IR/MetadataSnapshot；一个直接关系在第一记录拒绝、第二记录支持并有实际事件；无全局编号的局部对象经证明后展开一个属性；proof/revision/图快照落库；两个 Tab 共用运行；暂停恢复不重复已提交任务。它只写新运行域，不接旧候选审核/发布路径。

## Validation and Release Strategy

- **确定性核心**：先覆盖本体菜单、引用、表格物理 mention、两阶段守恒、身份/owner/predicate 正反例、依赖失效、循环与公平调度；测试桩不能代替语义质量。
- **数据库与 API**：SQLite 仅作快速回归；唯一 claim、lease fencing、`SKIP LOCKED`/隔离、事件/对象 CAS、文件—DB 故障和清理闭包必须在显式隔离 PostgreSQL 上通过。未配置而 skip 即发布未验收。
- **前端**：Node 测试显式提交、run/watermark 隔离、两个 Tab、筛选与控制状态；TypeScript、定向/全量 ESLint、production build 与浏览器流程分别记录。浏览器合成 API 不代替真实后端 SSE/鉴权。
- **真实质量**：预测前冻结专家标注、评分器、模型/本体/摘要/政策和预算；关键正反例至少三个独立新运行，不择优；当前 CMCReport 和近重复仅作开发输入。没有批准 SLO/保留集，不能宣称上线质量完成。
- **直接替换**：R7 维护窗口前先撤销旧 writer/lease，重算 manifest/hash，验证旧结果不导入、共享业务不变。旧入口和旧 runner 的移除只在所有门通过的一次切换中开放；失败保持维护并修复唯一新实现，不恢复旧协议。
- **G-C03**：清理工具必须 dry-run 优先、精确目标、依赖闭包、幂等计数，且不得提供绕过门的 `--force`。已发布/确认内容只追加撤销或失效并保留历史；它们出现在 physical-delete 集合时立即阻断清理和切换。

## Complexity Tracking

下列复杂度不是 Constitution 豁免；它们是满足既有版本、审计、恢复和权限要求的最小边界。G-C03 仍保持阻断，不能由本表论证绕过。

| Necessary complexity | Why needed | Simpler alternative rejected because |
|---|---|---|
| 独立 `DocumentAnalysisRun` 及新 Alembic 表，而非复用 `ExtractionJob`/`EvidenceJobState` | 新入口需要 owner 幂等、独立保留、双 artifact 状态、stable run ID、lease/control 和“不自动审核/发布”的运行边界。 | 隐藏创建 ExtractionJob 会继续耦合旧 Word runner、CandidateStore、审核/提交语义和多套 ID，无法做到直接替换及跨入口 owner 隔离。 |
| 追加式 event/object/proof revision 与水位完整 GraphSnapshot | 失租约、重验、merge/split、依赖失效、SSE 重连和两个 Tab 必须看到同一可重建水位。 | 就地更新候选或前端拼增量节点会丢失旧判断、静默漂移引用，并可能把新节点与旧边组合成从未提交过的图。 |
| PostgreSQL durable dispatcher、唯一 lease 与 fencing token | 浏览器断开/进程重启后仍须恢复，迟到模型响应不得写入，且 air-gap 部署不能依赖新中间件。 | FastAPI `BackgroundTasks`/进程内队列不持久；Celery/Redis 是未批准的新依赖和部署面。`BackgroundTasks` 仅可 best-effort 唤醒数据库队列。 |
| 运行级 artifact 授权、引用计数和最小 tombstone | 原文敏感、同 hash 不授予跨 owner 访问；删除必须保护共享解析并阻止旧 generation 复写。 | 按路径/hash 粗删或完全删除 run 身份会误删共享内容，且迟到 worker 可重建已删除数据。 |


## 报告预览专家意见入口（2026-09-11）

按用户进一步要求实施可保存、重读及导出的专家意见入口；需求、权限、版本、幂等及验收见[契约](contracts/expert-opinions.md)。该意见不自动变更图谱或校准质量状态。
