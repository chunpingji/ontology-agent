# 022 工程闭环验证

日期：2026-09-08 至 2026-09-09（UTC）。分支：`022-semantic-graph-closure`，实施前 HEAD：`f64220d03d8c62a011db441c40b54e7ec5440ac2`。

## 交付状态与证据边界

已实施 P0–P4 的主体菜单、排序、证明门、多跳图、反证失效/重验、持久恢复、只读展示和评分工具。本轮复核发现初轮“独立证明”仍来自同一次模型回答，不能证明独立验证调用已落实；现已拆成 discovery / verification 两次请求并补充反例。T017 的真实质量/成本门未验收；工程夹具不能证明 precision、recall 或调用成本已经改善。P5 补充精排及 E0/E1、F0/F1 实验仍为明确后续范围。

初轮工程验证采用既有 `backend/.venv` 与前端依赖；测试原件、本体和制品使用临时目录，模型输出/分数受控。初轮未下载权重。后续按用户要求准备真实 CPU 排序环境，结果单列于 [cpu-ranking.md](cpu-ranking.md)。初轮未启动或重启共享服务，未部署，未修改权威 TTL，未写业务事实，未改写历史冻结评测目录，也未执行 Git 提交；后续 GPU 线上部署和故障恢复按下方专题的实际时点记录。

初轮缺少完整校验的本地 embedding/reranker 制品及相关模型依赖；后续 CPU 环境已补齐，宿主、Compose 与指定文档真实排序均通过，实测数据以上述专项记录为准。独立质量评测仍缺专家裁决的独立文档参考及预注册质量/成本阈值。已有 legacy `MODELS.sha256` 为空，不能作为新模型身份验收。初轮未配置专用 PostgreSQL DSN；本轮独立库并发验收已执行，详见下方新增记录。生产构建未执行。

## 线上 DataError 与恢复闭环（T039，2026-09-09）

已修复共享调度表 32 字符列拒绝线上 36 字符 UUID、paused epoch 被原样复用，以及
真实恢复后暴露的大权重校验超过 30 秒租约未续期问题。线上实际无损迁移到
`0034_model_request_run_id`，原 379 条请求摘要不变；两次仅替换 backend，失败尝试保留。
9 项真实 PostgreSQL、18 项调度、60 项暂停恢复及 50 项冷启动/适配器回归通过；
各组范围和完整命令见 [ranking-dataerror.md](ranking-dataerror.md)。

同一用户运行已实际恢复，原 fingerprint/配置/预算保持一致。**05:27:29 UTC**
真实图谱 API 返回 semantic、未降级、未暂停、原因列表为空；首轮 64 条候选、128 个
双意图 raw score 完整提交。GPU **73 次 embedding + 32 次 reranker** 全部完成，
预留/已观察均 105 请求、预留/实测均 210,037 tokens，后续原文识别已开始、
tasks_attempted=1。T039 已完成，运行继续执行；这不代表整份图谱质量或 T017 验收通过。

## CUDA 12 GPU 增量（T032–T036，2026-09-09）

用户后续要求默认引擎改为 GPU，T037 已完成应用/Compose 默认、CPU 覆盖及配置回归：
**95 passed**、五种 Compose 组合及新镜像默认 CUDA kernel 验证通过。共享服务因有
两个持有效租约的 recognition 运行，当时未切换；该轮状态和制品见
[gpu-default.md](gpu-default.md)。用户随后明确启用，本机后端已加载 true/semantic，
最新部署与实际双模型验证见 [gpu-online.md](gpu-online.md)。以下保留切换默认前的 GPU 增量验收口径。

CPU 默认环境保留；新增独立 CUDA 12.6 / PyTorch 2.7.1+cu126 环境及镜像，P100
真实 kernel、离线模型、设备/数值冻结、取消/超时/OOM 清理均有新制品。
同依赖、同原件/IR/本体、同完整输入及预算的比较通过：86 records、172 对输入、
212,100 tokens，CPU 882.46 秒、GPU 147.42 秒（纯排序约 5.99 倍）；reranker 请求
686.89→39.16 秒。top-10 集合重合，部分名次因数值差异变化，不代表质量提升。

