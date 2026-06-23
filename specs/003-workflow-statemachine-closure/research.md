# Phase 0 Research: 分析结论工作流状态机闭环

**Feature**: `003-workflow-statemachine-closure` | **Date**: 2026-06-22 | **Plan**: [plan.md](./plan.md)

本特性无 NEEDS CLARIFICATION（4 项已在 `/speckit-clarify` 解决，见 spec.md §Clarifications）。Phase 0 的研究对象是**如何在既有 002 as-built 代码上以最小增量接通 G1/G2/G3 并显式化状态机**。每项决策均以"既有代码证据 → 决策 → 理由 → 备选/排除"组织。所有决策遵循宪章 V（最小复杂度与复用）。

---

## R1 — G1：结论的初始落库路径

**既有证据**：`api/reasoning.py::/assess`（line 43）构造 `AssessmentResponse` 后**直接返回，不持久化、无角色门禁**。唯一实例化 `ReasoningExecution` 的入口在 `incremental.recompute_subgraph`，但它只能从**已存在**的生效结论刷新而来（`effective.is_(True)` 过滤）——故"第一条结论"无源头。`ActionEngine.orchestrate` 已能据 `ReasoningExecution.results` 编排动作（`_plan(results)`）。

**Decision**：改造 `/assess`——在返回前持久化一条 `ReasoningExecution`（写 `results`/`risk_level`/`maco_*`/`scenarios_identified`/`input_params`），随即调用 `ActionEngine.orchestrate(conclusion)` 编排动作，并把新建结论的标识与状态回填进响应。`/assess` 加 `require_role(ROLE_SENIOR_ANALYST)` 门禁。

**Rationale**：`/assess` 是前端既有的评估入口，复用它即让"评估即落库"对用户透明；新建独立端点会分叉前端调用并留下"评估不落库"的旧路径，违反 G1 的自举目标与宪章 V。落库 + 编排 + 状态置定三步同事务，保证 SC-001（丢失率 0%）。

**Alternatives considered**：
- *新增 `POST /api/conclusions`（落库专用）*：排除——前端需改造双调用，且 `/assess` 旧路径仍可绕过落库，G1 缺口未真正闭合。
- *在前端落库*：排除——违反审计单写与服务端权威。

**关联**：FR-001/002/003/004、SC-001/002。门禁变更（`/assess` 现需 `X-User`/`X-Role`）记入 contracts 的"破坏性变更"提示。

---

## R2 — 持久化 `results` 字典的规范形状

**既有证据**：`ActionEngine._plan(results)` 读取 `requires_dedication`/`requires_inactivation`/`requires_recleaning`/`schedule_conflict` 等键决定编排；`reports/risk_report.build_report_json` 读取 `category`/`requires_dedication`/`contamination_scores`/`cfdi_scenarios`/`pde` 等键组装报告。两者都消费同一条结论的 `results`。

**Decision**：`/assess` 落库时写入的 `results` 字典 MUST 同时满足 `_plan` 与 `build_report_json` 的键集——即把 `AssessmentResponse` 推理出的判定（`requires_dedication`、命中场景、MACO、污染评分、CFDI 场景等）规范化为一个**单一 canonical results dict**，作为下游动作编排与报告导出的共同数据源。

**Rationale**：US1-AC4 要求结论落库后"无需中间补数据即可导出报告"；若 `results` 形状与 `build_report_json` 不符，报告导出会缺字段。统一形状是自举闭环（SC-002）的数据契约前提。

**Alternatives considered**：
- *分别为动作与报告存两份结构*：排除——重复数据、易漂移，违反宪章 V。
- *报告导出时再回算*：排除——回算依赖评估期上下文，落库后已不可得，破坏可追溯。

**关联**：FR-001/004、US1-AC4、SC-002。具体键清单在 data-model.md 固化。

---

## R3 — G2：强制 QA 闸门的自动 arm 判据

**既有证据**：`compliance.sign_conclusion` 的门禁不变式依赖 `requires_signature`；`pending_signatures` 过滤 `requires_signature=True ∧ effective=False ∧ superseded_by=None`。但**无任何代码据风险把 `requires_signature` 置 True**——模型默认 `False`，增量重算沿用旧值。高风险结论因此不会自动入待签。

