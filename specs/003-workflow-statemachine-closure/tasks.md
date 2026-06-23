---
description: "Task list for 分析结论工作流状态机闭环 (003)"
---

# Tasks: 分析结论工作流状态机闭环 —— 结论流水线自举与显式生命周期

**Input**: Design documents from `/specs/003-workflow-statemachine-closure/`

**Prerequisites**: [plan.md](./plan.md), [spec.md](./spec.md), [research.md](./research.md), [data-model.md](./data-model.md), [contracts/](./contracts/), [quickstart.md](./quickstart.md)

**Tests**: 包含。宪章 IV（测试纪律与契约优先）要求关键路径有契约/集成测试（pytest），且 quickstart 已声明具体测试文件——故本特性测试任务为**必需**，非可选。

**Organization**: 任务按用户故事（US1=P1 / US2=P2 / US3=P3 / US4=P4）分组，每组独立可实现、可测试、可作为增量交付。

**Stack（来自 plan.md）**: Python 3.12 · FastAPI（`APIRouter`+`Depends`）· SQLAlchemy 2.0 · Alembic · Pydantic v2 · Owlready2 · pytest（`TestClient`+`StaticPool`+`FakeOntologyEngine`）。改动集中于 `backend/app`，单一迁移 `0003`，无新增第三方依赖。

## Format: `[ID] [P?] [Story] Description`

- **[P]**: 可并行（不同文件、无未完成依赖）
- **[Story]**: 所属用户故事（US1/US2/US3/US4）；Setup/Foundational/Polish 无 Story 标签

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: 在既有 002 代码基线上确立可回归的起点（本特性为既有流水线的接线闭合，非新建项目）

- [ ] T001 建立回归基线：在 `backend/` 运行 `pytest` 确认既有测试全绿，作为后续重构（`/assess`、`sign`、`incremental` 改造）的回归护栏

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: 显式状态机的**脊柱**——`lifecycle_state` 列、迁移与单一迁移守卫。所有用户故事都经此落初始态或迁移。

**⚠️ CRITICAL**: 本阶段未完成前，任何用户故事都无法落库/迁移结论

- [ ] T002 在 `backend/app/models/reasoning.py` 为 `ReasoningExecution` 增列 `lifecycle_state: Mapped[str]`（`String(30)`，索引，default `"effective"`），对齐 data-model §1.1（FR-014）
- [ ] T003 在 `backend/app/models/reasoning.py` 为 `ActionExecution` 增加 `voided` 终态语义（状态常量/取值集），对齐 data-model §1.2（FR-012/020）
- [ ] T004 创建 Alembic 迁移 `backend/alembic/versions/0003_workflow_statemachine.py`（`revision="0003_workflow_statemachine"`，`down_revision="0002_extraction_realtime"`）：`sa.inspect` 守卫 `add_column` + 建索引 + 四态回填（data-model §4 优先级表），沿用 0002 防御式风格（依赖 T002）
- [ ] T005 [P] 创建 `backend/app/services/reasoning/lifecycle.py`：`LifecycleState` 枚举 + `LEGAL_TRANSITIONS`（T1–T5，data-model §2.2）+ `transition(db, conclusion, to_state, *, actor, reason)`（校验合法集→非法 raise `IllegalTransition` 且不改状态；更新 `lifecycle_state` 并同步既有 `effective`/`superseded_by`；写 `reasoning.transition` 审计 `commit=False`）+ `IllegalTransition` 异常（FR-014/015/016）

**Checkpoint**: 状态机脊柱就绪——用户故事可开始

---

## Phase 3: User Story 1 - 评估即落库，流水线自举 (Priority: P1) 🎯 MVP

**Goal**: 让 `/assess` 在返回结论的同时持久化为带唯一标识与初始生命周期状态的工作流对象并编排动作，使整条流水线从一次评估即可自举（G1，FR-001~004）。

**Independent Test**: 干净库（零预置结论）下发起一次评估 → ①返回 `execution_id` 且库中存在可按标识检索、带初始状态的结论；②隐含动作已编排登记；③可直接对其导出报告，无需补数据（quickstart US1）。

