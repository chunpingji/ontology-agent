---
description: "Task list for 多源抽取与实时推理 —— 能力二/能力三 GAP 闭合"
---

# Tasks: 多源抽取与实时推理 —— 能力二/能力三 GAP 闭合

**Input**: Design documents from `/specs/002-extraction-realtime-reasoning/`

**Prerequisites**: [plan.md](./plan.md), [spec.md](./spec.md), [research.md](./research.md), [data-model.md](./data-model.md), [contracts/](./contracts/)

**Tests**: 已包含（宪法原则 IV「测试纪律与契约优先」+ plan.md 枚举的 `backend/tests/test_api/` 契约/集成测试）。每个用户故事先写测试、确认 FAIL 再实现。

**Organization**: 任务按用户故事（US1–US6，对应 spec 优先级 P1–P5）分组，每组可独立实现与测试。

## Format: `[ID] [P?] [Story] Description`

- **[P]**: 可并行（不同文件、无未完成依赖）
- **[Story]**: 所属用户故事标签
- 每个任务含确切文件路径

## Path Conventions

Web 结构：后端 `backend/app/`、`backend/tests/`；前端 `frontend/src/`。沿用既有布局。

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: 项目初始化与新依赖

- [X] T001 在 `backend/pyproject.toml`（及锁文件）新增第三方依赖 `reportlab`（FR-024/R12），并在本地虚拟环境安装验证可 import
- [X] T002 [P] 创建后端新服务包目录与 `__init__.py`：`backend/app/services/reporting/`；确认 `backend/app/services/extraction/`、`backend/app/services/integration/`、`backend/app/services/reasoning/` 可新增模块
- [X] T003 [P] 在 `backend/app/config.py` 扩展 `Settings`：`aps_poll_interval_seconds`（默认 2）、`report_output_dir`、APS 连接配置键（仅引用 env，不含明文凭据，R7）

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: 数据库 schema、共享模型、审计单写路径与依赖装配——所有用户故事的阻塞前置

**⚠️ CRITICAL**: 本阶段完成前，任何用户故事不得开始

- [X] T004 [P] 扩展 `backend/app/models/extraction.py`：`ExtractionCandidate` 增 `candidate_kind`/`group_key`(index)/`is_canonical`/`source_ref`/`degraded_reason`/`merged_into_id`(FK self)/`action_conditions`（data-model §1.1）
- [X] T005 [P] 扩展 `backend/app/models/reasoning.py`：`AuditLog` 增 `prev_hash`/`entry_hash`(index)/`seq`(unique,index)；`ReasoningExecution` 增 `requires_signature`/`effective`/`signature_id`(FK)/`affected_subgraph`/`superseded_by`(FK self)；新增 `ActionExecution` 与 `ElectronicSignature` 模型（data-model §1.2/1.3/2.2/2.3）
- [X] T006 [P] 扩展 `backend/app/models/integration.py`：`IntegrationConnector` 增 `ingest_mode`/`poll_interval_seconds`/`sync_cursor`/`last_status`/`last_error`；新增 `FactMaterializationRun` 模型（data-model §1.4/2.1）
- [X] T007 创建单一 Alembic 迁移 `backend/alembic/versions/0002_extraction_realtime.py`，覆盖 T004–T006 全部新表与列扩展，`down_revision` 指向 `0001`，接入既有启动迁移链（`main._run_migrations` upgrade head）；依赖 T004,T005,T006
- [X] T008 实现审计哈希链单写服务 `backend/app/services/audit.py`：`append(action, actor, entity_iri, details)` 计算 `entry_hash=SHA-256(prev_hash‖规范化记录)`、递增 `seq`、append-only 写入；`verify()` 顺序重算并定位首个断裂 `seq`（FR-028/029, data-model VR-5）；依赖 T005,T007
- [X] T009 [P] 扩展 `backend/app/dependencies.py`：新增 `get_materializer`/`get_action_engine` 装配；复用既有 `require_role`/`Identity` 与三角色常量（无需新增角色，R10）
- [X] T010 [P] 在 `backend/app/main.py` 注册新路由占位（`actions`、`reports`、`compliance` import 与 `include_router`，先空 router）并预留启动期 `asyncio` 轮询任务挂载点（lifespan）

