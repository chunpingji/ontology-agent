# Tasks: 基于本体指引的文档结构与摘要元数据关系图谱识别

**Input**: `specs/021-ontology-guided-doc-graph/` 中的 specification、plan、research、data model、contracts 与 quickstart，以及 `docs/基于本体指引的文档结构和摘要元数据的关系图谱识别方案.md`

**Organization**: 任务按 R0–R7 依赖包和五个可独立验收的用户故事组织。任务编号 `Tnnn` 是实现清单编号；括号内验收项范围为 `AC-T01`–`AC-T31`，其中前 30 项继承源方案第 15.1 节，末项是本次批准的有限 CMC 本体扩展追加项。

**Execution status**: 本文件同时记录当前工作区执行状态。`[x]` 只表示对应工程任务已有实现与可复现证据；未勾选项可能尚未开始，也可能只有部分机制覆盖。真实 PostgreSQL、浏览器、专家质量、生产清理、部署和正式切换不会因本地回归通过而自动完成，具体见 `validation.md`。

## Format: `[ID] [P?] [Story] [R#] Description`

2026-09-13 生产活性修复任务追踪见
[022 CC 清单](../022-semantic-graph-closure/tasks.md#候选台账与完成修复--cc2026-09-13)。
本规范 FR-022/023/029/072、SC-004 与数据模型已按实际候选口径同步；旧冻结运行
不静默迁移。历史已勾选两阶段全文验收只适用于原策略，不代表新候选策略验收通过。

- **[P]**：与同阶段相邻任务修改不同文件，且不依赖其未稳定输出，可并行执行。
- **[US1]–[US5]**：映射到 specification 中的用户故事；Setup/Foundation/最终门禁不强行挂故事标签。
- **[R0]–[R7]**：映射到源方案第 14.1 节的重构包。
- 测试任务先写并确认能捕获缺口，再实现对应生产代码；不得通过降低断言、篡改固定参考或全量拒绝候选使测试变绿。

## Phase 1: Setup — 固定证据、基线与清理边界（R0）

**Purpose**: 在修改运行协议前冻结可重放输入、当前调用边界和旧域清理范围；历史评测只作离线证据，不作为新运行缓存或未见测试集。

- [x] T001 [R0] 将当前提交、Python/Node/数据库版本、模型/tokenizer/本体/摘要/解析器身份、全量测试收集数及执行命令写入 `specs/021-ontology-guided-doc-graph/baseline.json`；把跳过项和警告单列，不把历史“375 项”写成当前基线。
- [ ] T002 [P] [R0] 在 `backend/tests/fixtures/ontology_guided_documents.py` 建立确定性 DOCX/IR 场景工厂：明确 Product→API 组成正例、同名无桥接反例、跨段指代、共享合并单元格、Product/API 外观层级、CAS=N/A/布尔否/字段缺失及两个试验来源号。
- [ ] T003 [P] [R0] 在 `backend/tests/fixtures/ontology_guided_ontology.py` 建立最小多跳本体 fixture，覆盖继承数据属性、并列 range、合法 range 子类、本体循环和未知 IRI；fixture 必须保留约束来源，不能手写预期算法输出冒充本体编译。
- [x] T004 [P] [R0] 固定 09 归档、现有 assistant-silver 与目标 CMCReport 原件的 hash/用途/保留边界到 `specs/021-ontology-guided-doc-graph/evaluation-baseline.json`；明确当前 CMCReport 及近重复只属开发回归，参考不得进入模型输入。
- [x] T005 [R0] 实现只读盘点 `scripts/audit_word_recognition_retirement.py`，枚举旧 Word job、运行、候选、审核、断点、源文件、提交事实、报告 JSON 引用、模型请求及共享对象，不执行删除且不按目录或 `source_type` 粗匹配。
- [ ] T006 [R0] 将 T005 的 schema、目标记录、文件绝对路径/hash、独占/共享归属、依赖、计划动作和保护对象写入 `specs/021-ontology-guided-doc-graph/retirement/old-word-domain-manifest.json`；来源不明项以及任何已发布事实/快照/确认发布记录或不可变审计历史的物理删除均标为 Constitution G-C03 blocking，不生成恢复旧数据的迁移计划。
- [x] T007 [R0] 在 `specs/021-ontology-guided-doc-graph/validation.md` 建立证据模板，分别记录确定性机制、SQLite 回归、隔离 PostgreSQL、前端 Node/浏览器、真实模型质量、性能、清理 dry-run/执行和切换状态；未执行项必须明确为 pending。

**Checkpoint**: 固定输入与保护对象可复算，清理 manifest 只读且无模糊目标；之后的测试不得回写 08/09 归档或原银标。

---

## Phase 2: Foundational — 唯一领域协议与公共执行核心（R1）

**Purpose**: 先建立在线和活动评测共用的类型化协议、原子引用与受控模型执行边界；本阶段阻塞所有用户故事。

### Tests first

- [ ] T008 [P] [R1] 在 `backend/tests/test_extraction/test_ontology_guided_contracts.py` 为 `VerificationTarget`、四态 `SemanticDecision`、`PredicateEvidence`、版本化 candidate/proof refs、`RunProgress` 和追加事件写 round-trip/未知字段/错误角色/跨运行引用失败测试。
- [x] T009 [P] [R1] 在共享 citation fixtures 与 `test_evaluation_citation_{protocol,domains_v3,tables_v2}.py` 覆盖唯一精确子串、重复子串、表头上下文、合并单元格、counterevidence、越权 scope 和模型伪造坐标；确保 FactSpan 与 BindingSpan 权限不互相扩大。
- [x] T010 [P] [R1] 在 `backend/tests/test_extraction/test_ontology_guided_boundaries.py` 断言在线 `backend/app/` 不 import `app.evaluation`、新核心不写 `CandidateStore`/中央事实库、不自动审核，且协议中不存在 legacy/new runner 选择字段。

### Implementation

- [x] T011 [P] [R1] 在 `backend/app/services/extraction/ontology_guided/contracts.py` 实现 SourceMention、LocalReferent、EntityInterpretation、IdentityClaim、VerificationTarget、SemanticDecision、PredicateEvidence、candidate/event/checkpoint/progress 等冻结契约与稳定序列化。
- [x] T012 [P] [R1] 在 `backend/app/schemas/document_analysis.py` 定义创建运行、状态、metadata、graph、source selection、SSE event 和 lifecycle 请求/响应 schema，统一 `contract_version` 与 `recognition_run_id + candidate_id@revision`。
- [x] T013 [R1] 将原子引用校验从 `backend/app/evaluation/citation_protocol.py` 抽取到 `backend/app/services/extraction/ontology_guided/citations.py`，保留 evaluation 薄兼容 import，并使现有 citation/table 回归字节语义不变。
- [ ] T014 [P] [R1] 在 `backend/app/services/extraction/ontology_guided/context.py` 实现 ContextAssembler：冻结当前主体/谓词/对象版本、记录权限、端点证据、竞争者、条件与反证，并对 token 裁剪留下显式记录。
- [ ] T015 [R1] 在 `backend/app/services/extraction/ontology_guided/executor.py` 抽取 TaskExecutor，复用 `backend/app/services/extraction/local_semantic_model.py` 与 `backend/app/services/llm/model_scheduler.py` 的 tokenizer、预算、重试、取消能力；模型只能返回协议对象，不能持久化或改变本体。
- [ ] T016 [R1] 修改 `backend/app/services/extraction/extraction_tasks.py`，让共享任务原语调用 T011–T015 的公共契约，同时保留 Excel/声明式等非 Word 调用方的现有行为。
- [x] T017 [R1] 将 `backend/app/evaluation/quality_guided_variant.py` 改为公共核心的活动评测薄适配器；冻结归档 runtime 不改、评分器不进入识别输入、在线代码不反向 import evaluation。
- [x] T018 [R1] 扩展 `scripts/audit_word_recognition_retirement.py` 的静态检查，报告在线旧 runner/旧协议/旧候选写入调用边，并在检测到新增双轨分支时非零退出。

**Checkpoint**: T008–T010 通过；公共协议可被纯函数/模拟模型测试，其他业务共享回归通过，且尚未创建或清理任何线上数据。

---

## Phase 3: User Story 1 — 显式创建并查看持久化分层元数据（P1，MVP；R2/R4/R5/R6）

**Goal**: 用户选择 Word 和合法根类后才创建独立运行；刷新后仍能查看同一 IR、章节树和 MetadataSnapshot，元数据不等待图谱完成。

**Independent Test**: 关闭识别模型，上传合成 Word 并选择 root IRI；POST 返回 202，随后 GET 可从持久化运行恢复分层元数据与原文定位，刷新/切换不产生第二运行或模型调用。

### Tests first

- [ ] T019 [P] [US1] [R2] 在 `backend/tests/test_extraction/test_ontology_guided_records_metadata.py` 测试物理 cell/mention 与逻辑行分离、章节/页/标题路径保持、摘要先准备并冻结、structure-only/回退/失败状态，以及摘要只作排序提示不作事实来源（覆盖 AC-T03 的元数据侧）。
- [x] T020 [P] [US1] [R4] 在 `backend/tests/test_extraction/test_document_analysis_run_store.py` 测试 owner/request_key 唯一性、analysis/metadata/run 三种 ID 分离、真实 revision 不归一化、artifact hash/head CAS 和同 hash 不跨 owner 复用权限。
- [x] T021 [P] [US1] [R5] 在 `backend/tests/test_api/test_document_analysis.py` 覆盖缺类型、未知/非法 IRI、空/越界/伪装 Word、合法创建 202、同键同输入幂等、同键异输入冲突、更换类型新建运行及 GET 零模型副作用（AC-T23 的 API 侧）。
- [ ] T022 [P] [US1] [R6] 在 `frontend/tests/document-analysis-run.test.mjs` 测试拖入文件不提交、文件/类型缺一禁用开始、提交携带幂等键与完整 IRI、URL 恢复 `documentRun`、迟到创建/metadata 响应不覆盖当前运行。

### Implementation

- [x] T023 [P] [US1] [R4] 在 `backend/app/models/document_analysis.py` 创建 DocumentAnalysisRun、DocumentRecognitionEvent、DocumentRunCandidate、DocumentVerificationProof 与 artifact head 模型，约束 run 内序列、event key、candidate/proof revision 和 owner/request_key。
- [ ] T024 [US1] [R4] 新增 `backend/alembic/versions/0033_document_analysis_runs.py`，只创建新运行域表、唯一约束、索引和 FK；不迁移旧 job/candidate/review/checkpoint，upgrade/downgrade 须可在空库和现有 schema 上验证。
- [x] T025 [P] [US1] [R2] 在 `backend/app/services/extraction/ontology_guided/records.py` 以现有 DocumentIR/TableRecords 构建不可变 RecordIndex、FieldGroup、TableFrame 与物理 mention 观察映射。
- [x] T026 [P] [US1] [R2] 在 `backend/app/services/extraction/ontology_guided/metadata.py` 构建版本化 MetadataSnapshot，冻结章节/页摘要来源与提示，失败时保留结构并显式标注 fallback/failed。
- [x] T027 [US1] [R4] 在 `backend/app/services/document_analysis/run_store.py` 实现运行、事件、artifact manifest 与 head 的事务仓储，所有写入校验 owner/run/token/预期 head，读路径不生成任务。
- [x] T028 [US1] [R5] 在 `backend/app/services/document_analysis/artifact_store.py` 实现安全文件名、大小/解压资源限制、运行级 source 的临时写入—校验 hash—原子换名流程，并由 application/run store 登记引用和清理失败孤儿。
- [x] T029 [US1] [R5] 在 `backend/app/services/document_analysis/application.py` 与 `execution.py` 实现创建验证、幂等、本体快照、解析和 metadata 阶段编排；只有显式开始才登记持久后台工作，完整 recognition fingerprint 前不得识别图谱。
- [x] T030 [US1] [R5] 重构 `backend/app/api/document_analysis.py`，增加 `POST /api/document-analysis/runs` 与 run/status/metadata/source 只读 GET；暂不暴露旧同步响应作为兼容分支。
- [x] T031 [US1] [R6] 在 `frontend/src/lib/api.ts` 增加版本化 run create/status/metadata/source 客户端、AbortSignal 和错误契约；不在 GET 或 render 逻辑中隐式 POST。
- [x] T032 [US1] [R6] 重构 `frontend/src/components/analysis/document-analysis-panel.tsx` 为文件+本体类型+开始按钮和持久运行外壳，复用章节树/Word 预览/metadata 组件并显示真实摘要来源、artifact 状态及 expires_at。
- [x] T033 [US1] [R6] 更新 `frontend/src/app/(dashboard)/analysis/page.tsx` 的文案和 `documentRun` 恢复，保留 `/analysis?tab=document` 外层路由且不自动取消旧运行。

**Checkpoint**: US1 可单独演示持久化分层元数据；没有 root IRI 不启动，刷新只读恢复，同一运行的 source/IR/metadata 身份一致，图谱尚未完成也不影响元数据浏览。

---

## Phase 4: User Story 2 — 本体直接菜单与可证明关系图谱（P1；R2/R3）

**Goal**: 从用户给定根开始，按当前已证明主体的直接本体菜单逐层召回；只有原文支持类型、角色、端点与谓词桥接的断言进入有效肯定图。

**Independent Test**: 使用固定 IR、最小本体和模型响应 fixture 运行纯领域核心；无需 API/UI 或真实模型即可验证 Product→API 正例能形成两跳，缺桥接/错误 owner/来源号身份反例不能进入有效图，且其他记录继续执行。

### Tests first — ontology and retrieval

- [ ] T034 [P] [US2] [R2] 在 `backend/tests/test_extraction/test_ontology_guided_menu.py` 测试根首次只编译当前直接谓词/range，关联对象后才开放二跳；覆盖继承属性、并列 range 非 union、range 子类分批、约束来源、本体循环与未知 IRI 拒绝（AC-T01、AC-T02）。
- [ ] T035 [P] [US2] [R2] 在 `backend/tests/test_extraction/test_ontology_guided_retrieval.py` 测试摘要“含 API”但原文无组成时只能改变顺序；路由零分/阴性或 phase1 全拒后，phase2 的不同记录必须有真实调用事件，且每个主体—谓词计划数量守恒（AC-T03、AC-T04）。

### Tests first — mention, role and identity

- [ ] T036 [P] [US2] [R3] 在 `backend/tests/test_extraction/test_ontology_guided_mentions.py` 测试可靠类型但无唯一编号仍保留局部实体；API 表头+名称可支持类型，试验“来源”号不可成为 API 全局身份（AC-T05、AC-T06）。
- [ ] T037 [P] [US2] [R3] 在 `backend/tests/test_extraction/test_ontology_guided_table_roles.py` 测试两逻辑行共享一个合并 API cell 只产生一个物理 mention、多条观察不决定实体数，并复现 call 50/51 的表头/名称/row1 来源格定位而不把定位当身份或组成证明（AC-T07、AC-T08）。

### Tests first — predicate proof and decision states

- [ ] T038 [P] [US2] [R3] 在 `backend/tests/test_extraction/test_ontology_guided_predicate_proof.py` 测试只有 Product 代码、API 名称和父 describes 时，即使模型返回 supported 或谎报 `bridge_kind` 也不得形成第二跳；明确组成句、配方角色表、跨段明确指代三种形式必须能通过（AC-T09、AC-T10）。
- [ ] T039 [P] [US2] [R3] 在 `backend/tests/test_extraction/test_ontology_guided_ownership.py` 测试 response 错 target ID 硬拒绝、ID 对但讨论 root/其他对象仍被角色/谓词核验拒绝，以及 Product 口服片剂/API 粉末属性不跨层级串挂（AC-T11、AC-T12）。
- [ ] T040 [P] [US2] [R3] 在 `backend/tests/test_extraction/test_ontology_guided_decisions.py` 测试 CAS=N/A、布尔否、字段未提及分别成为 placeholder/有效 false/not_mentioned；binding unsupported 仅使当前后续 not_checked，兄弟提议和下一记录继续（AC-T13、AC-T14）。
- [ ] T041 [P] [US2] [R3] 在 `backend/tests/test_extraction/test_ontology_guided_failures.py` 测试空召回、拒答、引用错误、token 超限、超时、revision/计划变化与重试额度，技术失败不得伪造 unsupported 或无限阻塞；false/缺失/错误 true 身份在 router/shared/cache 各层均不能泄漏为可信 key（AC-T15、AC-T16）。
- [ ] T042 [P] [US2] [R3] 在 `backend/tests/test_extraction/test_ontology_guided_resolution.py` 测试名称+代码局部共指但无全局身份、跨制剂/批次同项目不合并，以及 merge/split/晚到 owner/第三模糊节点的版本和冲突闭包（AC-T17、AC-T18）。
- [ ] T043 [P] [US2] [R3] 在 `backend/tests/test_extraction/test_ontology_guided_frontier.py` 测试用户根可无入边启动、普通实体不可冒根、审阅拒绝阻断依赖路径、替代根路径可重算、独立根分支继续及本体循环终止（AC-T19）。

### Implementation

- [x] T044 [P] [US2] [R2] 在 `backend/app/services/extraction/ontology_guided/ontology_plan.py` 实现 LocalMenuCompiler，只读取 OntologySnapshot 中当前主体的直接 object/data predicates、range/subclasses、继承约束与定义来源，拒绝模型产生的 IRI。
- [x] T045 [P] [US2] [R2] 在 `backend/app/services/extraction/ontology_guided/retrieval.py` 实现统一 phase1 排序+phase2 补充召回、per-subject/predicate RecallLedger、记录订阅和完整计划守恒；摘要/标题只参与排名。
- [ ] T046 [P] [US2] [R3] 在 `backend/app/services/extraction/ontology_guided/mentions.py` 实现 SourceMention 与 LocalReferent registry，物理位置去重、观察引用追加、局部共指提议和竞争解释保持分离。
- [ ] T047 [P] [US2] [R3] 在 `backend/app/services/extraction/ontology_guided/verification.py` 实现 type、field-role、local-coreference、global-identity、predicate-entailment、applicability 的分阶段 Decision/Proof；理由、support/counterevidence/searched context 缺失时失败关闭。
- [ ] T048 [P] [US2] [R3] 在 `backend/app/services/extraction/ontology_guided/predicate_policy.py` 注册 describes、数据属性与组成关系的合法 bridge 结构/方向/角色要求，区分 schema 结构门和语义证明门。
- [ ] T049 [P] [US2] [R3] 在 `backend/app/services/extraction/ontology_guided/resolution.py` 实现局部归并/拆分、全局 identity claim、role/namespace/uniqueness gate 和版本化 ResolutionEvent；禁止按同名、项目号或单个模型 true 强合并。
- [ ] T050 [P] [US2] [R3] 在 `backend/app/services/extraction/ontology_guided/dependencies.py` 建立 type/role/identity/predicate/scope/review 的证明依赖图、失效闭包和替代路径重算。
- [x] T051 [US2] [R3] 在 `backend/app/services/extraction/ontology_guided/projection.py` 实现有效肯定图与全部候选投影；仅当前 revision、proof 完整、affirmed、适用且未被审核拒绝的边可连接根，negated/conditional/undetermined 保留但不驱动肯定递归。
- [x] T052 [US2] [R3] 在 `backend/app/services/extraction/ontology_guided/scheduler.py` 实现实例前沿、公平主体—谓词轮转、实际 phase2 执行、失败重试与明确 StopState；父关系只授权递归，不能证明子断言。

**Checkpoint**: AC-T01–AC-T19 确定性用例通过；至少一个真组成正例进入有效图，关键无桥接/来源号/层级反例被挡住且不会阻断其他记录，不能靠“全部拒绝”达成。

---

## Phase 5: User Story 3 — 同运行增量图、证据与覆盖（P1；R4/R5/R6）

**Goal**: 元数据与图谱共享一个运行；用户可看到原子批次后的完整图快照、全候选、独立证据、真实覆盖和未完成原因，Tab/刷新/SSE 不触发重新识别。

**Independent Test**: 用模拟模型驱动一个持久运行写入 Product@2、关系、属性与后续失效事件；API/页面按同一 event head 展示增量快照，断线 GET 可恢复，点击证据能定位同一运行的物理 cell/逻辑行。

### Tests first

- [ ] T053 [P] [US3] [R4] 在 `backend/tests/test_extraction/test_document_run_ledger.py` 测试 Product@2 首次落库、关系/属性的 subject/object/path_root/scope/dependency/binding 精确 revision、并发 head、重复 batch、merge/split 失效、文件/DB 故障及禁止旧 CandidateStore/中央事实写入（AC-T21、AC-T22）。
- [ ] T054 [P] [US3] [R5] 在 `backend/tests/test_api/test_document_analysis_artifacts.py` 测试 metadata ready 而 graph running/failed/paused、完整快照水位、默认图排除拒绝边、全部候选可追溯、空图不冒充完成、source evidence 只能定位本 run（AC-T25、AC-T26 的 API 侧）。
- [ ] T055 [P] [US3] [R5] 在 `backend/tests/test_api/test_document_analysis_events.py` 测试 SSE event ID 单调、Last-Event-ID 重连、游标丢失后 GET 重建、快速切换 run 的事件隔离，且 metadata/graph GET、刷新与订阅不增加模型或解析调用（AC-T24）。
- [ ] T056 [P] [US3] [R6] 扩展 `frontend/tests/document-analysis-run.test.mjs`，覆盖两 Tab 共用 recognition_run_id/metadata snapshot、AbortController/迟到响应隔离、SSE 重连后按水位重建、Tab 切换零 POST/零模型触发（AC-T24）。
- [ ] T057 [P] [US3] [R6] 在 `frontend/tests/document-analysis-graph.test.mjs` 测试有效图/全部候选/极性条件筛选、覆盖状态、拒绝原因、精确 candidate@revision、图中删除/失效和跨 Tab SourceSelection（AC-T25、AC-T26）。

### Implementation

- [x] T058 [P] [US3] [R4] 在 `backend/app/services/extraction/ontology_guided/ledger.py` 实现 append-only event batch、expected head/token CAS、幂等 batch hash、candidate/proof head 和 checkpoint 投影，禁止静默改写旧判定。
- [x] T059 [US3] [R4] 在 `backend/app/services/document_analysis/execution.py` 组合 scheduler/executor/ledger，以安全原子批次提交运行进度、RecallLedger、候选、proof、依赖和 graph snapshot；模型回调前后均校验 token。
- [ ] T060 [US3] [R4] 扩展 `backend/app/services/document_analysis/run_store.py`，从权威事件重建 checkpoint/GraphSnapshot，在 artifact 发布前校验 hash 与 durable head，并清理无引用临时产物。
- [x] T061 [US3] [R5] 扩展 `backend/app/api/document_analysis.py`，实现 graph GET、带 projection/filter 的全部候选、source evidence 定位和 `/events` SSE；所有 GET 只读且返回 artifact revision/event head。
- [x] T062 [US3] [R6] 在合并组件 `frontend/src/components/analysis/document-relationship-graph.tsx` 实现根、实体、属性、关系、方向、proof/状态/版本、有效图与全部候选筛选及空图/partial 解释。
- [ ] T063 [P] [US3] [R6] 在 `frontend/src/components/analysis/document-graph-evidence.tsx` 实现主体、对象/值、谓词桥接、条件、反证、竞争者和拒绝原因详情，点击证据产生包含 run/analysis/cell/record/span 的 SourceSelection。
- [x] T064 [P] [US3] [R6] 在合并组件 `frontend/src/components/analysis/document-relationship-graph.tsx` 实现 per-subject/predicate 的 phase1/phase2、planned/attempted/incomplete/unattempted、前沿与 StopState 展示。
- [x] T065 [US3] [R6] 将 T062–T064 接入 `frontend/src/components/analysis/document-analysis-panel.tsx` 中恰好两个并列结果 Tab，并使分层元数据与关系图谱共享 run、metadata 和 SourceSelection state。

**Checkpoint**: AC-T21、AC-T22、AC-T24–AC-T26 通过；任意页面水位都引用闭合，图谱 partial 与执行/语义完成度分开，刷新和 Tab 切换不重复模型调用。

---

## Phase 6: User Story 4 — 暂停/恢复/取消/删除、鉴权与保留（P1；R4/R5/R6）

**Goal**: 长任务由唯一持久租约执行，用户可安全暂停恢复或终止；所有 run/SSE/source/control 入口实施一致 owner 鉴权，过期或删除后晚到 worker 不能复写。

**Independent Test**: 在隔离 PostgreSQL 中让两个 worker 竞争、丢租约并恢复，同步从另一用户请求全部端点；验证只有当前 token 能提交、暂停不重做、取消/删除不可恢复、到期清理不误删共享 artifact。

### Tests first

- [x] T066 [P] [US4] [R4] 在 `test_document_analysis_{run_store,execution_recovery,dispatcher}.py` 用 SQLite/可控时钟覆盖 pause/resume、进程重启、fingerprint 不匹配、控制版本、checkpoint 水位、持续调度、租约续期、失败任务与 stop reason（AC-T20 的确定性侧）。
- [ ] T067 [P] [US4] [R4] 在 `backend/tests/test_extraction/test_document_run_execution_postgresql.py` 以 `DOCUMENT_ANALYSIS_TEST_DATABASE_URL` 运行独立连接并发测试：唯一 claim、lease/fencing、过期 worker 晚到响应、重复 delivery、事件/候选事务 CAS 和文件—DB 故障恢复（AC-T20、AC-T22 的真实数据库侧）。
- [ ] T068 [P] [US4] [R5] 在 `backend/tests/test_api/test_document_analysis_security.py` 覆盖其他用户访问 run/metadata/graph/events/source/pause/resume/cancel/delete、伪造文件路径、越界上传、非法 IRI 及同 hash 缓存越权，全部失败关闭（AC-T27）。
- [ ] T069 [P] [US4] [R5] 在 `backend/tests/test_api/test_document_analysis_retention.py` 覆盖暂停可恢复、取消不可恢复、删除/到期先撤 token、晚到 worker 不复写、共享 artifact 不误删、墓碑阻止迟到写和 expires_at/allowed_actions 正确（AC-T28）。
- [ ] T070 [P] [US4] [R6] 扩展 `frontend/tests/document-analysis-run.test.mjs`，测试 running/pausing/paused/cancelled/deleting/expired 的服务端驱动按钮状态、错误恢复和 expires_at 文案，前端不得仅凭点击乐观宣称完成。

### Implementation

- [x] T071 [US4] [R4] 扩展 `backend/app/services/document_analysis/execution.py` 实现持久队列 claim、唯一 lease/token、heartbeat/fencing、进程重启领取规则、fingerprint 校验及安全记录边界暂停；不得创建假 ExtractionJob 或并存第二套 lease。
- [x] T072 [P] [US4] [R5] 在 `backend/app/services/document_analysis/retention.py` 实现终态/paused 默认 7 天配置、到期领取阻断、先撤 token 后删除、共享归属检查、孤儿回收和最小墓碑。
- [x] T073 [US4] [R5] 扩展 `backend/app/services/document_analysis/application.py` 实现 pause/resume/cancel/delete 状态机、expected control version、allowed_actions 和审计；cancelled/deleted/expired 不可恢复。
- [x] T074 [US4] [R5] 扩展 `backend/app/api/document_analysis.py` 的 pause/resume/cancel/delete，并对所有 run、artifact、SSE、source 路由统一复用 `backend/app/dependencies.py` 的实际身份与 owner 检查。
- [x] T075 [US4] [R6] 在 `frontend/src/components/analysis/document-analysis-panel.tsx` 接入暂停/恢复/取消/删除、正在暂停/删除、到期时间与不可恢复说明；关闭页面或切 Tab 不自动取消后台运行。

**Checkpoint**: AC-T20、AC-T27、AC-T28 在 SQLite/API/隔离 PostgreSQL 层均有证据；未提供隔离 PG 时必须报告 skipped 并阻断发布，不能用默认 SQLite 结果代替。

---

## Phase 7: User Story 5 — 旧域清理盘点与唯一切换（P2；R7）

**Goal**: 在所有质量门通过后，于维护窗口停止旧写入，按显式依赖 manifest 一次性丢弃旧 Word 在线域数据，并只开放新 run 协议；共享任务、模板、报告、本体、审计链和离线评测归档保持完好。

**Independent Test**: 在复制的验收数据库/文件根中混合 Word、Excel、class_mapping、template_default、共享分析与报告引用；先 dry-run 再执行清理，核对精确计数、失效闭包、保护对象 hash 和旧入口不可达。

### Tests first

- [x] T076 [P] [US5] [R7] 在 `backend/tests/test_integration/test_word_domain_cleanup.py` 建立混合来源及共享/JSON/FK 引用 fixture，验证 manifest dry-run、缺归属阻断、幂等执行、依赖顺序、审计链追加、旧 worker fencing、共享对象保护与残留扫描（AC-T29 的合成副本侧）。
- [x] T077 [P] [US5] [R7] 在 `backend/tests/test_api/test_retired_word_analysis.py` 断言旧 `POST /api/document-analysis/word` 和旧 Word job 识别/恢复入口不可达，新 API 不接受 legacy runner/checkpoint/candidate ID。
- [x] T078 [P] [US5] [R7] 在 `frontend/tests/document-analysis-retirement.test.mjs` 断言 `analyzeWordDocument`、旧同步响应适配和旧识别入口调用均已删除，只存在版本化 `/api/document-analysis/runs` 客户端。

### Implementation

- [x] T079 [US5] [R7] 将 `scripts/audit_word_recognition_retirement.py` 的 manifest 校验复用于新 `scripts/cleanup_word_recognition.py`；默认只 dry-run，执行模式要求冻结 manifest/hash、明确维护窗口、全依赖闭包及 G-C03 通过，逐项输出删除/保留/失效/失败计数；不得提供 `--force` 绕过已发布内容的不可物理删除规则。
- [x] T080 [US5] [R7] 在 `backend/app/api/document_analysis.py` 删除旧同步 `/word` 协议，在 `backend/app/api/extraction.py` 停止旧 Word 图谱接单/恢复，并保留 Excel、声明式和 template_default 所需共享能力。
- [x] T081 [US5] [R7] 从 `frontend/src/lib/api.ts` 删除 `analyzeWordDocument` 与旧响应类型，从 `frontend/src/components/analysis/document-analysis-panel.tsx` 删除旧即选即传分支及旧文案。
- [x] T082 [US5] [R7] 更新 `backend/app/evaluation/quality_guided_variant.py`、`backend/app/evaluation/cmc_benchmark.py` 和 `backend/app/evaluation/README.md`，让活动评测只调用新核心并冻结 run manifest；不修改历史 runtime、trace、reference 或评分结果。
- [ ] T083 [US5] [R7] 更新 `scripts/redeploy.sh` 加入维护状态、旧 worker/token 撤销、清理 dry-run hash 复核、新 migration、新入口健康检查和残留审计；任何预切换门失败必须在破坏性清理前停止。
- [ ] T084 [US5] [R7] 将实际维护窗口命令、操作者、冻结构建/schema/model/ontology/policy 身份、G-C03 审批结论、清理前后计数、已发布内容的保留/反向失效批次、保护对象核验和唯一入口检查追加到 `specs/021-ontology-guided-doc-graph/validation.md`；未真实执行时保持 pending，不生成虚构计数。

**Checkpoint**: AC-T29 在验收副本通过，且只有当 Phase 8 的全部发布门通过后才允许执行真实清理与切换；切换不提供旧数据恢复或旧 runner 回退。

---

## Phase 8: End-to-End Quality & Release Gates — 真实验收（R7）

**Purpose**: 证明同一新核心既满足机制/页面/事务契约，也在独立来源的正反例上产生可回放的正确路径；确定性测试不能代替真实模型和专家质量门。

- [ ] T085 [P] [R7] 在 `backend/app/evaluation/fixtures/ontology_guided_reference_v1.json` 建立版本化标注包，包含 mention、类型、局部共指、identity role/scope、属性 owner/层级、关系方向/桥接、极性/条件与等价证据；由业务专家在预测前复核并记录 hash。
- [ ] T086 [P] [R7] 在 `backend/app/evaluation/ontology_guided_scorer.py` 实现与识别输入隔离的评分器，分别报告完整 tuple P/R/F1、禁止断言、错误 merge/split、identity precision、根可达正确路径、predicate proof、引用回放、coverage 守恒和未评分项。
- [ ] T087 [R7] 在 `backend/tests/test_extraction/test_ontology_guided_cmc_e2e.py` 用真实解析/本体/仓储/图投影和受控模型响应覆盖新上传 CMCReport 的 Product、describes、产品属性、独立真阳性 Product→API 两跳、缺组成反例、续检和其他根菜单（AC-T30 的工程侧）。
- [ ] T088 [R7] 在 `frontend/tests/document-analysis-browser.mjs` 以 production build 和拦截 API 运行浏览器流程：显式提交、metadata 先就绪、关系图增量、双 Tab、刷新/SSE、证据定位、覆盖、暂停恢复及迟到响应隔离；把浏览器版本与合成边界写入 validation。
- [ ] T089 [R7] 使用冻结代码/本体/模型/tokenizer/摘要/政策和预注册预算运行至少三个独立真实新 run，保存 manifest/calls/trace/ledger/coverage/graph/metrics 到外部受控评测目录；正例路径见证与关键反例零接受同时通过才满足 AC-T30 质量侧，不挑最好一次。
- [ ] T090 [R7] 对 structure-only、structure+summary、可选 GLiNER 做同核心消融，报告模型/非模型/排队/摘要成本、tokens、调用数、首个有效结果和完整性；不设未经数据支持的提速或 95%/99% SLO。
- [x] T094 [R7] 补充并运行 `backend/tests/test_cmc_intermediate_ontology.py` 及 `backend/tests/test_reasoning/test_ttl_roundtrip.py` 的 AC-T31 断言并记录到 `specs/021-ontology-guided-doc-graph/validation.md`：验证权威 CMC TTL 的 `producesFinalProduct` union domain/range、字符串 `intermediateIdentifier`、非 functional 的 `hasProcessIntermediate` 及两条属性链，确认 Owlready2/schema/local menu 正确展开、匿名 union/属性链及其 inverse/RDF list 往返保真，并证明未启用运行时推导时不会把属性链冒充已物化事实。
- [x] T091 [R7] 在 T094 完成后，在 `specs/021-ontology-guided-doc-graph/validation.md` 汇总 AC-T01–AC-T31 的命令、环境、结果与证据路径；没有专家复核/保留集/SLO、真实 PG、浏览器或关键正反例时明确发布未完成。
- [ ] T092 [R7] 执行完整工程门：`backend/.venv/bin/pytest -p no:cacheprovider -q`、带 `DOCUMENT_ANALYSIS_TEST_DATABASE_URL` 的 PostgreSQL suite、`backend/.venv/bin/ruff check app tests` 及本特性改动路径定向 Ruff、`node --test frontend/tests/*.test.mjs`、`npx tsc --noEmit`、全量及本特性定向 ESLint、`npm run build`、浏览器验证、静态退役审计及 `git diff --check`；全量既有债务与 T001 冻结基线比较，本特性路径零新增错误，所有失败/skip 原样记录到 validation。
- [ ] T093 [R7] 在 T091、T092 与 T094 完成后，由业务与技术共同签署单次切换门：T01–T31 无关键失败、保留集与 SLO 已预注册、正例有独立证明、零容忍反例未被接受、PG/鉴权/清理/共享对象检查及 Constitution G-C03 通过；否则不物理清理旧数据、不开放新入口，已发布旧事实只能保留并追加反向失效批次。

**Release Gate**: T093 是破坏性清理前的最后门，且必须以 T094 的 AC-T31 证据通过为前提；T094 本身不授权发布或清理。真实切换后若出现摘要事实泄漏、错误身份/组成、版本错配、跨用户读取或未授权事实提交，只停止新入口并修复唯一新实现，不篡改原事件，也不重启旧实现。

---

## Acceptance Traceability: AC-T01–AC-T31

| Acceptance | Primary test task(s) | Required layer |
|---|---|---|
| AC-T01–AC-T02 | T034 | 纯领域/真实本体 fixture |
| AC-T03–AC-T04 | T019, T035 | metadata + 检索/真实执行事件 |
| AC-T05–AC-T06 | T036 | mention/type/identity role |
| AC-T07–AC-T08 | T037 | 物理表格 cell + 原子引用 |
| AC-T09–AC-T10 | T038 | predicate bridge 正反例 |
| AC-T11–AC-T12 | T039 | target/owner/层级配对 |
| AC-T13–AC-T14 | T040 | 值状态 + 拒绝续检 |
| AC-T15–AC-T16 | T041 | 技术失败 + identity quarantine/cache |
| AC-T17–AC-T18 | T042 | 局部/全局身份 + merge/split 闭包 |
| AC-T19 | T043 | 根资格/依赖/循环/替代路径 |
| AC-T20 | T066, T067 | 生命周期 + PostgreSQL lease/fencing |
| AC-T21–AC-T22 | T053, T067 | 版本化存储 + 原子事务/故障 |
| AC-T23 | T021, T022 | 创建 API + 前端显式提交 |
| AC-T24 | T055, T056 | SSE/GET/双 Tab/迟到响应 |
| AC-T25–AC-T26 | T054, T057 | artifact 状态/图/证据定位 |
| AC-T27 | T068 | 全入口鉴权与输入安全 |
| AC-T28 | T069, T070 | 控制/保留/删除/晚到 worker |
| AC-T29 | T076–T084 | 混合数据清理与唯一入口 |
| AC-T30 | T087–T091 | 新核心 E2E + 独立真实质量 |
| AC-T31 | T094 | CMC 权威 TTL、union/schema/local menu、属性链与往返保真 |

## Dependencies & Execution Order

### R-package / phase dependencies

1. **Phase 1 / R0** 无代码依赖；T001–T007 完成后冻结基线。
2. **Phase 2 / R1** 依赖 R0，阻塞所有用户故事。
3. **US1 / Phase 3** 依赖 R1，交付可持久化 metadata MVP。
4. **US2 / Phase 4** 依赖 R1 与 T025–T026 的 RecordIndex/Metadata 契约；其纯领域测试可与 US1 的 API/UI 后半段并行。
5. **US3 / Phase 5** 依赖 US1 运行存储和 US2 proof/projection/scheduler。
6. **US4 / Phase 6** 依赖运行存储；安全/保留测试可与 US3 UI 并行，真实 lease 完成后才可验收控制 API。
7. **US5 / Phase 7** 的 dry-run/测试可提前准备，但真实删除、入口退役启用和部署只允许在 Phase 8 发布门通过后执行。
8. **Phase 8 / R7** 依赖所有目标用户故事；T089 真实模型运行必须晚于 fixture/评分器冻结且不得读取评分参考作为模型输入。
9. **AC-T31 / 发布顺序**：T094 是追加编号但先于汇总与签署执行，依赖顺序为 `T094 → T091 → T093`；T092 的完整工程门也必须包含 T094 指定测试，T094 本身不授权发布或清理。

### Parallel opportunities

- T002、T003、T004 可并行；T005 完成 schema 后 T006 串行固化 manifest。
- T008–T010 可并行；T011/T012/T014 可并行，T013 完成后再接 T015–T017。
- US1 的 records/metadata、DB schema 和前端测试可分工，但 `document-analysis-panel.tsx` 与 `api.ts` 各自只由一个任务串行修改。
- T034–T043 的测试文件可并行；T044–T050 分文件可并行，T051–T052 在其契约稳定后集成。
- T053–T057 可并行；graph/evidence/coverage 三个前端组件可并行，T065 最后集成。
- PostgreSQL T067、API 安全 T068、保留 T069 和前端状态 T070 可并行准备。
- T085 与 T086 可并行起草，但标注必须先于任何 T089 预测；T088 浏览器合成验证可与 T089 真实模型评测并行。

## Reusable Regression Assets

### 2026-09-09 历史入口补充

- [x] H001 [US1] 实现当前 owner 的只读分页列表、轻量 schema 和客户端契约，覆盖隔离、排序、保留期与零派发副作用。
- [x] H002 [US1] 页面常驻历史任务入口，创建后更新、点击恢复既有任务、显示加载/空态/失败及分页。
- [x] H003 [US1] 验证关闭结果和重新进入后的回看、同名多任务、快速切换隔离及定向静态检查；实际验证边界见 `history-validation.md`。
- [x] H004 [US1/US3] 按后续 UI 要求将分析历史移至左侧导航，章节树与预览共享，右侧节点元数据/关系图谱并列；保留证据联动与只读契约，适配窄栏图谱并验证实际浏览器布局，结果见 `layout-validation.md`。

- 原子引用/表格：`backend/tests/test_extraction/test_evaluation_citation_protocol.py`、`backend/tests/test_extraction/test_evaluation_citation_tables_v2.py`、`backend/tests/test_extraction/test_docx_structure.py`。
- 两跳与拒绝续检：`backend/tests/test_extraction/test_evaluation_cmc_product_api_path.py`、`backend/tests/test_extraction/test_evaluation_quality_rejection_continuation.py`、`backend/tests/test_extraction/test_staged_retrieval.py`。
- 身份、归并与失效：`backend/tests/test_extraction/test_identity_context_quarantine.py`、`backend/tests/test_extraction/test_instance_registry.py`、`backend/tests/test_extraction/test_evaluation_quality_review_gate.py`。
- 判定审计/图投影：`backend/tests/test_extraction/test_rejection_analysis.py`、`backend/tests/test_extraction/test_quality_graph_export.py`、`backend/tests/test_extraction/test_evidence_contracts.py`。
- IR/章节/摘要：`backend/tests/test_extraction/test_document_ir.py`、`backend/tests/test_extraction/test_word_section_tree.py`、`backend/tests/test_extraction/test_word_tree_summarizer.py`。
- 租约/调度：`backend/tests/test_extraction/test_annotation_execution.py`、`backend/tests/test_extraction/test_model_scheduler.py` 及其 `*_postgresql.py` 变体；只能复用机制，不得复用旧 job 身份。
- 前端测试方式：`frontend/tests/*.test.mjs` 的 Node test 模式与 `frontend/tests/reporting-browser.mjs` 的外置 Playwright 模式；项目当前没有 package.json `test` script，任务 T092 必须使用显式命令或另行新增脚本后记录。

## Baseline Risks to Keep Visible

- 默认 `backend/tests/conftest.py` 强制内存 SQLite、关闭语义对齐、替换 FakeOntologyEngine 且不运行 FastAPI lifespan；默认全绿不能证明 PostgreSQL 并发、真实本体启动或模型质量。
- PostgreSQL tests 无显式隔离 DSN 会 skip；发布报告必须把 skip 当未验收，而不是通过。
- 当前全仓 Ruff 与全量 ESLint 均已有基线失败；必须冻结问题清单、要求本特性路径零新增，并分别报告全量和定向结果，不能把历史债务藏掉或误报成本特性退化。
- 现有目标 CMCReport assistant-silver 未经领域专家复核且不是全文金标；当前文档及近重复不能进入保留测试集。
- 现有文档分析 API 测试明确断言同步、无图谱、无 job 且临时文件删除；重构后应替换这些旧契约，不能同时保留两套互相冲突的测试。
- 前端虽有 Node/browser 脚本，但 package.json 没有 test runner script，浏览器脚本依赖外置 Playwright且多用合成 API；它们不能替代真实后端、SSE、鉴权和文件生命周期集成。
- 仓库未配置覆盖率门、统一 CI workflow 或测试 marker；每个 validation 记录必须给出实际命令、环境、收集数、pass/fail/skip 和未覆盖边界。
- Constitution III 禁止物理删除或就地篡改已发布内容；生产旧域 manifest 尚未证明目标全是未发布独占产物，因此破坏性清理当前必须保持阻断，不能用用户要求或备份替代治理门。


## 报告预览专家意见入口（2026-09-11）

按用户进一步要求实施可保存、重读及导出的专家意见入口；需求、权限、版本、幂等及验收见[契约](contracts/expert-opinions.md)。该意见不自动变更图谱或校准质量状态。

- [x] 定义专家意见契约、不可变存储及 0036 迁移。
- [x] 实现原件/图谱归属校验、角色门禁、幂等与同事务审计。
- [x] 接入各类报告预览，提供表单、个人历史和 JSON 导出。
- [x] 完成定向 API 测试、前端类型与静态检查、隔离浏览器回归。
