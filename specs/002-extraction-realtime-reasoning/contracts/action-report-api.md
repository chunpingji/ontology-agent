# Contract: 能力三 — Action 引擎 / 工单·任务 / 报告导出

**Feature**: `002-extraction-realtime-reasoning` | Base: `/api/actions`、`/api/reports` | 覆盖 FR-020~FR-024

🆕 全部为新增端点。Action 编排由结论生效事件**自动驱动**（`action_engine`）；本契约的端点用于查询/流转/产物获取。

---

## 1. 🆕 GET `/api/actions` — 动作执行列表

Query：`conclusion_id`、`action_type`、`status`。
```json
{ "actions": [ {
  "id": "uuid",
  "conclusion_id": "uuid",
  "action_type": "dedication_work_order",
  "status": "pending",
  "payload": { "equipment": "EQ-2001", "reason": "需专用化" },
  "rule_chain": [ { "rule_id": "DED-003", "regulation_ref": "GMP附录..." } ],
  "writeback_status": null,
  "created_at": "..."
} ] }
```
`action_type ∈ {dedication_work_order, inactivation_task, recleaning_task, schedule_block, advisory_writeback, alert, generate_report}`（FR-020/021/022/024）。

**自动编排规则**（结论 `effective=true` 后触发，FR-020~022）：
| 结论 | 产生动作 |
|---|---|
| 需专用化 | `dedication_work_order` + `alert` |
| 需灭活/再清洁 | `inactivation_task` / `recleaning_task` |
| 不相容同设备同时段 | `schedule_block` + `advisory_writeback`（建议性） |

> 结论 `effective=false`（待 QA 签名）时，动作置 `suppressed`，**不触发对外动作**（FR-030/VR-6）。

---

## 2. 🆕 PATCH `/api/actions/{id}` — 人工流转状态

工单/任务为**平台内部记录**，可人工流转（FR-020/021）。
Request：`{ "status": "in_progress" }`（`pending→executed→in_progress→done` 或 `failed`）。
Response `200`：更新后的动作。

---

## 3. 🆕 POST `/api/actions/{id}/writeback-result` — 回写采纳结果

针对 `advisory_writeback`：外部排期方反馈是否采纳。
Request：`{ "writeback_status": "not_accepted" }`（`accepted`/`not_accepted`）。
**`not_accepted` 不视为失败**——结论与告警仍保留（FR-022/边界"动作回写被拒"）。

> 系统对外**仅建议性回写**，不直接改写外部权威数据（FR-022/VR-7/原则 II）。

---

## 4. 🆕 GET `/api/reports/{conclusion_id}` — 风险评估报告（JSON）

```json
{
  "conclusion_id": "uuid",
  "classification": { "risk_level": "high", "category": "..." },
  "dedication_decision": "required",
  "contamination_scores": { "...": 0.0 },
  "cfdi_scenarios": [ "..." ],
  "maco": { "value": 1.23, "method": "PDE", "pde": 10.0 },
  "rule_chain": [ { "rule_id": "...", "regulation_ref": "..." } ],
  "signature": { "signer": "qa01", "meaning": "已复核批准", "signed_at": "..." },
  "pdf_url": "/api/reports/{conclusion_id}/pdf"
}
```
含分类/专用化决策/污染途径评分/CFDI 情景/MACO·PDE/规则链与法规依据（FR-024/SC-007）。

## 5. 🆕 GET `/api/reports/{conclusion_id}/pdf` — 报告 PDF（归档产物）

`Content-Type: application/pdf`，`Content-Disposition: attachment`。PDF 含上述全部内容 **+ QA 签批信息**（经 `reportlab` 渲染，R12）。未签名的高风险结论报告 MUST 标注"未生效/待 QA 签批"。

---

## 错误与边界

| 场景 | 行为 |
|---|---|
| 结论未签名即请求动作 | 动作 `suppressed`，对外动作被抑制（FR-030） |
| 回写被拒 | `writeback_status=not_accepted`，不置失败（FR-022） |
| MACO 关键数据缺失 | 报告标注所用回退方法与数据缺口（复用既有计算器，边界用例） |
| 非授权角色流转动作 | 按 RBAC 限制（见 compliance-audit-api） |

每个动作执行均留痕（动作类型/触发结论/规则链/执行结果/回写状态，FR-023/SC-006），并写入审计哈希链。
