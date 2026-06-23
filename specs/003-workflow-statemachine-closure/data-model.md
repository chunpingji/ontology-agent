# Phase 1 Data Model: 分析结论工作流状态机闭环

**Feature**: `003-workflow-statemachine-closure` | **Date**: 2026-06-22 | **Plan**: [plan.md](./plan.md)

数据库：PostgreSQL 16（库 `slpra`）。本特性以**单一 Alembic 迁移** `alembic/versions/0003_workflow_statemachine.py`（`down_revision="0002_extraction_realtime"`）落地，接既有启动迁移链（`main._run_migrations → upgrade head`），沿用 0002 的防御式风格（`sa.inspect` 守卫 + `op.add_column`）。本特性**仅扩展一列**并**新增枚举语义**，不建新表——四态状态机与动作作废都落在既有 `reasoning_executions` / `action_execution` 上。

**本特性不写回权威 TTL、无 T-Box 写入**（宪章 II 不触发）：仅持久化 A-Box 推理结论与其流转。

图例：🆕 新增 · ➕ 列扩展 · ♻️ 既有不变（引用）。

---

## 1. ➕ 扩展既有表

### 1.1 `reasoning_executions`（➕ 扩展 `models/reasoning.py`）

承载结论的**显式生命周期状态**——把 002 隐式散在 `effective`/`superseded_by` 的状态固化为单一真理来源列。

| 新增列 | 类型 | 约束/默认 | 说明 |
|---|---|---|---|
| `lifecycle_state` | `String(30)` | `not null`, index, default `"effective"` | 显式四态：`pending_signature`/`effective`/`superseded`/`rejected`（FR-014）。是结论状态的**唯一真理来源** |

> **与既有布尔的关系（向后兼容）**：`lifecycle_state` 为权威；`transition()` 在迁移时**同步**维护既有 `effective`（bool）与 `superseded_by`（self-FK），使 002 既有查询（如 `pending_signatures`、`recompute_subgraph` 的 `effective.is_(True)` 过滤）无需重写即继续正确。映射不变式：
> - `effective` ⟺ `lifecycle_state == "effective"`
> - `superseded_by IS NOT NULL` ⟺ `lifecycle_state == "superseded"`
> - `requires_signature ∧ ¬effective ∧ superseded_by IS NULL ∧ ¬rejected` ⟺ `lifecycle_state == "pending_signature"`
> - `lifecycle_state == "rejected"`：**002 布尔无法表达**，故 `lifecycle_state` 不可或缺（区分"待签"与"已拒绝"，两者 `effective=False`）。

**既有列（♻️ 不变，本特性消费）**：`requires_signature`（G2 落库时由 `risk.requires_qa_signature` 置定）、`effective`、`signature_id`、`affected_subgraph`、`superseded_by`、`results`（R2 canonical dict）、`risk_level`、`maco_*`、`scenarios_identified`、`input_params`。

#### `results`（♻️ 既有 JSON 列）canonical 形状（R2）

`/assess` 落库写入的 `results` MUST 同时满足下游两个消费者的键集，作为动作编排与报告导出的**共同数据源**：

| 键 | 消费者 | 用途 |
|---|---|---|
| `category` | `risk_report.build_report_json` | 风险分级展示 |
| `requires_dedication` | `action_engine._plan` + `risk.requires_qa_signature` + 报告 | 触发专用化工单 + 高风险判据 |
| `requires_inactivation` | `action_engine._plan` | 触发灭活任务 |
| `requires_recleaning` | `action_engine._plan` | 触发再清洁任务 |
| `schedule_conflict` | `action_engine._plan` | 触发排期阻断 + 建议性回写 |
| `contamination_scores` | 报告 | 污染评分 |
| `cfdi_scenarios` | 报告 + `risk._hazardous_scenario` | CFDI 场景 / 高危场景命中 |
| `pde` | 报告 | PDE 值 |
| `hazardous_categories` | `risk._hazardous_scenario` | 青霉素/头孢/高致敏命中标识（初始集，可迭代） |

> 形状不全会破坏 US1-AC4（落库后无需补数据即可导出报告）与 SC-002（自举）。

### 1.2 `action_execution`（➕ 语义扩展 `models/reasoning.py`）

既有表结构不变（`status` 已是 `String(20)`），**新增一个状态值** `voided`（终态）支撑取代/拒绝时的动作作废（FR-012/020）。

| 列 | 变更 | 说明 |
|---|---|---|
| `status` | ➕ 取值 `voided` | 因结论被取代或被拒绝而撤销的未完成动作（**非执行失败**，与 `failed` 区分）；终态 |

