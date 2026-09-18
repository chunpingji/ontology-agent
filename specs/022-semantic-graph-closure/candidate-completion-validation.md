# 候选完成与运行活性修复验证记录

日期：2026-09-13。对应[候选完成契约](contracts/candidate-completion.md)及 CC 任务。
本记录只收录本次验证，不覆盖或改写历史评测。工程检查、迁移、真实模型质量和生产
部署分别记账；以下通过项不表示真实文档质量或生产运行耗时已经验收。

工程验收后，用户明确授权重新部署，已完成的实际结果见
[部署记录](candidate-completion-deployment.md)。下文未部署描述保留为工程验收时点的边界。

## 已确认结果

| 范围 | 本次结果 | 验证内容与边界 |
| --- | --- | --- |
| 最终后端整合回归 | **356 passed，0 skipped** | 最终代码去重后的 29 个测试文件，117.58 秒；包含以下 API、schema、核心、生命周期与费用恢复场景，不与分次结果累加 |
| 最终 PostgreSQL 整合回归 | **6 passed，0 skipped** | 最终代码串行执行两个 PostgreSQL 文件，4.41 秒；真实行锁竞争、过期拒写与已付阶段恢复 |
| 稀疏候选 API 集成 | **5 passed** | 真实 application 创建及冻结策略、持久排队、dispatcher 执行、状态与 graph GET；隔离 SQLite、假识别适配器与确定性排序，未调用真实模型 |
| 公共 schema 契约 | **7 passed** | candidate_policy 与完成字段、候选计数守恒、真实未完成义务拒绝、旧载荷字段省略 |
| PostgreSQL 执行契约 | **4 passed，0 skipped** | 独立可销毁 PostgreSQL 测试库；真实行锁后租约时钟复核、冷状态准备不阻塞心跳、唯一领取及旧 generation 拒写；不以 SQLite 替代 |
| PostgreSQL 证据修复恢复 | **2 passed，0 skipped** | 已付费发现阶段 worker 丢失后的恢复，使用独立可销毁 PostgreSQL 测试库 |
| 核心组合回归 | **188 passed** | 核心代理报告的本次组合执行结果；与下行稀疏专项存在重叠，不能相加当作独立测试总数；最终整合回归另记 |
| 稀疏候选专项 | **16 passed** | 包含 v3/v4 JSON 冷恢复；该专项结果不代替全部跨层或真实质量验收 |
| 生命周期专项 | **98 passed** | 8 个测试文件，含发布超时整体回滚、合作中断、旧检查点恢复、无进展有界失败和费用保留；已纳入最终整合 |
| 增量兼容性独立复核 | **通过** | 状态边界 21 项通过；1,500 组随机 JSON 前后态对照旧实现，digest、精确 delta、旧增量新解码及原快照不变性一致 |
| 前端定向回归 | **2 个测试文件通过** | 实际图谱/模板渲染区分本轮完成、未入选原文、语义未决与技术未完成，保留旧载荷解释；Node 汇总按测试文件计数，不冒充场景总数 |
| 前端静态检查 | **通过** | 改动路径 ESLint；最终文案收尾后再次运行两个 Node 测试文件和 `tsc --noEmit`，均通过 |
| 后端定向 Ruff | **通过** | 最终 28 个改动源码、迁移与测试文件使用 `--no-cache` 通过；不修改既有缓存目录权限 |
| 文档与差异 | **通过** | 90 个本地文档文件链接均存在，相关差异 `git diff --check` 通过；此数只表示文件目标检查，不表示外部链接或浏览器验收 |

API 测试明确核对：40 条原文、2 个本体槽位时实际候选台账小于 80 条；正常空召回
不会制造否定事实。已完成必要核验但语义仍 undetermined 的运行可以 finished，并保留
API/图谱未决数量；技术失败仍为 retryable_failure。零合法槽位不会生成虚构事实。
新运行初始 pending graph 即包含 candidate_policy，旧 pending/finished 载荷均省略。
重复 GET 和已结束队列检查不增加适配器调用，也不改冻结原件政策。