**Checkpoint**: Schema、模型、审计单写路径、依赖装配就绪——用户故事可开始

---

## Phase 3: User Story 1 - 多源抽取作业接线与进度反馈 (Priority: P1) 🎯 MVP

**Goal**: 创建抽取作业即真实触发流水线（解析→抽取→对齐→审核队列），进度经 SSE 实时可见；Word 正文→Action 候选；受控词表注入；LLM 不可用回退结构化抽取

**Independent Test**: 上传设备台账 Excel 与含表格+正文的 SOP Word 创建作业，流水线真实执行并产出候选、进度逐阶段可见；取消 LLM 能力后作业回退结构化抽取并完成（不失败）

### Tests for User Story 1 ⚠️

- [X] T011 [P] [US1] 契约测试 `backend/tests/test_api/test_extraction_pipeline.py`：`POST /api/extraction/jobs` 返回 `running`（非 `pending`）并触发流水线；`GET /api/extraction/jobs/{id}/progress` 为 SSE 逐阶段事件（contracts/extraction-alignment-api §1,2）
- [X] T012 [P] [US1] 集成测试（同文件追加）：Excel 端到端产出实例候选；Word 正文「若…则…必须…」产出 `candidate_kind=action` 候选；LLM 不可用回退、候选带 `degraded_reason`、作业完成（FR-004/005/007）

### Implementation for User Story 1

- [X] T013 [P] [US1] 实现作业进度事件总线 `backend/app/services/extraction/progress.py`：进程内 `asyncio` 发布/订阅，按 `job_id` 推送 `{stage,pct,status,degraded}`（R1）
- [X] T014 [US1] 扩展 `backend/app/services/extraction/pipeline.py`：各阶段（parsing/extracting/aligning/reviewing）回调 `progress`；Word 正文逻辑→带前置/后置条件 Action 候选写 `action_conditions`；注入 OEB/PDE/材质/洁净级别受控词表；依赖 T013（FR-002/004/005/006）
- [X] T015 [P] [US1] 扩展 `backend/app/services/extraction/llm_extractor.py`：LLM 不可用（无 key/调用失败）回退结构化原始数据抽取并返回 `degraded_reason`，不抛错（FR-007, R3）
- [X] T016 [US1] 扩展 `backend/app/schemas/extraction.py`：新增进度事件 DTO 与作业创建响应 `status` 字段对齐（running）
- [X] T017 [US1] 改造 `backend/app/api/extraction.py` `create_job`：接入 `BackgroundTasks` 触发 `run_extraction_pipeline`，作业置 `running`；新增 `GET /jobs/{id}/progress` 返回 `StreamingResponse`(text/event-stream) 订阅 T013；写审计（调用 `audit.append`）；依赖 T014,T013,T008（FR-001/002）
- [X] T018 [P] [US1] 前端 `frontend/src/components/extraction/job-create-form.tsx`：选择源类型/配置/上传文件并 `POST /jobs`
- [X] T019 [P] [US1] 前端 `frontend/src/components/extraction/job-progress.tsx`：`EventSource` 订阅 `/jobs/{id}/progress` 渲染进度条与阶段/降级标记
- [X] T020 [US1] 改造 `frontend/src/app/(dashboard)/extraction/page.tsx`：从静态占位改为创建表单 + 进度；并在 `frontend/src/lib/api.ts` 新增 `createExtractionJob`/进度订阅方法；依赖 T018,T019

**Checkpoint**: US1 可独立运行——上传即抽取、进度可见、回退可用（SC-001）

---

