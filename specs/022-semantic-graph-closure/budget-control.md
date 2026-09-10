# 运行级排序预算控制

日期：2026-09-09。对应 [FR-017](spec.md) 与已完成的 [T041](tasks.md)。本记录区分控制
契约、工程检查、真实控制往返和重载验收，不修改旧排序、GPU 或质量评测制品。

## 控制与记账契约

`ranking_budget_enabled` 是运行级独立控制值，缺省 `true`。新建运行从
`SEMANTIC_RANKING_BUDGET_ENABLED` 读取默认值；旧运行通过
[0035 迁移](../../backend/alembic/versions/0035_ranking_budget_control.py) 默认启用。
创建 API 不增加新的预算表单字段。

| 状态 | 累计排序额度检查 | 排序预算记账 | 既有记录 |
|---|---|---|---|
| 启用 | 检查累计 tokens、记录与请求次数 | 正常预扣与累计 | 保留 |
| 禁用 | 暂停上述累计限额检查 | 不预扣、不累计，预算统计冻结 | 保留关闭前累计量 |
| 重新启用 | 从关闭前累计量继续检查 | 恢复后续预算记账 | 禁用期间不追补、不回填 |

关闭预算限制后，embedding 召回与 reranker 精排仍可运行，缓存与排序结果仍按原契约
持久化。单次输入完整性和长度、超时、每次自动技术重试上限、取消与执行所有权保护
保持有效。文档识别的独立任务预算及主提议/验证模型的逐记录预算不受此开关影响。
关闭排序预算不能保证整份文档不再因其他额度或技术原因暂停。

关闭期间的模型调度技术日志与排序预算账是不同记录。预算未计账不表示没有模型请求、
token 消耗、时间或显存占用，也不能用于宣称零成本。UI 明确显示
“预算统计已暂停（显示启用期间累计值）”，单个未记账轮次标为“预算未计账”。

该控制值不进入 RankingPolicy、模型身份或运行 fingerprint。切换保留原数值限额、
源文档、本体、模型与数值环境身份、已有费用、已提交排序及图谱，不通过修改冻结配置
或手工清账获得新额度。恢复时以当前运行的控制值覆盖旧排序 snapshot 中的控制值。

## 状态、权限与 API

只有运行所有者的写入角色可以操作，且运行须为已暂停或可恢复失败；内部 failed 对外
表现为 `retryable_failure`。过期、删除/正在删除、依赖阻塞、已完成以及运行中/正在暂停的
运行不提供预算变更操作。运行中先使用既有软暂停流程，确认服务端进入暂停后再调整。

| 操作 | 请求路径 | 动作名 |
|---|---|---|
| 启用预算 | `POST /api/document-analysis/runs/{id}/ranking-budget/enable` | `ranking_budget_enable` |
| 禁用预算 | `POST /api/document-analysis/runs/{id}/ranking-budget/disable` | `ranking_budget_disable` |

两个请求均复用 `expected_revision`、`request_key`、`reason`，经过 owner/role 校验、
CAS、幂等回执和审计；并发冲突返回 409。服务端 `available_actions` 只暴露可执行的反向
操作，前端据此提供按钮，不以本地角色或状态猜测替代服务端授权。变更会撤销旧执行 fence，
阻止迟到 worker 按旧控制值持久提交。

切换后仍保留暂停/可恢复失败状态，不自动 resume，也不启动模型。若需要继续识别，用户
必须再调用既有 resume 或点击“恢复”。预算耗尽导致的暂停可在禁用预算后恢复同一运行；
重新启用若历史用量已到上限，既有数值限制仍然适用。

公开字段与兼容规则：

- 运行与控制回执顶层：`ranking_budget_enabled: boolean`，缺省启用。
- 图谱：`ranking.budget_enabled: boolean`，公开当前运行控制值。
- 单轮排序：`epochs[].budget_accounted: boolean`；历史缺省 `true`，禁用期未记账轮次为 `false`。
- 控制回执仍包含运行身份、revision/event/artifact 水位、状态、动作和 `available_actions`。
  前端拒绝过期或其他运行的回执；成功后仅合并授权字段并发起只读刷新，保留原覆盖与图谱。

实现入口为 [运行控制 API](../../backend/app/api/document_analysis.py)、
[应用服务](../../backend/app/services/document_analysis/application.py)、
[排序服务](../../backend/app/services/extraction/ontology_guided/semantic_reranker.py) 和
[前端分析面板](../../frontend/src/components/analysis/document-analysis-panel.tsx)。