前端正常空召回的 not_checked 显示“未形成判定（含未发现候选）”；技术未完成从
records_incomplete 展示，避免 finished 同时被误标大量“未完成核验”。旧模式保持原文案。

状态规模反例使用 500 条背景记录：未准入计划的 records、ledger 和 frozen_record_ids
均为空，每计划 JSON 少于 1,100 字符；共享搜索域仍包含全部 500 条记录。H3 只保存
实际探查的 2 个 ID，搜索快照少于 6,000 字符；依赖失效使用槽位标记而无逐记录默认
处置项，恢复后仍正确诊断 500 条来源，快照少于 2,500 字符。图谱映射增量反例在
100 个大计划中同时修改首个计划并追加新计划时，增量操作少于 1,000 字符，旧版本及
新版本均可精确恢复。
最终整合后将该增量反例补强为与现场一致的“修改早期计划并追加计划”，未改生产代码；
再次执行 `test_incremental_state.py` 与 `test_document_state_artifacts.py`，21 passed，
并通过该测试文件的 Ruff 和 `git diff --check`。

整合过程中，旧 API 测试仍要求“未配置模型也产生全文未执行台账”，与新契约冲突。
该测试已参数化覆盖旧策略和新策略：旧策略保留原断言；新策略要求实际候选为零、
completion=incomplete 且明确 retryable_failure。保留缺少主模型不能成功的反例。

## 故障运行旧制品的只读解码

本次通过独立 `docker exec` Python 进程对故障运行 `d485c968…` 的制品解码，数据库事务
设为 `READ ONLY`，进程设置 180 秒 alarm；没有迁移、重启或模型调用。测得：

| 制品 | revision | 解码耗时 |
| --- | --- | --- |
| model-calls | 4431 | 15.692 秒 |
| ranking | 694 | 22.153 秒 |
| graph | 800 | 73.089 秒，包含 361 个计划 |
| 三项合计 | — | 110.934 秒 |

这证明对应旧格式制品可读，也表明旧状态仍然很重。不能由该结果声称旧制品变小、
生产端到端耗时达标或真实模型识别完成。修复着力于锁外准备、后续状态映射增量及
新运行稀疏候选；不原地重写旧制品或混用新旧运行策略。

## 迁移验证及已知失败

- 已在独立可销毁库验证 `0036_report_expert_opinions` → `0037_doc_analysis_liveness`
  升级，并核对既有执行记录保留。验证先用当前 metadata 建结构，移除本次新增的
  3 列并插入旧执行记录以构造 0036 状态，再通过 Python Alembic `command.stamp`
  和 `command.upgrade` 配置该专用库执行增量迁移、复核记录。该结果只覆盖本次增量
  迁移，不等于从空库完整重放历史迁移链。
  迁移入口为 [0037_doc_analysis_liveness.py](../../backend/alembic/versions/0037_doc_analysis_liveness.py)。
- 空库从起点执行全链迁移时，在历史 `0007` 的 `generated_reports` 重复创建处失败。
  失败发生在到达 `0037` 之前，**不是本次 `0037` 的失败**；本轮未修复该历史问题。
  因此不声称空库全链迁移通过，也不由增量升级通过推导新部署初始化成功。
- PostgreSQL 测试使用 fixture 规定的专用可销毁库，0 skip；没有在生产数据库清表或
  借健康接口证明迁移完成。测试库地址和凭据不记录在本文。

## 定向验收入口

下列为对应已验证场景的可重复命令，不在示例中写入连接凭据。PostgreSQL 本次使用
数据库 `document_analysis_candidate_20260913_test`，连接端口为临时映射；命令中的
`<专用测试库URL>` 需替换为满足 fixture 要求的专用可销毁库地址，不使用生产数据库。
迁移验证采用上节所述程序性 runner，不能将其改写为“空库 upgrade head 成功”。

