# 新内核性能增量验证

日期：2026-09-10（UTC）。沿用022，实施前源码HEAD：
`59f4b8663d0776d4f8d47aa325d361fd18075fa5`。

下文为部署前工程及模型测试记录；后续已按用户授权完成
[本机重新部署](performance-deployment.md)，部署检查与质量验收分别记录。

用户最新验收口径：**仅测试新方案，暂不比较相对原方案的性能提升；体感评价由用户给出。**
本报告保留新方案正确性、稳定性、覆盖/费用和绝对运行指标；早先探索性对照不作为验收目标。

## 实施状态

PF-001–PF-009的工程改造及PF-010的模板公平开关已实现：紧凑展示制品、分离摘要/定位、
SSE合并与隐藏取消、索引树与共享节点引用、不可变状态块、单协调器模型预扣、双意图预算v2、
精确批量分词与缓存、惰性逻辑前沿。每任务原子提交，旧inline状态/前沿/预算仍可恢复。

默认新运行冻结存储/前沿版本2、识别在途1；模板公平增强默认关闭，batch保持4。
无新增表、依赖、运行期外网访问或向量精度变化。未部署、未重启线上服务、未恢复历史运行、
未提交业务事实，历史冻结评测目录和原件未改写。

## 历史状态回放

回放源为方案中的暂停运行`58fcc303-c20b-4a47-9390-aad12c83b701`。
本次新格式测试覆盖ranking_state 116版、graph 11版、recognition_checkpoint 11版。
这是11/3444机会的历史部分运行，仅复用其冻结载荷校验新格式，不恢复原运行或调用模型。

138/138版完整载荷hash相等，最终公开投影等价，模型调用0。
[机器可读报告](performance-evidence/state-replay.json)记录新方案自身结果：

| 指标 | 新方案实测 |
|---|---:|
| 138版累计逻辑JSON bytes | 27,177,787 |
| 不可变块 | 10,350个、27,146,461 bytes |
| 138个引用头 | 31,326 bytes |
| 同图读取的内部逻辑JSON bytes | 39,383 |
| 服务层中位数（30次） | 7.11ms |
| 服务层P95（30次） | 7.54ms |

体积采用Python紧凑JSON，包括全部引用块及头；原载荷、缓存精度、逻辑覆盖和费用均恢复一致。
计时来自隔离SQLite服务回放，不代表线上PostgreSQL/HTTP/浏览器P95、物理体积或WAL。
序列全量编码591.09s、逐版完整恢复校验549.33s。实际连续写者复用精确已提交边界，
冷恢复仍核验全部块。这些是绝对观测，不据此评价优化幅度。

锁修复后的最终大状态另做[三制品新格式补验](performance-evidence/final-boundary.json)：
graph/checkpoint/ranking各1版，完整恢复与投影相等，模型调用0；累计14,441,555逻辑bytes，
服务30次中位数7.29ms、P95 7.66ms。工具已取消旧实现计时，只执行新方案诊断。

原始gzip导出和隔离SQLite保存在`/tmp/ontology-performance-20260910/`，不入Git。
工具为[benchmark_document_state.py](../../backend/scripts/benchmark_document_state.py)，
每版重新编码并解码，比对完整逻辑hash；一次兼容读取仅提供公共投影的正确性参照，
只计时新方案服务，不比较原方案的性能。

## 真实GPU批次实验

[各批次测试结果](performance-evidence/batch-comparison.json)与
[原始证据哈希](performance-evidence/batch-evidence-manifest.json)。
三个独立运行固定同一原件、86条完整记录、172个双意图输入对、88条唯一完整文本；
原件SHA256为`e94822808601e73f4ba4c715c664812d4a01e42f0e4e84a6b08a3653b915daf7`，
有序输入对SHA256为`6370ab98d538a7c88c7e0eeb47b0e23eb055c956b363476cc0a43150701b6225`。

环境复用`.venv-cuda12`、本地完整校验BGE-M3/BGE-reranker-v2-m3，Tesla P100/cuda:0/
float16/CUDA12.6。冻结同一239个Python文件副本；仅batch变化，pool86、epoch1、pair4096、
timeout1200s、槽位4,194,304/运行8,388,608 tokens、v1策略及retry0固定。
本实验隔离batch因素，不冒称v2重试策略或主LLM质量验收。

| batch | 分词请求 | 推理请求 | 总请求 | 冷轮秒 | allocated峰值GiB | reserved峰值GiB |
|---|---:|---:|---:|---:|---:|---:|
| 4 | 23 | 67 | 90 | 105.806 | 2.834 | 3.859 |
| 8 | 12 | 34 | 46 | 101.427 | 3.541 | 5.561 |
| 16 | 7 | 19 | 26 | 99.816 | 4.959 | 8.250 |