**Decision**：新建 `services/reasoning/risk.py::requires_qa_signature(results, risk_level) -> bool`，判据 =
`risk_level == "HighRisk"` **OR** `results.get("requires_dedication")` **OR** `_hazardous_scenario(results)`，其中 `_hazardous_scenario` 命中青霉素/头孢/高致敏等高危场景标识。`/assess` 落库时调用它置 `requires_signature`，并据此走 R5 的状态机决定初始态（`待签` vs `生效`）。

**Rationale**：判据直接取自 spec 的高风险初始集（Assumptions）与 FR-005；抽为纯函数便于单测（US2 Independent Test 需构造命中/不命中两类）并在增量重算复用。判据可后续细化而不动调用点。

**Alternatives considered**：
- *把判据散写进 `/assess` 内联 if*：排除——不可复用于增量重算、难单测，且与"集中守卫"精神相悖。
- *从本体规则推导 requires_signature*：排除——属路线 B 数据化范畴，超出本特性。

**关联**：FR-005/006/007、SC-003。高危场景标识集在 data-model.md 列明，标注"初始集，可迭代"。

---

## R4 — G3：事实事件 → 增量重算的订阅桥接

**既有证据**：`services/integration/events.py` 的 `fact_event_bus = FactEventBus()` 已实现 `publish`（构造携 `affected_subgraph` 的事件并调所有订阅者）与 `subscribe(cb)`，但**零订阅者**。`materializer.run_sync`（line 74）对每条已应用变更调 `fact_event_bus.publish(connector_id, change)`，**位于最终 `self.db.commit()`（line 89）之前**。poll 与 webhook 两条路径都汇入 `run_sync`。`incremental.recompute_subgraph(db, affected_subgraph, engine)` 已实现完整重算 + 取代。

**Decision（D-A，采纳）**：新建 `services/reasoning/recompute_subscriber.py::make_recompute_subscriber()`，返回一个回调；回调内**开新 `SessionLocal`** 并用全局 `ontology_engine`，调用 `recompute_subgraph(session, event.affected_subgraph, engine)`。在 `main.py::lifespan` 启动时 `fact_event_bus.subscribe(make_recompute_subscriber())`，并加**幂等守卫**（避免重复注册）。订阅者工厂同时暴露**可直接调用的入口**供单测（不经 bus）。

**Rationale**：bus 订阅是 poll 与 webhook 的**唯一公共缝**（两路径都过 `run_sync→publish`），一处注册即覆盖全部事实源，符合 FR-010"自动、无人工触发"。新开 Session 隔离重算事务与物化事务，规避 publish 早于 materializer commit 的可见性问题（见下"事务性注记"）。工厂模式便于 US3 单测直接驱动回调。

**事务性注记**：publish 当前发生在 `run_sync` 最终 commit **之前**。订阅者新开的 Session 可能读不到尚未提交的物化变更。**缓解**：实现期将订阅触发点对齐到"物化提交后"——或令 materializer 在 commit 后再 publish，或回调读取 `event.affected_subgraph`（子图标识由事件自带，不依赖未提交行）后重算生效结论（结论表已提交）。data-model.md 与 contracts 标注此不变式；本特性以"子图标识自带、重算只读已提交结论表"为正确性基线。

**Decision（D-B，备选/记录）**：由 `materializer.run_sync` 在最终 commit **之后**直接调用 `recompute_subgraph`。排除为默认——会把推理依赖耦合进物化层、跨越 integration↔reasoning 模块边界，且失去 bus 的多订阅者扩展性；但作为"若 bus 注册带来生命周期复杂度"的回退方案保留。

**去抖动（future）**：高频事实变更可能触发密集重算；本特性不做去抖/合并窗口，记为后续优化（spec 未要求，YAGNI）。

**关联**：FR-010/011/012/013、SC-004。

---

## R5 — 显式生命周期状态机与单一守卫

**既有证据**：结论"状态"目前**隐式**散在三处——`effective`（bool）、`superseded_by`（self-FK）、`ActionExecution.status`；`requires_signature ∧ !effective ∧ superseded_by=None` 拼出"待签"，但**无法区分 `rejected` 与 `pending_signature`**（两者 `effective=False`、`superseded_by=None`）。各入口（sign/incremental）各自 `if` 自守，非法迁移不被一致拒绝。