> MVP 范围内结论均走 `effective`（高风险 arming 由 US2 叠加）；动作随结论生效进入 `pending`。

### Tests for User Story 1 ⚠️

- [ ] T006 [P] [US1] 集成测试 `backend/tests/test_api/test_assess_bootstrap.py`：评估落库返回 `execution_id`+`lifecycle_state`（AC1）、检索返回状态+结果+动作清单（AC2）、动作编排登记（AC3）、落库后直接导出报告成功（AC4）、无身份头 `403`

### Implementation for User Story 1

- [ ] T007 [P] [US1] 扩展 `AssessmentResponse`（增 `execution_id`/`lifecycle_state`/`requires_signature`/`effective` 追加字段，向后兼容）于 `backend/app/schemas/reasoning.py`（contracts/assess-bootstrap §1）
- [ ] T008 [US1] 在 `backend/app/api/reasoning.py` 实现 **canonical `results`** 组装（键集同时满足 `action_engine._plan` 与 `risk_report.build_report_json`，data-model §1.1 表），作为落库 `results` 与下游动作/报告的共同数据源（R2）
- [ ] T009 [US1] 改造 `backend/app/api/reasoning.py::/assess`：加 `require_role(ROLE_SENIOR_ANALYST)` 门禁；持久化 `ReasoningExecution`（写 canonical results/risk_level/maco_*/scenarios/input_params）→ 经 `lifecycle.transition` 落初始态 `effective`（T1）→ `ActionEngine.orchestrate` 编排动作 → 写 `reasoning.persist` 审计；单事务原子；回填响应标识/状态（依赖 T005、T007、T008）（FR-001/003/004/017）
- [ ] T010 [US1] 确认/扩展 `backend/app/api/reasoning.py::GET /conclusions/{id}` 返回 `lifecycle_state` + 已编排动作清单（FR-002）

**Checkpoint**: US1 可独立运行——干净库单次评估端到端自举（SC-001/002）

---

## Phase 4: User Story 2 - 高风险结论自动进入 QA 待签闸门 (Priority: P2)

**Goal**: 落库时据风险特征自动 arm QA 闸门——高风险结论入 `待签` 态、动作全抑制；低风险落库即 `生效`；QA 有效签名后生效解抑；并支持 QA 拒绝（G2，FR-005~009、FR-020）。

**Independent Test**: 构造命中/不命中高风险判据两条结论 → ①前者自动 `pending_signature`、零对外派发；②后者落库即 `effective`；③QA 签名后前者转 `effective`、动作解抑；④无签名无法生效；⑤QA 拒绝 → `rejected` 终态 + 动作作废（quickstart US2）。

**Depends on**: Foundational（T005 transition）+ US1（T009 落库站点）

### Tests for User Story 2 ⚠️

- [ ] T011 [P] [US2] 集成测试 `backend/tests/test_api/test_qa_gate.py`：高风险输入落库 `pending_signature` 且动作全 `suppressed`、对外派发数 0（AC1，SC-003）；低风险落库 `effective` 动作 `pending`（AC2）
- [ ] T012 [P] [US2] 集成测试 `backend/tests/test_api/test_qa_reject.py`：QA 签名后转 `effective`+解抑+审计（AC3）、无签名令生效/派发被拒（AC4）、QA 拒绝 → `rejected`+被抑动作 `voided`+审计、再签批/再拒绝 `409`（AC5、FR-020）

### Implementation for User Story 2

