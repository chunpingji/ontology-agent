# Contract: Document Analysis Runs API

版本：`document-analysis-runs-v1`

日期：2026-09-08

前缀：`/api/document-analysis/runs`

本契约完全替换旧 `POST /api/document-analysis/word`。不提供双读、双写、旧 response adapter、旧 checkpoint 恢复或算法选择开关。

## 1. 通用规则

### 1.1 身份、授权与内容类型

- 所有端点必须经现有 `get_current_user`；浏览器 EventSource 可沿用 `get_current_user_sse` 的受签名 token 查询参数方式。
- 创建、pause、resume、cancel、delete 属运行写操作，要求当前部署授权策略授予对应操作权限；契约不把某个具体角色名硬编码为领域语义。所有读写仍须 owner 或显式授权范围校验。
- 运行内容只对 owner 可见。本特性不新增运行分享接口；角色相同也不能读取另一 owner 的源、SSE 或 artifact。
- 对无权运行统一返回 404，避免枚举；已确认 owner 访问自己的已删除/过期 tombstone 可返回 410。
- JSON 响应均含 `contract_version: "document-analysis-runs-v1"`。
- 时间是 UTC ISO-8601；IRI 始终返回完整值；label 仅展示。
- GET、SSE 订阅、Tab 切换和 projection filter 不得触发解析、摘要、模型、候选写入、审核或业务事实提交。

### 1.2 错误对象

```json
{
  "contract_version": "document-analysis-runs-v1",
  "error": {
    "code": "RUN_REVISION_CONFLICT",
    "message": "运行状态已变化，请刷新后重试",
    "retryable": true,
    "current_revision": 8
  }
}
```

稳定错误码：

| HTTP | code | Meaning |
|---|---|---|
| 400 | INVALID_REQUEST | 字段组合/JSON 参数非法 |
| 401 | UNAUTHENTICATED | 无有效身份 |
| 403 | ROLE_FORBIDDEN | 身份有效但无运行写权限 |
| 404 | RUN_NOT_FOUND | 不存在或不属于调用者 |
| 409 | IDEMPOTENCY_CONFLICT | owner 下相同 request_key 对应不同输入 |
| 409 | RUN_REVISION_CONFLICT | expected_revision 过期 |
| 409 | RUN_STATE_CONFLICT | 当前状态不允许操作 |
| 409 | FINGERPRINT_MISMATCH | 当前依赖与冻结运行不一致，拒绝恢复 |
| 410 | RUN_DELETED / RUN_EXPIRED | owner 的 tombstone，不返回业务内容 |
| 413 | SOURCE_TOO_LARGE | 上传或解压资源超过配置上限 |
| 415 | UNSUPPORTED_SOURCE_TYPE | 非 `.doc`/`.docx` 或实际类型不符 |
| 422 | EMPTY_SOURCE / INVALID_WORD / INVALID_ROOT_CLASS | 可读但不满足启动条件 |
| 503 | ONTOLOGY_UNAVAILABLE | 创建前无法冻结/校验本体；不调用模型 |

后台转换、摘要或模型故障在已创建 run 的 `status/error/progress` 中表示，不把已接受的 202 事后改成 HTTP 错误。

### 1.3 水位与缓存

- 运行 response 返回 `run_revision`、`event_head`、`artifact_revision`。
- 创建事务已提交可下载的 source artifact，故首个公开 artifact 水位为 `1`；后续 structure、metadata、graph 每次提交继续单调递增。
- metadata/graph response 必须来自单个已提交 artifact 水位，不混合事务前后的对象。
- 可返回 `ETag: W/"run:<run-id>:revision:<run-revision>:artifact:<artifact-revision>"`；`If-None-Match` 命中返回 304，仍无模型副作用。
- 客户端只接受当前 `recognition_run_id` 且水位不低于本地水位的响应；迟到响应不得覆盖当前运行。

## 2. 创建运行

### 2.0 历史任务列表（2026-09-09 补充）