## 本轮已完成的工程验证

以下为本轮实际完成的检查；不同层次分列，不把前端合成响应当作真实后端结果。

| 检查 | 本轮结果 | 验证范围 |
|---|---|---|
| 前端既有 Node 测试 | 最终 23 passed | 显式控制端点、CAS/取消信号、旧字段默认、迟到/跨运行回执保护、暂停保持、冻结统计、未记账展示及禁用后旧预算暂停提示 |
| 前端 TypeScript | `tsc --noEmit` 通过 | 新字段、控制动作、调用方类型一致 |
| 前端定向 ESLint | 通过 | 本次 API、共享文案、面板/图谱及相关测试 |
| Chrome 合成 API 浏览器 | 通过 | 预算开关往返、409 后读取新水位再重试、无自动恢复、只读/运行中无操作按钮、原有历史/图谱/证据导航回归 |
| 后端控制/API/迁移组合 | 47 passed，4 条既有 warnings | 新增 API 15 项、SQLite 迁移 1 项、既有回归 31 项 |
| 主代理最终后端组合 | 80 passed，定向 Ruff 通过 | 执行/投影/终态/恢复、排序预算、API、迁移与共享识别依赖边界 |
| 专用 PostgreSQL 目标迁移 | 通过 | `0034 → 0035`；旧行原字段保留，旧运行预算默认启用 |
| 专用 PostgreSQL 独立回归 | 2 passed | 并发/控制隔离；同 revision 并发仅一次变更成功，另一请求冲突，胜出操作可幂等重放，运行仍暂停且原进度保留 |

前端命令在 `frontend/` 执行：

```bash
node --test tests/document-analysis-runs.test.mjs tests/document-ranking.test.mjs
./node_modules/.bin/tsc --noEmit
npm run lint -- src/lib/api.ts src/lib/document-analysis.ts \
  src/components/analysis/document-analysis-panel.tsx \
  src/components/analysis/document-relationship-graph.tsx \
  tests/document-analysis-runs.test.mjs tests/document-ranking.test.mjs \
  tests/document-analysis-history-browser.mjs
```

后端 47 项实际命令在 `backend/` 执行：

```bash
.venv/bin/python -m pytest -p no:cacheprovider -q \
  tests/test_api/test_document_analysis_ranking_budget.py \
  tests/test_extraction/test_ranking_budget_control_migration.py \
  tests/test_api/test_document_analysis.py \
  tests/test_api/test_document_analysis_history.py \
  tests/test_extraction/test_document_analysis_run_store.py \
  tests/test_extraction/test_document_analysis_public_projection.py
```

该组合验证 owner/role、CAS/replay、暂停/失败态门禁、全局默认、旧 DELETE 回执兼容、
撤销旧 worker fence、切换后显式恢复保持 fingerprint，以及账本/图谱制品不变；后端
实现与测试的定向 Ruff 通过。此组合的结果来自本轮终端执行，未另存独立日志。

主代理最终 80 项组合命令同样在 `backend/` 执行：

```bash
.venv/bin/python -m pytest -p no:cacheprovider -q \
  tests/test_extraction/test_document_analysis_budget_execution.py \
  tests/test_extraction/test_document_analysis_public_projection.py \
  tests/test_extraction/test_document_analysis_terminal_progress.py \
  tests/test_extraction/test_document_analysis_execution_recovery.py \
  tests/test_extraction/test_semantic_ranking_budget_control.py \
  tests/test_api/test_document_analysis_ranking_budget.py \
  tests/test_api/test_document_analysis.py \
  tests/test_extraction/test_ranking_budget_control_migration.py \
  tests/test_extraction/test_ontology_guided_boundaries.py
```

PostgreSQL 证据为 [目标迁移结果](../../evaluations/022-ranking-budget-control-20260909/postgres-migration.json)，
并发用例为 [test_ranking_budget_control_postgresql.py](../../backend/tests/test_extraction/test_ranking_budget_control_postgresql.py)。
使用专用、可销毁测试库，不以 SQLite 通过替代 PostgreSQL 锁与并发检查。
本次专用 PostgreSQL 测试容器已清理，未删除业务库或业务卷。