受影响 CPU 回归 **194 passed**；收尾修正后的 GPU 依赖环境定向 **59 passed**。
GPU 首轮真实清理 **6/6**，发现进度锁 semaphore 警告后改为私有线程锁，真实取消/超时
复测 **2/2** 且不再出现该警告。单卡进程显存峰值为此文档 4.15 GiB、
4 × 4067-token 边界 6.57 GiB；多卡分摊未实现。完整口径、命令入口、制品和部署状态见
[gpu-ranking.md](gpu-ranking.md) 与 [GPU_CUDA12.md](../../backend/GPU_CUDA12.md)。

最终 GPU 镜像在禁用网络、单卡透传、只读模型挂载的独立容器中完成真实双模型冒烟：
**passed、105.13 秒、18/18 请求完成**，完整双意图、无降级，恢复新增 0 请求；
本次容器输入为合成夹具。临时容器已退出，共享后端未切换部署或重启，T032–T036 已完成。

以上是 GPU 工程/环境与排序性能验收；T017 的专家质量与成本门仍待外部材料，不勾选。

## 本轮缺口复核与补验（T020–T031）

- 独立模型验证：冻结候选/target，第二阶段重新引用原文和独立判断桥接；缺项、重复、错目标、极性/条件漂移、预算不足和取消均有反例。核心 9 文件组合 **72 passed**。
- 每次请求前持久化预扣；副本恢复不刷新预算；写入失败零派发。图进度分别给出已记账模型调用、预扣及结果待核实数量。完成响应先保存再软暂停，连续两阶段间暂停使用独立 continuation，不耗技术重试名额。
- 全部剩余记录不可精排时继续确定性原文探索，保留不可精排观察和空分值。取消、owner 和模型调度租约丢失直接中止，不转技术重试/降级。
- 活动评测使用独立 scheduler SQLite、run/task/stage 归属和逐记录冻结预算；异常退出仍保留预扣、实际请求、费用与制品 hash。4 文件组合 **39 passed**。
- 专用 PostgreSQL `ontology-022-pg-test` 使用独立 tmpfs 与本机端口 55433；DocumentAnalysisRun 与 scheduler 并发验收 **8 passed**。空库真实迁移在 0007 因 `generated_reports` 重复创建失败：0001/0002/0004 读取活动 metadata 建表，后续迁移重复创建。后续并发验收明确使用当前模型建库并 stamp 0033，**不作为迁移成功证据**。日志位于 `evaluations/022-pg-online-20260908/`。
- 原件新 preparation 与真实集成实验位于 `evaluations/022-independent-upload-23c872fb-20260908/`，与 CPU 排序专项 preparation 分开，保留失败试跑。首轮发现响应缺少必需关系端点，已用按候选种类区分的 schema 修复。第二轮实际完成 8 次发现、6 次独立验证及 115 次 CPU 排序请求；14 次主模型调用均有预扣与完成账，未知主调用为 0。结果仍 incomplete：86 条记录中 4 条 examined、2 条 incomplete、80 条 unattempted；8 条候选边全部 unsupported/undetermined，有效边 0。这证明实际独立调用链已执行，不能证明事实质量达标。
- `diagnostic-06` 精确重放第二轮首条任务，确认模型把根实体 ID 和类名作为原文引用而被正确拒绝。该次类型判定显式 supported，因此不能把第二轮所有 type=undetermined 都归因于字段遗漏。原始诊断和失败结果保留，根引用协议与显式 verdict 缺项问题按 T029 修复并另建身份复测。详见 [独立验证实测](independent-verification.md)。

本体快照追加修复：相同 TTL 的无序属性枚举曾改变语义 hash。新建快照规范化声明顺序，保留同 IRI 不同 range 约束；9 项排序/真实变更/legacy/历史回放反例通过。实际以 `PYTHONHASHSEED=4/5` 分别创建 `prepared-04/05`，原件、IR、TTL 树、本体语义、快照 ID/文件和 runtime 七项身份完全一致，见 `preparation-stability.json`。旧快照未改写。

上列测试分组有重叠，不相加为总通过数。初次扩大组合为 244 passed / 1 failed，失败是 API 旧单次响应夹具；按两阶段协议修正后 API 与预算组合 **16 passed**。以下保留各次实际组合结果，最终版本以 `backend-regression-03.log` 为准。

