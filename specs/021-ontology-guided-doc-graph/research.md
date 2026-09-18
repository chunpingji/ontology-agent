# Research: 本体指引的文档结构与摘要元数据图谱识别

日期：2026-09-08。范围是 021 特性的只读仓库研究、当前文档与冻结评测证据核对，以及实现决策；不是实现完成或真实模型质量报告。

## R1 Spec Kit 工作流与制品边界

**Decision**：继续使用仓库已初始化的 Spec Kit 布局，在 `specs/021-ontology-guided-doc-graph/` 依次维护 `spec.md → plan.md → tasks.md → implement`，Phase 0/1 制品为本文件、`data-model.md`、`contracts/` 和 `quickstart.md`。若计划发现规范缺口，先修订 `spec.md`，不在实现中自行补需求。

**Rationale**：仓库已有 `.specify/`、Constitution 1.1.0 和 001—020 连续规格，021 是现有 Web 应用内的新特性，不是新项目或 Spec Kit extension。

**Alternatives rejected**：直接从 904 行设计文档开工会跳过可测试需求和契约；把设计文档复制为任务清单会混合需求、技术方案、实施状态和发布证据。

**Documentation**：Context7 `/github/spec-kit`，核对日期 2026-09-08；当前官方资料确认规范驱动制品和 `/speckit.implement` 按依赖任务执行的工作流。本仓库 Constitution 规定的 `specify → clarify → plan → tasks → implement` 为项目内最终流程约束。

## R2 当前入口不是目标运行域

**Decision**：删除并替换 `POST /api/document-analysis/word` 的同步一次性协议；新入口使用独立 `DocumentAnalysisRun`，不创建伪 `ExtractionJob`，也不借用 `EvidenceJobState` 或 `CandidateStore` 作为新运行仓储。

**Rationale**：

- `backend/app/api/document_analysis.py` 当前只接收文件，在请求级临时目录中解析、预览和摘要，响应后删除源文件；没有本体类型、运行身份、图谱、数据库会话或恢复能力。
- `frontend/src/components/analysis/document-analysis-panel.tsx` 当前选择/拖入文件即调用同步 API，所有结果只在 React state；刷新即丢失。
- `backend/app/models/evidence.py::DocumentAnalysisRecord` 只是以内容 ID 保存 IR，并通过 `EvidenceJobState.job_id` 间接依附 `ExtractionJob`，不能表达 owner、幂等请求、生命周期、租约、事件水位、保留或双 Tab 产物。
- `CandidateStore.persist_validated()` 为作业公共候选建立另一套公共 ID/revision 映射并自动审核；目标设计要求一个运行内所有端点、证明和引用使用真实 `candidate_id@revision`，且分析产物不自动审核或提交业务事实。

**Alternatives rejected**：给同步接口增加超长超时无法支持刷新、暂停和进程恢复；用隐藏 ExtractionJob 承接会保留两条 Word 路径及旧审核/发布副作用；只把 JSON 缓存改名不能提供事务水位与引用闭包。

## R3 复用一份规范原文解析

**Decision**：复用 `analyze_word_core()`、`DocumentIR`、`parse_docx_structure()`、`annotate_word(..., structure_only=True)`、`table_records()` 和现有原文回放；新域只增加运行级源文件所有权、`RecordView`/`FieldGroup` 语义视图和持久 artifact，不建立第二个 DOCX 解析器。

**Rationale**：现有 IR 已冻结原件/转换件 hash、parser/structure policy、章节/块/递归表格、物理单元格、合并覆盖、证据 ID 和 Unicode 精确 span。`WordViewer` 已能消费带 evidence/source 坐标的预览。重复解析会使图谱证据与元数据 Tab 的坐标分叉。

**Alternatives rejected**：基于摘要或纯文本再建图失去表格与物理来源；二次 python-docx 扫描只能在现有 IR 上补视觉样式，不得重建事实坐标；已退役的 `document_profile.py`/业务 finder 不恢复。

## R4 本体快照与直接菜单

**Decision**：从 `OntologyEngine` 只读构建增强 `OntologySnapshot`，为当前已支持主体动态编译一个谓词步骤的 `LocalMenu`。快照保留 `declared_by`、原 domain/range 表达式、继承路径、约束来源 hash 和 `constraint_unresolved`，不得只消费当前按 IRI 覆盖继承定义的扁平 schema。

