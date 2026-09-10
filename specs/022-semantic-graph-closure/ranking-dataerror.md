# 线上 ranking_paused 故障修复与恢复

日期：2026-09-09 UTC。对应用户报告的 `semantic → deterministic`、
`ranking_technical_failure:DataError`、请求与 tokens 均为 0。
本次沿用运行 `4cfb7417-a873-45b4-ae78-a30632523300`，不重建识别输入，
不改变模型、GPU 配置、预算或冻结 fingerprint。

## 已复现的故障链

1. 共享 PostgreSQL 调度表 `local_model_requests.run_id` 原为 `varchar(32)`；
   在线传入完整 UUID，长度 36。首个 `ranking_count_tokens` 请求入库时即触发
   `DataError / SQLSTATE 22001`，尚未派发 embedding/reranker，因此计数为 0。
   已明确开启的排序按 `failure_policy=pause` 暂停；界面的 deterministic 是失败
   尝试的回退顺序，不能解释为 semantic 排序已完成。
2. 原恢复逻辑直接复用 paused pending epoch。即使修好调度入库，恢复也会返回同一
   失败状态。现在恢复时在原依赖与权限校验后归档旧失败，再按累计额度重新尝试。
3. 第一次实际恢复越过入库错误后，又暴露冷启动租约缺口：请求于
   **05:09:18.857 UTC** 开始，租约到 **05:09:48.811 UTC**；父进程同步重校验两套
   权重约 60 秒，其间未续租，后续 heartbeat 丢失调度槽位，运行转为
   `failed / model_interrupted`。这一尝试不是恢复成功，原失败请求完整保留。

此前独立冒烟使用 SQLite 和 32 位运行标识，不能覆盖 PostgreSQL 的真实长度限制；
小型制品夹具也没有覆盖大权重校验超过调度租约的边界。

## 修复内容

- [模型字段](../../backend/app/models/model_request.py) 和
  [0034 迁移](../../backend/alembic/versions/0034_model_request_run_id.py) 将 `run_id`
  无损扩展为 64 字符，完整关联旧 hex UUID、线上 UUID、带前缀评测 ID 和 64 位标识。
  降级先排他锁表，有超过 32 字符的记录时拒绝收缩，防止截断及并发写入竞争。
- [排序服务](../../backend/app/services/extraction/ontology_guided/semantic_reranker.py)
  将恢复前失败保存在可选 `paused_attempts`；完整成功批次可经 `score_cache` 复用。
  费用、预扣、逐记录调用和重试次数不清零；失败、不完整、非有限或过期响应不进入缓存。
  耗尽预算保持暂停，同一实例反复 prepare 不自动无限重试。
- [模型适配器](../../backend/app/services/llm/semantic_ranking.py) 在目录遍历、
  每 1 MiB 校验分块和最终清单校验时检查取消/原截止时间，按 5 秒间隔续租。
  完整 SHA256 校验、30 秒租约、模型 timeout 与数值身份不变；失租立即中断。
  子进程启动失败时关闭两端管道，避免对未启动进程 join 掩盖原始异常。

## 已执行验证

工作目录 `backend/`；复用既有 `.venv`。

| 验证 | 实际结果与范围 |
|---|---|
| `test_model_request_run_id_postgresql.py` | **9 passed**；专用 PostgreSQL 随机 schema，复现 36/41 位 ID 在旧列的失败，验证 32/36/41/64 位完整请求生命周期、64 位 task ID、旧行保留和安全/并发降级 |
| 调度回归 | **18 passed**；日志 `scheduler-regressions-01.log` |
| 暂停恢复四文件组合 | **60 passed，4 warnings，26.13 秒**；新增专项 7 例，另 53 例为既有排序与真实持久化 hook 回归 |
| 冷启动续租三文件组合 | **50 passed，4 warnings，8.19 秒**；新增 6 例，其中长校验用例模拟完整权重校验耗时 80 秒；核验真实 RequestTicket 续租、逐字节校验、取消/超时/失租、尾字节篡改及 spawn 失败清理 |
| 定向 Ruff、差异检查 | 上述模型、迁移、恢复服务及测试通过 |

恢复组合实际命令：

```bash
.venv/bin/python -m pytest -p no:cacheprovider -q \
  tests/test_extraction/test_semantic_ranking_paused_retry.py \
  tests/test_extraction/test_semantic_ranking.py \
  tests/test_extraction/test_semantic_ranking_execution.py \
  tests/test_extraction/test_document_analysis_execution_recovery.py
```

60 项组合结果保留在工具执行记录中，没有独立落盘日志。PG 验证是调度表升级及
生命周期验收，不是从空库运行全部历史迁移链，也不代表完整后端测试通过。

冷启动组合实际命令及输出保存在 `cold-load-lease-regression-01.log`：

```bash
.venv/bin/python -m pytest -p no:cacheprovider -q \
  tests/test_extraction/test_semantic_ranking_load_lease.py \
  tests/test_extraction/test_semantic_ranking_adapter.py \
  tests/test_extraction/test_semantic_ranking_cuda.py
```

这里的 80 秒为受控时钟，数据库使用隔离 SQLite，子进程为真实 spawn/受控响应；
真实 GPU 与在线 PostgreSQL 的执行结果另见下文。不把该用例称为真实模型耗时测量。

## 已执行线上变更

**05:03:58 UTC** 实际迁移 `0033_document_analysis_runs → 0034_model_request_run_id`，
列宽 32 → 64。旧 **379** 条调度记录的数量及内容摘要前后一致：
`379:30ea1054756f8658457cbf2b97a4ead6`。实际数据库 revision 与代码 head 已另行核对。

