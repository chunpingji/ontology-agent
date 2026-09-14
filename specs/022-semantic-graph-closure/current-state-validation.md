# 当前状态暂停继续验证记录

日期：2026-09-14。代码与迁移文件已实现，后续按用户授权完成业务数据库迁移及后端重启，详见下方部署记录。未执行历史清理或真实模型整轮评测。

## 实现范围

- 新运行冻结 `state_storage_version=4`。运行行的 `work_version/work_batch_id` 是唯一当前恢复入口；`request_version/ranking_version` 分别跟随独立提交，公共 revision 保持客户端控制契约。
- `document_analysis_current_state` 按运行、分区及业务键保存当前值。候选和证明沿用精确版本表；原始任务、搜索/调度、依赖、覆盖、分层、审核及修复状态直接加载，不重放任务或历史精排 epoch。
- 执行器在业务更新处标记变化，提交仅编码变化行。计划重新取证形成新代际时移除该槽位旧分区，不保留过期的搜索记录或游标。原子批次回执使用 `committed_work_version`，不要求检查点制品。
- 最新展示按候选变更更新；文档证据索引及同类型菜单复用。展示缓存不参与恢复；丢失/损坏时由权威状态无模型重建。GET 的组件读取与写入采用相同锁顺序，部分图仍返回 `partial`。
- 精排结果与主识别协议响应保存在 `document_analysis_results`，请求、累计次数及协议引用保存在 `document_analysis_requests`。主识别预扣先保存派发认领，结果与请求引用一起提交；未知结果保持未知费用，继续不重置额度。工作状态独立保存已消费结果标记。
- 属性修复从当前候选原始任务建立边界，记录 `base_work_version`，保留精确审核/候选/原文证据。新格式无需大检查点外键，旧格式字段及语义保留。

迁移为 `0040_current_recognition_state`，基于 `0039_property_review_repair`。迁移只增加当前表和字段，并允许新格式批次/修复的旧检查点外键为空；不会升级旧运行。既有删除流程覆盖新表，本次没有清理用户数据。

## 工程验证

较大范围后端回归：**359 passed、4 skipped**，耗时 127.36 秒。范围包含文档分析 API/状态读取、旧格式恢复/状态块、精排预算与派发、当前格式恢复、分层和稀疏检索、证据反证失效、审核与局部修复、删除及共享核心依赖边界。4 个 skip 是 PostgreSQL 的旧/新格式已付费响应恢复用例。

随后对证明代际替换的分区清理做了补充，当前恢复、证据修复与分层定向回归 **75 passed**；quickstart 中的组合命令实际执行结果为 **22 passed、1 skipped**，耗时 12.00 秒。各组存在重叠，不将通过数相加当作独立测试数量。

最终补齐精排展示缓存丢失/损坏时从独立结果重建，避免将已发生费用显示为零；当前状态、预算开关与 API 展示的定向复核 **15 passed**。重建不调用模型，不改写权威工作状态。

独立状态事务检查：**3 passed、1 skipped**。验证无关公共 revision 更新不阻止工作提交，真实工作版本冲突、其他 owner 读取、失租 worker 写入均被拒绝；事务失败保留原工作值。独立结果从 20 增至 400 条，单次工作提交都仅校验一条工作分区，不读取结果历史。skip 是两连接同时提交同一工作版本的 PostgreSQL 用例。

以上为专用库配置前的工程记录，所有 skip 均因当时没有配置 `DOCUMENT_ANALYSIS_TEST_DATABASE_URL`。未连接替代数据库；**SQLite 不能证明 PostgreSQL 锁竞争行为**。迁移测试实际在隔离 SQLite 结构上执行 upgrade/downgrade，核对旧外键和新空检查点回执。后续 PostgreSQL 补验见下节。

新增/扩展的主要行为用例：

| 场景 | 证据入口 |
|---|---|
| 首批提交后冷恢复；启发式与 enhanced 自适应搜索；相同批次重试与异内容冲突 | `test_document_current_state.py` |
| 独立精排已付费但未提交任务；生产识别协议响应后中断；预扣累计 | `test_document_current_state.py`、`test_evidence_repair.py` |
| 分层子主体直接恢复；审核修复后二次恢复；新证明代际替换旧分区 | `test_current_work_resume.py` |
| 晚到反证使路径失效，冷恢复后仍失效且不重复调用 | `test_evidence_repair.py` |
| 预算耗尽、暂停、关闭预算后继续不修改原累计费用 | `test_document_analysis_budget_execution.py` 的旧/新格式参数 |
| 缓存丢失确定性重建；权威控制丢失阻止继续；GET 不改写覆盖 | `test_document_current_state.py`、`test_api/test_document_analysis.py` |
| 属性审核使用精确原任务，修复不引用大检查点 | `test_document_current_state.py` |

以上未注明目录的测试均位于 `backend/tests/test_extraction/`。定向复现命令见 [quickstart](quickstart.md)。本次修改的 Python 文件通过 Ruff E/F/I 检查；未修改前端契约字段，不需要前端构建。

## 独立 PostgreSQL 补验（2026-09-14）

已在现有 PostgreSQL 16.14 实例创建独立库 `ontology_document_analysis_test`，地址为
`127.0.0.1:55432`，使用无超级用户、建库及建角色权限的 `ontology_test` 账号。连接只保存于
Git 忽略、权限为 `0600` 的 `backend/.env.postgresql-test`。入口和用法见
[PostgreSQL 测试说明](../../backend/tests/POSTGRESQL.md)。

测试库由当前 metadata 建表，再 stamp 至 `0040_current_recognition_state`；已独立查询核对
实际 revision 与代码 head 一致，当前工作、独立结果和请求表均存在。**未执行完整历史迁移链，
也未迁移业务库或重启业务服务。**

