# Quickstart: 本体指引的文档分析运行验收

> **当前状态：工程实现验证，发布门禁未完成。** 新 API、迁移、领域核心、持久恢复、前端及退役防护已在当前工作区落地，实际命令和限制记录在 `validation.md`。本文件仍是可重跑契约；它不表示真实 PostgreSQL、浏览器、真实模型/专家质量、旧数据清理、部署或正式切换已完成。

## 1. 验收边界

2026-09-13 新候选策略验收见
[022 候选完成场景](../022-semantic-graph-closure/quickstart.md#候选台账与本轮完成2026-09-13)。
新运行 `candidate_policy=sparse-candidates-v1` 的 records_* 是实际准入任务；旧冻结
运行沿用原记录覆盖。不要对旧运行原地更换策略、重置预算，或将本轮完成当作全文穷尽。

一次合格验证必须把以下证据分开记录：

1. 纯领域与契约测试：菜单、引用、身份、谓词证明、两阶段守恒、依赖与图投影。
2. SQLite 快速回归：schema、序列化、基本 CAS；不证明生产并发。
3. 隔离 PostgreSQL：claim、lease/fencing、并发 head、事件事务和清理闭包。
4. API 与安全：202、幂等、owner/授权范围、SSE、source、控制、保留和 GET 零副作用。
5. 前端：显式开始、两个结果 Tab、水位/迟到响应隔离、证据定位与 production build/browser。
6. 真实质量：预测前冻结的专家标注、保留集和批准 SLO，关键正反例至少三个独立新运行。
7. 退役与清理：只读 manifest、G-C03、保护对象、旧入口残留和维护窗口记录。

默认 pytest 使用 SQLite/FakeOntologyEngine 且可能跳过 PostgreSQL，Node/browser 也可使用合成响应；这些结果不能彼此替代。所有命令须把实际环境、收集数、passed/failed/skipped、版本/hash 和证据路径写入 `validation.md`。

## 2. 准备本地开发环境

前置条件：Python >=3.11 的 `backend/.venv` 已同步，前端依赖已安装，测试用 PostgreSQL 是专用可销毁数据库，且待测 Word 文件不含生产敏感数据。运行期不需要公网。

```bash
cd /opt/dev/chen/ontology-agent
git branch --show-current
git status --short

cd backend
.venv/bin/alembic upgrade head
.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000
```

另一个终端启动前端：

```bash
cd /opt/dev/chen/ontology-agent/frontend
NEXT_PUBLIC_API_URL=http://127.0.0.1:8000 npm run dev
```

打开 `http://127.0.0.1:3000/analysis?tab=document`。选择文件本身不得发出上传、解析、摘要或模型请求；只有文件与合法完整根 IRI 都存在且用户点击“开始分析”后才能创建运行。

## 3. 后端目标测试命令

### 3.1 领域核心和契约

实现完成后，先运行不依赖真实模型的确定性集合：

```bash
cd /opt/dev/chen/ontology-agent/backend
.venv/bin/pytest -p no:cacheprovider -q \
  tests/test_extraction/test_ontology_guided_boundaries.py \
  tests/test_extraction/test_ontology_guided_core.py \
  tests/test_extraction/test_ontology_guided_evaluation.py \
  tests/test_extraction/test_evaluation_citation_protocol.py \
  tests/test_extraction/test_evaluation_citation_domains_v3.py \
  tests/test_extraction/test_evaluation_citation_tables_v2.py \
  tests/test_cmc_intermediate_ontology.py \
  tests/test_reasoning/test_ttl_roundtrip.py
```

最低判据：AC-T01—AC-T19 与 AC-T31 全部有正向与失败关闭断言；明确组成正例能通过，缺桥接、来源号身份、错误 owner/层级反例不能进入有效肯定图；phase1 拒绝后必须看到 phase2 不同记录的实际 task/call event，不能只检查队列非空。权威 CMC TTL 必须保留路线/步骤到最终产品/API、中间体标识、产品/API 的 0..* 中间体可推导关系及两条属性链；顺序只来自 `stepOrder`/`nextStep`，匿名 union/属性链往返不丢失，且无 reasoner 时不把推导关系冒充已物化事实。

### 3.2 运行仓储、API 和生命周期

```bash
cd /opt/dev/chen/ontology-agent/backend
.venv/bin/pytest -p no:cacheprovider -q \
  tests/test_extraction/test_document_analysis_run_store.py \
  tests/test_extraction/test_document_analysis_artifact_store.py \
  tests/test_extraction/test_document_analysis_dispatcher.py \
  tests/test_extraction/test_document_analysis_public_projection.py \
  tests/test_extraction/test_document_analysis_execution_recovery.py \
  tests/test_extraction/test_document_analysis_retention.py \
  tests/test_api/test_document_analysis.py \
  tests/test_api/test_document_analysis_schemas.py \
  tests/test_api/test_document_analysis_events.py \
  tests/test_api/test_retired_word_analysis.py
```

最低判据：稳定 `recognition_run_id` 与每次领取的新 execution token 分离；所有对象保留真实 revision；同 batch 幂等而异 hash 冲突；metadata/graph 水位闭合；GET/SSE/filter 的解析、摘要、模型和写 store 调用均为 0；跨 owner、任意路径、伪造 selection 和同 hash 越权全部失败关闭。

### 3.3 隔离 PostgreSQL（发布必需）

将下例 DSN 替换为专用测试库。测试必须拒绝非测试数据库，并在结束后核对没有活动 lease 或孤立 artifact。

```bash
cd /opt/dev/chen/ontology-agent/backend
export DOCUMENT_ANALYSIS_TEST_DATABASE_URL='postgresql+psycopg2://test_user:test_password@127.0.0.1:5432/slpra_document_analysis_test'
.venv/bin/pytest -p no:cacheprovider -q \
  tests/test_extraction/test_document_run_execution_postgresql.py \
  tests/test_integration/test_word_domain_cleanup.py
```

未设置 DSN 而被 skip、只在同一 SQLAlchemy session 中模拟并发、或仅由 SQLite 通过，都必须记录为发布门未完成。测试至少覆盖两个独立连接竞争、租约过期、迟到响应、CAS 冲突、重复 delivery、文件—事务故障和共享 artifact 引用检查。

### 3.4 全量与静态门

```bash
cd /opt/dev/chen/ontology-agent/backend
.venv/bin/pytest -p no:cacheprovider -q
.venv/bin/ruff check \
  alembic/versions/0033_document_analysis_runs.py \
  app/api/document_analysis.py \
  app/schemas/document_analysis.py \
  app/models/document_analysis.py \
  app/services/document_analysis \
  app/services/extraction/ontology_guided \
  tests/test_api/test_document_analysis.py \
  tests/test_api/test_document_analysis_events.py \
  tests/test_api/test_document_analysis_schemas.py \
  tests/test_api/test_retired_word_analysis.py \
  tests/test_cmc_intermediate_ontology.py \
  tests/test_extraction/test_document_analysis_artifact_store.py \
  tests/test_extraction/test_document_analysis_dispatcher.py \
  tests/test_extraction/test_document_analysis_execution_recovery.py \
  tests/test_extraction/test_document_analysis_public_projection.py \
  tests/test_extraction/test_document_analysis_retention.py \
  tests/test_extraction/test_document_analysis_run_store.py \
  tests/test_extraction/test_document_run_execution_postgresql.py \
  tests/test_extraction/test_ontology_guided_boundaries.py \
  tests/test_extraction/test_ontology_guided_core.py \
  tests/test_extraction/test_ontology_guided_evaluation.py \
  tests/test_reasoning/test_ttl_roundtrip.py \
  ../scripts/audit_word_recognition_retirement.py \
  ../scripts/cleanup_word_recognition.py
.venv/bin/ruff check app tests
```

全仓既有 lint 债务与本特性定向结果分别报告。本特性路径必须零新增错误；不得把全量基线问题隐去，也不得因基线问题声称本特性定向失败无关紧要。

## 4. 前端目标测试命令

```bash
cd /opt/dev/chen/ontology-agent
node --test \
  frontend/tests/document-analysis-runs.test.mjs \
  frontend/tests/document-analysis-retirement.test.mjs

cd frontend
npx tsc --noEmit
npx eslint \
  'src/app/(dashboard)/analysis/page.tsx' \
  src/components/analysis/document-analysis-panel.tsx \
  src/components/analysis/document-relationship-graph.tsx \
  src/lib/api.ts
npm run lint
npm run build
```

已有两个浏览器脚本，均需显式提供外置 Playwright 模块。`document-analysis-browser.mjs`
读取指定隔离服务与已准备运行，验证真实 API 读取与证据定位；
`document-analysis-history-browser.mjs` 拦截合成 API，验证历史导航、工作区布局、标签切换
和窄屏显示。按脚本开头的环境变量说明配置服务与输出目录，不对共享运行执行创建测试。

```bash
cd /opt/dev/chen/ontology-agent
node frontend/tests/document-analysis-browser.mjs
node frontend/tests/document-analysis-history-browser.mjs
```

浏览器判据：分析历史位于左侧；桌面工作区共享章节树和文档预览，右侧只有“节点元数据”
和“关系图谱”两个同级 Tab。切换 Tab 不卸载预览，点击图谱证据保留图谱并定位原文。
两个 Tab 的 run/analysis/metadata identity 一致；metadata 可先显示；默认图不混入
拒绝/条件/待定边；证据能定位到同一运行的段落或物理表格单元；刷新、SSE 重连、筛选和
快速换运行不会额外 POST、调用模型或让迟到响应串文档。外置 Playwright 缺失或浏览器
脚本只拦截合成 API 时须明确记录，不能冒充完整真实集成。布局专项结果另记于
[工作区布局验证](layout-validation.md)。

## 5. 手工 API 流程

以下使用开发环境可信网关头演示；启用正式认证时改用登录得到的 bearer token。示例角色只是当前开发身份，不定义领域契约的固定角色名。需要 `curl` 和 `jq`。

本节严格使用 [API contract](./contracts/document-analysis-runs-api.md) 的字段：

| Flow | Request / response fields |
|---|---|
| create | multipart `file`、`root_class_iri`、`request_key`、可选 `metadata_mode`；首次 202 为 `status=queued`、`stage=accepted` |
| status | 顶层 `run_revision`、`event_head`、`artifact_revision`、`status`、`stage`；调用数在 `progress.model_calls`；冻结身份在 `identities` |
| metadata | 顶层 `availability`；IR 身份为 `analysis.analysis_id`，元数据身份为 `metadata_snapshot.snapshot_id` |
| graph | query `projection` 只能取 `effective_affirmed`、`all_candidates`、`unassociated`、`negated`、`conditional`、`undetermined`、`rejected`；响应回显顶层 `projection`；`entities`、`properties`、`relationships`、`coverage` 在响应顶层，`graph_snapshot` 只公开快照身份/水位头，不暴露内部持久化 payload |
| source | preview 为 JSON；原件下载显式使用 `?format=original`；定位只接受本运行登记的 opaque `selection_ref` |
| events | `Last-Event-ID` 使用 SSE `id:` 的数字 sequence，不使用 data 内的 opaque `event_id` |
| pause/resume/cancel | JSON body 固定为 `expected_revision`、`request_key`、`reason` |
| delete | `expected_revision` 与 `request_key` 是 DELETE query 参数；控制响应含 `operation`、`operation_status` 和服务端计算的 `available_actions` |

```bash
API_BASE='http://127.0.0.1:8000/api'
DOC_FILE='/absolute/path/to/CMCReport.docx'
ROOT_CLASS_IRI='https://ontology.pharma-gmp.cn/slpra/drug-development/CMCReport'
CREATE_REQUEST_KEY='quickstart-create-001'
AUTH_ARGS=(-H 'X-User: analyst' -H 'X-Role: senior_analyst')

CREATE_RESPONSE=$(curl -fsS "${AUTH_ARGS[@]}" \
  -F "file=@${DOC_FILE};type=application/vnd.openxmlformats-officedocument.wordprocessingml.document" \
  -F "root_class_iri=${ROOT_CLASS_IRI}" \
  -F "request_key=${CREATE_REQUEST_KEY}" \
  -F 'metadata_mode=generate_summary' \
  "${API_BASE}/document-analysis/runs")

printf '%s\n' "$CREATE_RESPONSE" | jq .
RUN_ID=$(printf '%s' "$CREATE_RESPONSE" | jq -r '.recognition_run_id')
test "$(printf '%s' "$CREATE_RESPONSE" | jq -r '.contract_version')" = 'document-analysis-runs-v1'
test "$(printf '%s' "$CREATE_RESPONSE" | jq -r '.status')" = 'queued'
test "$(printf '%s' "$CREATE_RESPONSE" | jq -r '.stage')" = 'accepted'
test "$(printf '%s' "$CREATE_RESPONSE" | jq -r '.run_revision')" = '1'
test "$(printf '%s' "$CREATE_RESPONSE" | jq -r '.event_head')" = '1'
test "$(printf '%s' "$CREATE_RESPONSE" | jq -r '.artifact_revision')" = '1'
```

创建响应必须是 202，返回 run/status/metadata/graph/source/events links。用相同 owner、request key、文件、规范文件名、根 IRI 和模式重放，必须返回同一 `RUN_ID` 且不创建任务；同键改变任一输入必须返回 409。

等 metadata 已为 `ready|partial` 且 graph 已有 snapshot 后，读取状态、元数据、图和原文，并按契约中的实际嵌套路径核对身份：

```bash
STATUS_RESPONSE=$(curl -fsS "${AUTH_ARGS[@]}" \
  "${API_BASE}/document-analysis/runs/${RUN_ID}")
METADATA_RESPONSE=$(curl -fsS "${AUTH_ARGS[@]}" \
  "${API_BASE}/document-analysis/runs/${RUN_ID}/metadata")
GRAPH_RESPONSE=$(curl -fsS "${AUTH_ARGS[@]}" \
  "${API_BASE}/document-analysis/runs/${RUN_ID}/graph?projection=effective_affirmed")
SOURCE_RESPONSE=$(curl -fsS "${AUTH_ARGS[@]}" \
  "${API_BASE}/document-analysis/runs/${RUN_ID}/source")

printf '%s\n' "$STATUS_RESPONSE" | jq .
printf '%s\n' "$METADATA_RESPONSE" | jq .
printf '%s\n' "$GRAPH_RESPONSE" | jq .
printf '%s\n' "$SOURCE_RESPONSE" | jq .

STATUS_ANALYSIS_ID=$(printf '%s' "$STATUS_RESPONSE" | jq -r '.identities.analysis_id')
STATUS_METADATA_ID=$(printf '%s' "$STATUS_RESPONSE" | jq -r '.identities.metadata_snapshot_id')
METADATA_ANALYSIS_ID=$(printf '%s' "$METADATA_RESPONSE" | jq -r '.analysis.analysis_id')
METADATA_SNAPSHOT_ID=$(printf '%s' "$METADATA_RESPONSE" | jq -r '.metadata_snapshot.snapshot_id')
GRAPH_ANALYSIS_ID=$(printf '%s' "$GRAPH_RESPONSE" | jq -r '.graph_snapshot.analysis_id')
GRAPH_METADATA_ID=$(printf '%s' "$GRAPH_RESPONSE" | jq -r '.graph_snapshot.metadata_snapshot_id')
SOURCE_ANALYSIS_ID=$(printf '%s' "$SOURCE_RESPONSE" | jq -r '.analysis_id')

test "$(printf '%s' "$GRAPH_RESPONSE" | jq -r '.projection')" = 'effective_affirmed'
printf '%s' "$GRAPH_RESPONSE" | jq -e \
  'has("entities") and has("properties") and has("relationships") and has("coverage")' >/dev/null
test "$STATUS_ANALYSIS_ID" = "$METADATA_ANALYSIS_ID"
test "$STATUS_ANALYSIS_ID" = "$GRAPH_ANALYSIS_ID"
test "$STATUS_ANALYSIS_ID" = "$SOURCE_ANALYSIS_ID"
test "$STATUS_METADATA_ID" = "$METADATA_SNAPSHOT_ID"
test "$STATUS_METADATA_ID" = "$GRAPH_METADATA_ID"
```

每个响应都必须有相同 `recognition_run_id`；metadata 与 graph 必须引用相同 `analysis_id`/`metadata_snapshot_id`，graph 的内容只来自一个完整 `event_head`。根 seed 不计为抽取真阳性，空图必须同时显示真实 coverage，不能写成“全文无关系”。

在另一终端订阅 durable SSE，观察到事件 ID 后可中断并用该 ID 重连：

```bash
API_BASE='http://127.0.0.1:8000/api'
RUN_ID='a4f9885d-1b70-4718-9a97-2eb03c3ef8c4'
AUTH_ARGS=(-H 'X-User: analyst' -H 'X-Role: senior_analyst')

curl -N "${AUTH_ARGS[@]}" \
  -H 'Accept: text/event-stream' \
  "${API_BASE}/document-analysis/runs/${RUN_ID}/events"

LAST_EVENT_ID='35'
curl -N "${AUTH_ARGS[@]}" \
  -H 'Accept: text/event-stream' \
  -H "Last-Event-ID: ${LAST_EVENT_ID}" \
  "${API_BASE}/document-analysis/runs/${RUN_ID}/events"
```

SSE 必须从 durable event 续读；游标已按保留策略清理时返回 410 并要求客户端用只读 GET 重建，不能重启运行。不得用未签名 `owner`/`role` 查询参数绕过认证。

### 5.1 Pause、只读零副作用、resume、cancel、delete

先读最新 revision，再请求暂停：

```bash
RUN_REVISION=$(curl -fsS "${AUTH_ARGS[@]}" \
  "${API_BASE}/document-analysis/runs/${RUN_ID}" | jq -r '.run_revision')

PAUSE_RESPONSE=$(curl -fsS -X POST "${AUTH_ARGS[@]}" \
  -H 'Content-Type: application/json' \
  -d "{\"expected_revision\":${RUN_REVISION},\"request_key\":\"quickstart-pause-001\",\"reason\":\"检查当前持久水位\"}" \
  "${API_BASE}/document-analysis/runs/${RUN_ID}/pause")

printf '%s\n' "$PAUSE_RESPONSE" | jq .
test "$(printf '%s' "$PAUSE_RESPONSE" | jq -r '.operation')" = 'pause'
test "$(printf '%s' "$PAUSE_RESPONSE" | jq -r '.operation_status')" = 'accepted'
```

202 仅表示 pause 已接受；必须轮询到 worker 在安全批次边界提交 `paused`，不能由前端立即假定暂停完成。暂停后验证 GET、projection 切换和 Tab 读取不增加模型调用：

```bash
CALLS_BEFORE=$(curl -fsS "${AUTH_ARGS[@]}" \
  "${API_BASE}/document-analysis/runs/${RUN_ID}" | jq -r '.progress.model_calls')

METADATA_READ=$(curl -fsS "${AUTH_ARGS[@]}" \
  "${API_BASE}/document-analysis/runs/${RUN_ID}/metadata")
EFFECTIVE_GRAPH=$(curl -fsS "${AUTH_ARGS[@]}" \
  "${API_BASE}/document-analysis/runs/${RUN_ID}/graph?projection=effective_affirmed")
ALL_GRAPH=$(curl -fsS "${AUTH_ARGS[@]}" \
  "${API_BASE}/document-analysis/runs/${RUN_ID}/graph?projection=all_candidates")
curl -fsS "${AUTH_ARGS[@]}" \
  "${API_BASE}/document-analysis/runs/${RUN_ID}/source" >/dev/null

CALLS_AFTER=$(curl -fsS "${AUTH_ARGS[@]}" \
  "${API_BASE}/document-analysis/runs/${RUN_ID}" | jq -r '.progress.model_calls')
test "$CALLS_BEFORE" = "$CALLS_AFTER"
test "$(printf '%s' "$EFFECTIVE_GRAPH" | jq -r '.projection')" = 'effective_affirmed'
test "$(printf '%s' "$ALL_GRAPH" | jq -r '.projection')" = 'all_candidates'
test "$(printf '%s' "$EFFECTIVE_GRAPH" | jq -r '.event_head')" = \
  "$(printf '%s' "$ALL_GRAPH" | jq -r '.event_head')"
test "$(printf '%s' "$EFFECTIVE_GRAPH" | jq -r '.graph_snapshot.snapshot_id')" = \
  "$(printf '%s' "$ALL_GRAPH" | jq -r '.graph_snapshot.snapshot_id')"
```

恢复必须保持同一 run ID、更换 execution generation，并从 checkpoint 水位继续：

```bash
RUN_REVISION=$(curl -fsS "${AUTH_ARGS[@]}" \
  "${API_BASE}/document-analysis/runs/${RUN_ID}" | jq -r '.run_revision')

RESUME_RESPONSE=$(curl -fsS -X POST "${AUTH_ARGS[@]}" \
  -H 'Content-Type: application/json' \
  -d "{\"expected_revision\":${RUN_REVISION},\"request_key\":\"quickstart-resume-001\",\"reason\":\"继续验证恢复\"}" \
  "${API_BASE}/document-analysis/runs/${RUN_ID}/resume")

printf '%s\n' "$RESUME_RESPONSE" | jq .
test "$(printf '%s' "$RESUME_RESPONSE" | jq -r '.operation')" = 'resume'
test "$(printf '%s' "$RESUME_RESPONSE" | jq -r '.operation_status')" = 'accepted'
test "$(printf '%s' "$RESUME_RESPONSE" | jq -r '.recognition_run_id')" = "$RUN_ID"
```

取消会撤销执行权并不可恢复；删除随后异步清理本运行独占且未发布的内容并保留最小墓碑：

```bash
RUN_REVISION=$(curl -fsS "${AUTH_ARGS[@]}" \
  "${API_BASE}/document-analysis/runs/${RUN_ID}" | jq -r '.run_revision')

CANCEL_RESPONSE=$(curl -fsS -X POST "${AUTH_ARGS[@]}" \
  -H 'Content-Type: application/json' \
  -d "{\"expected_revision\":${RUN_REVISION},\"request_key\":\"quickstart-cancel-001\",\"reason\":\"结束 quickstart 运行\"}" \
  "${API_BASE}/document-analysis/runs/${RUN_ID}/cancel")

printf '%s\n' "$CANCEL_RESPONSE" | jq .
test "$(printf '%s' "$CANCEL_RESPONSE" | jq -r '.operation')" = 'cancel'
test "$(printf '%s' "$CANCEL_RESPONSE" | jq -r '.operation_status')" = 'accepted'

RUN_REVISION=$(curl -fsS "${AUTH_ARGS[@]}" \
  "${API_BASE}/document-analysis/runs/${RUN_ID}" | jq -r '.run_revision')

DELETE_RESPONSE=$(curl -fsS -X DELETE "${AUTH_ARGS[@]}" \
  "${API_BASE}/document-analysis/runs/${RUN_ID}?expected_revision=${RUN_REVISION}&request_key=quickstart-delete-001")

printf '%s\n' "$DELETE_RESPONSE" | jq .
test "$(printf '%s' "$DELETE_RESPONSE" | jq -r '.operation')" = 'delete'
test "$(printf '%s' "$DELETE_RESPONSE" | jq -r '.operation_status')" = 'accepted'
```

删除进入 `deleting` 前必须先 fence 旧 token。完成后 owner GET 返回 410 且不含正文/候选，其他用户仍按统一防枚举策略返回 404；任何迟到 worker batch 都必须被拒绝。共享 artifact 只删除 run 引用。

## 6. 旧实现退役检查

实现完成且尚未执行生产清理时，先做静态与 API 检查：

```bash
cd /opt/dev/chen/ontology-agent
rg -n 'analyzeWordDocument|/document-analysis/word|legacy.*runner|old.*checkpoint' \
  backend/app frontend/src scripts

curl -sS -o /dev/null -w '%{http_code}\n' \
  -X POST http://127.0.0.1:8000/api/document-analysis/word
```

最终唯一实现中，旧同步路由必须返回 404，旧客户端/runner/恢复器不得从生产可达路径命中。静态搜索结果若来自冻结历史评测或退役审计 allowlist，须逐项列入 validation；不能用宽泛忽略隐藏在线引用。Excel、数据库、声明式和 `template_default` 共享能力必须单独回归。

## 7. 清理 manifest 与 G-C03

只允许先运行只读盘点和 dry-run：

```bash
cd /opt/dev/chen/ontology-agent
MANIFEST_PATH='specs/021-ontology-guided-doc-graph/retirement/old-word-domain-manifest.json'

backend/.venv/bin/python scripts/audit_word_recognition_retirement.py \
  --output "$MANIFEST_PATH"

backend/.venv/bin/python scripts/cleanup_word_recognition.py \
  --manifest "$MANIFEST_PATH" \
  --dry-run
```

manifest 必须逐项给出精确表/主键或已解析绝对文件路径、hash、来源模式、独占/共享归属、完整引用、发布状态、动作和切后核验，并单列以下集合：

- `unpublished_exclusive`：撤销所有写入资格并核验依赖闭包后，才可能进入物理删除集合。
- `shared_or_unknown`：不得删除；先解决所有权与引用。
- `published_or_confirmed`：不得物理删除；只能保留历史并追加撤销/失效/消费隔离批次。

dry-run 必须在以下任一条件成立时非零退出并输出 G-C03 blocked：物理删除集合含已确认、已提交、已发布、被引用内容或不可变审计历史；归属/引用未知；旧 lease/晚到写仍有效；manifest hash 不匹配；保护对象核验缺失。脚本不得提供 `--force` 或等价绕过。

**本 quickstart 不提供生产 execute 命令。** 只读 manifest 或 dry-run 成功不等于获准清理。只要 `published_or_confirmed` 物理删除集合非空，或独立质量门/保留集/批准 SLO、真实 PostgreSQL、权限、浏览器、T01—T31、维护窗口任一证据不可用，就禁止运行破坏性清理，也禁止正式切换。若确实要求物理删除已发布内容，必须先完成 Constitution 修订或正式治理决定，再重新生成并审批 manifest。

## 8. 完成判据

历史入口补充验收（2026-09-09）：在 `/analysis?tab=document` 显式创建两个同名 Word 分析，确认历史列表分别显示两条任务及创建时间/状态。关闭结果、刷新，再从历史列表打开旧任务；确认 URL 的 `documentRun` 与所选任务一致，分层元数据和图谱沿用同一运行，网络无新建任务 POST。超过 10 条后翻页，模拟列表 GET 失败再重试；快速切换历史项不会显示上一任务迟到的结果。其他 owner、已删除和已过期任务不在列表中，现有保留政策继续生效。

只有以下全部有可复现证据时才能把特性标为可切换：

- AC-T01—AC-T31 的确定性、本体、API、前端和执行契约无关键失败，skipped 单列且发布必需项为 0；其中 AC-T31 的权威 TTL、schema/local menu、匿名 union/属性链往返和未物化边界均有独立证据。
- 隔离 PostgreSQL 证明唯一 lease/fencing、原子事件/对象 head、故障恢复和共享清理边界。
- 两个 Tab 共用同一 run/IR/MetadataSnapshot；GET、SSE、刷新、Tab/筛选切换新增模型调用为 0。
- 每条有效实体/属性/关系都有当前精确 revision 和可回放原文；摘要、同名、邻近或父路径单独产生的有效事实为 0。
- 独立专家标注、保留集和批准质量门已在预测前冻结；明确两跳正例有独立证明，关键无桥接/身份/owner 反例在至少三个新运行中未被错误接受。
- G-C03 通过：已发布/确认/共享/未知内容不在物理删除集合，保护对象不变，清理与残留计数可对账。
- 旧同步路由、旧 Word runner、旧 checkpoint 恢复和旧客户端生产可达数为 0；新运行写旧 CandidateStore 或中央事实库次数为 0。

缺少任一项时，可以继续开发和非破坏性验证，但 `validation.md` 必须标记发布/清理/切换为 pending 或 blocked，不能用“测试桩全绿”“图连通”或“全部拒绝”替代真实完成。


## 报告预览专家意见入口（2026-09-11）

按用户进一步要求实施可保存、重读及导出的专家意见入口；需求、权限、版本、幂等及验收见[契约](contracts/expert-opinions.md)。该意见不自动变更图谱或校准质量状态。

使用高级分析师或 QA 打开「报告中心 → 预览 → 专家意见」，选择类型、填写意见并保存；
重新打开应显示个人历史，可以导出 JSON。普通角色无提交表单。原文版本变化应拒绝保存，
翻阅历史不应清空正在填写的草稿，所有 GET 操作不得创建识别任务。

后端定向回归（在 `backend/`）：
```bash
.venv/bin/python -m pytest -p no:cacheprovider -q tests/test_api/test_report_expert_opinions.py tests/test_api/test_report_document_runs.py
```

前端验证（在 `frontend/`，浏览器脚本支持指定已有的 `ESBUILD_MODULE`、`PLAYWRIGHT_MODULE`）：
```bash
./node_modules/.bin/tsc --noEmit
npm run lint -- src/components/reports/expert-opinion-entry.tsx src/components/reports/report-word-workspace.tsx src/components/reports/batch-demo-workspace.tsx 'src/app/(dashboard)/reports/[reportId]/page.tsx' src/lib/api.ts
node tests/expert-opinion-browser.mjs
node tests/report-word-workspace-browser.mjs
```

部署核对数据库实际 revision 为 `0036_report_expert_opinions`，并检查入口 GET。
若有识别在途，按既有运行控制协议暂停后重启，完成后仅恢复本次暂停的运行。
