# Contract: 显式生命周期守卫 + QA 拒绝 + 事实变更自动重算（P4/G3/FR-020）

**Feature**: `003-workflow-statemachine-closure` | Base: `/api/compliance` · `/api/reasoning` | 覆盖 FR-010~016、FR-020

本契约定义显式状态机的迁移守卫、新增的 QA 拒绝端点、取代/拒绝时的动作作废，以及事实变更 → 自动重算的**系统行为契约**（G3 无新端点，为进程内订阅）。鉴权头贯穿全 API。

---

## 1. 单一迁移合法性来源（FR-015，`services/reasoning/lifecycle.py`）

所有入口经 `transition(db, conclusion, to_state, *, actor, reason)` 校验同一 `LEGAL_TRANSITIONS`：

| # | From → To | 入口 | 守卫 | 审计动作 |
|---|---|---|---|---|
| T1 | （落库）→ `effective` | `/assess`（不需签批） | `requires_qa_signature == False` | `reasoning.persist` |
| T2 | （落库）→ `pending_signature` | `/assess`（需签批） | `requires_qa_signature == True` | `reasoning.persist` |
| T3 | `pending_signature` → `effective` | `POST /signatures`（签批） | Part 11 重认证；非终态 | `compliance.sign` |
| T4 | `pending_signature` → `rejected` | `POST /reject`（拒绝） | 角色 qa；处于待签 | `compliance.reject` |
| T5 | `effective` → `superseded` | 增量重算 | 子图相交；处于生效 | `reasoning.recompute` |

**非法迁移**（FR-016）：`(from,to) ∉ LEGAL_TRANSITIONS` → 拒绝、**不改任何状态**、返回可操作原因。终态 `superseded`/`rejected` 任何外迁非法。**多入口判定一致**——不存在某入口有守卫、另一入口无（US4-AC3）。

---

## 2. POST `/api/compliance/reject` — QA 拒绝待签结论（🆕，qa）

QA 复核员拒绝一条 `pending_signature` 结论（FR-020）。

**Request**：
```json
{
  "conclusion_id": "uuid",
  "username": "qa01",
  "password": "••••••",
  "reason": "数据不足，需补充灭活验证"
}
```

**行为**（MUST，单事务原子）：
1. 校验角色 `qa`；结论当前 `lifecycle_state == "pending_signature"`（否则 T4 非法 → 拒绝）。
2. 重认证（用户名+密码，Part 11 同签批口径）。
3. 经 `lifecycle.transition(→rejected)`（T4），置 `rejected` 终态。
4. 该结论全部**被抑制**动作（`suppressed`）置 `voided`，每条写 `action.void` 审计。
5. 写 `compliance.reject` 审计（含 signer/role/signed_at/reason）。

**Response `201`**：
```json
{ "conclusion_id": "uuid", "lifecycle_state": "rejected", "voided_actions": 2, "rejected_at": "..." }
```

**错误**：`401`（重认证失败）、`403`（非 qa）、`404`（结论不存在）、`409`（结论非 `pending_signature`——已生效/已取代/已拒绝，附当前态）。

> **纠正路径**：`rejected` 为终态、MUST NOT 再签批/再拒绝（`409`）；须重新 `/assess` 产生新结论（spec edge "QA 拒绝"）。

---

## 3. GET `/api/compliance/signatures/pending` — 待签列表（♻️ 过滤口径变更，qa）

**变更**：过滤条件由 002 的布尔组合（`requires_signature=True ∧ effective=False ∧ superseded_by=None`）改为 **`lifecycle_state == "pending_signature"`**，确保 `rejected` 结论**不再出现**在待签列表（002 布尔无法区分 rejected/pending）。

---

## 4. POST `/api/compliance/signatures` — QA 签批（♻️ 经守卫重构，qa）

行为不变（Part 11 重认证 + 绑定 + 解抑 + 审计），但**改经 `lifecycle.transition(→effective)`（T3）**统一守卫：对 `superseded`/`rejected` 结论签批 → T3 非法 → `409`（spec edge "签批竞态"：对已取代待签结论签批被拒，提示已取代）。

---

## 5. 事实变更 → 自动重算（G3，系统行为契约，无新端点）

**触发链**（FR-010/013，无人工触发）：
```
物化 materializer.run_sync ──publish(携 affected_subgraph)──▶ fact_event_bus
                                                                   │
                            🆕 recompute_subscriber (main.lifespan 注册) ◀┘
                                                                   ▼
                            incremental.recompute_subgraph(新 Session, affected_subgraph, engine)
```

**契约**（MUST）：
- **C-1**（FR-010）：物化层（poll 或 webhook）每应用一次事实变更，自动召回重算受影响结论，无需调用 `/api/reasoning/incremental`。
- **C-2**（FR-011）：仅重算 `lifecycle_state == "effective"` 且子图与 `affected_subgraph` 相交的结论；`pending_signature`/`superseded`/`rejected` 及不相交者**不被触动**（spec edge "重算遇待签"）。
- **C-3**（FR-012）：产出刷新结论时，旧结论经 T5 置 `superseded` 并 `superseded_by → new.id`（取代链可追溯）；旧结论**非终态**动作置 `voided` + `action.void` 审计；刷新结论据 `results` 重新编排动作（与触发**同事务**）。
- **C-4**（FR-013）：变更 → 刷新端到端时延 **≤ 5 秒**。
- **C-5**（事务性不变式，research R4）：订阅者重算 MUST 只读**已提交**的结论表 + 事件自带的 `affected_subgraph`；触发点对齐到物化提交后，避免读未提交物化行。

> 既有 `POST /api/reasoning/incremental`（senior_analyst 手动触发，♻️）保留——自动订阅与手动触发共用 `recompute_subgraph`，行为一致。

---

## 6. PATCH `/api/actions/{id}` — 动作流转（♻️ 增 from-status 守卫，senior_analyst/qa）

**变更**（R8/FR-003/009）：增 from-status 守卫——
- 终态（`voided`/`done`/`failed`）MUST NOT 外迁 → `409`。
- `suppressed` 动作 MUST NOT 直接人工流转（须先经所属结论生效解抑为 `pending`）→ `409`（spec edge "动作早派发防护"）。
- 写 `action.transition` 审计。

---

## 7. 错误与边界汇总

| 场景 | 行为 | 来源 |
|---|---|---|
| 非法状态迁移（任一入口） | 拒绝 + 状态不变 + 原因 | FR-016/SC-005 |
| 对已取代待签结论签批 | `409`（已取代，引导操作最新替代） | edge "签批竞态" |
| 对已拒绝结论再签批/再拒绝 | `409`（终态） | FR-020/edge "QA 拒绝" |
| 事实变更命中待签结论 | 不重算、不取代（保留待签） | FR-011/edge "重算遇待签" |
| 取代旧结论有流转中动作 | 随取代置 `voided` + 审计 | FR-012/edge "取代作废动作" |
| 派发未生效结论的动作 | 拒绝/抑制（`409`） | FR-003/009/edge "动作早派发防护" |
| operator 尝试迁移/签批/拒绝 | `403`（只读） | FR-017/SC-007 |
| 建议性回写 not_accepted | 不视为失败 | FR-019/VR-10 |
| 审计链被篡改/缺失 | `verify` 定位首个断裂 `seq` | FR-018/SC-006 |