**Rationale**：现有 `semantic_schema_from_engine()` 很适合模型菜单，但会将继承属性按 IRI 合并为一个投影，无法证明并列 range、显式 union 或更窄继承限制未被放宽。用户给定根类型是运行输入，domain/range 只约束允许输出，不证明边存在。

**Alternatives rejected**：硬编码 CMCReport 的 12 条关系会随本体漂移；预先展开多跳可达类到全文召回使二跳类型绕过已证明边；模型生成 IRI 或改 range 违反本体权威性。

## R5 元数据在识别前冻结

**Decision**：解析后显式产生并冻结 `MetadataSnapshot`，模式为 `cached_summary`、`generate_summary` 或 `structure_only`；模式、摘要来源/模型/版本及 dependency hash 进入 `RunFingerprint`。摘要和标题只生成 `RetrievalHint`，authority 固定为 `retrieval_only`。

**Rationale**：当前作业路径先运行 `GenericExtractionRunner`，再生成或延迟生成摘要；这不能证明图谱使用了页面看到的摘要。冻结快照让两个 Tab、恢复任务和评测引用同一元数据。可用摘要先展示结构，不能改变已开始运行的排序。

**Alternatives rejected**：GET 时补摘要违反只读契约；晚到摘要就地更新运行会破坏重放和缓存身份；把摘要句保存成 EvidenceAnchor 会把非原文升级成事实来源。

## R6 记录检索、授权与两阶段守恒

**Decision**：把 `app.evaluation.record_retrieval` 和 `staged_retrieval` 中已验证的机制抽到 `app.services.extraction.ontology_guided`，并扩展到每个范围内的属性和关系。第一阶段最多三个高相关章节并按章节轮转；第二阶段保留其余所有非标题记录。排序、发现、绑定和已授权 claim 范围分别保存。

**Rationale**：现有评测原型已区分物理记录、逻辑行、表头/父容器、排名与授权，并证明路由零分仍能留在第二阶段；但 `focus_path` 下的完整两阶段尚未统一覆盖全菜单，在线代码不得 import `app.evaluation`。

**Alternatives rejected**：只查 top-k 会把低分/路由阴性误作无事实；检索到一行即授权全部字段给当前主体会造成 owner 错配；将超预算逻辑行切成孤立单格会隐去竞争主体和表头。

## R7 物理 mention、局部实体和全局身份分离

**Decision**：引入 `SourceMention → LocalReferent → EntityInterpretation → IdentityClaim` 四层对象，并将 `FieldRoleClaim`、`ValueObservation` 独立保存。物理 span 去重不触发实体合并；无全局编号不阻断有充分局部证据的类型、关系或属性。

**Rationale**：当前 `Candidate.identity` 同时承载 document root、key 和 instance IRI，`identity_supported` 是单个布尔值，无法表达项目号/试验来源号/文档号分别标识谁、在哪个 namespace/批次范围内唯一。合并 Word 单元格还会在多个逻辑行观察到同一物理 mention。

**Alternatives rejected**：同名或同项目号自动归并会洗掉剂型/批次冲突；每个逻辑行创建一个物理 mention 会伪造实体数量；要求全局唯一键后才抽局部关系会损失真实事实。

## R8 目标绑定、判定与谓词证明

**Decision**：服务端冻结 `VerificationTarget`；模型只返回目标允许的引用。类型、字段角色、局部共指、全局身份、谓词蕴含与适用性分别产生追加式 `SemanticDecision`。属性/关系必须有 `PredicateEvidence`，其 bridge kind 由版本化 `PredicatePolicyRegistry` 允许并经独立语义核验。

**Rationale**：现有 `Candidate` 已对端点、predicate、polarity、binding 和精确原文做重要硬检查，evaluation 的 citation protocol 也能关闭引用域；但一个 `supported` 或合法 JSON bridge 名称仍不能证明模型实际讨论了正确 owner/层级/谓词。目标签名、结构门和语义门必须分开。

**Alternatives rejected**：两端都有 span、同节、近邻、父图路径或 domain/range 均不是关系证明；模型拒答不是原文否定；空结果不是已经逐项检查的 unsupported；不能用置信度补唯一性。

## R9 统一资格、依赖失效与公平递归

