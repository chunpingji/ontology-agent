# 药研分析思路工作流 —— Status 状态机 + 迁移 Action 设计（含实现现状对照）

> 版本：0.2（已对齐实现） | 日期：2026-06-22
>
> 范式来源：Palantir Foundry 本体动能层（Action Type + 对象状态机 + Automate）
>
> 对照基准：commit `b729aed`「闭合能力二/三 GAP — 多源抽取对齐 + 实时事实源推理 + 合规硬化」+ feature `002-extraction-realtime-reasoning`
>
> 关联文档：[`gap-analysis.md`](./gap-analysis.md)、[`临床药物智能辅助生产平台方案.md`](./临床药物智能辅助生产平台方案.md)
>
> 状态图例：✅ 已实现 · 🟡 部分/未接线 · ❌ 缺口

---

## 0. 一句话结论（v0.2 更新）

本文 v0.1 提出"评估实例 + `workflow_status` 状态机 + `OntologyAction` 驱动迁移"的抽象。`002` 这一轮**实现了其大部分意图，但采用了不同的架构**：

- **没有**引入 `AssessmentInstance`/`workflow_status`，**没有**用 `OntologyAction` 在运行期驱动迁移。
- 改为**结论中心（conclusion-centric）的合规/动作流水线**：`ReasoningExecution`（结论）+ `ActionExecution`（动作）+ `ElectronicSignature`（签名）+ **全局哈希链审计**。
- "状态机"是**隐式**的，分散在三个字段上：`ReasoningExecution.effective`（合规闸门）、`ReasoningExecution.superseded_by`（取代链）、`ActionExecution.status`（动作生命周期）。迁移逻辑**硬编码在 Python**，未数据化进本体库。

**净结论**：合规硬化（哈希链 ✅、QA 电子签名 ✅、Action 引擎 ✅、增量重算 ✅、报告导出 ✅）已落地；但本文的核心论点——**把"思路迁移"做成本体库里可维护的数据（Palantir Action Type 范式）——未被采纳**。另有 **3 处接线缺口**（§4）使结论流水线尚不能端到端自举。

---

## 1. 实现现状对照（as-built reconciliation）

| 需求 | v0.1 意图 | 实测状态 | 代码证据 |
|---|---|---|---|
| **R-WF1** 持久化、有状态的评估对象 | `AssessmentInstance` | 🟡 以 `ReasoningExecution`（结论）承载状态，但**非"评估实例"语义**，且初始落库未接线（见 G1） | `models/reasoning.py:20` |
| **R-WF2** 状态机推进、非法跃迁被拒 | `workflow_status` 显式状态机 | 🟡 隐式状态机（`effective`/`superseded_by`/`ActionExecution.status`）；仅签名处有 409 守卫，动作 PATCH 无 from-status 守卫 | `compliance.py:135`、`actions.py:55` |
| **R-WF3** 迁移定义存于本体库、可维护可发布 | `OntologyAction` 驱动迁移 | ❌ **未采纳**：迁移硬编码于 `action_engine`/`compliance`/`incremental`；`OntologyAction` 运行期仍无人读取 | `services/reasoning/action_engine.py` |
| **R-WF4** 角色门禁 | require_role | ✅ `ROLE_SENIOR_ANALYST`/`ROLE_QA`；动作流转限维护者/QA，operator 只读 | `actions.py:23`、`compliance.py:25` |
| **R-WF5** 强制 QA 电子签名（Part 11） | 签名行 | 🟡 签名机制 ✅（重认证→绑定结论→生效→解抑动作）；但**闸门触发未接线**：`requires_signature` 无人据风险置 True（见 G2） | `compliance.py:125`、`models/reasoning.py:36` |
| **R-WF6** 追加式哈希链、可验真 | 每实例链 | ✅ **全局单链**（`seq`/`prev_hash`/`entry_hash`，单写路径 + `verify` 定位断裂） | `services/audit.py`、`models/reasoning.py:47` |
| **R-WF7** 复用推理内核、不重写规则 | 副作用调用 | ✅ `incremental` 调 `run_assessment`，规则零改动 | `incremental.py:64` |
| **R-WF8** 事实变更自动召回/重算 | Automate | 🟡 物化→发事件 ✅、子图增量重算 + 取代链 ✅；但**事件→重算未自动订阅**，仅手动 API 触发（见 G3） | `materializer.py:74`、`events.py`、`reasoning.py:97` |
| **R-WF9** 生成 RiskAssessmentReport | 导出器 | ✅ JSON + PDF 报告 | `services/reporting/risk_report.py`、`api/reports.py` |