**Decision**：
1. `models/reasoning.py` 增列 `lifecycle_state: Mapped[str]`（`String(30)`，索引），值域 `pending_signature`/`effective`/`superseded`/`rejected`。
2. 新建 `services/reasoning/lifecycle.py`：`LifecycleState` 枚举 + `LEGAL_TRANSITIONS`（集合）+ `transition(db, conclusion, to_state, *, actor, reason) -> None`。`transition` MUST：校验 `(from, to) ∈ LEGAL_TRANSITIONS`（否则 raise 拒绝、不改任何状态）、更新 `lifecycle_state` **同时**维护既有布尔（`effective`/`superseded_by`，保持向后兼容与既有查询）、写审计链 `reasoning.transition`。
3. 重构落库（R1）、`compliance.sign`、新 `compliance.reject`（R7）、`incremental` 取代（R6）**全部经 `transition()`**，杜绝"某入口有守卫、另一入口无"。

**合法迁移集**（FR-015）：`落库→effective`（不需签批）、`落库→pending_signature`（需签批）、`pending_signature→effective`（QA 签批）、`pending_signature→rejected`（QA 拒绝）、`effective→superseded`（增量取代）。`superseded`/`rejected` 为终态，任何外迁非法。

**Rationale**：单独的 `lifecycle_state` 列是唯一能区分四态（尤其 `rejected` vs `pending_signature`）的真理来源，满足 FR-014；集中 `transition()` 是 FR-015"单一合法性来源、多入口判定一致"的直接实现；同步维护旧布尔避免改写所有既有读路径（最小侵入，宪章 V）。

**Alternatives considered**：
- *继续用布尔组合推断状态*：排除——无法表达 `rejected`，且守卫分散（FR-014/015/016 不可达）。
- *引入状态机库（如 `transitions`）*：排除——新依赖，违反宪章 V；五态五迁的规模用一个枚举集 + 一个函数足矣（YAGNI）。

**关联**：FR-014/015/016、SC-005、US4。

---

## R6 — 取代时旧动作的自动作废（Q3）

**既有证据**：`incremental.recompute_subgraph` 置 `old.superseded_by=new.id`、`old.effective=False`、写 `reasoning.recompute` 审计，但**未处置旧结论已解抑的 `ActionExecution`**——它们可能停在 `pending`/`in_progress`，会按过时结论继续被人工流转。`ActionExecution.status` 当前无 `voided` 值。

**Decision**：`recompute_subgraph` 中，旧结论 `effective→superseded` 经 `lifecycle.transition` 后，将其**非终态**（`pending`/`in_progress` 等、非 `done`/`failed`）`ActionExecution` 批量置 `voided`，每条写 `action.void` 审计（经 `audit.append(..., commit=False)` 批量、随重算事务一并提交）。`ActionExecution` 状态机增 `voided` 终态。

**Rationale**：FR-012 明确"被取代旧结论中未完成动作 MUST 随取代自动作废、作废入审计链"；`voided` 与 `failed` 语义不同（非执行失败，而是因取代而撤销），单列终态便于审计区分。批量 `commit=False` 保证取代与作废**同事务原子**。

**关联**：FR-012、US3-AC4、edge "取代作废动作"。

---

## R7 — QA 拒绝路径（Q2，新端点）

**既有证据**：`api/compliance.py` 有 `sign_conclusion` 但**无 reject 端点**；`pending_signatures` 仅按布尔过滤。模型无法表达"已拒绝"。

**Decision**：新增 `POST /api/compliance/reject`（`require_role(ROLE_QA)`）：对一条 `pending_signature` 结论，经 `lifecycle.transition(→rejected)`，将其全部**被抑制**（`suppressed`）动作置 `voided`，写 `compliance.reject` 审计（含签名人/角色/时间/理由）。`rejected` 为终态——再签批/再拒绝返回 `409`。`pending_signatures` 列表改按 `lifecycle_state=='pending_signature'` 过滤（替代旧布尔组合，确保 `rejected` 不再出现在待签列表）。

**Rationale**：FR-020 要求拒绝 → `rejected` 终态 + 作废动作 + 审计 + 不可再签；reject 与 sign 对称，复用同一 `transition` 守卫保证多入口一致（FR-015）。纠正路径为重新评估（R1 落库新结论）。

**Alternatives considered**：
- *复用 sign 端点加 `decision` 参数*：排除——签名（Part 11 重认证）与拒绝语义/审计动作不同，合并降低契约清晰度。

**关联**：FR-020、US2-AC5、edge "QA 拒绝"。

---

## R8 — 动作派发的 from-status 守卫

**既有证据**：`api/actions.py::patch_action`（line 44）`a.status = req.status`**无 from-status 守卫**——任意态可跳转任意态，且未拦截"结论未生效时派发动作"。