本轮中间组合 `backend-regression-02.log`：**31 文件、257 passed、4 warnings**，涵盖
独立验证、预算/暂停/反证/恢复、在线 API、排序制品及活动评测记账。
前端全部 Node 测试 **44 passed**，`api.ts`、`document-analysis-panel.tsx`、
`document-relationship-graph.tsx` 定向 ESLint 与 `tsc --noEmit` 通过。

真实 API/浏览器最终证据：`evaluations/022-browser-online-20260908/browser-06/`，
**32 个 GET、0 页面错误、0 API 失败**。核验真实持久图谱、语义排序/降级、原文锚点、
投影切换、请求在途时切换另一运行及刷新；数据库表计数、运行水位及 56 次受控模型响应
基线完全不变。图谱制品由受控两阶段响应生成，浏览器无 API 拦截；此结果不计入真实
模型质量。截图已实际查看，Tailwind 样式正常。独立服务和临时数据库已清理。

固定池工具补验见 [FIXED_POOL.md](../../backend/app/evaluation/FIXED_POOL.md)。
真实 `integration-01` 的完整已提交 epoch 已实际经 CLI 导出，形成
`fixed-pool-export-01.json`，内容 hash 为
`6d3dccf7e96b297111d522f87fd6baf8a487783e953e731301f63c890497fdf0`。
源图谱未完成不影响该排序池的完整性，但该导出没有专家参考，也没有实际执行三轮 A–D。固定池专用回归最终 **14 passed**，包括 C/D 与 B 相同 embedding 输入和增量精排费用的核验。当时 **56 个 Python 文件 Ruff 通过**，`git diff --check` 与本轮文档链接检查通过。

## 最终协议修复与回归

`v4.3-independent` 将七项 verdict 和四组原文支持数组全部设为显式必填；根响应只能
`subject_support=[]`，程序核对根 ID/revision/class、task/target 主体及所有片段文档 hash。
错配零调用拒绝；局部主体归属、谓词桥接、类型、极性、条件及反证证明要求不变。
`diagnostic-08` 保留了根修复后“结论支持但遗漏证据数组”的失败模式；最终
`diagnostic-09` 同一任务/原文上下文真实复测 **2 次请求，1 条 supported 且 policy_eligible
关系，1 份证明及 5 个独立决策**。原文对象和谓词引用均唯一匹配，未伪造根提及。
这关闭已复现的协议缺口，尚不构成专家判定或全文质量通过。

最终 `integration-03` 已完成 CPU 排序与真实独立验证集成：**4 个 inspect、7 次主模型请求
（4 discovery + 3 verification）、122 次排序请求**；两轮 semantic epoch 均 committed、
未降级，完整调用预扣一致、待核实主调用为 0。墙钟 **618.611 秒**，总已测输入
108,822 token，生成输出 5,469 token。共 8 条候选关系中 **2 条 supported 且 policy_eligible**，
分别到达 Reactor 和 FilterPress；实际第 4 任务已检查子 Reactor 的 equipmentID，属性未决，
未取得事实资格。详细制品与成本见 [独立验证实测](independent-verification.md)。

该运行预算只有 4 个任务。新关系展开为 17 个主体—谓词槽位，每槽覆盖同一份 86 条原始
记录，故台账计划为 **1,462 个槽位记录机会，4 examined、0 incomplete、1,458 unattempted**，
不是 1,462 条不同原文。整图仍 incomplete/partial，不能算全文或完整多跳质量通过。
运行制品保存的 `stop_reason=attempted_incomplete` 是过宽回退标签；最终追加 T031 仅修正
预算停止原因展示，不回写此实验，也不改变其实际调用或证明结果。

最终组合 `backend-regression-03.log`：**33 文件，301 passed，4 warnings，71.48 秒**。
这次包含固定池工具、全部最终 schema、根隔离及本体排序新增反例，不再与前述分组相加。
实际命令（`backend/`）：