- [ ] T013 [P] [US2] 创建 `backend/app/services/reasoning/risk.py`：`requires_qa_signature(results, risk_level)` = 高风险等级 OR `requires_dedication` OR `_hazardous_scenario`（青霉素/头孢/高致敏，初始集），纯函数可独立单测（FR-005，contracts/assess-bootstrap §3）
- [ ] T014 [US2] 在 `backend/app/api/reasoning.py::/assess` 接入闸门：落库时 `requires_signature = risk.requires_qa_signature(...)`，据判据经 `transition` 分流 `effective`(T1)/`pending_signature`(T2)，`pending` 时 `orchestrate` 将动作置 `suppressed`、零派发（依赖 T013）（FR-005/006/007）
- [ ] T015 [US2] 重构 `backend/app/api/compliance.py::sign_conclusion` 经 `lifecycle.transition(→effective)`（T3）统一守卫：保留 Part 11 重认证、签名不可分割绑定、`suppressed→pending` 解抑、`compliance.sign` 审计；对 `superseded`/`rejected` 签批 → `409`（FR-008/009、签批竞态）
- [ ] T016 [US2] 实现 `POST /api/compliance/reject`（`require_role(ROLE_QA)`）于 `backend/app/api/compliance.py`：重认证 + `transition(→rejected)`（T4）+ 被抑动作置 `voided`（每条 `action.void` 审计）+ `compliance.reject` 审计；终态不可再签批/再拒绝（依赖 T005、T003）（FR-020）
- [ ] T017 [US2] 将 `backend/app/api/compliance.py::pending_signatures` 过滤改为 `lifecycle_state == "pending_signature"`，确保 `rejected` 不再出现于待签列表（contracts/lifecycle-guard §3）

**Checkpoint**: US1 + US2 独立可用——高风险 100% 待签、零越闸派发，QA 签批/拒绝闭环（SC-003）

---

## Phase 5: User Story 3 - 事实变更自动召回重算（近实时）(Priority: P3)

**Goal**: 为既有 `fact_event_bus` 注册订阅者，桥接物化事件到增量重算，使"事实变更 → 结论刷新"无人工触发、≤5s 完成；取代旧结论并作废其未完成动作（G3，FR-010~013）。

**Independent Test**: 存在若干生效结论时注入相交事实变更 → ①无人工触发自动重算相交且生效结论；②不相交/已失效/待签不被触动；③旧结论 `superseded`+链接替代；④旧动作 `voided`；⑤端到端 ≤5s（quickstart US3）。

**Depends on**: Foundational（T005 transition T5）+ US1（需生效结论作重算对象）

### Tests for User Story 3 ⚠️

- [ ] T018 [P] [US3] 集成测试 `backend/tests/test_api/test_auto_recompute.py`：事件 → 自动重算相交生效结论（AC1）、跳过不相交/失效/`pending_signature`（AC2，FR-011）、旧结论 `superseded`+`superseded_by` 链接（AC3）、旧非终态动作 `voided`+审计（AC4，FR-012）

### Implementation for User Story 3

- [ ] T019 [P] [US3] 创建 `backend/app/services/reasoning/recompute_subscriber.py`：`make_recompute_subscriber()` 返回回调（开新 `SessionLocal` + 全局 `ontology_engine`，调 `recompute_subgraph(session, event.affected_subgraph, engine)`）；暴露可直接调用入口供单测（R4-D-A，FR-010）
- [ ] T020 [US3] 改造 `backend/app/services/reasoning/incremental.py::recompute_subgraph`：旧结论 `effective→superseded` 经 `transition`(T5)；显式 `lifecycle_state == "effective"` 过滤（FR-011）；被取代旧结论**非终态**动作批量置 `voided` + `action.void` 审计（`commit=False`，与取代同事务）（依赖 T005、T003）（FR-011/012）
- [ ] T021 [US3] 在 `backend/app/main.py::lifespan` 注册 `fact_event_bus.subscribe(make_recompute_subscriber())`，加幂等守卫避免重复注册（依赖 T019）（FR-010）
- [ ] T022 [US3] 保证发布/提交顺序不变式（C-5）：令订阅者重算只读**已提交**结论表——将 `backend/app/services/integration/materializer.py` 的 `fact_event_bus.publish` 触发点对齐到最终 `commit` 之后（必要时同步调整 `events.py`），规避读未提交物化行（research R4 事务性注记）

**Checkpoint**: US1–US3 独立可用——事实一变结论自动跟随，≤5s（SC-004）

