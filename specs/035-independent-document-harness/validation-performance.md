# 性能与候选剪枝实施验证（2026-09-30）

本轮对应 `038-harness-pruning` 分支、035 活动规范的 HP-FR-001—010 和 T301—T313。
仅新运行采用 `document-harness-v2` 公开协议及 `document-harness-v4` 模型协议。
本文件只记录本轮实际工程验证，历史 quickstart 的模型结果不计入本轮。

## 实现与需求覆盖

| 需求 | 实现和验证入口 |
|---|---|
| HP-FR-001/002 | 0044 轻量调用列、accounting 差量、runtime 原子保存、单次 bundle 读取；`test_accounting_storage.py` |
| HP-FR-003/004 | 线索、字段归属、明确引用召回；有界弱候选及固定桶准入；`test_pruning.py`、`test_planning.py` |
| HP-FR-005 | 属性独立任务、菜单分片进度、语义依赖反向索引；`test_work.py`、`test_controller_budget.py` |
| HP-FR-006/007 | 精确表格程序证明、独立语义核对、R/T 同批和一次补证；`test_evidence_gate.py`、`test_work_evidence.py` |
| HP-FR-008/009 | 窗口树、物理字符去重、当前批次及原付费结果复用；`test_work_recovery.py`、`test_coreference_runtime.py` |
| HP-FR-010 | v2 公开模型、前端覆盖/任务/证明展示与缓存错误保图；`test_public_v2_contract.py`、前端 Node 测试 |

确定性反例已覆盖：1,000 个无连接提及零关系配对；弱范围最多 64 项、单桶最多 8 项物化；
明确否定及条件不受普通配额排除；同一桶继续不轮换剪枝、不再次精排。
远端 incoming/outgoing 引用的反证会立即使旧证明失效，并进入实际复核原文。
端点仅改变采信状态时复用原语义结果；冲突参与解释保持未决，原关系/时间判断不被改写。
合法冻结表格映射可在关系对齐前完成证明，两阶段均零 LLM；默认规则为空，不假设生产表格契约。

## 可重复工程检查

在 `backend/`：

```bash
.venv/bin/python -m pytest -p no:cacheprovider -q \
  tests/test_document_harness \
  tests/test_extraction/test_document_harness_runtime.py \
  tests/test_extraction/test_document_analysis_run_store.py \
  tests/test_extraction/test_ontology_guided_boundaries.py
```

后端实际结果：**547 passed，3 skipped**。3 项为需要专用 PostgreSQL URL 的新用例，
已另在下方临时真实 PostgreSQL 组实际运行通过；4 项 warning 为既有依赖警告。

在 `frontend/`：

```bash
node --test tests/source-harness.test.mjs tests/target-graph.test.mjs
./node_modules/.bin/tsc --noEmit
npm run lint -- src/lib/api.ts src/lib/source-harness.ts \
  src/components/analysis/source-harness-panel.tsx \
  src/components/analysis/source-harness-shared.tsx \
  src/components/analysis/source-harness-workspace.tsx \
  src/components/analysis/source-harness-observations.tsx
```

前端实际结果：57 项 Node 测试通过，TypeScript 与定向 ESLint 通过。
未运行浏览器集成、生产构建或真实后端页面验证。

本次所有受影响 Python 文件的 Ruff 检查通过。扩展检查了 API/schema/performance、
run_store、dispatcher 和共享识别边界共 6 个既有测试文件，结果为 40 passed / 29 failed。
为区分本次回归，在隔离工作树检出 HEAD `bc2092b636819925f477d4fdf2cfb1fccfb90527`，
复用相同 Python 环境执行同一组：仍为 40 passed / 29 failed。
29 个失败用例名称及全部异常行在归一化临时路径/内存地址后完全一致，本次没有新增失败。
失败集中在旧 document_analysis 创建/协议及旧 dispatcher 预期；未为其添加新旧兼容执行路径或放宽断言。
临时对比证据为 `/tmp/harness-api-head-baseline.txt`、`/tmp/harness-api-baseline-comparison.json`；
临时工作树已清理。当前 Harness 的 API/所有权/暂停和只读展示另由上方当前协议测试覆盖。

## PostgreSQL 与迁移

使用本机 PostgreSQL 14 原生二进制，在 `/tmp` 创建独立可销毁实例，仅监听专用 Unix socket；
专用库名含 fixture 要求的 test 标记。未使用应用数据库或已有后台服务。
设置专用 `DOCUMENT_ANALYSIS_TEST_DATABASE_URL` 后，运行：

```bash
.venv/bin/python -m pytest -p no:cacheprovider -q \
  tests/test_document_harness/test_accounting_storage.py \
  tests/test_extraction/test_document_run_execution_postgresql.py
```

