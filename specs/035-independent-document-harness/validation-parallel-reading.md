# 阅读窗口并行与后置共指实施验证

日期：2026-10-01。对应 PW-FR-001—010 和独立方案 A01—A20。代码已实现、工程验证通过，并按用户后续授权部署到现有环境；部署验收见下文。

## 实际行为

- 新运行冻结 `flow=local_reading`、`reading_concurrency=2`，模型协议为 `document-harness-v5`。并发设为 1 时仍走同一流程；平台全局模型限额不变。
- 阶段顺序为 `reading → entities → coreference → graph → done`。阅读只用本窗口原文和固定本体指引，删除 `known_mentions`；任一请求完成即释放窗口槽位。
- `reading.py` 纯解码与确定性合并：原字段、文档根字段、重叠物理提及和名称候选保留；归属按窗口顺序确定，不依赖回答到达顺序。
- `active_batches` 是唯一当前恢复入口中的有界映射。`batch_id` 表示应用目标，`call_key` 表示精确请求；相同请求只传输、计账一次，但分别应用于对应窗口。
- 模型线程仅执行传输；协调线程负责业务 Session、账本、结果验证与增量保存。线程显式继承运行上下文并进入阶段 `model_scope`。普通暂停停止派发/重试，保存已开始的回答；继续复用已付费结果。
- lookup 每窗口保存 `draft_call_key` 和可选反馈，复用现有 draft/query/refine；不在窗口状态复制草稿请求和回答。
- 全部阅读收束后再核验实体、解析引用、核验共指和规划图谱。相同语义依赖的规划保留完成状态、属性菜单进度与已付费绑定；成员拆分重绑所有窗口及相关观察。
- 编号共指候选只使用本体身份属性、已声明命名空间/作用域及局部绑定原文齐全的键组。未核验的外部记录不作为身份证明，同名不创建共指任务。
- UI 使用 `progress.phase`、`reading_windows.{total,saved,complete,incomplete,active}`，阅读窗口仅统计叶窗口。公开 engine、路由不变；旧执行策略继续时明确要求新建运行。

`calls.py` 是独立执行/测试的内存端口，在线执行始终使用 `runtime.Repository` 的持久化请求和回答。没有新增数据库表、迁移、历史快照或第二套执行主循环。

## 工程结果

| 检查 | 本次结果 |
|---|---|
| 全 Harness、运行集成、依赖边界，并启用专用 PostgreSQL fixture | **608 passed，0 skipped**；4 个既有依赖警告；60.87 秒 |
| 主回归后的合并结合律补充与确定性同步测试 | `test_parallel_reading.py` **10 passed**；含 1 个新增三路候选合并反例 |
| 旧策略执行入口提示的最终定向回归 | `test_parallel_runtime.py` + `test_document_harness_runtime.py` **37 passed，1 skipped**；临时 PostgreSQL 已清理，该项已在上面的主回归通过 |
| 共享模型调度及调度性能回归 | **28 passed**，包括 4 路 HTTP 消费者共享全局 2 槽限额 |
| 前端全部现有 Node 测试 | **217 passed** |
| 前端 TypeScript `tsc --noEmit` | 通过 |
| 前端修改文件 ESLint | 通过 |
| 修改的在线 Python 文件、评测调用适配及新增并发测试 Ruff | 通过 |
| `git diff --check` | 通过 |

后端主回归命令（`backend/`；环境变量仅指向专用可销毁测试库）：

```bash
.venv/bin/python -m pytest -p no:cacheprovider -q \
  tests/test_document_harness \
  tests/test_extraction/test_document_harness_runtime.py \
  tests/test_extraction/test_ontology_guided_boundaries.py --tb=short
```

前端命令（`frontend/`）：

```bash
node --test tests/*.test.mjs
./node_modules/.bin/tsc --noEmit
npm run lint -- src/lib/api.ts src/components/analysis/source-harness-panel.tsx
```

### 并发与语义验收证据

| 范围 | 主要测试文件与验证内容 |
|---|---|
| A01—A07 | `test_parallel_reading.py`、`test_controller.py`、`test_work_recovery.py`：真实线程重叠、快窗口补位、并发 1/2 同一请求集合、重叠提及/字段合并、名称冲突、拆分与覆盖 |
| A08—A11 | `test_parallel_planning.py`、`test_parallel_runtime.py`、`test_lookup.py`、`test_work_recovery.py`：两条查询续步、草稿只存账本引用、双路暂停继续、失败收齐兄弟回答、已付费复用、3 次传输重试上限、未知用量、重复完成与回滚 |
| A12—A17 | `test_parallel_planning.py`、`test_coreference.py`、`test_grouped_alignment.py`、`test_work_evidence.py`：双向线索合一、无依据不合并、作用域原文要求、全窗口成员重绑、恢复规划幂等、完整组和证据门控 |
| A18 | `test_parallel_runtime.py::test_graph_get_during_two_running_calls_is_read_only` 使用真实后端路由和隔离数据库，双路在途期间 3 次 GET 均返回 active=2，未改变 revision 或新增模型调用；前端阶段与计数测试通过 |
| A19—A20 | `test_pruning.py` 的 1000 提及反例、`test_parallel_runtime.py` 的线程/上下文/协调者断言，以及 `test_model_scheduler.py` 的跨任务共享 HTTP 槽位测试；全局调度实现未改动 |