## Phase 4: User Story 2 - 跨源实体对齐与人工审核闭环 (Priority: P2)

**Goal**: 跨源同一对象对齐归组并标记规范实例；低置信候选进入审核队列，支持确认/拒绝/合并/拆分，确认后入库；支持数据库源抽取（表→实体、外键→关系）

**Independent Test**: US1 产出基础上，跨源同一设备归为一组并标记规范实例；分析师确认/拒绝/合并/拆分，仅确认项入库；对数据库源运行抽取，表结构→实体候选、外键→关系候选入同一审核队列

### Tests for User Story 2 ⚠️

- [X] T021 [P] [US2] 契约+集成测试 `backend/tests/test_api/test_alignment_review.py`：候选按 `group_key` 归组、`is_canonical` 标记；`PUT /candidates/{id}/review` 仅 `confirmed` 落 `committed_iri`；`merge`/`split` 端点；DB 源作业产出 `class`/`link` 候选（contracts/extraction-alignment-api §3-6, FR-009/010/012）

### Implementation for User Story 2

- [X] T022 [P] [US2] 实现数据库源读取器 `backend/app/services/extraction/db_reader.py`：SQLAlchemy `inspect()` 反射→表=Class 候选、列=数据属性、外键=Link 候选（只读、`dsn_ref` 经 env，R2/R7, FR-012）
- [X] T023 [US2] 扩展 `backend/app/services/extraction/pipeline.py`：对齐阶段计算 `group_key`（设备=唯一编号；药品=活性成分+剂型+规格）与 `is_canonical`，歧义不自动合并（复用 `aligner.align_entity`）；DB 源分支调用 T022；依赖 T022（FR-009/011）
- [X] T024 [US2] 扩展 `backend/app/api/extraction.py`：`GET /jobs/{id}/candidates` 改为按 `group_key` 归组响应；`create_job` 支持 `source_type=database`(`db_source`)；新增 `POST /candidates/merge` 与 `POST /candidates/{id}/split`；审核确认走 commit 入库 + 审计；依赖 T023,T008（FR-010/012/013）
- [X] T025 [P] [US2] 扩展 `backend/app/schemas/extraction.py`：归组响应 DTO、merge/split 请求、DB 源请求体
- [X] T026 [US2] 前端 `frontend/src/components/extraction/alignment-review.tsx`：展示候选/置信度/跨源归组/规范实例标记，承载确认/拒绝/合并/拆分；嵌入 `extraction/page.tsx`；`lib/api.ts` 增审核/合并/拆分方法；依赖 T024

**Checkpoint**: 能力二闭环——跨源对齐 + 审核 + DB 源（SC-002/003）

---

## Phase 5: User Story 3 - 实时事实源接入与增量物化 (Priority: P3)

**Goal**: APS 真实连接器替换 Stub（轮询≤2s + 可选 push）；增量归一物化为 A-Box 事实、发布事实变更事件；仅受影响子图增量重算并 ≤5s 刷新；源超时保留上一良好状态

**Independent Test**: 配置 APS 连接器注入排产变更，增量物化为事实实例 + 产生 `fact_materialization_run` 与事件，受影响子图重算 ≤5s 刷新；源超时保留良好状态并告警，重复/乱序事件幂等

### Tests for User Story 3 ⚠️

- [X] T027 [P] [US3] `backend/tests/test_api/test_aps_connector.py`：APSConnector 契约（真实 test/sync）+ 超时保留上一良好状态、`run.status=timeout`、`cursor_to=null`（FR-014/018）
- [X] T028 [P] [US3] `backend/tests/test_api/test_fact_materialization.py`：增量归一→A-Box + `fact_materialization_run` 留痕 + 事件发布；重复/乱序事件幂等去重（FR-015/016/019, VR-3/4）
- [X] T029 [P] [US3] `backend/tests/test_api/test_incremental_reasoning.py`：事实变更仅触发受影响子图重算（非全量）、≤5s 行为、`affected_subgraph` 非空（FR-017, VR-8, SC-005）