> 已注册路由（`main.py:91-99`）：`ontology / entities / reasoning / extraction / kg / integration / actions / reports / compliance`。**无 `assessments` 路由**——印证 v0.1 的"评估实例工作流对象"未建。

---

## 2. 实测架构（as-built）—— 结论中心的合规/动作流水线

### 2.1 三个核心对象与其（隐式）生命周期

**① `ReasoningExecution`（结论，`reasoning_executions`）** — `models/reasoning.py:20`

承载一次推理的结论 + 合规/实时字段：`requires_signature`、`effective`、`signature_id`、`affected_subgraph`、`superseded_by`(self-FK)。隐式状态：

```
创建 ──┬─ requires_signature=False ──▶ effective=True ─────────────┐
       └─ requires_signature=True  ──▶ effective=False（待签）──签名──▶ effective=True
                                                                     │
                            事实变更增量重算：新建刷新结论，旧结论 effective=False + superseded_by=新id
```

**② `ActionExecution`（动作，`action_execution`）** — `models/reasoning.py:65`

结论生效后由 `ActionEngine.orchestrate` 编排（`action_engine.py:39`）。`_plan` 由结论 `results` 推导动作类型：

| 结论标志 | 编排动作 |
|---|---|
| `requires_dedication` | `dedication_work_order` + `alert` |
| `requires_inactivation` | `inactivation_task` |
| `requires_recleaning` | `recleaning_task` |
| `schedule_conflict` | `schedule_block` + `advisory_writeback` |

`status` 生命周期：`suppressed`（结论未生效时，**不触发对外动作**）→ 签名后由 `compliance.sign` 批量解除为 `pending` → 经 `PATCH /api/actions/{id}` 人工流转。`writeback_status`：`pending` → `accepted`/`not_accepted`（`not_accepted` **不**视为 failed，仅建议性回写，原则 II）。

**③ `ElectronicSignature`（签名，`electronic_signatures`）** — `models/reasoning.py:86`

`signer/signer_role/meaning/reauth_verified/signed_at/audit_seq`。经 `POST /api/compliance/signatures`：重认证（`settings.qa_reauth_secret` 占位）→ 绑定 `conclusion.signature_id` + `effective=True` → 解除被抑制动作 → 写审计链。

### 2.2 全局哈希链审计 — `services/audit.py`（R-WF6 ✅）

`append()` 单写路径：`entry_hash = SHA-256(prev_hash ‖ 规范化记录)`，`seq` 单调唯一；`verify()` 顺序重算定位首个断裂。全链路事件入链：`integration.materialize` / `reasoning.recompute` / `action.orchestrate` / `action.transition` / `action.writeback` / `compliance.sign`。

> **设计决策落定**：v0.1 开放问题 #4（哈希链范围）→ 采用**全局单链**，非每实例链。

### 2.3 实时事实流（R-WF8 🟡）

`poller` 轮询 → `FactMaterializer.run_sync` 物化为 A-Box 影子 + `fact_event_bus.publish`（携带 `affected_subgraph`）→ **（缺订阅）** → `incremental.recompute_subgraph` 仅重算相交且生效的结论，新建刷新结论并取代旧结论，再 `orchestrate` 动作。

### 2.4 主要 API