`GET /api/document-analysis/runs?limit=10&offset=0`，认证和 owner 边界沿用通用规则。
`limit` 默认 20，范围 1—100；`offset` 默认 0，非负。非法参数返回 400 `INVALID_REQUEST`。
按 `created_at DESC, recognition_run_id DESC` 稳定排序，返回：

```json
{
  "contract_version": "document-analysis-runs-v1",
  "items": [],
  "has_more": false
}
```

每个 item 包含 `RunWatermark` 全部字段、`status`、`stage`、`input`（与创建回执一致）、
`created_at`、`expires_at`；不携带源文件、图谱、内部路径、owner 或执行凭据。
仅查询当前 owner 的 `document_graph` 运行；排除已删除、墓碑和 `expires_at <= now` 的运行。
正在删除的运行返回 `deleting`，前端禁用其查看入口。列表查询不触发模型、派发、租约或清理写入。
分页为当前数据库视图；创建任务后前端回到首页刷新，读取失败保留错误提示并允许重试。

```http
POST /api/document-analysis/runs
Content-Type: multipart/form-data
Authorization: Bearer <token>

file=<one .doc or .docx file>
root_class_iri=https://ontology.example/CMCReport
request_key=browser-generated-unique-key
metadata_mode=generate_summary
```

Fields：

| Field | Required | Rule |
|---|---|---|
| file | yes | 单个非空 Word；basename 安全化；流式计数/hash，不把整个文件读入 RAM |
| root_class_iri | yes | 完整 IRI；必须存在于当前本体且通过 RootSeedPolicy |
| request_key | yes | 1—200 字符，owner 范围幂等 |
| metadata_mode | no | `cached_summary | generate_summary | structure_only`，默认 `generate_summary`；实际值进入 fingerprint |

在线页面创建 `scope_mode=document_graph`。`focus_path` 是同一核心的受控评测入口，不作为首期页面/公共创建参数，避免用户借任意谓词放宽菜单。

处理边界：

1. 在模型调用前完成身份/角色、后缀与实际类型、大小、空文件、basename 和 root IRI 校验。
2. 将上传写到 run-scoped 临时位置并计算 hash；创建 run/source artifact/queued event 的事务成功后才返回。
3. response 之后 durable dispatcher 执行转换、解析、元数据和图谱。FastAPI `BackgroundTasks` 若使用，只能唤醒 dispatcher；数据库 queued row 是权威任务。
4. 创建不会写 `ExtractionJob`、旧 CandidateStore、中央 KG、EvidenceCommit 或自动审核记录。

Success `202 Accepted`：

```json
{
  "contract_version": "document-analysis-runs-v1",
  "recognition_run_id": "a4f9885d-1b70-4718-9a97-2eb03c3ef8c4",
  "run_revision": 1,
  "event_head": 1,
  "artifact_revision": 1,
  "status": "queued",
  "stage": "accepted",
  "idempotent_replay": false,
  "input": {
    "filename": "CMCReport.docx",
    "root_class_iri": "https://ontology.example/CMCReport",
    "root_class_label": "CMC 报告",
    "metadata_mode": "generate_summary"
  },
  "created_at": "2026-09-08T12:00:00Z",
  "expires_at": null,
  "links": {
    "self": "/api/document-analysis/runs/a4f9885d-1b70-4718-9a97-2eb03c3ef8c4",
    "metadata": "/api/document-analysis/runs/a4f9885d-1b70-4718-9a97-2eb03c3ef8c4/metadata",
    "graph": "/api/document-analysis/runs/a4f9885d-1b70-4718-9a97-2eb03c3ef8c4/graph",
    "source": "/api/document-analysis/runs/a4f9885d-1b70-4718-9a97-2eb03c3ef8c4/source",
    "events": "/api/document-analysis/runs/a4f9885d-1b70-4718-9a97-2eb03c3ef8c4/events"
  }
}
```

