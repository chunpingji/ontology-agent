# Contract: 评估即落库 + 强制 QA 闸门自动 arm（G1/G2）

**Feature**: `003-workflow-statemachine-closure` | Base: `/api/reasoning` | 覆盖 FR-001~009、FR-014

本契约定义 002 既有 `POST /api/reasoning/assess` 的**行为变更**（从无状态 → 落库自举 + 自动闸门）。鉴权头（`X-User`/`X-Role`）贯穿所有 API。

---

## 0. ⚠️ 破坏性变更提示

| 项 | 002（变更前） | 003（变更后） |
|---|---|---|
| 持久化 | 不落库，仅返回 | **落库** `ReasoningExecution` + 编排动作 |
| 角色门禁 | **无**（任意访问） | `require_role(senior_analyst)`——现需 `X-User`/`X-Role` 头 |
| 响应体 | `AssessmentResponse`（无标识） | ➕ `execution_id`/`lifecycle_state`/`requires_signature`/`effective`（向后兼容追加字段） |

> 前端 `lib/api.ts` 调用 `/assess` 时 MUST 携带身份头；响应新增字段为追加，旧字段不变，向后兼容。

---

## 1. POST `/api/reasoning/assess` — 评估即落库（senior_analyst）

对一个共线生产场景发起风险评估，**返回结论的同时持久化为带唯一标识与初始生命周期状态的工作流对象**，并据结果编排隐含动作。

**Request**（既有 `AssessmentRequest`，不变）：
```json
{
  "drug_iri": "http://slpra/onto#DrugX",
  "equipment_iris": ["http://slpra/onto#EquipA"],
  "assessment_type": "full"
}
```

**行为**（MUST，单事务原子）：
1. 执行既有推理，组装 **canonical `results`**（含 `requires_dedication`/`cfdi_scenarios`/`pde`/… 见 data-model §1.1）。
2. `requires_signature = risk.requires_qa_signature(results, risk_level)`（FR-005，判据 = 高风险等级 OR 需专用化 OR 命中青霉素/头孢/高致敏）。
3. 据判据经 `lifecycle.transition` 落库置初始态：`requires_signature ? pending_signature : effective`（T1/T2，FR-006/007/014）。
4. `ActionEngine.orchestrate(conclusion)` 编排动作：结论 `pending_signature` 时动作全置 `suppressed`、零对外派发；`effective` 时置 `pending`（可派发）（FR-003）。
5. 写审计 `reasoning.persist`（FR-018）。

**Response `201`**（扩展 `AssessmentResponse`）：
```json
{
  "execution_id": "uuid",
  "drug_iri": "http://slpra/onto#DrugX",
  "equipment_iris": ["http://slpra/onto#EquipA"],
  "risk_level": "HighRisk",
  "rules_fired": [ ... ],
  "scenarios": [ ... ],
  "requires_dedication": true,
  "maco": { ... },
  "recommendations": [ ... ],

  "lifecycle_state": "pending_signature",
  "requires_signature": true,
  "effective": false
}
```

**错误**：`403`（身份头缺失/非 senior_analyst，FR-017）、`400`（drug_iri/equipment_iris 无效）。

---

## 2. GET `/api/reasoning/conclusions/{id}` — 结论检索（♻️ 既有，状态字段对齐）

返回结论当前生命周期状态、结论结果与（如有）已编排动作清单（FR-002）。

**Response `200`**：
```json
{
  "id": "uuid",
  "lifecycle_state": "pending_signature",
  "requires_signature": true,
  "effective": false,
  "risk_level": "HighRisk",
  "results": { "...canonical..." },
  "affected_subgraph": { ... },
  "superseded_by": null,
  "actions": [
    { "id": "uuid", "action_type": "dedication_work_order", "status": "suppressed" },
    { "id": "uuid", "action_type": "alert", "status": "suppressed" }
  ]
}
```

> `lifecycle_state` 为状态真理来源；`effective`/`superseded_by` 为兼容映射（data-model §1.1 不变式）。

---

## 3. 高风险判据（FR-005，`services/reasoning/risk.py`）

`requires_qa_signature(results, risk_level) -> bool` =
`risk_level == "HighRisk"` **OR** `results.get("requires_dedication")` **OR** `_hazardous_scenario(results)`

`_hazardous_scenario`：`results["hazardous_categories"]` / `cfdi_scenarios` 命中 **青霉素 / 头孢 / 高致敏** 之一即 True。

> **初始集**，可后续迭代细化（spec Assumptions）；抽为纯函数，落库与增量重算共用、可独立单测。

---

## 4. 自举不变式（SC-002）

在**零预置结论、零人工补数据**环境下，单次 `/assess` 即让下游可直接引用该结论：
- 复核/查询：`GET /conclusions/{id}` ✅
- QA 签批/拒绝：`/api/compliance/signatures` · `/api/compliance/reject`（见 [lifecycle-guard-api.md](./lifecycle-guard-api.md)）✅
- 动作派发：`PATCH /api/actions/{id}`（结论生效后）✅
- 报告导出：`GET /api/reports/{conclusion_id}`（`results` canonical 形状保证无需补数据）✅

---

## 5. 错误与边界

| 场景 | 行为 |
|---|---|
| 身份头缺失/非 senior_analyst 发起评估 | `403`（FR-017/SC-007） |
| 命中高风险判据 | 落库即 `pending_signature`，动作全 `suppressed`、零派发（FR-006/SC-003） |
| 未命中判据 | 落库即 `effective`，动作 `pending` 可派发（FR-007） |
| 重复评估同输入 | 各自落库为独立结论（非幂等，spec edge "重复评估"） |
| 落库后即导出报告 | 成功，无需补数据（US1-AC4/SC-002） |