| 路由 | 端点 | 作用 |
|---|---|---|
| `/api/reasoning` | `/assess`（无状态）、`/incremental`、`/conclusions/{id}`、`/conclusions/{id}/trace`、`/rules`、`/calculate/{pde,maco}` | 推理 + 增量重算 + 规则链溯源 |
| `/api/actions` | `GET ``、`PATCH /{id}`、`POST /{id}/writeback-result` | 动作列表/流转/回写反馈 |
| `/api/compliance` | `GET /audit`、`GET /audit/verify`、`GET /signatures/pending`、`POST /signatures` | 审计链查询/验真 + QA 签名 |
| `/api/reports` | `GET /{conclusion_id}`、`GET /{conclusion_id}/pdf` | 风险评估报告 JSON/PDF |

前端：`reasoning/qa-signature-dialog.tsx`、`integration/realtime-inference-panel.tsx` 已建（无 v0.1 设想的 `assessment-workflow-panel.tsx`）。

---

## 3. 设计意图 vs 实测：对齐与分歧

**对齐（实测以不同形态达成了意图）**：合规闸门（`effective` ≈ 我的 `QASigned` 关口）、动作抑制/释放（`suppressed→pending` ≈ 签名前不外发副作用）、取代链（`superseded_by` ≈ 我的 `Recall` 重算）、哈希链 + 电子签名 + 报告，**意图基本达成**。

**核心分歧（本文论点未被采纳）**：

| 维度 | v0.1 提案 | 实测 |
|---|---|---|
| 迁移定义 | **数据**（`OntologyAction` 行，可维护/发布回 TTL） | **代码**（Python 硬编码于 `action_engine`/`compliance`/`incremental`） |
| 状态机 | 单对象 `workflow_status` 显式状态集 | 三字段隐式状态，无统一状态枚举/校验 |
| 顺序/守卫 | `from_status` + `guard_expr` 数据驱动 | 各处 if 分支 |
| 思路演进 | 增删一条 Action 定义即可 | 改 Python + 发版 |

> 也就是说：gap-analysis 的**合规/Action 引擎缺口已闭合**，但"**把分析思路工作流沉淀为本体库可治理资产**"这个最初动机**仍未实现**——思路依然活在代码里。

---

## 4. 仍存在的接线缺口（高优先，有代码证据）

| ID | 缺口 | 证据 | 后果 |
|---|---|---|---|
| **G1** | **结论无初始落库路径**：`ReasoningExecution(...)` 仅在 `incremental.recompute_subgraph` 中实例化（且只从**已存在**的受影响结论刷新而来）；`POST /api/reasoning/assess` 仍**无状态**、不持久化 | `grep ReasoningExecution(` 仅命中 `incremental.py:74`；`reasoning.py:43` `/assess` 只返回不落库 | 整条结论流水线**无法自举**——没有首条结论，增量重算/动作/签名/报告均无源头 |
| **G2** | **强制 QA 闸门未自动 arm**：`requires_signature` 除模型默认 `False` 与增量沿用旧值外，**无任何据风险（HighRisk/专用化/青霉素）置 True 的逻辑** | `grep requires_signature` 无置 True 点 | 高风险结论不会自动进入待签态，R-WF5 闸门形同虚设 |
| **G3** | **事实事件→增量重算未自动订阅**：`fact_event_bus` 有 `subscribe()` 但**无订阅者**调用 `recompute_subgraph`；重算只能手动 `POST /api/reasoning/incremental` | `grep subscribe/recompute_subgraph` 无 bus→recompute 桥接 | R-WF8 实为**半自动**：物化与重算之间断链，近实时（≤5s）链路未闭合 |

**最小补齐建议**：
1. **G1**：`/assess` 落 `ReasoningExecution`（持久化结论 + 据结果置 `requires_signature`）并 `orchestrate` 动作——一处即同时解决 G1+G2。
2. **G2**：在结论落库处加判定 `requires_signature = risk_level=='HighRisk' or results.get('requires_dedication') or 命中青霉素场景`。
3. **G3**：启动时 `fact_event_bus.subscribe(lambda e: recompute_subgraph(db, e['affected_subgraph'], engine))`，桥接物化→重算。

---

## 5. 设计动机（Palantir 范式，保留）

Palantir 本体分**语义层**（Object/Link/Property）与**动能层**（Action/Function）。工作流不是独立原语，由四者组合涌现：① Action Type（受治理、可审计的原子写回）；② 对象状态机（`status` + 迁移 Action，顺序由守卫数据驱动）；③ Functions（业务逻辑/编排）；④ Automate（事件触发）。

对照实测：②③ 以**硬编码**形态部分存在，①（数据化 Action）与④（自动 Automate 订阅）**未落地**。本文 §6 据此给出取舍。

---

## 6. 后续取舍（需拍板）

实测既已偏离 v0.1 的数据驱动方案，有两条路：

- **路线 A（务实·推荐先做）**：先补 §4 的 G1/G2/G3，让**现有结论中心流水线端到端跑通**，并把隐式状态固化为显式枚举 + 集中式守卫校验（一个 `ReasoningExecution.lifecycle_state` + 一张迁移合法性表），**暂不**追求数据化迁移定义。投入小、立即闭环。
- **路线 B（理想·中期）**：把迁移定义沉淀为 `OntologyAction`（Palantir Action Type 范式），由通用 `transition_engine` 解释执行——实现"思路工作流即本体库可治理资产"的最初动机。投入大，建议在路线 A 跑通、需求稳定后再做。

> 建议：**先 A 后 B**。A 解决"能不能用"，B 解决"思路能不能在本体库治理"。

---

## 附录 A：v0.1 原始提案（实例状态机方案，部分被实测取代）

> 保留以备路线 B 参考。下列设计**未被 `002` 采纳**，但其抽象（显式状态集、数据化迁移、per-transition 守卫/签名/审计）仍是路线 B 的蓝本。

- **状态集**：`Draft → Assessed → UnderReview → QASigned → Released`（+ `Rejected`），作用于一个 `AssessmentInstance`（A-Box），与 T-Box 的 `STATUS_DRAFT/IN_REVIEW/PUBLISHED` 发布生命周期正交。
- **迁移定义复用 `OntologyAction`**：`precondition.{from_status,require_fields,guard_expr}` / `postcondition.{to_status,route,edits,emit_events}` / `params.{required_role,requires_signature,regulation_ref}`，可发布回 TTL。
- **迁移引擎** `transition_engine.execute_transition(...)`：加载→状态合法性→角色门禁→守卫（受限 AST 白名单，禁 `eval`）→签名校验→路由→副作用（含 `invoke: run_assessment`）→审计上链→发事件。
- **强制 QA 分支**：`Approve` 目标态由 `risk_level=='HighRisk' or requires_dedication or 青霉素` 路由到 `QASigned`，否则 `UnderReview→Released` 直发。
- **预置 6 条迁移 Action**：`Create / RunAssessment / SubmitForReview / Approve / QASign / Release / Reject`（速查见 v0.1 历史版本）。

> 实测与本提案的字段级映射：`AssessmentInstance.workflow_status` → 拆为 `ReasoningExecution.effective` + `superseded_by` + `ActionExecution.status`；per-instance 哈希链 → 全局 `audit_log` 单链；`OntologyAction` 驱动 → 改为 Python 硬编码。

---

## 附录 B：相关代码索引

| 关注点 | 文件 |
|---|---|
| 结论/动作/签名/审计模型 | `backend/app/models/reasoning.py` |
| 哈希链单写路径 + 验真 | `backend/app/services/audit.py` |
| Action 编排引擎 | `backend/app/services/reasoning/action_engine.py` |
| 增量重算 + 取代链 | `backend/app/services/reasoning/incremental.py` |
| QA 签名 / 审计 API | `backend/app/api/compliance.py` |
| 动作流转 / 回写 API | `backend/app/api/actions.py` |
| 报告导出 | `backend/app/services/reporting/risk_report.py`、`backend/app/api/reports.py` |
| 实时物化 / 事件总线 | `backend/app/services/integration/{materializer,events,poller,aps_connector}.py` |
| 前端 | `frontend/src/components/reasoning/qa-signature-dialog.tsx`、`integration/realtime-inference-panel.tsx` |
| 迁移 | `backend/alembic/versions/0002_extraction_realtime.py` |
</content>
</invoke>