**动作状态机**（FR-003/009/012，含本特性新增 `voided` 与 from-status 守卫）：
```
suppressed ──(结论生效/签批解抑)──▶ pending ──(人工)──▶ in_progress ──▶ done
   │                                   │                                 └─▶ failed
   │ (结论被拒绝)                        │ (所属结论被取代)
   └──────────────▶ voided ◀────────────┘
                    (终态：因取代/拒绝撤销，不得再流转)
advisory_writeback.writeback_status: advised ──▶ accepted | not_accepted   (外部决定，not_accepted 不算失败，FR-019)
```
- **from-status 守卫**（R8，`patch_action`）：终态（`voided`/`done`/`failed`）MUST NOT 外迁；`suppressed` MUST NOT 直接人工流转（须先经结论生效解抑为 `pending`）。
- **作废批量**：取代（FR-012）/拒绝（FR-020）时，所属结论的**非终态**动作（`suppressed`/`pending`/`in_progress`）批量置 `voided`，每条写 `action.void` 审计，与触发事务（重算/拒绝）**原子提交**。

---

## 2. 结论生命周期状态机（核心交付）

### 2.1 四态与终态（FR-014）

| 状态 | 含义 | 终态 | 对外动作可派发 |
|---|---|---|---|
| `pending_signature`（待签） | 高风险结论落库后等待 QA 签批 | 否 | ❌ 全抑制 |
| `effective`（生效） | 已生效（不需签批直接生效，或经 QA 签批生效） | 否 | ✅ 可派发 |
| `superseded`（已取代） | 被增量重算的刷新结论取代 | **是** | ❌（动作已作废） |
| `rejected`（已拒绝） | QA 拒绝的待签结论 | **是** | ❌（动作已作废） |

### 2.2 合法迁移表（FR-015 —— 单一合法性来源）

`services/reasoning/lifecycle.py::LEGAL_TRANSITIONS` 是**唯一**迁移合法性来源，所有入口（落库/签批/拒绝/取代）经 `transition()` 校验：

| # | From | To | 触发入口 | 守卫/前置 | 审计动作 |
|---|---|---|---|---|---|
| T1 | （落库） | `effective` | `/assess` 落库（不需签批） | `requires_qa_signature == False` | `reasoning.persist` |
| T2 | （落库） | `pending_signature` | `/assess` 落库（需签批） | `requires_qa_signature == True` | `reasoning.persist` |
| T3 | `pending_signature` | `effective` | `POST /api/compliance/signatures`（QA 签批） | 有效 Part 11 重认证；结论非 `superseded`/`rejected` | `compliance.sign` |
| T4 | `pending_signature` | `rejected` | `POST /api/compliance/reject`（QA 拒绝） | 角色 `qa`；结论处于 `pending_signature` | `compliance.reject` |
| T5 | `effective` | `superseded` | `incremental.recompute_subgraph`（增量取代） | 子图相交；结论处于 `effective` | `reasoning.recompute` |

**非法迁移**（FR-016，一律拒绝 + 不改状态 + 返回可操作原因）示例：
- `pending_signature → effective` **绕过签批**（无有效签名）：拒绝。
- `superseded → *` / `rejected → *`（自终态外迁，如对已取代结论再签批、对已拒绝结论再签批/再拒绝）：拒绝。
- `effective → effective` 经签批（重复签名）：拒绝（`409`，唯一绑定）。
- 越过 `pending_signature` 直接 `落库 → effective` 但 `requires_signature == True`：由 T1/T2 据判据分流，不存在该非法路径。

```
                ┌────────────────────────────┐
   /assess ─────┤ requires_qa_signature?       │
                └───────┬──────────────┬───────┘
                   True │              │ False
                        ▼              ▼
              pending_signature ──▶ effective ──▶ superseded (终态)
                   │   │            (T5 增量取代)
            (T3 签批)│   │(T4 拒绝)
                   ▼   ▼
              effective  rejected (终态)
```

### 2.3 `transition()` 契约（`services/reasoning/lifecycle.py`）

```
transition(db, conclusion, to_state, *, actor, reason=None) -> None
  1. from_state = conclusion.lifecycle_state
  2. if (from_state, to_state) ∉ LEGAL_TRANSITIONS: raise IllegalTransition  # 不改任何状态（FR-016）
  3. conclusion.lifecycle_state = to_state
  4. 同步既有布尔：effective / superseded_by（向后兼容，§1.1 不变式）
  5. audit.append(db, action="reasoning.transition", actor=actor,
                  entity=conclusion.id, details={from,to,reason}, commit=False)
  # 调用方负责事务提交（便于与动作作废/签名写入同事务原子化）
```