### Implementation for User Story 3

- [X] T030 [P] [US3] 实现 `backend/app/services/integration/aps_connector.py`：实现 `ExternalSystemConnector`，增量拉取（轮询）+ 超时/不可达处理，替换 `StubConnector`（R4, FR-014/018）
- [X] T031 [P] [US3] 实现事实变更事件总线 `backend/app/services/integration/events.py`：进程内发布/订阅 + 受影响子图（设备/产品/区域）解析（R6, FR-016/017）
- [X] T032 [US3] 实现物化服务 `backend/app/services/integration/materializer.py`：增量归一→经 `OntologyEngine` + `KGStore.sync_individual_to_shadow` 写 A-Box；幂等（`connector_id`+版本/哈希）；写 `FactMaterializationRun`；超时不推进 `cursor_to`、告警；发布事件(T031)；写审计；依赖 T030,T031,T008（FR-015/016/018/019）
- [X] T033 [US3] 实现增量重算编排 `backend/app/services/reasoning/incremental.py`：订阅事件→按受影响子图调用既有 `reasoning.engine.run_assessment`→刷新结论（写 `affected_subgraph`/`superseded_by`）；风暴时合并/限流降级；依赖 T031（FR-017/027, R6）
- [X] T034 [US3] 扩展 `backend/app/api/integration.py`：真实 `test`/`sync`、`GET /connectors/{id}/runs`、`GET /runs/{id}`、`GET /facts`、`GET /events`(SSE)、`POST /connectors/{id}/webhook`（push 汇入同一队列）；连接器创建支持 `ingest_mode`/`poll_interval`；依赖 T032（FR-014/016, contracts/integration-realtime-api §1-3）
- [X] T035 [US3] 扩展 `backend/app/api/reasoning.py`：新增 `POST /incremental`（受影响子图重算入口）与结论 `effective` 状态查询；依赖 T033（FR-017）
- [X] T036 [P] [US3] 新增 `backend/app/schemas/integration.py`：连接器/物化运行/事件 DTO
- [X] T037 [US3] 在 `backend/app/main.py` lifespan 挂载启动期 `asyncio` 轮询后台任务（按 `poll_interval` 调度活跃连接器 sync）；依赖 T034,T010（R4）

**Checkpoint**: 能力三实时链路起点就绪——事实流 + 增量重算 ≤5s（SC-004/005）

---

## Phase 6: User Story 4 - Action 编排引擎与风险评估报告 (Priority: P4)

**Goal**: 结论生效后自动编排动作（专用化工单/告警、灭活·清洁再验证任务、排期阻断+建议性回写、报告生成）并留痕；风险评估报告 PDF+JSON 双产物

**Independent Test**: US3 事实流上注入触发「需专用化」事实，自动生成工单+告警并留痕；注入不相容同设备同时段排期，阻断并建议性回写；对一次评估生成 PDF+JSON 报告

### Tests for User Story 4 ⚠️

- [X] T038 [P] [US4] `backend/tests/test_api/test_action_engine.py`：结论→工单/任务/排期阻断+建议回写编排与留痕；`writeback_status=not_accepted` 不算失败；未签名结论动作 `suppressed`（FR-020/021/022/023, VR-6/7）
- [X] T039 [P] [US4] `backend/tests/test_api/test_risk_report.py`：`GET /api/reports/{id}` JSON 含分类/专用化/污染评分/CFDI/MACO·PDE/规则链；`GET /api/reports/{id}/pdf` 产出 PDF（含签批信息位）（FR-024, SC-007）

### Implementation for User Story 4