相同 owner + request_key + request hash 再次提交仍返回 202、同一 run，`idempotent_replay=true`，不得新增任务/模型调用。相同键而文件、规范文件名、root IRI 或模式不同返回 409。

## 3. 读取运行

```http
GET /api/document-analysis/runs/{recognition_run_id}
```

`200 OK`：

```json
{
  "contract_version": "document-analysis-runs-v1",
  "recognition_run_id": "a4f9885d-1b70-4718-9a97-2eb03c3ef8c4",
  "run_revision": 8,
  "event_head": 35,
  "artifact_revision": 4,
  "status": "running",
  "stage": "extracting",
  "input": {
    "filename": "CMCReport.docx",
    "root_class_iri": "https://ontology.example/CMCReport",
    "root_class_label": "CMC 报告",
    "metadata_mode": "generate_summary",
    "scope_mode": "document_graph"
  },
  "identities": {
    "analysis_id": "analysis:...",
    "ontology_snapshot_id": "ontology-snapshot:...",
    "metadata_snapshot_id": "metadata-snapshot:...",
    "graph_snapshot_id": "graph-snapshot:...",
    "fingerprint_status": "frozen"
  },
  "artifacts": {
    "source": "ready",
    "structure": "ready",
    "metadata": "ready",
    "graph": "partial"
  },
  "progress": {
    "candidate_policy": "sparse-candidates-v1",
    "completion": "incomplete",
    "tasks_attempted": 12,
    "model_calls": 20,
    "records_planned": 117,
    "records_examined": 8,
    "records_incomplete": 1,
    "records_unattempted": 108,
    "phase_counts": {"phase1": 8, "phase2": 0},
    "decisions": {
      "supported": 3,
      "unsupported": 4,
      "undetermined": 2,
      "not_checked": 3
    },
    "pending_frontiers": 5,
    "stop_reason": null,
    "contract_version": "document-analysis-runs-v1",
    "event_head": 35,
    "artifact_revision": 4
  },
  "error": null,
  "available_actions": ["pause", "cancel", "delete"],
  "created_at": "2026-09-08T12:00:00Z",
  "started_at": "2026-09-08T12:00:01Z",
  "paused_at": null,
  "finished_at": null,
  "expires_at": null
}
```

`available_actions` 由服务端状态/角色/expiry 计算，前端不能自行授予。response 不包含 execution token、checkpoint、完整 prompt 或私有模型响应。

## 4. 读取分层元数据

```http
GET /api/document-analysis/runs/{recognition_run_id}/metadata
```

结构尚未提交时仍返回 `200`：

```json
{
  "contract_version": "document-analysis-runs-v1",
  "recognition_run_id": "...",
  "run_revision": 2,
  "event_head": 3,
  "artifact_revision": 1,
  "availability": "pending",
  "stage": "parsing",
  "retry_after_ms": 1000,
  "metadata": null,
  "error": null
}
```

结构/元数据可用时：

```json
{
  "contract_version": "document-analysis-runs-v1",
  "recognition_run_id": "...",
  "run_revision": 6,
  "event_head": 20,
  "artifact_revision": 2,
  "availability": "ready",
  "stage": "extracting",
  "analysis": {
    "analysis_id": "analysis:...",
    "document_hash": "<sha256>",
    "structure_hash": "<sha256>",
    "parser_version": "2",
    "structure_policy_version": "word-structure-v1"
  },
  "metadata_snapshot": {
    "snapshot_id": "metadata-snapshot:...",
    "generation_source": "model_summary",
    "summary_version": "word-tree-summary-v1",
    "summary_model_identity": "local-model@sha256:...",
    "dependency_hash": "<sha256>",
    "frozen": true
  },
  "filename": "CMCReport.docx",
  "content": {"type": "doc", "content": []},
  "section_tree": {"node_id": "document", "children": []},
  "pagination": {
    "mode": "single_page_fallback",
    "physical_page_numbers_available": false,
    "is_estimated": true,
    "warning": "Word 未保存可靠分页标记"
  },
  "warnings": []
}
```

