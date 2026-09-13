# S0 本体原生词汇语义检索：隔离验收

对应 [spec.md](spec.md) 的 OL-AC-01～07，实施契约见
[ontology-label-retrieval.md](contracts/ontology-label-retrieval.md)。
本轮无需词表发布、数据库迁移、服务重启或本体写入。

## 1. 环境与隔离

使用 `backend/.venv` 及 `backend/tests/conftest.py`；本体采用临时 RDF/TTL，
原文、快照和运行制品显式绑定 `tmp_path`。模型替身只捕获实际调用接口，
不加载真实模型、不访问生产数据库、不启动服务器。隔离真实 BGE 的诊断另见
第 4 节，下列命令为实际存在的新增词汇及适用回归入口。

在 `backend/` 执行：

```bash
.venv/bin/python -m pytest -p no:cacheprovider -q \
  tests/test_extraction/test_ontology_lexical_snapshot.py \
  tests/test_extraction/test_ontology_lexical_retrieval.py \
  tests/test_property_cardinality_snapshot.py \
  tests/test_extraction/test_ontology_snapshot_ordering.py \
  tests/test_extraction/test_ontology_guided_core.py \
  tests/test_extraction/test_ontology_guided_boundaries.py \
  tests/test_extraction/test_semantic_ranking.py \
  tests/test_extraction/test_adaptive_retrieval.py \
  tests/test_extraction/test_semantic_ranking_execution.py \
  tests/test_extraction/test_document_analysis_execution_recovery.py
```

本次执行结果见第 4 节；重复运行应继续使用临时本体与制品路径。

## 2. 工程场景

| 场景 | 操作与可执行判据 | 验收号 |
| --- | --- | --- |
| 原生来源 | 临时类、关系、属性各含仅 label、仅 altLabel、并存、多语言及无语言值；快照逐条保留 text/language/predicate_iri；只有 altLabel 时不提升为快照单标签 | OL-AC-01 |
| 实际语义消费 | 合法目标类型声明“反应釜”及英文，调用 RankingService 的真实准备路径；捕获 embed 的查询和 score_pairs 的查询部分，均包含选中词 | OL-AC-02 |
| 角色与权限 | 无关类型同名、类型别名、未证实实例名以及否定/条件原文；只出现合法本体语境，可信 subject_mentions 无新增别名 | OL-AC-03 |
| 版本与后续调用 | 打乱注释枚举身份不变；只改词汇/语言则 context/ontology/query 依赖变化；后续检索及回放仍消费原冻结载荷 | OL-AC-04 |
| 缺失与失败 | 无别名正常；完整注释过量仍全部冻结，采用受 8/80/640 限制且有审计；v2 原展示 label 不能重新引入被拒用的长词，无入选词则回退 IRI；源读取失败与载荷篡改明确拒绝 | OL-AC-05 |
| 校准与缓存 | v2 校准要求本体 hash、词汇 hash、选词版本精确匹配；新 v2 查询配 v1 或失配 trial/enforce 校准在模型调用计数为零时拒绝，旧无载荷 v1 与 enhanced 无剪枝路径分别可回归 | OL-AC-06 |

必须验证模型接口处的输入，不能仅断言 `model_text` 拼接、日志或 H1 命中。
捕获实际调用的模型替身属于工程验证，不能据此宣称向量召回质量提高。
旧缺省字段保持省略、hash 和 v1 查询不变；不能为使新测试通过重写旧恢复断言。

## 3. 独立真实模型对照：待验

OL-AC-07 在新的独立评测目录/运行身份内执行，使用同一文档、本体逻辑、模型
制品、预算、调度和无剪枝模式，仅改变词汇来源：

- E0：旧查询链路。
- E1：补齐多语言 `rdfs:label`。
- E2：在 E1 上加入 `skos:altLabel`。

