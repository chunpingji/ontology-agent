# Quickstart: 分析结论工作流状态机闭环 —— 端到端验证

**Feature**: `003-workflow-statemachine-closure` | **Date**: 2026-06-22 | **Plan**: [plan.md](./plan.md)

本指南给出 US1–US4 的**可执行端到端验证场景**，证明 G1/G2/G3 闭环与显式状态机成立。契约见 [contracts/](./contracts/)，状态机与字段见 [data-model.md](./data-model.md)。本文件为验证/运行指南，不含实现代码（实现属 `tasks.md` 与实现阶段）。

---

## 前置

- 已应用迁移至 `0003_workflow_statemachine`（启动经 `main._run_migrations → upgrade head` 自动；或 `alembic upgrade head`）。
- 后端可用：`docker-compose up`（db/backend）或本地 `uvicorn app.main:app`。
- 身份头（可信网关注入）：分析师 `-H "X-User: analyst" -H "X-Role: senior_analyst"`；QA `-H "X-User: qa01" -H "X-Role: qa"`；operator `-H "X-User: op" -H "X-Role: operator"`。
- QA 重认证密钥经 env 注入（`SLPRA_QA_REAUTH_SECRET`，默认 `qa-reauth`），**不入库**。
- 验证环境为**干净库**（零预置结论），以证自举（SC-002）。

---

## 自动化验证（pytest，权威判据）

```bash
cd backend
pytest tests/test_api/test_assess_bootstrap.py \
       tests/test_api/test_lifecycle_machine.py \
       tests/test_api/test_qa_reject.py \
       tests/test_api/test_auto_recompute.py -v
```
测试复用 `tests/conftest.py` 既有夹具（`client`/`db`/`analyst_headers`/`qa_headers`/`operator_headers`/`FakeOntologyEngine` + 内存 SQLite `StaticPool`）。全绿即满足下列 SC。

---

## US1 — 评估即落库，流水线自举（P1 / G1）

**目标**：干净环境单次评估即落库可寻址结论 + 编排动作 + 可直接导出报告（FR-001~004，SC-001/002）。

```bash
# 1. 发起评估（带身份头）→ 期望 201 + execution_id + lifecycle_state
curl -sS -X POST localhost:8000/api/reasoning/assess \
  -H "X-User: analyst" -H "X-Role: senior_analyst" -H "Content-Type: application/json" \
  -d '{"drug_iri":"http://slpra/onto#DrugLowRisk","equipment_iris":["http://slpra/onto#EquipA"]}'

# 2. 按返回 execution_id 检索 → 期望含 lifecycle_state + actions 清单
curl -sS localhost:8000/api/reasoning/conclusions/<execution_id> \
  -H "X-User: analyst" -H "X-Role: senior_analyst"

# 3. 直接导出报告（无需补数据）→ 期望 200
curl -sS localhost:8000/api/reports/<execution_id> \
  -H "X-User: analyst" -H "X-Role: senior_analyst"
```

**预期**：
- ✅ `/assess` 返回 `201`，体含 `execution_id`、`lifecycle_state`、`requires_signature`、`effective`（US1-AC1）。
- ✅ 检索返回当前状态 + 结论结果 + 已编排动作清单（US1-AC2）。
- ✅ 结论隐含的操作动作已编排登记；未生效前为 `suppressed`、不对外派发（US1-AC3）。
- ✅ 报告成功生成，无中间补数据（US1-AC4）。
- ✅ 无身份头 → `403`（SC-007）。

---

## US2 — 高风险结论自动进入 QA 待签闸门（P2 / G2）

**目标**：高风险自动入 `pending_signature` + 动作全抑制；低风险落库即 `effective`；QA 签批后生效解抑；无签名不可生效（FR-005~009，SC-003）。

```bash
# A. 高风险输入（命中专用化/青霉素等判据）→ 期望 lifecycle_state=pending_signature
curl -sS -X POST localhost:8000/api/reasoning/assess \
  -H "X-User: analyst" -H "X-Role: senior_analyst" -H "Content-Type: application/json" \
  -d '{"drug_iri":"http://slpra/onto#Penicillin","equipment_iris":["http://slpra/onto#EquipShared"]}'

# B. 查看待签列表（QA）→ 期望含上一条；不含任何 rejected
curl -sS localhost:8000/api/compliance/signatures/pending -H "X-User: qa01" -H "X-Role: qa"

# C. QA 电子签名（Part 11 重认证）→ 期望 201，结论转 effective、动作解抑
curl -sS -X POST localhost:8000/api/compliance/signatures \
  -H "X-User: qa01" -H "X-Role: qa" -H "Content-Type: application/json" \
  -d '{"conclusion_id":"<id>","username":"qa01","password":"qa-reauth","meaning":"已复核批准生效"}'
```

**预期**：
- ✅ 高风险结论落库即 `pending_signature`，全部动作 `suppressed`、对外派发数 **0**（US2-AC1，SC-003）。
- ✅ 低风险结论（如 US1 的 `DrugLowRisk`）落库即 `effective`，动作 `pending` 可派发（US2-AC2）。
- ✅ 有效 QA 签名后结论 `effective`、签名不可分割绑定、`suppressed→pending` 解抑、签批入审计链（US2-AC3）。
- ✅ 无有效签名时令其生效/派发动作 → 拒绝、保持 `pending_signature`、动作仍抑制（US2-AC4，FR-009）。
- ✅ 重认证失败 → `401`；非 QA 签名 → `403`；重复签名 → `409`。

---

## US3 — 事实变更自动召回重算（P3 / G3，近实时）