`availability = pending | ready | partial | failed`。`partial` 可表示结构 ready 而摘要仍处理/已按冻结政策回退。metadata 中没有 candidate、事实关系或业务提交状态。摘要文本没有可作为 fact 的 evidence ID。

## 5. 读取关系图谱

```http
GET /api/document-analysis/runs/{recognition_run_id}/graph?projection=effective_affirmed
```

`projection`：`effective_affirmed`（默认）、`all_candidates`、`unassociated`、`negated`、`conditional`、`undetermined`、`rejected`。它只过滤同一 `GraphSnapshot`，不得启动新任务。

尚无 snapshot 时返回 200 + pending；已有完整水位时：

```json
{
  "contract_version": "document-analysis-runs-v1",
  "recognition_run_id": "...",
  "run_revision": 8,
  "event_head": 35,
  "artifact_revision": 4,
  "availability": "partial",
  "projection": "effective_affirmed",
  "graph_snapshot": {
    "snapshot_id": "graph-snapshot:...",
    "analysis_id": "analysis:...",
    "metadata_snapshot_id": "metadata-snapshot:...",
    "ontology_snapshot_id": "ontology-snapshot:...",
    "root_ref": {"entity_id": "entity:root", "revision": 1},
    "projection_policy": "proof-gate-v1",
    "generated_at": "2026-09-08T12:03:00Z"
  },
  "entities": [
    {
      "entity_id": "entity:root",
      "revision": 1,
      "class_iri": "https://ontology.example/CMCReport",
      "class_label": "CMC 报告",
      "label": "CMCReport.docx",
      "seed_origin": "user_selected",
      "identity_state": "document_local",
      "independent_review": "unreviewed",
      "source_selection_refs": []
    }
  ],
  "properties": [],
  "relationships": [],
  "invalidated_refs": [],
  "coverage": {
    "candidate_policy": "sparse-candidates-v1",
    "subjects": [],
    "records_planned": 117,
    "records_examined": 8,
    "records_incomplete": 1,
    "records_unattempted": 108,
    "phase2_started": false,
    "pending_frontiers": 5,
    "stop_reason": null
  },
  "unresolved": {
    "unsupported": 4,
    "undetermined": 2,
    "not_checked": 3,
    "unassociated_entities": 0
  }
}
```

Property/relationship item 还必须包含：

- 精确 subject/object/value refs 与 revision；predicate IRI/label、方向、polarity、conditions/applicability。
- `structural_valid、model_supported、policy_eligible、independent_review` 四层状态。
- `proof_ref`、当前 decision refs、dependency refs 和失效/拒绝/待定 reason code。
- subject、object/value、predicate bridge、condition、counterevidence 的 `source_selection_refs`，不能只返回两端 span。

根 seed 不计为抽取 TP；仅根的空图必须同时显示运行/coverage，不能称“全文无关系”。

新运行的 `candidate_policy=sparse-candidates-v1` 表示 `records_*` 只统计实际准入的
主体—谓词候选任务；同一原文跨槽位可分别计数，未入选全文记录属于搜索诊断而非
unattempted。`progress.completion=policy_complete` 与
`stop_reason=candidate_search_exhausted` 表示本轮策略结束，状态为 finished，
不代表全文事实穷尽。失败、预算不足与未执行的必要补验仍阻止该完成结论；已完成
核验的语义未决/冲突独立保留并展示，不要求其转为有效肯定事实才结束运行。
旧载荷省略新字段、保留原冻结口径；完整判据与迁移见
[候选完成契约](../../022-semantic-graph-closure/contracts/candidate-completion.md)。

## 6. 读取原文与定位

### 6.1 Preview / evidence selection

```http
GET /api/document-analysis/runs/{recognition_run_id}/source
GET /api/document-analysis/runs/{recognition_run_id}/source?selection_ref=selection%3A...
```

`200` JSON：