**Decision**：以一个 `EligibilityPolicy` 计算 root seed、可用候选和可递归边；建立带 AND 必要依赖、OR 替代证明及竞争集合订阅的 `DependencyIndex`。调度以主体—谓词—记录为单位，轮转根分支、子分支、关系和属性，只有当前版本有效、无条件肯定且未被审阅拒绝的边驱动递归。

**Rationale**：当前 `positive_eligible`、evaluation `_usable` 和前端 `publishableEvidence` 各自判断，容易漂移。现有 quality runner 的晚到竞争主体失效和 2:1 公平调度可复用思想，但须成为在线/评测共用的领域核心。

**Alternatives rejected**：父边拒绝后删除整个对象历史会丢失独立替代路径；静默重写引用 revision 会让旧 proof 看似继续有效；深度优先会让长子分支饿死其他根谓词。

## R10 独立持久运行、事件水位与 worker

**Decision**：以 PostgreSQL 为运行、任务、事件批次、对象 revision、proof、图快照、租约和 tombstone 的权威存储。`recognition_run_id` 在创建后稳定；worker token 每次领取/恢复重签。每批提交检查 token、expected event head、对象 revision 和完整引用闭包，并在一个事务内追加事件和更新 head。图以不可变水位快照供前端读取。

**Rationale**：现有 `AnnotationExecution`、`WorkerLease`、`CheckpointJournal` 和本地模型 SQL 调度器提供 fencing/心跳/原子文件写的可复用思路，但全部以 `ExtractionJob.job_id` 为所有权根，且候选、缓存、checkpoint 分属 SQL/文件多套水位。新运行必须只有一个 durable head。

**FastAPI decision**：`POST /runs` 使用 `UploadFile + Form + Depends` 完成流式接收、验证、落库并立即返回 202；所有 GET 只读。SSE 使用 `StreamingResponse` 的异步生成器。FastAPI `BackgroundTasks` 只适合响应后的轻量同进程工作，官方对重型计算建议专用多进程任务工具；因此它最多作“唤醒持久 dispatcher”的 best-effort 通知，不能是任务存在或恢复的唯一证据。为避免在 air-gap 环境新增 Celery/Redis，首期复用 PostgreSQL durable queue、租约和应用 worker；模型/CPU 同步调用经线程卸载，进程重启由 queued/过期 lease 扫描恢复。

**Documentation**：Context7 `/websites/fastapi_tiangolo`，核对日期 2026-09-08；核对了 forms/files、`UploadFile`、依赖注入的 `BackgroundTasks`、其 heavy-computation caveat 及 `StreamingResponse`。

**Alternatives rejected**：仅依赖 `BackgroundTasks` 无法承诺进程重启恢复；在请求内等待图谱会占用连接且破坏 202；引入 Celery/Redis 增加未批准依赖和离线部署面；只写 JSON checkpoint 无法对对象版本和图快照做数据库 CAS。

## R11 API、鉴权和前端复用

**Decision**：统一 `/api/document-analysis/runs` 契约；所有运行、源文件、SSE 和控制入口执行相同 owner/角色检查。保留外层 `/analysis?tab=document`，使用 `documentRun=<id>` 只读恢复；内层恰好两个结果 Tab。复用 `ChapterTreePanel`、`WordViewer`、`MetadataPanel` 与证据树的展示原语，但新图只消费服务端 `GraphSnapshot`，不在前端通过连通性计算事实资格。

**Rationale**：当前全局 auth middleware 和 `Depends(get_current_user)` 可复用；当前 evidence graph 能展示实体/属性/边及来源，但其输入是 ExtractionJob Candidate，不能成为新运行事实来源。运行 ID、水位和 AbortController/迟到响应隔离需进入新客户端。

**Alternatives rejected**：两个 Tab 各自启动分析会重复模型调用；把 checkpoint 暴露给前端会耦合私有状态；按 document hash 跨 owner 复用响应会泄露原文。

## R12 保留、删除与 Constitution 阻断门

**Decision**：新运行的未发布独占分析产物可在撤销 token、依赖闭包核验、追加删除事件并保留最小 tombstone 后，按 owner 删除或保留到期物理清理；默认终态/paused 保留 7 天且可配置。旧域清理必须先生成只读 manifest，逐项区分未发布运行产物、已提交/已发布事实和共享依赖。

**Blocking gate — G-C03**：Constitution III 明确“已发布内容 MUST NOT 被物理删除或就地篡改；撤销以反向变更的新批次实现”。因此：