```bash
.venv/bin/python -m pytest -p no:cacheprovider -q \
  tests/test_extraction/test_semantic_*.py \
  tests/test_extraction/test_independent_semantic_verification.py \
  tests/test_extraction/test_ontology_guided*.py \
  tests/test_extraction/test_ontology_snapshot_ordering.py \
  tests/test_extraction/test_document_analysis*.py \
  tests/test_extraction/test_evaluation_execution_accounting.py \
  tests/test_extraction/test_fixed_pool_benchmark.py \
  tests/test_api/test_document_analysis.py
```

最终 **58 个变动 Python 文件 Ruff 通过**。API 与真实浏览器制品生成脚本的受控响应夹具
已迁移到七项判定和四组证据数组；API 严格核验根版本、类型和程序来源，并继续回放对象及
谓词原文。本次协议修改后未再运行浏览器，前述真实浏览器结果针对其当时的冻结响应；
最后另将预算耗尽说明显示为中文，并再次执行前端 Node、ESLint、TypeScript 检查；结果见下方最终收尾记录。

## 初轮验证记录（历史；本轮结果以上节为准）

### 后端主回归

工作目录 `backend/`：

```bash
.venv/bin/python -m pytest -p no:cacheprovider -q \
  tests/test_extraction/test_semantic_ranking.py \
  tests/test_extraction/test_semantic_scheduler.py \
  tests/test_extraction/test_semantic_ranking_execution.py \
  tests/test_extraction/test_semantic_ranking_adapter.py \
  tests/test_extraction/test_semantic_ranking_evaluation.py \
  tests/test_extraction/test_semantic_graph_closure.py \
  tests/test_extraction/test_semantic_proof_regressions.py \
  tests/test_extraction/test_ontology_guided_core.py \
  tests/test_extraction/test_ontology_guided_evaluation.py \
  tests/test_extraction/test_ontology_guided_boundaries.py \
  tests/test_extraction/test_document_analysis_execution_recovery.py \
  tests/test_extraction/test_document_analysis_dispatcher.py \
  tests/test_extraction/test_document_analysis_public_projection.py \
  tests/test_extraction/test_document_analysis_artifact_store.py \
  tests/test_extraction/test_document_analysis_retention.py \
  tests/test_extraction/test_document_analysis_run_store.py \
  tests/test_api/test_document_analysis.py
```

首轮组合结果：**140 passed，4 warnings**。随后交叉复核新增的排序等待、节点解释和独立评测原件入口回归见下方最终整合记录；首轮数量不代替最后修改的验证。

### 补充回归与 PostgreSQL 门

工作目录 `backend/`：

```bash
.venv/bin/python -m pytest -p no:cacheprovider -q -rs \
  tests/test_extraction/test_document_run_execution_postgresql.py \
  tests/test_extraction/test_model_scheduler.py \
  tests/test_extraction/test_evaluation_cmc_benchmark.py \
  tests/test_extraction/test_evaluation_cmc_product_api_path.py \
  tests/test_reasoning/test_ttl_roundtrip.py \
  tests/test_cmc_intermediate_ontology.py
```

结果：**39 passed，1 skipped，4 warnings**。跳过的是 `test_postgresql_claim_is_unique_and_expired_generation_fences_late_writes`：未配置 `DOCUMENT_ANALYSIS_TEST_DATABASE_URL`。SQLite/受控调度测试不证明 PostgreSQL 的锁、独立连接并发或实际迁移状态。CMC legacy 路径测试是兼容回归，不作为活动新核心的多跳证据。

四项 warning 为既有 Starlette/httpx 弃用、两处 `schema_json` 字段遮蔽和 Pydantic class Config 弃用；本次没有用依赖升级消除它们。

### 前端与静态检查

工作目录 `frontend/`：

```bash
node --test tests/*.test.mjs
npm run lint -- src/lib/api.ts src/components/analysis/document-relationship-graph.tsx
./node_modules/.bin/tsc --noEmit
```

结果：**44 passed**；定向 ESLint、TypeScript 均通过。Node 包括受控客户端及服务端组件渲染，不能替代真实浏览器与后端联调。

仓库根对 `git diff --name-only -- '*.py'` 和 `git ls-files --others --exclude-standard -- '*.py'` 的并集运行 `backend/.venv/bin/ruff check`，首轮 **37 个 Python 文件全部通过**。最终变动文件与 `git diff --check` 在交付前再次核验。