```json
{
  "contract_version": "document-analysis-runs-v1",
  "recognition_run_id": "...",
  "analysis_id": "analysis:...",
  "document_hash": "<sha256>",
  "structure_hash": "<sha256>",
  "filename": "CMCReport.docx",
  "content": {"type": "doc", "content": []},
  "selection": {
    "selection_ref": "selection:...",
    "section_node_id": "section:12",
    "source_record_ref": "record:...",
    "record_view_ref": "record-view:...",
    "source_cell_id": "cell:...",
    "span_refs": ["span:..."],
    "selection_role": "predicate_bridge"
  }
}
```

`selection_ref` 必须是该运行 snapshot 已登记的 opaque ref；不接受 path、URL、任意 evidence ID 或客户端坐标。不存在/不属于运行返回 404/422。

### 6.2 Original download

```http
GET /api/document-analysis/runs/{recognition_run_id}/source?format=original
```

返回授权原件的 `StreamingResponse`、正确 media type、`Content-Disposition` 和 `X-Document-Hash`。服务端从 run artifact 解析路径；客户端不能提交路径。源已按合法删除/到期清理时返回 410，不返回无法回放的旧图内容。

## 7. Durable SSE

```http
GET /api/document-analysis/runs/{recognition_run_id}/events
Accept: text/event-stream
Last-Event-ID: 34
```

浏览器不能设置 Authorization 时可使用现有签名 bearer `?token=...`；不得使用未签名 owner/role 参数绕过生产认证。token、源片段和 prompt 不写通用访问日志。

事件格式：

```text
id: 35
event: artifact
data: {"contract_version":"document-analysis-runs-v1","event_id":"event:...","recognition_run_id":"...","run_revision":8,"event_head":35,"artifact_revision":4,"status":"running","stage":"extracting","artifact_kind":"graph","availability":"partial"}

```

允许事件：`run_state、progress、artifact、warning、error、heartbeat、tombstone`。data 只含安全状态/计数/水位；artifact 事件额外包含 `artifact_kind` 与 `availability`，不含 artifact link、原文、模型 prompt/response 或 execution token。

- 事件来自 durable `document_analysis_events`，进程内 bus 仅作唤醒。
- `Last-Event-ID` 后按 sequence 重放；重复连接不创建任务。
- cursor 仍保留但 run 没有新事件时发送 heartbeat。
- cursor 已被合法 retention 清理时，返回 410 + 当前 GET links；客户端以 GET 重建，不重启运行。
- 断线不改变 run；EventSource 迟到事件必须由 run ID/event head 过滤。

## 8. 控制运行

所有控制 body 使用：

```json
{
  "expected_revision": 8,
  "request_key": "unique-operation-key",
  "reason": "用户请求暂停以检查当前结果"
}
```

`request_key` 在 `(run_id, operation)` 范围幂等；reason 1—4000 字符。相同操作键/内容返回原结果，不重复推进 revision；内容不同 409。该保证跨越异步物理清理边界：公开 DELETE 的运行行和控制表已移除后，owner 使用原键和原内容仍得到首次 202 的完全相同响应，且不再次调度清理。

### 8.1 Pause

```http
POST /api/document-analysis/runs/{recognition_run_id}/pause
```

- 允许 `queued | running`；返回 `202`，状态可先为 running + `pause_requested=true`。
- worker 在下一个安全 batch 边界提交 paused event/checkpoint；已提交 artifact 保留。
- 已 paused 的同键调用幂等；finished/cancelled/deleting/deleted/expired 返回 409/410。

### 8.2 Resume

```http
POST /api/document-analysis/runs/{recognition_run_id}/resume
```

- 仅 `paused | retryable_failure` 且未到期。
- 提交前验证 final fingerprint、checkpoint event head 和 artifact/object heads；不匹配 409，不尝试旧 runner。
- 返回 202、同一 recognition_run_id、新 execution generation；不重做已持久完成任务。

### 8.3 Cancel

```http
POST /api/document-analysis/runs/{recognition_run_id}/cancel
```