- [X] T040 [P] [US4] 实现 Action 引擎 `backend/app/services/reasoning/action_engine.py`：订阅结论生效事件→编排 `dedication_work_order`/`alert`/`inactivation_task`/`recleaning_task`/`schedule_block`/`advisory_writeback`/`generate_report`，写 `ActionExecution` 留痕；未签名结论置 `suppressed`；依赖 T031,T008（FR-020-023, R11）
- [X] T041 [P] [US4] 实现报告渲染 `backend/app/services/reporting/risk_report.py`：组装 JSON + 经 `reportlab` 渲染 PDF（含 QA 签批信息）落 `report_output_dir`（R12, FR-024）
- [X] T042 [US4] 新增 `backend/app/api/actions.py`：`GET /actions`、`PATCH /actions/{id}`（人工流转）、`POST /actions/{id}/writeback-result`；依赖 T040（contracts/action-report-api §1-3）
- [X] T043 [US4] 新增 `backend/app/api/reports.py`：`GET /reports/{conclusion_id}`(JSON) 与 `GET /reports/{conclusion_id}/pdf`；依赖 T041（contracts/action-report-api §4-5）
- [X] T044 [P] [US4] 新增 `backend/app/schemas/reporting.py` 与扩展 `backend/app/schemas/reasoning.py`：动作/报告 DTO
- [X] T045 [US4] 在 `backend/app/main.py` 将 `actions`/`reports` 路由由占位改为实路由（替换 T010 占位）；依赖 T042,T043

**Checkpoint**: 结论→可执行动作 + 可交付报告（SC-006/007）

---

## Phase 7: User Story 5 - 实时推理看板 (Priority: P5)

**Goal**: 设备×产品共线相容性热力图 + 未来排期风险 + 推理链溯源；事实变更后近实时（≤5s）刷新

**Independent Test**: US3/US4 基础上看板展示热力图与排期风险；触发事实变更看板近实时刷新；点击结论展开规则链 ID 与法规依据

### Tests for User Story 5 ⚠️

- [X] T046 [P] [US5] 契约测试（追加至 `backend/tests/test_api/test_incremental_reasoning.py` 或新 `test_dashboard.py`）：`GET /api/integration/dashboard` 返回 `compatibility_matrix`+`schedule_risks`；`GET /api/reasoning/conclusions/{id}/trace` 返回 `rules_fired`（FR-025/027, contracts/integration-realtime-api §4）

### Implementation for User Story 5

- [X] T047 [US5] 扩展 `backend/app/api/integration.py`：`GET /dashboard`（设备×产品相容性矩阵 + 未来排期风险，复用结论）；依赖 T033,T035（FR-025）
- [X] T048 [US5] 扩展 `backend/app/api/reasoning.py`：`GET /conclusions/{id}/trace` 返回规则链 ID + 法规依据（复用 `run_assessment` 的 `rules_fired`，FR-027）
- [X] T049 [P] [US5] 前端 `frontend/src/components/integration/connector-manager.tsx`：连接器 CRUD + 测试 + 同步触发 + 物化运行列表
- [X] T050 [P] [US5] 前端 `frontend/src/components/integration/realtime-inference-panel.tsx`：d3 相容性热力图 + 排期风险 + 点击展开规则链溯源；事实变更近实时刷新（订阅事件/轮询）
- [X] T051 [US5] 改造 `frontend/src/app/(dashboard)/integration/page.tsx`：从只读 specs 改为连接器管理 + 实时看板；`lib/api.ts` 增 dashboard/trace/连接器方法；依赖 T049,T050

**Checkpoint**: 能力三可视化闭环（SC-005/007）

---

## Phase 8: User Story 6 - 合规硬化：审计哈希链、QA 强制电子签名与角色门禁 (Priority: P5)

**Goal**: 全链路审计 append-only 哈希链 + 完整性校验；高风险/专用化/合规阻断结论强制 QA 21 CFR Part 11 电子签名后方生效（未签名抑制对外动作）；RBAC 扩展到 operator/QA

**Independent Test**: 高风险结论未签名不生效、不触发对外动作，QA 签名后生效；审计哈希链校验通过、篡改后定位断链；operator/QA 角色边界生效