已有定义背景、快照展示 label、H0/H1 固定词及输入、其他策略保持一致；记录类型语境变化。
新快照 rdfs 多值展示的确定性选择可能影响词面输入，该变量不混入语义对照。
保存真实 embedding/rerank 输入、候选 Recall@K、最终事实 precision/recall、
完整路径、错误挂接/否定/条件、费用/耗时，以及未决、未尝试和技术失败。
未见文档及独立参考不用于生成词汇；样本与门槛未固定前不宣称质量或泛化达标。
本轮完整 OL-AC-07 对照仍待验，真实质量收益及 S0 质量退出继续待验。
隔离真实 BGE 的小样本排序诊断单独登记；没有完整事实/路径及独立质量验收，
不能用该诊断替代本节的退出条件。

## 4. 本次验证记录

2026-09-13 工程实现及组合验收：

- 第 1 节 10 文件联合 pytest：**169 passed，4 个既有 warnings，60.05 秒**。
  覆盖原生 RDF 注释、仅 altLabel、多语言/去重/来源、快照 hash 与篡改、角色化
  选词、实际 embedding/rerank 调用输入、v1 兼容、v2 精确校准及执行/恢复。
- 末轮收窄快照展示 label 后，以相同 pytest 参数复验
  `test_ontology_lexical_snapshot.py`、`test_ontology_lexical_retrieval.py`、
  `test_property_cardinality_snapshot.py`、`test_ontology_snapshot_ordering.py`：
  **61 passed，4 个既有 warnings，3.83 秒**。这是联合验收的受影响子集，不能累加为新覆盖数量。
- 全部 14 个本次源码/测试文件定向 Ruff `--no-cache`、全工作区
  `git diff --check` 通过；本次 7 个文档的 60 个本地链接、8 条需求/7 个验收/
  14 个任务编号检查通过。
- 已执行第 5 节的隔离真实 BGE 小样本排序诊断；完整质量门仍未完成。

以上是工程证据。完整真实模型事实质量 OL-AC-07、S0 质量退出及后续词表产品
继续待验；本次未执行生产部署或数据库迁移。

## 5. 隔离真实模型排序诊断

本次使用实际本地 BGE-M3 与 BGE-reranker-v2-M3，CPU/float32、batch 4，
从当前本体读取原生注释，固定展示 label、8 条合成记录及定向目标范围，比较
E0/E1/E2 的发现/反证查询。该诊断对固定完整池评分，没有执行 H0/H1 或动态
候选选择，未运行主识别模型、完整本体执行或事实写入。
冻结原文、来源、查询、输入对、原始分数、环境和成本见
[真实排序记录](../../docs/evaluations/ontology-label-retrieval-s0-20260913/README.md)。

- 成功轮耗时 **139.38 秒**，**19** 个调度请求全部完成，计量 **22,799 tokens**。
  8 条原文的 embedding 在三组间共享；不按各组重复计算公共费用。
- 所有 E0/E1/E2、双意图、dense/rerank 组合的合成 **Recall@5 均为 0.8**。
  E2 部分 nDCG 和别名原文名次改善，但英文正例在精排中下降到第 6；不能宣称
  召回普遍提高或业务质量通过。
- 测量后的代码收窄经[当前查询兼容核验](../../docs/evaluations/ontology-label-retrieval-s0-20260913/current_query_compatibility.json)
  验证，6 条查询的完整 JSON、文本、依赖 hash 与 ID 均一致；该核验不重新调用模型。
- 首次实验受 batch 上限拒绝，**57.19 秒**、**2** 个已完成的计数请求、0 输入
  tokens，保留[失败记录](../../docs/evaluations/ontology-label-retrieval-s0-20260913/failed_batch_limit/report.json)。
  修正诊断脚本的分批调用后新建成功运行，没有放宽运行时上限。

该记录的参考为合成诊断标记，未进入模型输入，也不是专家金标。诊断只证明当前
真实双模型消费词汇并产生可观察的排序变化；尚无未见业务文档、最终事实 precision/
recall、完整路径与总体预算质量验收，因此 OL-T014 和 OL-AC-07 继续待验。