1. 若旧域 manifest 中待物理删除集合包含任何已发布事实、发布快照、确认发布记录或其不可变审计历史，清理和单次切换 **MUST BLOCK**；脚本不得提供 `--force` 绕过。
2. 允许的处理是保留历史并追加撤销/失效批次，使新入口不导入、不展示、不恢复这些旧事实；或者先完成正式 Constitution 修订/治理决定，再重新生成并审批 manifest。
3. 只有 manifest 证明目标均为未发布且运行域独占，或已发布集合已从物理删除目标中排除，G-C03 才通过。
4. “用户要求丢弃旧在线识别数据”不覆盖 Constitution；软件实现、非破坏测试与 dry-run 盘点可继续，生产破坏性清理和切换不能越过此门。

**Rationale**：当前 `evidence_commits`、`evidence_assertions`、`evidence_snapshots`、报告 JSON 引用、World 文件与全局 audit hash 链并非都由级联外键覆盖；粗删会留下可消费悬挂引用并破坏审计。

**Alternatives rejected**：按 `source_type=word` 全删会误删 template_default 与共享源；清空 `data/uploads` 或 evidence-worlds 会影响其他业务；备份后物理删除已发布事实仍违反不可物理删除原则；将旧候选导入新运行则违反直接替换与身份隔离。

## R13 质量验收不是工程回归

**Decision**：T01—T31 机制/API/前端/本体测试、独立正反例、保留集真实模型评测和清理一致性是不同门。历史 09 silver 与冻结 trace 保持不改，只作历史对照；新金标需业务专家冻结 mention、类型、局部共指、身份角色、owner、谓词桥接、方向、极性、条件和允许的等价证据。

**Rationale**：现有 quality/root-guided 原型证明若干调度和拒绝机制，但目标 CMCReport 可能确实缺少 Product→API 组成证据。安全拒绝不能代替另一个明确正例的召回能力，测试桩通过也不能证明生产模型质量。

**Alternatives rejected**：用图节点数/连通性作为准确率；用全部拒绝通过反例；覆盖原 silver 以匹配新评分器；挑选三次运行中最好结果；在没有保留集和批准 SLO 时宣称上线质量达标。

## Read-only repository findings

- 技术栈：Python >=3.11、FastAPI、Pydantic 2、SQLAlchemy 2/PostgreSQL、Owlready2/rdflib、python-docx；前端 Next.js 16、React 19、React Query、Tiptap、d3、Tailwind/shadcn；pytest/Node/TypeScript/ESLint/build。
- 最佳复用点：`word_analysis.py`、`document_ir.py`、`docx_structure.py`、`table_records.py`、`literal_normalizer.py`、`hierarchical_context.py`、`model_protocol.py`、`local_semantic_model.py`、`model_scheduler.py`、`WordViewer` 和现有章节/元数据组件。
- 应抽成公共核心的评测机制：`quality_guided_variant.py`、`record_retrieval.py`、`staged_retrieval.py`、`instance_registry.py`、`quality_reconciliation.py` 和 `citation_protocol.py`；在线模块不得反向 import `app.evaluation`。
- 应退役的新 Word 域入口：同步 `/document-analysis/word`、`analyzeWordDocument()`、DocumentAnalysisPanel 自动提交、`_compute_annotation()` 的 GenericExtractionRunner Word 编排及其 AnnotationExecution/CandidateStore 接入。共享给其他业务的低层执行能力按引用审计保留。
- 当前没有运行保留期限、上传大小、解压资源上限或独立 DocumentAnalysisRun 表；必须通过 Settings、Alembic 和契约测试补齐。
- 当前工作区目标设计文档已有未提交改动；021 实现不得覆盖设计/评测证据或把其状态描述成已完成。

## Unresolved release inputs

- 生产旧域 dry-run manifest 及其中已发布事实集合尚未生成，G-C03 当前为 **未通过/阻断破坏性清理**。
- 独立专家金标、保留集、批准的质量 SLO 和真实模型重复运行尚未提供；生产质量门保持未完成。
- 默认 7 天保留、上传/解压具体上限须在实现前由配置给出并在部署说明中冻结；没有配置时不得以代码常量冒充已批准运营策略。
- 单进程应用 worker 是现有部署假设；若部署扩为多实例，必须用相同 PostgreSQL claim/fencing 契约验证，不得依赖进程内事件总线。