PostgreSQL 使用临时独立实例、私有 Unix socket 和 `document_analysis_parallel_test` 库，没有连接应用数据库。覆盖双路计账、无重复完成、等待期间释放 fence、并发完成和展示一致性。

尝试从空库执行现有完整 Alembic 链时，基线迁移 `0007_add_generated_reports` 报 `DuplicateTable: generated_reports`。本次无结构变更；测试库改为按当前 ORM metadata 建表，并标注 `0044_harness_accounting` 供现有并发 fixture 验证。以上结果证明当前表结构上的并发行为，**不代表完整迁移链通过**，未修改这项无关基线问题。

### 扩展检查中的基线失败

额外运行旧文档分析 API、历史/事件/性能和 dispatcher 的 5 个测试文件：**12 passed，29 failed**。主要是旧创建夹具收到 `HARNESS_INPUT_INVALID`（422）及旧调度契约。

在独立工作树检出实施前 `HEAD=9c08091` 后，用同一环境和命令复测：仍为 **12 passed，29 failed**，失败测试 ID 集合完全一致，没有新增失败。本次不修改旧执行域测试、降低断言或声称全仓库测试通过。

## 部署验收（2026-10-01 02:48 UTC）

用户明确要求部署后，沿用容器标签确认的 `docker-compose.yml` + `docker-compose.override.yml` 配置。
当前后端挂载 `backend/app`，前端挂载源码并使用 `next dev`；本次没有依赖或镜像构建文件变更，
因此无需重建镜像，通过重启两个应用服务加载当前工作区实现，没有切换为生产 standalone 模式。

```bash
docker compose -f docker-compose.yml -f docker-compose.override.yml restart -t 60 backend frontend
```

- 后端启动时间 `2026-10-01T02:48:18.984386708Z`，前端启动时间 `2026-10-01T02:48:18.02498979Z`。
  数据库和 Nginx 未重启，数据库卷、上传原件、模型权重及既有运行保留。
- 重启前没有 queued/running/pausing 文档运行或在途模型请求；6 条 active 执行记录均属于已暂停运行，租约已过期。
  重启后运行状态分布仍为 paused 35、failed 3、cancelled 8、blocked_dependency 3。
- 部署前后实际数据库 revision 均为 `0044_harness_accounting`，与容器 `alembic heads` 一致；没有执行新增结构迁移。
- 经 Nginx 的 `/api/health`、`/openapi.json`、`/analysis?tab=graph-analysis` 均返回 200。
  重启前 HTTP OpenAPI 仍含旧 `windows_total/windows_discovered/windows_reviewed`；重启后实际服务返回
  `phase` 和 `reading_windows.{total,saved,complete,incomplete,active}`，`active` 最大值为 2，确认服务进程已加载新契约。
- 容器实际配置为 GPT `gpt-6-sol` 已启用、全局调用上限 8、运行调度上限 8。
  当前策略工厂冻结 `document-harness-v5`、`flow=local_reading`、`reading_concurrency=2`；新建运行默认开启两窗口并行。
  以上是部署配置及代码生效验证，没有为验收新建真实模型运行。
- 对用户原运行 `bbc6faa6-1c47-4ca7-a7b6-0d08456d7aed` 连续执行 3 次认证 GET：均为 200，revision 恒为 7720，
  模型请求总数恒为 33156，增量为 0。只读检查旧策略门禁返回 `HARNESS_NEW_RUN_REQUIRED`（“该运行使用旧执行策略，请新建运行”）；未尝试恢复旧运行。
- 在现有允许的域名 `http://sldpr-demo.infilake.com:8081` 上运行 `frontend/tests/source-harness-browser.mjs`：
  14 组检查通过、22 次合成 GET、0 次写请求、无页面或控制台错误；显示新阶段和叶窗口计数，并覆盖刷新、共指、关系组、原文和移动布局。
  API 全部由浏览器夹具拦截，这项结果不等同真实 GPT 全流程验收。
  记录与截图位于 `/tmp/parallel-reading-deploy-browser-domain-20261001/`。
- 首次以 `127.0.0.1` 作为浏览器 origin 的检查因现有 Next.js `allowedDevOrigins` 限制失败；改用已配置的访问域名后通过，未放宽来源配置。

## 尚未验证与使用边界

- 实施阶段宿主 Python 配置为 `local_llm_enabled=False`；部署核对确认容器实际已启用 GPT，不能以宿主配置推断线上状态。容器 `local_llm_model_revision` 仍为空。本次没有调用真实 GPT，真实模型并发 1/2 的总耗时、token、事实质量对照仍为 PW009。线程重叠验证不能代替真实效率/质量结论。
- 已部署到现有源码挂载环境，实际 HTTP 与合成 API 浏览器检查通过；未构建或切换生产 standalone 镜像，也未执行真实模型浏览器全流程。
- 既有运行不迁移。部署后应新建运行验证；已有失败运行不会因代码修改而自动重跑。
- 后续真实对照按独立方案第 11.3 节固定原文、本体、模型和预算，交替执行并发 1/2；分别报告阅读耗时、全流程耗时、调用与 token、覆盖和最终事实质量。当前不承诺两倍提速。
