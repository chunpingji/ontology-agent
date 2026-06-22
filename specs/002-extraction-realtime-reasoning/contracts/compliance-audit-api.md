# Contract: 跨切 — 审计哈希链 / QA 电子签名 / RBAC

**Feature**: `002-extraction-realtime-reasoning` | Base: `/api/compliance` | 覆盖 FR-028~FR-031

🆕 全部为新增端点。鉴权头贯穿所有 API。

---

## 0. 鉴权与 RBAC（FR-031，全 API 适用）

所有请求带：
- `X-User`: 用户标识
- `X-Role`: `senior_analyst | operator | qa`

**角色边界**（经既有 `dependencies.require_role` 扩展，R10）：

| 能力 | senior_analyst | operator | qa |
|---|---|---|---|
| 知识模型维护/发布（能力一） | ✅ | ❌ | ❌ |
| 抽取作业/对齐审核（能力二） | ✅ | ❌ | ❌ |
| 查看推理/看板/报告 | ✅ | ✅（只读） | ✅ |
| 触发增量重算 | ✅ | ❌ | ❌ |
| QA 复核与电子签名 | ❌ | ❌ | ✅ |
| 动作状态人工流转 | ✅ | ❌ | ✅ |

越权 → `403 { "detail": "role <X> not permitted" }`（SC-010）。
> 身份层保持**可插拔**（X-User/X-Role）；企业 SSO 为后续接入，**不在本特性范围**。

---

## 1. 审计哈希链（FR-028/029）

### GET `/api/compliance/audit/verify` — 完整性校验
按 `seq` 顺序重算 `entry_hash = SHA-256(prev_hash ‖ 规范化记录)`，校验链连续性。
Response `200`（完好）：
```json
{ "ok": true, "verified_count": 1284, "head_seq": 1284 }
```
Response `200`（检测到篡改）：
```json
{ "ok": false, "broken_at_seq": 532, "expected_hash": "ab…", "actual_hash": "cd…" }
```
MUST 定位**首个断裂记录** `broken_at_seq`，不静默续写（FR-029/SC-008/边界"审计链中断恢复"）。

### GET `/api/compliance/audit` — 审计记录查询（只读）
Query：`actor`、`action`、`entity_iri`、时间范围。返回带 `seq`/`prev_hash`/`entry_hash` 的只追加记录。**无 UPDATE/DELETE 端点**（append-only，VR-5）。

> 全链路操作（抽取/对齐/物化/推理/动作/签名）由服务端经单写路径 `audit.py` 自动写链，非客户端直接写。

---

## 2. QA 电子签名（FR-030，21 CFR Part 11）

### GET `/api/compliance/signatures/pending` — 待签名结论（QA）
返回 `requires_signature=true ∧ effective=false` 的高风险/专用化/合规阻断结论。`role=qa` only。

### POST `/api/compliance/signatures` — QA 电子签名
Request：
```json
{
  "conclusion_id": "uuid",
  "username": "qa01",
  "password": "••••••",
  "meaning": "已复核批准生效"
}
```
- MUST **重新认证**（username+password 校验）（Part 11）。
- 记录 `signer`/`meaning`/`signed_at`，**不可分割地绑定** `conclusion_id`。
- 成功后置结论 `effective=true`、回填 `signature_id`，并解除该结论 `suppressed` 动作、写审计链。

Response `201`：
```json
{ "signature_id": "uuid", "conclusion_id": "uuid", "effective": true, "signed_at": "..." }
```
错误：`401`（重认证失败）、`403`（非 QA）、`409`（已签名）、`404`（结论不存在）。

**门禁不变式**（VR-6/SC-009）：未签名前结论 `effective=false`，对外动作（工单/告警/回写）被抑制并置待签名；签名后方生效并触发动作。

---

## 错误与边界

| 场景 | 行为 |
|---|---|
| 篡改审计记录 | `verify` 返回 `ok=false` + `broken_at_seq`（FR-029） |
| 重认证失败 | `401`，不签名、不生效 |
| 非 QA 尝试签名 | `403`（FR-031） |
| operator 尝试写操作 | `403`（只读边界，SC-010） |
| 重复签名同一结论 | `409`（唯一绑定） |
