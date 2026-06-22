# Contract: 能力三 — 连接器 / 物化 / 事件 / 增量重算 / 看板

**Feature**: `002-extraction-realtime-reasoning` | Base: `/api/integration`、`/api/reasoning` | 覆盖 FR-014~FR-019, FR-025~FR-027

♻️=既有扩展，🆕=新增。时间 ISO-8601 UTC。

---

## 1. ♻️ 连接器管理（扩展真实 test/sync 与调度字段）

### POST `/api/integration/connectors` — 创建连接器
Request：
```json
{
  "system_type": "APS",
  "name": "生产排期-APS",
  "ingest_mode": "hybrid",
  "poll_interval_seconds": 2,
  "connection_config": { "base_url": "http://aps.internal", "dsn_ref": "APS_DSN" },
  "field_mapping": { "equipment": "eq_no", "product": "prod_code", "slot": "time_slot" }
}
```
> `connection_config` **不含明文凭据**；凭据经 env/`settings` 注入（R7）。

### POST `/api/integration/connectors/{id}/test` — 连接测试（真实）
替换原 mock，对真实 APS 探活。Response：`{ "ok": true, "latency_ms": 120 }` 或 `{ "ok": false, "error": "timeout" }`。

### POST `/api/integration/connectors/{id}/sync` — 手动触发一次增量同步
Response `202`：`{ "run_id": "uuid", "status": "running" }`。后台拉取→归一→物化→发布事件。

### POST `/api/integration/connectors/{id}/webhook` — 🆕 可选 push 接收
支持推送的源经此投递增量；与 poll **汇入同一物化队列**（R4）。Request 体为源原生增量；Response `202 { "accepted": true }`。**不引入消息中间件**。

---

## 2. 🆕 物化运行与事实

### GET `/api/integration/connectors/{id}/runs` — 物化运行列表
```json
{ "runs": [ {
  "id": "uuid", "status": "success",
  "started_at": "...", "finished_at": "...",
  "cursor_from": {...}, "cursor_to": {...},
  "change_count": 7, "event_ids": ["uuid"]
} ] }
```
失败运行 `status∈{timeout,error}` 且 `cursor_to=null`（保留上一良好状态，FR-018/VR-4）。

### GET `/api/integration/runs/{run_id}` — 单次运行追溯
返回来源/水位/`changes`/产生的事件（US3 AC4）。

### GET `/api/integration/facts` — 已物化事实实例（A-Box 投影）
Query：`equipment`/`product`/`time_window`。返回经 `entity_shadow` 投影的事实个体。

---

## 3. 🆕 事实变更事件与增量重算

### GET `/api/integration/events` — 事实变更事件流
SSE 或分页列表，每条带 `affected_subgraph`（设备/产品/区域标识）（FR-016/017）。

### POST `/api/reasoning/incremental` — 增量重算入口（内部/可显式触发）
Request：`{ "affected_subgraph": { "equipment": ["EQ-2001"], "product": ["P-A"] } }`
仅对受影响子图调用既有 `run_assessment` 重算（**禁止全量**，VR-8）。Response `200`：刷新的结论列表（含 `effective` 状态）。端到端（事件→刷新）目标 **≤ 5s**（SC-005）。

**幂等/抗风暴**：同 `affected_subgraph` 短时重复触发可合并/限流；风暴时降级批量刷新并标注（边界用例）。

---

## 4. 🆕 GET `/api/integration/dashboard` — 实时看板数据

```json
{
  "compatibility_matrix": [
    { "equipment": "EQ-2001", "product": "P-A", "risk_level": "high", "conclusion_id": "uuid" }
  ],
  "schedule_risks": [
    { "date": "2026-06-23", "equipment": "EQ-2001", "conflict": true, "detail": "不相容同时段" }
  ],
  "updated_at": "..."
}
```
- `compatibility_matrix`：设备×产品共线相容性（热力图数据源，FR-025）。
- `schedule_risks`：未来若干天排期风险。
- 每单元 `conclusion_id` 可经 reasoning 溯源端点展开规则链（见 action-report-api / FR-025）。
- 看板在事实变更后近实时刷新（前端订阅 `/events` 或轮询，≤5s，FR-026/SC-005）。

### GET `/api/reasoning/conclusions/{id}/trace` — 规则链溯源
Response：`{ "rules_fired": [ { "rule_id": "...", "group": "...", "inputs": {...}, "conclusion": "...", "regulation_ref": "..." } ] }`（复用既有 `run_assessment` 的 `rules_fired`，FR-027/SC-007）。

---

## 错误与边界

| 场景 | 行为 |
|---|---|
| 事实源超时/不可达 | run `status=timeout/error`，保留上一良好状态，告警，不污染（FR-018） |
| 重复/乱序事件 | 物化幂等去重（`connector_id`+版本/哈希），不重复物化（FR-019/VR-3） |
| 重算风暴 | 子图重算合并/限流，必要时降级批量刷新并标注 |
| 凭据缺失 | `test`/`sync` 返回明确错误，不写脏数据 |