在 `backend/`：

```bash
.venv/bin/python -m pytest -p no:cacheprovider -q tests/test_api/test_sparse_document_analysis_runs.py
.venv/bin/python -m pytest -p no:cacheprovider -q tests/test_api/test_sparse_candidate_contract.py
DOCUMENT_ANALYSIS_TEST_DATABASE_URL='<专用测试库URL>' .venv/bin/python -m pytest -p no:cacheprovider -q --tb=short tests/test_extraction/test_document_run_execution_postgresql.py
DOCUMENT_ANALYSIS_TEST_DATABASE_URL='<专用测试库URL>' .venv/bin/python -m pytest -p no:cacheprovider -q --tb=short tests/test_extraction/test_evidence_repair_postgresql.py
.venv/bin/ruff check --no-cache app/services/document_analysis/application.py tests/test_api/test_sparse_document_analysis_runs.py
```

在 `frontend/`：

```bash
node --test tests/document-ranking.test.mjs tests/document-analysis-runs.test.mjs
npm run lint -- src/lib/api.ts src/lib/document-analysis.ts src/components/analysis/template-document-graph-panel.tsx src/components/analysis/document-relationship-graph.tsx src/components/analysis/document-analysis-panel.tsx
./node_modules/.bin/tsc --noEmit
```

最终 356 项整合使用的命令（`backend/` 工作目录）：

```bash
.venv/bin/python -m pytest -p no:cacheprovider -q --tb=short \
  tests/test_api/test_sparse_candidate_contract.py \
  tests/test_api/test_sparse_document_analysis_runs.py \
  tests/test_api/test_document_analysis_schemas.py \
  tests/test_api/test_document_analysis.py \
  tests/test_api/test_document_analysis_performance.py \
  tests/test_api/test_document_analysis_history.py \
  tests/test_api/test_document_analysis_events.py \
  tests/test_api/test_document_analysis_ranking_budget.py \
  tests/test_extraction/test_sparse_candidate_planning.py \
  tests/test_extraction/test_ontology_guided_core.py \
  tests/test_extraction/test_semantic_ranking.py \
  tests/test_extraction/test_semantic_ranking_execution.py \
  tests/test_extraction/test_heuristic_executor.py \
  tests/test_extraction/test_heuristic_search.py \
  tests/test_extraction/test_adaptive_retrieval.py \
  tests/test_extraction/test_incremental_performance.py \
  tests/test_extraction/test_evidence_repair.py \
  tests/test_extraction/test_ontology_guided_boundaries.py \
  tests/test_extraction/test_document_analysis_run_store.py \
  tests/test_extraction/test_document_state_artifacts.py \
  tests/test_extraction/test_incremental_state.py \
  tests/test_extraction/test_document_analysis_model_call_recovery.py \
  tests/test_extraction/test_document_analysis_execution_recovery.py \
  tests/test_extraction/test_document_analysis_dispatcher.py \
  tests/test_extraction/test_document_analysis_terminal_progress.py \
  tests/test_extraction/test_document_analysis_budget_execution.py \
  tests/test_extraction/test_semantic_model_call_budget.py \
  tests/test_extraction/test_document_analysis_public_projection.py \
  tests/test_extraction/test_semantic_proof_persistence.py
```

## 未执行项与上线边界

- 本次未执行真实模型质量评测或真实文档完整运行耗时验收。
- 本次未部署生产服务，也未原地更换用户旧运行的指纹、预算或识别策略。
- 上线需先执行本次增量迁移，再使后端进程加载新代码。当前工作区另含既有未提交
  改动，服务重启会一起加载这些文件；本次未以测试通过代替该部署范围的核对。
- 使用新候选策略须创建新运行；旧运行保留原策略及费用，并受无进展停止保护。
- `policy_complete` 只表示冻结策略已执行结束。未入选全文尚未核验，已核验的未决、
  条件或冲突仍保留；工程通过不等于全部事实确认、全文穷尽或专家质量门通过。