**05:05:59 UTC** 首次仅替换 backend，镜像 ID 为
`sha256:5e94ae1fb79cd96432d64b67c548d2889c6d4fbd0d4c6a1f161dbdb09b240a9e`。
切换前无排队/执行中文档任务、有效执行租约或活动模型请求；db/frontend/web 容器
身份及启动时间不变，两个健康入口均 200。

首次通过既有 control/CAS/审计路径恢复，fingerprint 始终为
`d83702d00a332d1a5c1f9daccb830d150ba6d1bae0ee521e1ed7b6c18fdb4868`。
首个请求完整保存了 36 字符 run ID 和 64 字符 task ID，证明原入库错误已经解除；
随后发生上文冷启动租约中断，未把这一阶段记为排序恢复成功。

**05:18:22 UTC** 冷启动续租修复通过回归后，再次仅替换 backend，最终镜像为
`sha256:ca0ebecefe198e9107b4c04d8b5a7fc035d4e64a67839abf7b43501cd9d38c87`。
切换前活动运行、执行租约、模型请求仍均为 0，其他三个服务未变；两个健康入口均 200。
**05:18:50 UTC** 通过新的幂等控制键再次恢复同一任务，原 fingerprint 保持一致。

实际 PostgreSQL 观察到新 tokenizer 请求于 **05:19:12.988 UTC** 开始，
**05:20:12 UTC** 已运行 59.30 秒，租约已续期到 **05:20:38.356 UTC**，
比 started_at 晚 85.37 秒，已越过原 30 秒失租边界。

## 实际恢复验收通过

**05:27:29 UTC** 通过运行所有者的短时认证读取实际图谱 GET API，仅保存技术诊断，
不保存认证信息或原文。结果：

| 项目 | 线上实测 |
|---|---|
| 当前运行 | 同一 ID、同一 fingerprint；`running`，stop_reason 为空，`tasks_attempted=1`，主模型 discovery 已开始继续处理原文 |
| 公开排序状态 | requested=`semantic`，actual=`[semantic]`，paused=`false`，degraded=`false`，reasons=`[]`，HTTP 200 |
| 完整排名 | **1** 个 semantic epoch committed，**64** 条候选完整排序，每条 discover/counterevidence 各一项有限 raw score，共 **128** 项 |
| GPU embedding | **73** 个请求 completed，**67,673 tokens**；全部记录 `cuda:0 / float16` |
| GPU reranker | **32** 个请求 completed，**142,364 tokens**；全部记录 `cuda:0 / float16` |
| 排序费用 | 预留 **105** 请求、已观察 **105** 请求；预留/实测输入均 **210,037 tokens**，未知请求 **0**，技术重试 **0** |
| 失败与恢复留痕 | 原 `DataError` epoch 在 `paused_attempts`；首次恢复的失败 tokenizer 请求仍在调度表，未删除或改写成成功 |

公开累计排序耗时 **471.373 秒**，包含最初失败的 **6.753 秒**；这是当前文档的在线
排序时长，含完整校验、冷加载、实际请求及持久化开销，不是纯 GPU 推理性能测试。
该值为公开排序账本累计，不是整个故障恢复的总墙钟时间；首次恢复的失败 tokenizer
请求另有 **60.891 秒**，保存在共享调度记录中，未纳入这一公开累计值。
图谱仍为 partial，识别运行继续执行。本次不暂停或取消用户任务，不宣称全文事实
质量或多跳路径完成；T017 保持未勾选。

**05:29:50 UTC** 再次只读观察：运行仍 running、有效租约在续期，已尝试 **5** 项
原文任务，主模型已记录 **6** 次 discovery 与 **1** 次 verification 请求；后续槽位
排序继续执行，287 个 embedding 缓存命中，未出现新的暂停原因。

T039 的线上排序故障修复与同运行恢复验收通过。专用 tmpfs PostgreSQL 测试容器
`ontology-022-ranking-pg-test-20260909` 已精确停止并自动移除，业务数据库与卷未清理。

原始技术证据目录：`evaluations/022-ranking-dataerror-20260909/`。

- [修复前状态](../../evaluations/022-ranking-dataerror-20260909/online-before.json)
- [迁移结果](../../evaluations/022-ranking-dataerror-20260909/online-migration.json)
- [实际 revision/head/列宽](../../evaluations/022-ranking-dataerror-20260909/live-migration-check.json)
- [首次后端切换](../../evaluations/022-ranking-dataerror-20260909/backend-switch.json)
- [首次恢复控制](../../evaluations/022-ranking-dataerror-20260909/online-resume.json)
- [首次恢复中断](../../evaluations/022-ranking-dataerror-20260909/interruption-01.json)
- [最终镜像与源码 hash](../../evaluations/022-ranking-dataerror-20260909/backend-image-02.json)
- [第二次后端切换](../../evaluations/022-ranking-dataerror-20260909/backend-switch-02.json)
- [第二次恢复控制](../../evaluations/022-ranking-dataerror-20260909/online-resume-02.json)
- [真实冷启动续租](../../evaluations/022-ranking-dataerror-20260909/live-load-lease-01.json)
- [实际图谱 API 与完整排名核验](../../evaluations/022-ranking-dataerror-20260909/public-ranking-recovered.json)
- [恢复验收断言结果](../../evaluations/022-ranking-dataerror-20260909/recovery-acceptance.json)
- [后续识别继续执行](../../evaluations/022-ranking-dataerror-20260909/recovery-final.json)

已验收 API/数据库技术摘要快照的 SHA256（不是完整 HTTP 响应原文字节）：
`f69626eeca56bc2dd70a420fe703cb72f7f1a775c6fdd0a11f8183faaa215ecb`。

本次是线上调度、排序恢复及实际执行验收；检索分数仍只决定处理顺序。
整份文档的事实精确率、召回率、完整多跳路径及 T017 专家质量验收另行判断。