### Tests for User Story 6 ⚠️

- [X] T052 [P] [US6] `backend/tests/test_api/test_compliance.py`：哈希链 `verify` 对完好链 `ok:true`、篡改后 `ok:false`+`broken_at_seq`；QA 重认证签名前结论 `effective=false`+动作 `suppressed`、签名后生效；operator 写操作 `403`（FR-028-031, SC-008/009/010）

### Implementation for User Story 6

- [X] T053 [US6] 新增 `backend/app/api/compliance.py`：`GET /audit/verify`（调用 T008 `audit.verify`）、`GET /audit`（只读查询）、`GET /signatures/pending`、`POST /signatures`（重认证+绑定结论+置 `effective=true`+解除 `suppressed`+写审计）；依赖 T008,T040（contracts/compliance-audit-api §1,2）
- [X] T054 [US6] 实现结论生效门禁：在 `reasoning/incremental.py` 与 `action_engine.py` 中对 `requires_signature=true` 结论置 `effective=false` 并抑制对外动作（`ActionExecution.status=suppressed`），签名后由 T053 触发释放；依赖 T053,T033,T040（FR-030, VR-6）
- [X] T055 [P] [US6] RBAC 接线：对能力三端点应用 `require_role`——`operator` 只读推理/看板（reasoning GET、integration/dashboard）、`qa` 复核签名（compliance signatures）、维护/发布/审核/增量触发限 `senior_analyst`；改 `backend/app/api/{reasoning,integration,actions,reports,compliance,extraction}.py` 相应端点依赖项（FR-031, contracts/compliance-audit-api §0）
- [X] T056 [US6] 在 `backend/app/main.py` 将 `compliance` 路由由占位改为实路由（替换 T010 占位）；依赖 T053
- [X] T057 [P] [US6] 前端 `frontend/src/components/reasoning/qa-signature-dialog.tsx`：Part 11 重认证（用户名+密码）+ 签名含义 + 提交；改造 `frontend/src/app/(dashboard)/reasoning/page.tsx` 结论列表 + 待签名入口；`lib/api.ts` 增签名/审计校验方法

**Checkpoint**: 合规硬化完成——ALCOA+ 哈希链 + Part 11 签名 + RBAC（SC-008/009/010）

---

## Phase 9: Polish & Cross-Cutting Concerns

**Purpose**: 跨故事打磨与端到端验证

- [X] T058 [P] 按 [quickstart.md](./quickstart.md) 执行 US1–US6 端到端验证并记录结果
- [X] T059 [P] 审查全链路审计写入覆盖（抽取/对齐/物化/推理/动作/签名均经 `audit.append`），补齐遗漏调用点（FR-028）
- [X] T060 [P] 凭据安全核查：连接器敏感凭据不入 `connection_config`/不入库/不入版本库，日志脱敏（R7、宪法安全约束）
- [X] T061 [P] 更新 `docs/gap-analysis.md` 状态（P2–P5 闭合）与后端 README/API 文档
- [X] T062 运行后端测试套件 `cd backend && pytest`，确认新增契约/集成测试全部通过且既有 36 项不回归

---

## Phase 10: Convergence

> 由 `/speckit-converge` 追加：评估当前代码相对 spec/plan/constitution 的剩余缺口。仅追加，不改动既有任务。

- [X] T063 [US1] 将领域受控词表（OEB/PDE/材质/洁净级别，`vocabulary.CONTROLLED_VOCAB`）注入 LLM 抽取提示——扩展 `backend/app/services/extraction/llm_extractor.py` `build_extraction_prompt` 增加"受控取值约束"段，并由 `pipeline.py` 抽取阶段传入相关词表；保留既有 `tag_controlled_vocab` 后处理归一化作为兜底。补充 `test_extraction_pipeline.py` 断言提示含受控词表取值 per FR-006 / US1-AC3 (partial)

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: 无依赖，立即开始
- **Foundational (Phase 2)**: 依赖 Setup；**阻塞所有用户故事**（schema/模型/审计/装配）
- **User Stories (Phase 3–8)**: 均依赖 Foundational
  - 推荐按优先级顺序 P1→P2→P3→P4→P5；US4 依赖 US3 事实流，US5 依赖 US3/US4，US6 的动作抑制依赖 US4 Action 引擎
  - US1、US2 可在 Foundational 后较独立推进