## 闭环回归所捕获的真实失败模式

1. **反证子区间伪覆盖**：仅同 `evidence_id` 不够；重验须覆盖完整必需 anchor 身份和范围。错误子串不能恢复旧肯定断言。
2. **子图无法恢复/错误复活**：父关系完整重验通过后重建子主体计划与证明 generation；子属性必须实际重新调用并取得新完整证明才进入有效图。旧证明仍失效。
3. **条件反证被 scope hash 隔离**：后来条件否定影响原无条件肯定；只有明确不相交的 applicability 才隔离。
4. **成本和重试丢失**：非法模型 schema 仍记实际调用；验证客户端禁用隐藏 timeout retry。排序调用先持久化预扣预算，崩溃后不刷新精确请求/逐记录额度；失败写入时模型调用为零。
5. **异步恢复时序漂移**：用 Event 控制 B 槽位慢请求，A 在其间执行并 checkpoint；恢复按历史临时排除集合派发，已执行前缀不重调，已提交顺序不重评分。
6. **历史精排池被当永久就绪**：槽位下一普通机会必须已经提交排序；历史池耗尽不能放行整个尾部。到期台账探索和必要重验单独保留。
7. **不可变候选和证明版本**：同物理节点保留首版解释；主体提及由有效精确入边及其证明授权。断言载荷变化正规为新 revision，同目标不同模型 attempt 的决策/证明具有独立身份，整图回放不能把不同 attempt 的决策拼回旧证明。真实批次存储和 checkpoint 回放共同核验。
8. **过期路径借资格**：活动主体判定核对每条入边两端 revision，旧中间节点版本不能授权当前下游主体。
9. **模型与评分身份漏洞**：加载前再次核验文件及冻结 manifest 身份；过期端点、弱名称绕过来源、多条同名路径借用证明及固定池漏项均拒绝。

## SR01–SR26 可核验映射

下表中的测试均在 `backend/tests/test_extraction/`，API 测试另注明。工程“通过”只指所列受控行为；真实模型小评测和统计收益仍待验。