> `transition()` **不**自身 commit——由调用入口统一提交，保证"迁移 + 动作作废 + 签名/取代"原子。落库（T1/T2）因 `from` 为初始无前态，以"创建即置态"形式经同一合法集校验。

---

## 3. 实体关系总览（本特性视角）

```
reasoning_executions (➕ lifecycle_state ◀── 唯一真理来源; ♻️ effective/superseded_by 同步维护)
    │
    ├─ 落库(T1/T2) ── risk.requires_qa_signature(results,risk_level) ─▶ 初始态
    ├─1──* action_execution (➕ voided 终态)   ── suppressed↔pending↔…↔voided
    ├─1──1 electronic_signatures (♻️ Part 11 绑定) ── T3 签批
    └─ superseded_by ─▶ reasoning_executions (♻️ 取代链, T5)

fact_event_bus (♻️ events.py, 既有零订阅) ◀── 🆕 recompute_subscriber 注册(main.lifespan)
    ▲                                              │
    │ publish(携 affected_subgraph)                ▼
materializer.run_sync (♻️ poll/webhook 公共缝)   incremental.recompute_subgraph
                                                   └─ T5 取代 + 旧动作 voided(FR-012)

audit_log (♻️ 全局追加式哈希链) ◀── reasoning.persist / reasoning.transition /
        compliance.sign / compliance.reject / reasoning.recompute / action.void / action.transition
```

---

## 4. 回填规则（Alembic `0003`，R10）

升级时对 `reasoning_executions` 既有行计算 `lifecycle_state`（优先级自上而下，命中即停）：

| 优先级 | 条件（既有列） | 回填 `lifecycle_state` |
|---|---|---|
| 1 | `superseded_by IS NOT NULL` | `superseded` |
| 2 | `effective IS TRUE` | `effective` |
| 3 | `requires_signature IS TRUE`（且 ¬effective ∧ superseded_by IS NULL） | `pending_signature` |
| 4 | 其余（历史无标记） | `effective`（保守：存量结论视为已生效） |

> 002 布尔无法表达 `rejected`，故存量行不会被回填为 `rejected`（升级前不存在"已拒绝"语义，符合预期）。迁移经 `sa.inspect` 守卫保证可重入（与 0002 一致）。

---

## 5. 校验规则（来自需求）

- **VR-1**（FR-001/014）：每条经 `/assess` 落库的结论 MUST 有非空 `lifecycle_state` ∈ 四态，且与 `requires_signature` 自洽（T1/T2 分流）。
- **VR-2**（FR-015/016）：任一状态迁移 MUST 经 `lifecycle.transition` 校验 `LEGAL_TRANSITIONS`；`(from,to)` 不在集内即拒绝、**不改任何状态**、附原因。
- **VR-3**（FR-006/009）：`lifecycle_state == "pending_signature"` 的结论，其全部 `action_execution` MUST 为 `suppressed`，且无任何途径在无有效 QA 签名时令其 `effective` 或派发其动作。
- **VR-4**（FR-008/T3）：QA 签批 MUST 重认证（用户名+密码），成功后经 T3 置 `effective`、不可分割回填 `signature_id`、解抑动作（`suppressed→pending`）、写审计；每条 `conclusion_id` 至多一条有效签名（重复签名 `409`）。
- **VR-5**（FR-020/T4）：QA 拒绝 MUST 仅作用于 `pending_signature`；成功后经 T4 置 `rejected`（终态）、其非终态动作置 `voided`、写 `compliance.reject` 审计；`rejected` MUST NOT 再签批/再拒绝。
- **VR-6**（FR-011/T5）：自动重算 MUST 仅作用于 `lifecycle_state == "effective"` 且子图相交的结论；`pending_signature`/`superseded`/`rejected` 及不相交者 MUST NOT 被重算覆盖。
- **VR-7**（FR-012）：取代发生时，被取代旧结论的**非终态**动作 MUST 置 `voided`，作废与取代**同事务**，每条写 `action.void` 审计。
- **VR-8**（FR-018）：落库/迁移/签批/拒绝/派发/作废/取代 MUST 各写一条审计链记录（经单写路径 `audit.py`）；链验真定位首个断裂 `seq`。
- **VR-9**（FR-013）：物化发布事件 → 订阅者重算的端到端时延 MUST ≤ 5 秒。
- **VR-10**（FR-019）：`advisory_writeback.writeback_status == "not_accepted"` MUST NOT 置动作 `failed`（建议性、非权威）。