- **Polish (Phase 9)**: 依赖所需用户故事完成

### User Story Dependencies

- **US1 (P1)**: 仅依赖 Foundational — MVP，独立可测
- **US2 (P2)**: 依赖 Foundational；复用 US1 流水线产出但可独立测试
- **US3 (P3)**: 依赖 Foundational；实时链路起点，独立可测
- **US4 (P4)**: 依赖 US3 事实流/事件（T031）与结论
- **US5 (P5)**: 依赖 US3/US4 的事实流与结论/动作
- **US6 (P5)**: 审计链/签名独立；动作抑制门禁（T054）依赖 US4 Action 引擎

### Within Each User Story

- 测试先写并 FAIL → 模型（已在 Foundational）→ 服务 → 端点 → 前端集成
- 故事完成再进入下一优先级

### Parallel Opportunities

- Setup：T002、T003 并行
- Foundational：T004、T005、T006 并行（不同模型文件）；后 T007 迁移汇总
- 各故事测试任务（标 [P]）可并行先行
- 服务模块跨故事不同文件可并行（如 T030/T031 APS+事件；T040/T041 引擎+报告）
- 前端组件不同文件并行（T018/T019；T049/T050）
- Foundational 完成后，若多人协作可并行推进 US1/US2/US3

---

## Parallel Example: User Story 3

```bash
# 先并行启动 US3 全部测试（应 FAIL）：
Task: "test_aps_connector.py 契约/超时 in backend/tests/test_api/test_aps_connector.py"
Task: "test_fact_materialization.py 幂等/留痕 in backend/tests/test_api/test_fact_materialization.py"
Task: "test_incremental_reasoning.py 受影响子图/≤5s in backend/tests/test_api/test_incremental_reasoning.py"

# 再并行实现独立服务模块：
Task: "APSConnector in backend/app/services/integration/aps_connector.py"
Task: "事实变更事件总线 in backend/app/services/integration/events.py"
```

---

## Implementation Strategy

### MVP First (User Story 1)

1. Phase 1 Setup → 2. Phase 2 Foundational（关键，阻塞全部）→ 3. Phase 3 US1
4. **STOP & VALIDATE**：独立测试 US1（上传即抽取、进度可见、回退可用）→ 可演示 MVP

### Incremental Delivery

Setup+Foundational → US1（MVP，能力二抽取接线）→ US2（能力二闭环）→ US3（实时事实流）→ US4（动作+报告）→ US5（看板）→ US6（合规硬化）。每个故事独立增值、不破坏既有。

### Parallel Team Strategy

Foundational 完成后：开发 A 走 US1→US2（能力二）；开发 B 走 US3→US4（能力三核心）；开发 C 在 US3/US4 基线上做 US5 看板与 US6 合规硬化前端。集成点：事件总线（T031）、审计服务（T008）、结论生效门禁（T054）。

---

## Notes

- [P] = 不同文件、无未完成依赖
- [Story] 标签用于可追溯性与独立交付
- 严格复用既有内核：`run_extraction_pipeline`/`aligner`/`reasoning.engine`/`KGStore`/`ExternalSystemConnector`/`require_role`（FR-027、宪法原则 V）
- 本特性**不回写权威 TTL**：物化仅写 A-Box；DB 源 T-Box 候选仅入审核队列（宪法原则 II）
- 唯一新增第三方依赖：后端 `reportlab`
- 每个任务或逻辑组完成后提交；可在任一 Checkpoint 停下独立验证