**目标**：上游事实变更**无人工触发**自动重算相交且生效的结论；待签/不相交者不动；旧结论取代 + 旧动作作废；时延 ≤5s（FR-010~013，SC-004）。

> APS 连接器轮询模式：设 `SLPRA_REALTIME_POLLING_ENABLED=true`、`aps_poll_interval_seconds=2`；或经 webhook 投递变更。两路径都汇入 `materializer.run_sync → fact_event_bus.publish`，由 `recompute_subscriber` 自动重算。

```bash
# 1. 先经 US1/US2 落库 ≥1 条 effective 结论（其 affected_subgraph 覆盖某设备/产品/区域）
# 2. 注入与该子图相交的事实变更（webhook 示例）
curl -sS -X POST localhost:8000/api/integration/connectors/<aps_id>/webhook \
  -H "X-User: analyst" -H "X-Role: senior_analyst" -H "Content-Type: application/json" \
  -d '{"change":{"entity_iri":"http://slpra/onto#EquipA","kind":"schedule_update", ...}}'
# 3. 轮询结论：旧结论应转 superseded 并 superseded_by 指向刷新结论
curl -sS localhost:8000/api/reasoning/conclusions/<old_id> -H "X-User: analyst" -H "X-Role: senior_analyst"
```

**预期**：
- ✅ 无任何人工触发（未调 `/api/reasoning/incremental`），相交且 `effective` 结论被自动重算并产出刷新结论（US3-AC1）。
- ✅ 仅作用于子图相交且 `effective` 的结论；不相交、已失效、`pending_signature` 者不受影响（US3-AC2，FR-011；spec edge "重算遇待签"）。
- ✅ 旧结论置 `superseded` 并链接替代结论（取代链可追溯），刷新结论据结果重新编排动作（US3-AC3）。
- ✅ 被取代旧结论已解抑/流转中的动作随取代置 `voided`，作废入审计链（US3-AC4，FR-012）。
- ✅ 变更 → 刷新端到端时延 **≤ 5 秒**（US3-AC5，SC-004）。

---

## US4 — 显式生命周期与集中守卫（P4）

**目标**：四态显式枚举 + 单一守卫一致校验多入口；非法迁移一致拒绝且不改状态；operator 越权被拒（FR-014~017，SC-005/007）。

```bash
# 非法迁移示例：对一条 pending_signature 结论尝试经动作派发"绕过"生效
curl -sS -X PATCH localhost:8000/api/actions/<suppressed_action_id> \
  -H "X-User: analyst" -H "X-Role: senior_analyst" -H "Content-Type: application/json" \
  -d '{"status":"in_progress"}'              # 期望 409（suppressed 不可直接流转）

# 对已拒绝结论再签批 → 期望 409（终态）
curl -sS -X POST localhost:8000/api/compliance/signatures \
  -H "X-User: qa01" -H "X-Role: qa" -H "Content-Type: application/json" \
  -d '{"conclusion_id":"<rejected_id>","username":"qa01","password":"qa-reauth","meaning":"x"}'

# operator 尝试签批 → 期望 403
curl -sS -X POST localhost:8000/api/compliance/reject \
  -H "X-User: op" -H "X-Role: operator" -H "Content-Type: application/json" \
  -d '{"conclusion_id":"<id>","username":"op","password":"x","reason":"y"}'
```

**预期**：
- ✅ 合法迁移按四态推进并记审计（US4-AC1）。
- ✅ 非法迁移（违反 from-前置、自终态外迁、绕过待签）被拒、**状态不变**、附明确原因（US4-AC2，SC-005）。
- ✅ 同类迁移多入口（落库/签批/拒绝/取代）判定一致——无"某入口有守卫、另一入口无"（US4-AC3）。
- ✅ operator 尝试任何写/迁移/签批/拒绝 → `403`（US4-AC4，SC-007）。

---

## QA 拒绝路径（FR-020，US2-AC5 / 补充）

```bash
curl -sS -X POST localhost:8000/api/compliance/reject \
  -H "X-User: qa01" -H "X-Role: qa" -H "Content-Type: application/json" \
  -d '{"conclusion_id":"<pending_id>","username":"qa01","password":"qa-reauth","reason":"数据不足"}'
```
**预期**：结论转 `rejected` 终态、其全部被抑制动作 `voided`、拒绝事件入审计链；对 `rejected` 再签批/再拒绝 → `409`；纠正路径为重新 `/assess`。

---

## 审计链验真（FR-018，SC-006，跨 US）

```bash
curl -sS localhost:8000/api/compliance/audit/verify -H "X-User: qa01" -H "X-Role: qa"
# 期望 { "ok": true, "verified_count": N, "head_seq": N }
```
**预期**：US1–US4 全程事件（`reasoning.persist`/`reasoning.transition`/`compliance.sign`/`compliance.reject`/`reasoning.recompute`/`action.void`/`action.transition`）齐备且连续；任一篡改/缺失 → `ok=false` + `broken_at_seq` 定位首个断裂点。

---

## 成功判据对照

| SC | 验证场景 |
|---|---|
| SC-001（结论丢失率 0%） | US1 步 1–2 |
| SC-002（自举成功） | US1 步 1–3（干净库端到端） |
| SC-003（高风险 100% 待签、派发 0） | US2 A/B |
| SC-004（≤5s、误重算 0%） | US3 步 2–3 |
| SC-005（非法迁移 100% 拒绝、不改态） | US4 全部 |
| SC-006（审计齐备可验真、定位断裂） | 审计链验真 |
| SC-007（operator 写拦截 100%） | US1 无头、US4 operator |