三轮所有输入、逐条token、发现/反证原始分数和最终排名完全一致：0分数差、0排名移动。
每轮实测与预扣均为212,100 tokens（嵌入23,436+精排188,664），恢复新增请求0且状态不变。
另用两套真实离线tokenizer逐条及batch4/8/16复算全部88条文本，精确相等；未截断或少计量。
各batch仅一次冷轮，宿主共享CPU负载和页缓存不同；记录各配置的请求和时长，
不作性能优劣或提升幅度判断。总请求执行时间约97秒、精排约40–41秒。
GPU总采样含既有llama占用；峰值表来自worker的PyTorch统计。未改变默认batch或启用双任务。

原始报告、运行副本、调度账本、资源样本和逐条分词证据位于
`/tmp/ontology-performance-20260910/batch-{4,8,16}`及同级目录；只将指标和哈希加入仓库。

## 合成规模检查

[新队列规模测试](performance-evidence/scheduler.json)固定6槽位×1000记录，各7次：
前沿JSON 199,456 bytes，peek中位数0.455ms，恢复33.078ms。
10,000机会的测试确认规划/恢复创建0个RecognitionTask，peek/派发只创建选中项，
全部逻辑机会仍计pending及未尝试覆盖。这些仅用于记录新方案复杂度和行为。

## 新方案真实模板有界测试

仅运行新方案，旧配置组未启动。正式创建入口与本地模型均实际执行，使用另建专库
`new_template_performance_test`和独立storage/OWL；测试新run
`ebbfeb74-97e2-4ccb-abb6-0cbd8f54e416`，生产旧运行保持原状。
原件SHA256为`2c1174bf616f30dd28c655fef4261c167762de807c8ad79e148fb8ef18e16436`，
模板v2.2/revision2，17条优先路径完整有序相等；真实解析再次核验287记录、1193证据单元、
3444根机会。新测试与上方batch实验使用不同真实文档，不合并计算质量或耗时。

冻结参数：performance=true、任务8、lineage6（主请求至多48次）、semantic retry1、
batch4/pool64、template_interleaving=false、排序槽位524288/运行4194304 tokens，
主输入32768/输出20480/超时600s。任务2后在合法批次请求暂停，再同身份恢复，预算不刷新。
测试不携带业务金标；原文核心断言由独立脚本在识别结束后读取结果核验。

测试已结束，结果为`failed / ANALYSIS_INCOMPLETE / task_budget_exhausted`。
这是有界诊断完成、完整模板验收未通过。没有启动旧方案组，也没有为通过断言调整提示词、
模板顺序、输入上限或继续调用模型。脱敏结果见[模板指标](performance-evidence/template-new.json)
及[恢复与账本核验](performance-evidence/template-validation.json)。

| 项目 | 新方案实际结果 |
|---|---|
| 派发/覆盖 | 8次、8个不同task_id、7个记录机会；末次为technical_once重试 |
| 完整覆盖分母 | 3444；examined 7、unattempted 3437、未决claim 1 |
| 主模型 | discovery 8 + verification 3；预扣11、已记账11、未决请求0 |
| 主模型已知tokens | prompt 54,325、completion 14,567 |
| 排序调度 | tokenizer 74完成；embedding 74完成；rerank 64完成、1失败 |
| 排序持久费用 | 139次推理预扣、281,702 tokens、1次技术重试 |
| 排序已知输入tokens | 279,647；另1次失败rerank缺测，不能按0补齐 |
| 最终有效肯定关系 | 0；首有效关系及完整模板路径时间未产生 |
| 原文回放 | 226个anchor，回放错误0；模板核心断言未通过 |
| 总运行时间 | 1610.79秒 |
| 排序持久化 | 141次、累计1001.68秒、中位数8.64秒 |
| 任务批次持久化 | 8次、累计181.47秒、中位数22.59秒 |
| SELECT | 725,315次；仅作本次新方案绝对诊断 |

任务2后正式请求pause耗时31.26ms，约35.19秒后已到合法暂停状态，`error=null`；
resume请求23.82ms，同一fingerprint恢复。暂停前两个任务没有重做，预算没有刷新。
结束后使用当前正式reader在只读事务中完整恢复排序、主模型费用和检查点，新增模型请求0。

失败rerank发生于暂停时，request_id为`be086330f6814082a20ab032c7780d25`，
`input_tokens=null`。持久预扣与已知输入小计相差2055 tokens，该差额保留在预扣账本；
缺测请求的实际消耗仍未知，不能把预扣差额直接当作实耗。tokenizer工作与推理请求分别统计。
持久化/SQL为函数和调用观测，其时间可能重叠，不能与总时长相加。

质量复核保留了15条关系候选：4条肯定候选未通过有效性门；11条否定`describes`候选虽被
模型判为supported，却把同一条以“结构特点”开头的化学描述句当作实体label，并枚举11个
不同本体子类。这些候选尚不能视为可信否定事实。默认肯定投影正确排除了它们，但投影
过滤与anchor可回放均不能证明主体、实体类型或否定语义正确。第7次任务还遇到
`verification_context_budget_exceeded`，保留32768输入上限，没有截断证据以通过验证。