在仓库根目录执行：

```bash
backend/.venv/bin/python backend/scripts/test_document_analysis_postgresql.py
```

首次结果为 19 passed、1 failed，暴露旧格式精排的相同内容并发提交被误判为版本冲突。
修复为在持有执行锁后刷新最新精排头，只有权威内容与待提交状态完全相同时确认成功；
不同内容和费用回退仍拒绝提交。沿用原有并发断言，未放宽测试。

修复后 **20 passed、0 skipped**，耗时 13.08 秒。覆盖实际连接间的执行竞争、等待锁后续期、
冷准备期间续期、旧状态块事务可见性、迟到提交、精排相同提交及费用回退、预算开关竞争、
派发认领与未知费用、旧/新格式已付费响应恢复，以及两个连接提交同一当前工作版本时仅一个成功。
此前缺少配置的 4 个响应恢复用例和 1 个当前工作竞争用例已实际补验。

相关 SQLite 回归在 `backend/` 执行：

```bash
.venv/bin/python -m pytest -p no:cacheprovider -q --tb=short \
  tests/test_extraction/test_document_state_artifacts.py \
  tests/test_extraction/test_semantic_ranking_dispatch_persistence.py \
  tests/test_extraction/test_semantic_ranking_paused_retry.py \
  tests/test_extraction/test_document_analysis_execution_recovery.py
```

结果为 **63 passed**，耗时 44.15 秒；测试入口和精排保存修改通过 Ruff。两组均有 4 条既有
依赖弃用/字段命名警告，无真实模型调用；结果不构成整轮提速或模型质量评测。

## 业务库迁移与后端重启（2026-09-14）

在用户后续明确授权后，核对运行中的后端实际连接 PostgreSQL `db:5432/slpra`，当前 revision
为 `0039_property_review_repair`。后端已挂载工作区代码及迁移目录，无需重建镜像。
操作前图谱执行租约均已撤销，旧标注执行均暂停，模型请求无活动项，报告运行均已完成；
旧抽取作业仍有历史 `running` 标记，本次不修改这些记录。

实际依次执行停止后端、备份业务库、迁移、核验、启动原有后端容器：

- `docker compose stop --timeout 60 backend`，保留容器、环境和数据卷。
- `pg_dump -Fc` 备份 `slpra`，文件为 `backend/data/deployment-backups/20260914T012415Z/slpra-before-0040.dump`，
  共 348,499,815 字节，权限 `0600`，已被 Git 忽略；`pg_restore --list` 成功读取目录。
- 通过 `docker compose run --rm --no-deps -T backend python -` 在临时容器中确认业务连接和
  前置 revision，再实际调用 `alembic.command.upgrade(config, "head")`。输出确认执行
  **`0039_property_review_repair → 0040_current_recognition_state`**，未使用 stamp。
- 核验三个新表、运行版本字段及检查点引用可空；迁移前后均为 9 个运行、113,974 个制品、
  4,181 个事件批次、0 个属性修复。图谱状态保持 2 paused、6 cancelled、1 blocked_dependency。
- `docker compose start backend`，原有业务后端于 **01:26:27 UTC** 重新启动；数据库、前端和
  网关容器保持运行。容器内新状态模块可加载，四个相关代码/迁移文件的 SHA256 与工作区一致，
  新运行默认 `state_storage_version=4`，旧运行继续使用冻结格式。

截至 **01:29:34 UTC**，后端直连 `:8000/api/health`、业务网关 `:8081/api/health`、容器本地健康
接口及网关首页均返回 200；健康载荷为 `status=ok`、`modules_loaded=true`、`module_count=10`。
从重启后的业务配置再次查询，数据库 revision 与容器代码 head 均为 `0040_current_recognition_state`。
本次启动日志未发现迁移跳过或 traceback；已暂停任务仍暂停，无活动模型请求。

本次证明现有业务库的 **0039→0040 增量迁移**成功，不改变测试库初始化未执行完整历史迁移链的
事实，也不构成真实模型质量或整轮提速评测。备份同目录的 `deployment.json` 保存本次操作时间、
迁移前后计数及代码核对结果。

## 状态开销观测

使用真实执行器、合成文档及确定性无事实适配器，逐记录完成相同的业务义务。比较普通提交，排除首批准入和终态收尾。下面为一次观测值，非硬件性能保证：

| 已完成任务数 | 同类提交变化行数 | 编码字节 | JSON 编码毫秒 | 当前工作冷加载及终态投影毫秒 |
|---:|---:|---:|---:|---:|
| 20 | 23 | 14276 | 0.131 | 8.902 |
| 100 | 23 | 14278 | 0.166 | 20.341 |
| 400 | 23 | 14318 | 0.175 | 83.236 |

独立结果存储的隔离 SQLite 观测：20/400 条历史结果时，固定单行工作提交分别约 3.749/2.688 毫秒，仅校验 `work:control` 一行；样本很小，时间差不构成快慢结论。执行器编码测试不包含数据库提交；冷加载观测也不包含数据库读取。不要把它们合并为真实运行的全链路耗时。

当前图及覆盖的投影仍会遍历必要的当前对象；冷恢复仍要校验当前工作集，加载独立结果缓存与请求状态。精排私有 worker 还会复制容器索引，历史结果加载尚非按页延迟加载。本次消除的是每批重复全量快照、历史状态链解码和任务前缀重放，不能声称所有状态操作都与运行规模无关。

真实模型调用次数、输入长度、原文义务与证据门未因存储改造减少。首条关系、目标路径、整轮耗时和事实质量没有本轮真实模型结果；[历史耗时分析](../../docs/关系图谱识别缓慢与状态复杂度分析-20260913.md)的数据不算本次提速证据。