| 项目 | 工程证据 | 结果与范围 |
|---|---|---|
| SR01 当前主体直接菜单 | `test_ontology_guided_core.py::test_direct_menu_does_not_preexpand_product_properties`；`test_semantic_ranking.py::test_root_and_local_queries_keep_unverified_identifier_out_of_model_text` | 通过；不提前展开 API 菜单 |
| SR02 表头与编号角色 | `test_semantic_ranking.py::test_view_keeps_table_roles_and_long_records_untruncated`；核心 physical mention 测试 | 通过；来源号不成为可信 API 身份 |
| SR03 摘要无事实权限 | 核心 ProofGate/scope 测试；`test_semantic_graph_closure.py` 实际适配两跳反例 | 通过；相似度/摘要无 PredicateEvidence 权限 |
| SR04 高相似假边、低分真边 | `test_semantic_graph_closure.py::test_real_adapter_two_hops_receives_subject_source_and_rejects_unbound_owner`；scheduler 实际探索测试 | 通过受控原文/响应；真实同义表达质量待验 |
| SR05 否定与条件 | `test_semantic_proof_regressions.py` 条件反证；核心 nonaffirmed 测试 | 通过；独立极性与条件门 |
| SR06 负分/失败保留全集 | ranking 整池失败/负分/预算及 adapter timeout 测试 | 通过；失败不算 examined 或语义否定 |
| SR07 物理提及与视图去重 | 核心 merged rows 测试；ranking pool dedup 测试 | 通过；两种去重身份独立 |
| SR08 公平与真实执行 | `test_semantic_scheduler.py` 多主体/章节轮转/台账探索 | 通过；检查实际 dispatch 序列 |
| SR09 长原文与必要闭包 | ranking long view；closure required context | 通过；不静默截断，缺失/预算不足 incomplete |
| SR10 排序及恢复 | ranking immutable epoch；ranking_execution；在线 execution_recovery | 受控恢复及专用 PostgreSQL 并发通过；历史空库迁移失败单列 |
| SR11 主体/身份失效 | core 过期引用/ProofGate；closure transitive invalidation；proof descendant recheck | 通过版本/依赖失效，另由 `test_semantic_subject_versions.py` 和 `test_ontology_guided_node_revisions.py` 核验；不新增实体自动归并/拆分功能 |
| SR12 无桥接高分 | 核心同名拒绝；closure 两跳错误 owner | 通过；两端有效引用不能替代谓词桥接 |
| SR13 真证据变化与纯重排 | scheduler task identity；proof 必要反证重验 | 通过普通重验；P5 补充精排未实施 |
| SR14 权限与晚到响应 | ranking permission cache；document run_store/retention/execution_recovery | owner/fence 及专用 PostgreSQL 并发通过 |
| SR15 展示只读 | `tests/test_api/test_document_analysis.py::test_get_tabs_and_etag_are_side_effect_free`；前端 document API/ranking tests | 真实 API/浏览器通过，32 个 GET 前后无模型请求或运行水位变化 |
| SR16 排序指标和小型真实评测 | `test_semantic_ranking_evaluation.py` retrieval/closure/cost；活动 evaluator 测试 | 工具与真实调用集成已运行；正式事实质量仍未通过 |
| SR17 整池统一名次 | ranking 64 条池逆序返回、缺项/RRF 和分批失败测试 | 通过；批次不单独排名，缺项整池失败 |
| SR18 必要来源及迟到反证 | closure required context/late counterevidence；proof wrong subspan/descendant restore | 通过；必要引用绕过普通池配额 |
| SR19 同租约旧查询 | ranking old query；executor epoch/source validation；在线 fenced commit | 通过，旧结果不能改新计划 |
| SR20 两阶段真实机会 | scheduler 多主体多谓词冷启；ranking_execution 异步恢复 | 通过；计数和临时排除恢复 |
| SR21 plan 与 proof 身份 | scheduler ranking identity；ranking restore；proof recheck budget | 通过；重排不重置语义任务或额度，`test_semantic_proof_persistence.py` 核对不可变证明 |
| SR22 空根/局部主体/隔离编号 | ranking root/local query；closure 原文主体绑定 | 通过；文件名、未验证编号不当可信属性 |
| SR23 独立全集 | `test_semantic_ranking.py::test_independent_universe_rejects_plan_and_ledger_losing_same_record` | 通过；连冻结字段一起伪造仍须与 RecordIndex 比较 |
| SR24 完整评分分母 | ranking_evaluation 重复、等价证明、空图、未裁决、零相关、端点身份/路径测试 | 通过工程评分门；专家完整裁决待验 |
| SR25 消融隔离 | ranking_evaluation 固定池/未注册字段漂移；活动 ablation manifest | A–D 工具通过；真实 A–D 及 P5 E/F 组待验 |
| SR26 多意图成本 | ranking both intents/cost；adapter token/scheduler tests | 通过；两意图各自整池排名，长记录不拆隐式截断窗口，全部实际输入计账 |

## 外部验收门

2026-09-09 已按用户要求重新审计材料并实际调用正式评分入口；当前结果为
`pending_expert_reference / not_run`，不是质量指标失败或通过。完整执行记录、可裁决
审阅包和预测前协议草案见 [T017 本次验收记录](acceptance.md)。

T017 保持未勾选。本地模型依赖和制品已就绪，当前阻碍已不再是排序环境缺失。仍缺专家裁决参考、独立文档样本及预注册阈值；最新真实有界运行已获得 2 条通过程序证明门的设备关系并实际验证子主体属性，但覆盖仍不完整；尚无专家事实 P/R、有效多跳路径及成本收益证据。按 [quickstart.md](quickstart.md) 准备上述独立材料；固定池 B/C 控制查询/视图/池，动态 A–D 从同一显式根重新执行。至少三个真实新 run 保留输入、本体、模型、政策、成本及结果 hash；金标不进入识别输入。先报告精度上下界、召回、路径、覆盖与未裁决，再判断收益。不能使用历史冻结输出补齐本次运行数量。

## 初轮整合记录（历史）

初轮实现文件冻结后，工作目录 `backend/` 实际运行：