- 允许活动/paused/retryable/blocked；原子撤销 token 并进入 cancelled，返回 202/200 当前状态。
- cancel 不删除已有 artifact，但不可 resume；coverage 保存真实未完成范围。
- finished 不接受 cancel，返回 409 且保持 finished；删除需独立调用 DELETE。

### 8.4 Delete

```http
DELETE /api/document-analysis/runs/{recognition_run_id}?expected_revision=9&request_key=delete-op-key
```

- 先 CAS 置 `deleting` 并撤销 execution generation，再异步清理未发布独占产物；返回 202。
- shared artifact 只删本 run 引用；保留最小 tombstone，删除完成后 owner GET 返回 410。tombstone 只保留 owner hash、fence/final state 以及公开 DELETE 的 request key/hash 和字段受限的原始控制响应/hash，不保留正文、候选、proof、artifact 或可恢复业务内容。
- 物理清理完成后，相同 owner 以相同 `request_key + expected_revision` 重试 DELETE，仍返回首次 202 响应；相同 key 改变输入返回 409，其他 key 返回 410，其他 owner 一律返回 404。任何重放都不得再次提交清理任务。
- 不自动删除中央 KG、EvidenceCommit、全局 audit、本体、模型或其他运行。
- 若依赖分类为已发布/共享/未知，删除失败并进入带安全错误的 blocked state，不扩大目标。

Success response（所有控制共用）：

```json
{
  "contract_version": "document-analysis-runs-v1",
  "recognition_run_id": "...",
  "run_revision": 9,
  "event_head": 36,
  "artifact_revision": 4,
  "status": "paused",
  "stage": "extracting",
  "operation": "pause",
  "operation_status": "accepted",
  "available_actions": ["resume", "cancel", "delete"]
}
```

## 9. 状态/Artifact 组合

| Scenario | Required response behavior |
|---|---|
| metadata ready, graph running | metadata=ready；graph=partial/pending；同一 run/analysis/metadata ID |
| 摘要失败但结构可用 | metadata=partial/ready + honest fallback/structure_only；Tab GET 不重试 |
| graph failed/paused/cancelled | metadata 和最后完整 graph snapshot 可读；error/coverage/available actions 真实 |
| 尚无有效边 | graph 为空且包含 pending/coverage/undetermined，不声明全文无关系 |
| SSE reconnect | 从 durable event ID 续读或 GET 重建；模型调用计数不增加 |
| source deleted/expired | owner 410；不能显示仍可回放的假证据 |

## 10. 明确不存在的副作用/兼容面

- 不存在 `POST /api/document-analysis/word`；旧路由测试应为 404，旧 `analyzeWordDocument` 客户端符号删除。
- `/runs` 不调用 `create_auto_job`、`_compute_annotation` 的旧 Word runner、`CandidateStore.persist_validated`、自动 review 或 FactCommit。
- 新 graph 不接受客户端 candidates、checkpoint、model verdict、IRI 菜单或 source path。
- 没有灰度/旧算法选择字段；离线冻结评测可保留旧 runtime，但不挂在线 API。
- 本契约没有人工编辑/确认/发布业务事实接口；`independent_review` 默认 `unreviewed`，不得由完成状态提升。

## 11. OpenAPI/契约测试要求

实现前必须先固定 Pydantic request/response model 和以下 contract tests：

1. multipart 必填/类型/大小/root policy/幂等及 202。
2. owner + role 在 run/status/metadata/graph/source/SSE/control/delete 全路径一致。
3. 每个 GET 在模型、解析和写 store 上均为零调用。
4. metadata/graph 水位自洽，partial/failed/paused/empty 组合可序列化。
5. pause/resume/cancel/delete CAS、幂等、不可逆和 lost-worker fencing。
6. SSE durable resume、过期 cursor 和迟到 run 过滤。
7. source selection 只接受 run-owned opaque ref；任意 path/URL/foreign ref 拒绝。
8. 旧路由和客户端不可达；新运行不创建 ExtractionJob/旧候选/commit。