---

## Phase 6: User Story 4 - 显式生命周期与集中守卫 (Priority: P4)

**Goal**: 把分散自守固化为显式四态 + 单一守卫的治理层——所有入口经同一合法性来源、非法迁移一致拒绝且不改状态、动作早派发与越权被一致拦截（路线 A 硬化，FR-014~017）。

**Independent Test**: 枚举合法/非法迁移序列 → ①合法按四态推进；②每类非法被拒、状态不变、附原因；③多入口判定一致；④operator 任何写/迁移/签批被拒（quickstart US4）。

**Depends on**: Foundational（T005 guard）+ US1/US2/US3（守卫已被各入口调用，本阶段做全覆盖硬化与一致性验证）

### Tests for User Story 4 ⚠️

- [ ] T023 [P] [US4] 综合状态机测试 `backend/tests/test_api/test_lifecycle_machine.py`：全部合法迁移（T1–T5）按集推进并记审计（AC1）；每类非法迁移（绕过待签、自终态外迁、对已取代/已拒绝再迁移）被拒且状态不变（AC2，SC-005）；同类迁移多入口（落库/签批/拒绝/取代）判定一致（AC3）
- [ ] T024 [P] [US4] RBAC 测试 `backend/tests/test_api/test_rbac_guard.py`：operator 对 `/assess`、`/signatures`、`/reject`、`PATCH /actions/{id}` 均 `403`（AC4，SC-007）

### Implementation for User Story 4

- [ ] T025 [P] [US4] 在 `backend/app/api/actions.py::patch_action` 增 from-status 守卫：拒绝终态（`voided`/`done`/`failed`）外迁、拒绝 `suppressed` 直接流转 → `409`；写 `action.transition` 审计（FR-003/009、动作早派发防护）
- [ ] T026 [US4] 在 `reasoning.py`/`compliance.py`/`actions.py` 各入口统一将 `IllegalTransition` 映射为 `409` + 可操作被拒原因（from/to/reason），确保多入口一致（FR-016）
- [ ] T027 [US4] 在 `backend/app/schemas/integration.py::ConclusionResponse` 一致暴露 `lifecycle_state`（与既有 `effective`/`superseded_by` 并存），使对外结论视图以显式态为准（FR-014）

**Checkpoint**: 全部用户故事独立可用，状态治理收口（SC-005/007）

---

## Phase 7: Polish & Cross-Cutting Concerns

**Purpose**: 跨故事的回填正确性、审计完整性与端到端验真

- [ ] T028 [P] 迁移回填测试 `backend/tests/test_api/test_migration_backfill.py`：验证 `0003` 由旧布尔（`superseded_by`/`effective`/`requires_signature`）映射到四态正确（data-model §4），且迁移可重入
- [ ] T029 [P] 审计链工作流验真测试 `backend/tests/test_api/test_audit_chain_workflow.py`：US1–US4 全程事件（`reasoning.persist`/`reasoning.transition`/`compliance.sign`/`compliance.reject`/`reasoning.recompute`/`action.void`/`action.transition`）齐备连续；篡改 → `verify` 定位首个 `broken_at_seq`（FR-018/SC-006）
- [ ] T030 [P] 验证建议性回写 `writeback_status == "not_accepted"` 不置动作 `failed`（FR-019/VR-10），补/核 `backend/tests/test_api/` 相应断言
- [ ] T031 按 [quickstart.md](./quickstart.md) 执行 US1–US4 + 审计验真端到端验证（curl + pytest 四文件全绿）
- [ ] T032 全量回归：`cd backend && pytest` 确认本特性与既有 002 测试全绿，无回归

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: 无依赖，立即开始
- **Foundational (Phase 2)**: 依赖 Setup；**阻塞所有用户故事**（T002→T004；T005 可与 T004 并行）
- **User Stories (Phase 3–6)**: 均依赖 Foundational 完成
  - US1（P1）：Foundational 后即可——无其他故事依赖（MVP）
  - US2（P2）：依赖 US1 的 `/assess` 落库站点（T009）以接入闸门与初始态分流
  - US3（P3）：依赖 US1（需生效结论作重算对象）；与 US2 相互独立
  - US4（P4）：依赖 T005 守卫被各入口调用（US1–US3 已接入），本阶段做全覆盖硬化与一致性验证