模型运行使用执行前冻结的239个Python文件副本，manifest SHA256为
`4cddee588a00d0b5af876927b2fcd42e780a5efafc360a675d9f87045d15397a`。
后续写锁修复由下方48项定向与21项专库测试验证；没有重跑整轮真实模型来冒充最终代码
的端到端验收。原始制品、候选、证明、独立核验输出及`database.pgcustom`备份留在
`/tmp/ontology-performance-20260910/template-new/`，仓库仅保存指标与哈希。

## 工程验证

| 验证 | 本次实际结果 | 证据边界 |
|---|---|---|
| 后端相关广泛回归 | 449 passed、9 skipped，135.89s | 9项PG用例随后在专用库全部执行；非全后端测试 |
| 最后摘要竞态/API+存储补验 | 8 passed | 摘要读后fresh run核对；预算/过期/删除/归属 |
| 新旧检查点恢复补验 | 32 passed | 总开关true/false均实际崩溃恢复；任务不重做 |
| 存储+清理补验 | 10 passed | 引用保留、回收及跨运行隔离 |
| 最后锁修复定向回归 | 48 passed | 存储、崩溃恢复、模型等待与仓储 |
| 专用PostgreSQL组合（最后锁修复后） | 21 passed、0 skipped | 原子提交、换代失租、CAS竞争、预扣确认失败/丢失、预算控制 |
| 前端Node（最终共享hook复核） | 25 passed | document-analysis-runs与template-document-performance；随后tsc通过 |
| 浏览器真实组件+模拟API | 9项通过，27 GET、2取消、1摘要冲突恢复、零写请求 | 无线上后端/真实模型 |
| 静态检查 | 本次Python文件Ruff通过；前端tsc、定向ESLint通过 | 较宽ESLint另有slot-editor既有meta.status依赖warning |
| Spec Kit/差异 | require-tasks/include-tasks前置检查、git diff --check通过 | 无Git提交或部署 |

后端广泛回归的精确文件列表见[regression-files.txt](performance-evidence/regression-files.txt)，
原始日志`/tmp/ontology-performance-20260910/regression-final.log`。
各表测试范围有重叠，不相加冒充唯一用例数量。测试保留既有Pydantic/Starlette弃用警告。

浏览器证据见[browser-result.json](performance-evidence/browser-result.json)：SSE健康时无轮询、
断线退避、隐藏不请求并取消在途、同文档定位不重取全文或重挂载、排序不重取图、共享节点
懒挂载/引用跳转、摘要版本冲突恢复、文档切换迟到结果隔离。

## PostgreSQL环境与复现命令

使用本次创建的`ontology-performance-pg-20260910`（postgres:16-alpine，127.0.0.1:55440），
两个专用可销毁库，fixture会清表。未连接线上数据库执行fixture。

```bash
# 工作目录backend；仅适用于下述本次专用临时数据库。
DOCUMENT_ANALYSIS_TEST_DATABASE_URL=postgresql+psycopg2://postgres@127.0.0.1:55440/document_analysis_performance_test \
MODEL_REQUEST_TEST_DATABASE_URL=postgresql+psycopg2://postgres@127.0.0.1:55440/document_analysis_ranking_test \
.venv/bin/python -m pytest -p no:cacheprovider -q \
  tests/test_extraction/test_performance_postgresql.py \
  tests/test_extraction/test_document_run_execution_postgresql.py \
  tests/test_extraction/test_ranking_budget_control_postgresql.py \
  tests/test_extraction/test_model_request_run_id_postgresql.py
```

空库`alembic upgrade head`遇到既有0007重复创建generated_reports：0001使用活动模型先建表。
为本次并发测试采用当前`Base.metadata.create_all`并stamp `0035_ranking_budget_control`。
因此21项证明当前schema的事务/并发行为，**不表示迁移链通过**。本次没有schema变更。
最后锁修复的两项新增PG反例：同token/calls2先写，竞争calls1被受控拒绝；同状态calls2
再次提交幂等。最终事件/头均1、费用仍2，所有块均从成功根可达，无失败遗留块。
复用现有执行锁，先持锁再核验费用及创建块，等待锁后重验租约，幂等返回释放锁。

其余定向命令见[quickstart.md](quickstart.md)。

## 尚未验收的范围

T056的新方案batch4/8/16及8任务真实模板诊断已执行并记账。T017专家precision/recall与
至少三个真实新run、T044完整模板的首关系/关键路径/正确结果/全覆盖验收尚未通过；
上述否定候选语义问题仍待解决，不能以有界测试完成代替质量通过。没有执行旧方案组。
固定池batch实验不包含主LLM，真实模板诊断另计；未启用较小首池或模板公平新策略。
合成共享节点/大队列只验证复杂度，不能代替真实业务质量。
T057双任务是条件门：本次没有共同负载下主模型排队P95、额外费用与端到端吞吐证据，保持1。