```bash
.venv/bin/python -m pytest -p no:cacheprovider -q -rs \
  tests/test_extraction/test_semantic_ranking*.py \
  tests/test_extraction/test_semantic_scheduler.py \
  tests/test_extraction/test_semantic_graph_closure.py \
  tests/test_extraction/test_semantic_proof_regressions.py \
  tests/test_extraction/test_semantic_proof_persistence.py \
  tests/test_extraction/test_semantic_subject_versions.py \
  tests/test_extraction/test_semantic_evaluation_prepare.py \
  tests/test_extraction/test_ontology_guided*.py \
  tests/test_extraction/test_document_analysis*.py \
  tests/test_api/test_document_analysis.py \
  tests/test_extraction/test_document_run_execution_postgresql.py \
  tests/test_extraction/test_model_scheduler.py \
  tests/test_extraction/test_evaluation_cmc_benchmark.py \
  tests/test_extraction/test_evaluation_cmc_product_api_path.py \
  tests/test_reasoning/test_ttl_roundtrip.py \
  tests/test_cmc_intermediate_ontology.py
```

初轮整合结果：**200 passed，1 skipped，4 warnings，44.15 秒**。当时唯一 skip 为专用 PostgreSQL 并发门，本轮已另行补验。该组合覆盖当时的排序/节点版本/证明持久化/撤回恢复及独立 DOCX 入口修改；它是受影响路径的组合，不称为全库测试通过，也不替代本轮独立请求及预扣修改后的结果。

当时 **41 个 Python 文件 Ruff 全部通过**；前端代码自本节之前的 44 项 Node、ESLint、TypeScript 通过后未再变动。新增节点版本测试的 9 个参数化场景均实际调用批次存储，再从检查点无模型回放；包括单批同候选多次升版、父边撤回后新证明恢复及子属性真实重验。独立 Word preparation 的 5 项测试覆盖非 CMC 合法根、原件/目录保护与不访问旧作业库。

Spec Kit `check-prerequisites.sh --json --require-tasks --include-tasks` 通过并定位本特性；11 份相关 Markdown 的本地链接校验通过。交付前 `git diff --check` 通过；差异范围已复核。T001–T016、T018–T019 完成，T017 保持外部待验，未执行提交或部署。


## 最终收尾（2026-09-09 UTC）

T031 仅修正停止原因：总体及实际被预算阻断的槽位显示 `task_budget_exhausted`，
已完成槽位仍为 `queue_exhausted`，实际技术未完成仍有独立状态和计数。前端显示
“本轮任务预算已用完，仍有原文待检查。”。恢复与终态控制没有依赖旧回退标签，
本次没有改动调度、模型输入、证明或覆盖语义。`integration-03` 冻结在这次展示修正之前；
实际模型结果继续引用原 runtime 和原始制品，不声称该制品包含后来的标签修正。

最后针对停止原因、任务预算、调度、在线模型预扣/恢复与 API 六文件组合实际运行：

```bash
.venv/bin/python -m pytest -p no:cacheprovider -q \
  tests/test_extraction/test_ontology_guided_stop_reasons.py \
  tests/test_extraction/test_semantic_model_call_budget.py \
  tests/test_extraction/test_semantic_scheduler.py \
  tests/test_extraction/test_document_analysis_model_call_recovery.py \
  tests/test_extraction/test_document_analysis_execution_recovery.py \
  tests/test_api/test_document_analysis.py
```

结果 **46 passed，4 warnings，13.22 秒**，日志 `backend-stop-reason-final.log`。
这与之前 301 项组合有重叠，不相加。最终前端全部 Node **44 passed**，
两图谱组件及 `api.ts` ESLint、TypeScript 检查通过；日志 `frontend-regression-04.log`。
最终 **59 个变动 Python 文件 Ruff 通过**；本轮变动范围与差异检查通过，未改动用户原有
`frontend/next-env.d.ts` 和其他特性文件，未部署或提交。

T001–T016、T018–T031 已完成对应工程工作；**T017 仍未完成**，所缺独立专家材料、
三轮真实 A–D 对照、事实/路径质量和成本门在上文及专题中明确保留。CPU 环境和真实
独立请求链路已经补齐，不能继续把排序模型环境缺失列为 T017 的当前原因。