- **Polish (Phase 7)**: 依赖所有目标用户故事完成

### User Story Dependencies

- **US1 (P1)**: 仅依赖 Foundational —— 独立可测（MVP）
- **US2 (P2)**: 依赖 Foundational + US1 落库站点；其余独立可测
- **US3 (P3)**: 依赖 Foundational + US1 生效结论；与 US2 独立
- **US4 (P4)**: 依赖 Foundational；对 US1–US3 入口做一致性硬化（建议在三者之后收口）

### Within Each User Story

- 测试先写并先失败 → 模型 → 服务 → 端点 → 集成
- 同文件任务顺序执行（US1 的 T008→T009→T010 同 `api/reasoning.py`；US2 的 T015→T016→T017 同 `api/compliance.py`）

### Parallel Opportunities

- Foundational：T005（新文件）可与 T004（迁移）并行
- US1：T006（测试）、T007（schema）可并行启动；T008–T010 同 `reasoning.py` 顺序执行
- US2：T011/T012（测试）、T013（`risk.py` 新文件）、T014（`reasoning.py`）可并行；T015–T017 同 `compliance.py` 顺序执行
- US3：T018（测试）、T019（`recompute_subscriber.py` 新文件）可并行；T020/T022 不同文件可并行，T021 依赖 T019
- US4：T023/T024（测试）、T025（`actions.py`）、T027（`schemas/integration.py`）可并行；T026 跨入口收尾
- Polish：T028/T029/T030 可并行；T031→T032 顺序收口
- 团队并行：Foundational 完成后，US1 先行交付 MVP；US2 与 US3 可由不同成员并行（仅 US2 需等 US1 的 T009）

---

## Parallel Example: User Story 2

```bash
# 测试与新文件并行启动：
Task: "集成测试 test_qa_gate.py"            # T011
Task: "集成测试 test_qa_reject.py"          # T012
Task: "创建 services/reasoning/risk.py"     # T013
Task: "接入闸门到 /assess（reasoning.py）"   # T014（依赖 T013）
# compliance.py 顺序执行：
#   T015 sign 经守卫 → T016 reject 端点 → T017 pending 过滤
```

---

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Phase 1 Setup（回归基线）
2. Phase 2 Foundational（状态机脊柱 —— 阻塞项）
3. Phase 3 US1（评估即落库自举）
4. **STOP & VALIDATE**：干净库端到端跑通"评估 → 检索 → 报告"（SC-001/002）
5. 可演示 MVP

### Incremental Delivery

1. Setup + Foundational → 脊柱就绪
2. + US1 → 自举 MVP（演示）
3. + US2 → 高风险 QA 闸门 + 拒绝（演示）
4. + US3 → 事实变更近实时自动重算（演示）
5. + US4 → 显式状态治理收口
6. Polish → 回填/审计/回归验真
   每一步增量交付且不破坏既有故事

---

## Notes

- **[P]** = 不同文件、无未完成依赖
- 测试为**必需**（宪章 IV）：每故事测试先失败再实现
- 本特性为既有 002 流水线的**接线闭合**：多为改造既有文件（`api/reasoning.py`/`api/compliance.py`/`api/actions.py`/`services/reasoning/incremental.py`/`main.py`）+ 三个新建薄模块（`lifecycle.py`/`risk.py`/`recompute_subscriber.py`）+ 一列一迁移
- **本特性不写回权威 TTL、无 T-Box 写入**（宪章 II 不触发）
- `transition()` 统一 `commit=False`，由各入口同事务提交（迁移 + 动作作废 + 签名/取代原子化）
- 每个 checkpoint 可暂停独立验证；每任务或逻辑组后提交