**已知迁移缺口**：隔离空库的完整历史迁移链在 `generated_reports` 重复建表处失败。
本轮目标迁移检查使用前序表结构，证明 `0034 → 0035` 预算字段增量及旧字段保留；
没有证明从空库完整执行历史 `upgrade head` 成功。本轮未修复该独立历史问题。

浏览器使用现有 [document-analysis-history-browser.mjs](../../frontend/tests/document-analysis-history-browser.mjs)、
本机 Chrome 131 与 Compose 开发前端 `http://localhost:8081`，全 API 请求由合成夹具
拦截，没有访问真实应用后端或模型。最终
[browser-02/report.json](../../evaluations/022-ranking-budget-control-20260909/browser-02/report.json)
记录两次成功预算变更、一次 409 冲突和一次既有显式合成创建；没有 resume 请求。
禁用/启用均显示原有 100 tokens 累计值，证明前端按回执和快照展示，不代替后端执行期
记账冻结验收。1440/1920 桌面及 768 像素布局、章节/图谱切换和证据导航同时通过。

可核验截图：
[预算已禁用](../../evaluations/022-ranking-budget-control-20260909/browser-02/ranking-budget-disabled.png)、
[预算已启用](../../evaluations/022-ranking-budget-control-20260909/browser-02/ranking-budget-enabled.png)。

## 真实线上验证与后端重载

本轮只读检查确认共享数据库已经处于 `0035_ranking_budget_control`，新预算控制 API
也已加载。该迁移来自其他会话于 07:59 UTC 触发的后端重启；不能归为本轮脚本实施，
本轮也没有其迁移前快照，不能用隔离目标迁移结果冒充线上迁移前后数据比对。

08:13 UTC，主代理完成真实浏览器禁用→启用往返，未拦截 API，结果见
[live-budget-check.json](../../evaluations/022-ranking-budget-control-20260909/live-budget-check.json)。
目标运行 `4cfb7417-a873-45b4-ae78-a30632523300` 最终保持预算启用且已暂停，
没有自动 resume；该运行模型请求记录数保持 638，fingerprint、完整 progress 以及全部
制品头的 revision/hash 均不变。图谱仍为 revision 76，排序状态仍为 revision 248。

真实验收一共进行了两轮、四次预算控制，不只报告最终成功轮次：

1. 第一轮禁用已经成功，脚本错误要求 HTTP 200，而接口按契约正确返回 202，导致脚本
   中断。随后显式调用启用恢复为 `true`；两次操作均保留审计，revision 从 1227 到 1231。
2. 修正脚本的状态码断言后，第二轮完整禁用→启用通过，revision 从 1231 到 1235；
   上述 JSON 对应这轮，记录两个实际控制 POST、无 resume、无页面错误和所有不变性核验。

该结果验证预算控制 API 的线上可用性和无模型调用往返，没有启动禁用预算后的整份文档
识别，不能当作真实模型运行期间记账冻结或最终图谱质量验收。

08:04 UTC 的旧 DELETE 回执兼容收尾补丁晚于此前后端重启。主代理等待其他活动归零后，
于 08:15 UTC 完成仅后端的重载，见
[backend-reload.json](../../evaluations/022-ranking-budget-control-20260909/backend-reload.json)。
重载前 `active_runs`、`live_leases`、`active_requests`、`live_annotations` 均为 0；
数据库、前端和 web 服务身份均未改变。目标运行状态、fingerprint、progress、全部制品头
与 638 条模型请求记录跨重载保持不变，旧 DELETE 回执兼容收尾已加载。

08:17 UTC 的 [online-final.json](../../evaluations/022-ranking-budget-control-20260909/online-final.json)
确认健康状态 `ok`、实际数据库 revision 为 `0035_ranking_budget_control`；目标运行
仍是 revision 1235、`paused / ranking_paused`，预算启用且禁用操作可用。
排序账保留 240 次调用、605284 tokens；语义排序仍启用，新运行预算默认仍启用。
该最终核验没有写 API，也没有恢复目标识别任务。

本轮验收已汇总至 [acceptance.json](../../evaluations/022-ranking-budget-control-20260909/acceptance.json)，
T041 已由主代理完成勾选。完整空库历史迁移链的 `generated_reports` 独立缺口继续按上文
披露；本次上线核验不扩大为全历史迁移链或整份文档质量验收。

正式质量 T017 的专家参考、独立文档与预注册比较门槛保持原状；排序预算控制的工程
验收不能代替关系事实精度或完整图谱质量验收。
