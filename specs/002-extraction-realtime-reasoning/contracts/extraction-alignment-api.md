# Contract: 能力二 — 抽取作业接线 / 进度 SSE / 对齐审核 / DB 源

**Feature**: `002-extraction-realtime-reasoning` | Base: `/api/extraction` | 覆盖 FR-001~FR-013

约定：JSON over HTTP；鉴权经 `X-User`/`X-Role` 头（见 compliance-audit-api）。时间 ISO-8601 UTC。错误体 `{ "detail": "<message>" }`。♻️=既有端点（行为扩展），🆕=新增。

---

## 1. ♻️ POST `/api/extraction/jobs` — 创建抽取作业（接线触发流水线）

**变更**：创建后 MUST 经 `BackgroundTasks` **实际触发** `run_extraction_pipeline`，作业不得停留 `pending`（FR-001）。

Request（multipart 或 JSON）：
- `source_type`: `"excel" | "word" | "database"`（必填）
- `config_id`: UUID（抽取配置，必填）
- `file`: 文件（`excel`/`word` 必填）
- `db_source`: 对象（`source_type=database` 必填）：`{ "dsn_ref": "<env 键, 不含明文凭据>", "schema": "<schema>", "include_tables": ["..."] }`（R2/R7）

Response `202 Accepted`：
```json
{ "job_id": "uuid", "status": "running", "source_type": "excel" }
```
错误：`400`（缺文件/源配置）、`403`（无权限）。

**验收**：作业进入 `running`→…→`reviewing`/`completed`；产出候选；不停留 `pending`（SC-001）。

---

## 2. 🆕 GET `/api/extraction/jobs/{job_id}/progress` — 进度 SSE

`Content-Type: text/event-stream`（FR-002）。逐阶段推送：
```
event: progress
data: {"job_id":"...","stage":"parsing","pct":10,"status":"running"}

event: progress
data: {"job_id":"...","stage":"extracting","pct":40,"degraded":false}

event: done
data: {"job_id":"...","status":"reviewing","total_candidates":42}
```
`stage ∈ {parsing, extracting, aligning, reviewing}`；`degraded=true` 且带 `degraded_reason` 表示 LLM 回退（FR-007）。连接在 `done`/`error` 后关闭。浏览器用原生 `EventSource`。

---

## 3. ♻️ GET `/api/extraction/jobs/{job_id}/candidates` — 候选列表（扩展归组）

Query：`review_status`、`candidate_kind`、`group_key`（均可选过滤）。

Response `200`：
```json
{
  "job_id": "uuid",
  "groups": [
    {
      "group_key": "EQ-2001",
      "canonical_candidate_id": "uuid",
      "candidates": [
        {
          "id": "uuid",
          "candidate_kind": "instance",
          "target_class_iri": "...#Equipment",
          "extracted_properties": { "equipmentNo": "EQ-2001", "name": "..." },
          "alignment_result": "merge",
          "aligned_iri": "...#EQ_2001",
          "match_score": 0.93,
          "is_canonical": true,
          "source_ref": "设备台账.xlsx",
          "review_status": "pending",
          "action_conditions": null
        }
      ]
    }
  ]
}
```
跨源同一对象按 `group_key` 归组，`is_canonical` 标记规范实例（FR-009/SC-003）。Action 候选 `candidate_kind="action"` 带 `action_conditions`（FR-005）。

---

## 4. ♻️ PUT `/api/extraction/candidates/{candidate_id}/review` — 审核（确认/拒绝）

Request：
```json
{ "status": "confirmed", "edited_properties": { "name": "修正名" } }
```
`status ∈ {confirmed, rejected}`；`confirmed` 触发 commit（落 `committed_iri`，进入知识库）。`rejected` 不进入（FR-010/VR-2）。

Response `200`：`{ "id": "uuid", "review_status": "committed", "committed_iri": "...#EQ_2001" }`

---

## 5. 🆕 POST `/api/extraction/candidates/merge` — 手动合并

Request：`{ "source_ids": ["uuid", "uuid"], "target_id": "uuid" }`
合并源候选到 `target_id`（源置 `merged`、落 `merged_into_id`）。Response `200`：合并后规范候选。歧义场景**不自动合并**，须经此显式操作（FR-010/011）。

## 6. 🆕 POST `/api/extraction/candidates/{candidate_id}/split` — 手动拆分

Request：`{ "splits": [ { "extracted_properties": {…} }, { "extracted_properties": {…} } ] }`
原候选置 `split`，派生多个新 `pending` 候选。Response `201`：`{ "derived": ["uuid", "uuid"] }`。

---

## 错误与边界

| 场景 | 行为 |
|---|---|
| LLM 不可用 | 作业不失败，回退结构化抽取，SSE `degraded=true` + `degraded_reason`（FR-007） |
| DB 源不可达 | `400`/作业 `failed` 并记 `error_message`，不产生脏候选 |
| 对齐歧义 | 候选保持 `pending`，不自动 merge（FR-011） |
| 非 senior_analyst 审核 | 审核/合并/拆分按 RBAC（维护类）限制（见 compliance-audit-api） |