实际结果 **24 passed，0 skipped**，其中 7 个用例实际使用 PostgreSQL，17 个仍使用隔离 SQLite fixture。
覆盖未提交事务只读旧完整 head/metrics/base、提交后同版展示、模型等待不持有运行锁、
独立连接并发 finish 同一 attempt 只计费一次，以及原有租约/执行所有权边界。

迁移 `0044_harness_accounting` 在临时 PostgreSQL 上真实降级、升级通过；保留其他执行域旧行，
新增列对这些行保持 NULL，7 个非负约束存在。基线由当前模型建表后标记版本，
验收范围为本次 0044，不声称验证全部历史迁移从空库运行。临时实例已关停清理。

## Spec Kit 与交付范围

规范、澄清、research、plan、data-model、contracts、tasks 与 requirement checklist 已同步。
`check-prerequisites.sh --json --require-tasks --include-tasks` 返回活动规范 035；
实现没有新增依赖、T-Box 变更、云服务、历史快照或旧运行适配。
所有新行为直接接入新建 Harness 运行；精排沿冻结既有配置，表格规则沿新运行冻结配置。

上述工程验证阶段尚未重启或部署服务，也未迁移应用数据库或改动原长运行。
真实模型及原 11,600 秒文档的前后对照未运行：尚无冻结且匹配本次输入、本体、模型的完整参考基线与评测预算。
原运行的阶段累计耗时是调查依据，不能据此声称当前加速倍数或召回率不下降。
后续真实评测须使用新运行、固定输入/本体/模型/参考/预算，并分别报告候选规模、调用成本和事实质量。

## 开发 Compose 部署（2026-09-30）

经用户授权，在当前机器默认开发 Compose 栈部署了本轮源码。原运行
`b8a9e3af-2c75-4d0e-804e-8e96cbdb5652` 当时仍在执行；先通过现有暂停 API
等待它进入 `paused`、租约撤销，确认没有其他排队或执行中的文档分析运行，再停止后端。
应用数据库备份为 `backups/slpra-deploy-20260930-141002.dump`，大小 300,979,790 字节，
`pg_restore -l` 可读取。迁移 `0043_interpretation -> 0044_harness_accounting` 成功；
容器内 `alembic current` 和 `heads` 均为 `0044_harness_accounting (head)`，
`document_analysis_requests` 中 12 个新增调用统计字段存在。

重启后，后端及边缘代理的 `/api/health`、分析页面均返回 HTTP 200。
使用合成 Word 文档创建新运行 `7327b8ec-af35-4fdd-90ce-e892d76d83d2` 后立即暂停；
经边缘代理读取图谱返回 HTTP 200、`document-harness-v2`，数据库冻结的模型协议为
`document-harness-v4`。该烟测运行最终为 `paused`，没有遗留排队或执行中的分析运行。
原运行仍为 `paused`，旧 `document-harness-v1` 图谱读取返回 HTTP 409；旧协议不迁移，
应用本轮性能优化须新建运行。此次未执行完整真实模型分析或性能/质量前后对照。

## 通用指令与 Schema 描述清理（2026-09-30，部署后代码修订）

按用户要求逐项检查 `protocols.py` 的 10 个阶段指令和 12 个输出模型（含发现的两种查询模式）。
移除内置对象类别、虚构编号与字段原文示例；编号边界、匿名指称、完整分组及竞争解释改用
通用语义描述。名称和编号值由原文提供，类型与属性语义由当前冻结本体提供。
同一约束已明确写入 spec，并覆盖 Schema 字段描述。保留独立成员定位、复合编号完整性、
主体归属、对象层级、否定、条件与未决语义。

在 `backend/` 执行：

```bash
.venv/bin/python -m pytest -p no:cacheprovider -q \
  tests/test_document_harness/test_fixed_stage_protocol.py \
  tests/test_document_harness/test_model.py \
  tests/test_document_harness/test_controller_budget.py \
  tests/test_document_harness/test_evidence_spans.py \
  tests/test_document_harness/test_referent_decision.py \
  tests/test_document_harness/test_enumerated_mentions.py \
  tests/test_document_harness/test_unlabelled_fields.py \
  tests/test_document_harness/test_value_boundaries.py \
  tests/test_document_harness/test_source_anchors.py \
  tests/test_document_harness/test_grouped_alignment.py \
  tests/test_extraction/test_ontology_guided_boundaries.py
.venv/bin/ruff check app/services/document_harness/protocols.py
```

最终结果 **167 passed，4 个既有 warning**，Ruff 通过。修改前后 12 个模型的 JSON Schema
去除 `description` 后逐项相同；字段、枚举、必填与校验约束保持一致。实际序列化的指令和
Schema 中已无本次清理的对象名及编号示例。此结果是工程回归验证，没有执行真实模型质量
对照，也未再次重启后端；此前部署记录不代表此后新增的指令修订已经加载。