**Decision**：`patch_action` 增轻量 from-status 守卫：拒绝从终态（`voided`/`done`/`failed`）外迁、拒绝把 `suppressed` 动作直接流转（须先经结论生效解抑为 `pending`）。守卫逻辑与结论 `lifecycle` 解耦（动作有自己的小状态机），但共享"非法即拒绝、不改状态、写审计"的一致风格。

**Rationale**：FR-003/009 要求结论未生效前动作不可派发、edge"动作早派发防护"要求拒绝早派发；当前无守卫是合规漏洞。动作状态机简单（`suppressed→pending→in_progress→done/failed`，旁加 `voided` 终态），单独守卫即可，不必并入结论 `lifecycle.py`。

**关联**：FR-003/009、edge "动作早派发防护"。

---

## R9 — 自动重算仅作用于生效结论（Q4）

**既有证据**：`recompute_subgraph` 已过滤 `effective.is_(True) AND superseded_by.is_(None)`——天然跳过待签/已失效结论。

**Decision**：保留该过滤并**显式化**——以 `lifecycle_state == 'effective'` 表达（与新状态机口径一致），并补一条 US3 测试断言"命中待签结论的事实变更不触发其重算/取代"。

**Rationale**：FR-011 要求"仅重算生效结论；待签不被自动覆盖"。既有过滤已满足，但需随状态机口径显式化并加测试固化（002 缺 `recompute_subgraph` 覆盖测试）。

**关联**：FR-011、US3-AC2、edge "重算遇待签"。

---

## R10 — Alembic 迁移策略

**既有证据**：迁移头 `0002_extraction_realtime`（`down_revision="0001_ontology_meta"`）采用**防御式**模式：`Base.metadata.create_all(checkfirst=True)` + 经 `sa.inspect` 守卫的 `op.add_column`，以 `_FEATURE_TABLES`/`_COLUMN_EXT` 字典驱动。启动经 `main._run_migrations → upgrade head`。

**Decision**：新建 `alembic/versions/0003_workflow_statemachine.py`（`revision="0003_workflow_statemachine"`，`down_revision="0002_extraction_realtime"`），沿用 0002 防御式风格：经 `sa.inspect` 守卫 `op.add_column('reasoning_executions', lifecycle_state String(30))` + 建索引。**回填**既有行：`superseded_by IS NOT NULL → superseded`；否则 `effective IS TRUE → effective`；否则 `requires_signature IS TRUE → pending_signature`；其余 `→ effective`（保守：历史无标记者视为已生效）。

**Rationale**：与既有迁移链与风格一致（宪章 IV 质量门禁要求结构变更经 Alembic）；防御式 `inspect` 守卫保证可重入（与 0002 一致）；回填规则由旧布尔语义映射到新四态，保证升级后存量结论状态自洽。

**Alternatives considered**：
- *运行期惰性回填*：排除——读路径需到处兜底 `None`，复杂且违反"单一真理来源"。

**关联**：data-model.md §回填、plan.md Storage。

---

## 决策汇总

| ID | 决策 | 主要 FR | 落点 |
|---|---|---|---|
| R1 | `/assess` 落库 + orchestrate + 角色门禁 | FR-001~004 | `api/reasoning.py` |
| R2 | canonical `results` 字典（动作+报告共用） | FR-001/004 | `api/reasoning.py`、data-model |
| R3 | `risk.requires_qa_signature()` 高风险判据 | FR-005~007 | `services/reasoning/risk.py` |
| R4 | `recompute_subscriber` 注册到 `fact_event_bus`（D-A） | FR-010/013 | `recompute_subscriber.py`、`main.py` |
| R5 | `lifecycle_state` 列 + `lifecycle.transition()` 单一守卫 | FR-014~016 | `models/reasoning.py`、`lifecycle.py` |
| R6 | 取代时旧动作 `voided` + 审计 | FR-012 | `incremental.py` |
| R7 | `POST /api/compliance/reject`（QA 拒绝→终态） | FR-020 | `api/compliance.py` |
| R8 | `patch_action` from-status 守卫 + `voided` | FR-003/009 | `api/actions.py` |
| R9 | 自动重算仅 `effective`（显式化 + 测试） | FR-011 | `incremental.py`、测试 |
| R10 | `0003` 迁移 + 四态回填 | — | `alembic/versions/` |

**Output**: 所有设计决策已解析，无遗留 NEEDS CLARIFICATION。进入 Phase 1。
