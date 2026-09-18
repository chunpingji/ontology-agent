# S0 本体标签语义检索：真实模型小样本诊断

本目录冻结 2026-09-13 的一次合成原文固定池诊断。真实本地 BGE embedding 和 reranker 已执行，证明 `rdfs:label` / `skos:altLabel` 进入实际语义模型输入，并观察排序变化。**它不是业务文档质量评测、专家金标验收、完整文档分析运行或部署证明。** 后续复跑使用新目录，不覆盖这里的制品。

## 输入与隔离范围

- 权威来源为 `ontology/slpra/slpra-drug-development.ttl` 和 `slpra-equipment.ttl`，文件 hash 保存在 [report.json](results/report.json)。只读取其中 CMCReport、usesEquipment、Equipment、Reactor 的 `rdfs:label` / `skos:altLabel`；[源注释](results/source_annotations.json)保留 IRI、语言和来源属性。已有定义各组保持相同，不从定义生成词汇。
- 查询主体为 CMCReport，谓词为 usesEquipment。保留声明 range Equipment，固定本次关注的合法目标子类 Reactor。该范围用于排序诊断，不声称执行了完整 range 闭包或全本体分析。
- [合成原文](results/synthetic_relevance.json)共 8 条，覆盖“反应器”“反应釜”“Reactor”、肯定、否定、条件、培训背景和两个无关干扰。DOCX 由脚本临时生成；归档保留 JSON 原文、[DocumentIR](results/document_ir.json)、[metadata](results/metadata.json)与[检索视图](results/retrieval_views.json)，不复制二进制 DOCX。
- 各组复用同一批原始 records、检索视图、定义背景、主体/谓词/目标的展示 label。展示 label 在脚本中从同一 TTL 按中文优先选择一次，随后固定。E1/E2 使用同一 classes，只改变冻结词汇上下文；未通过全本体快照构建器重新选择展示 label，因此本次不测快照展示标签规范化的独立影响。
- 不执行 H0/H1、固定硬编码别名、动态候选选择、剪枝、图谱识别或事实核验。直接使用线上 `build_subject_queries()`、`build_retrieval_views()`、`LocalSemanticRanking.embed()` / `score_pairs()` 和 `cosine_scores()`；所有组都对同一完整 8 条池评分。H0/H1 的命中不能解释本次变化，本诊断也不能证明其行为保持不变。
- 使用独立临时 SQLite 调度库；运行前将 `DATABASE_URL` 指向该私库，并启用 `HF_HUB_OFFLINE=1`、`TRANSFORMERS_OFFLINE=1`。未启动服务、访问共享业务数据库、调用主 LLM、下载权重或提交事实。SQLite 不进入归档，只保留[请求遥测 JSON](results/scheduler_requests.json)。

| 组 | 实际查询版本 | 新增词汇输入 |
|---|---|---|
| E0 | subject-slot-query-v1 | 旧查询，无新增词汇上下文 |
| E1 | subject-slot-query-v2 | 任务所涉概念的多语言 `rdfs:label` |
| E2 | subject-slot-query-v2 | E1 加本体已有 `skos:altLabel`，包括“反应釜” |

每组均构建 discover 与 counterevidence 两种 intent。E1 的两个模型查询均不含“反应釜”，E2 均包含。实际查询、传给 embedding 的完整文本、传给 reranker 的 48 个查询/原文输入对分别保存在 `results/E*-queries.json` 与 `results/E*-*-model_inputs.json`。

## 观察结果与限制

成功运行使用本地 bge-m3 与 bge-reranker-v2-m3，CPU / float32，batch size 4，每对输入上限 1024 tokens，OMP/MKL 线程数均为 4。两份模型目录均完成完整文件清单及逐文件 SHA256 校验；revision、manifest hash、tokenizer hash、包版本与数值配置见报告。未复制任何模型文件。

| 合成指标 | E0 | E1 | E2 |
|---|---:|---:|---:|
| discover dense nDCG@5 | 0.7093 | 0.7093 | 0.8166 |
| discover rerank nDCG@5 | 0.7093 | 0.7093 | 0.8546 |
| counterevidence dense nDCG@5 | 0.8166 | 0.8166 | 0.8166 |
| counterevidence rerank nDCG@5 | 0.7093 | 0.8166 | 0.8546 |
| 各 intent / 通道的 Recall@5 | 0.8 | 0.8 | 0.8 |

别名肯定记录在 discover dense 的位置为 2 → 2 → 1，rerank 为 4 → 4 → 2。别名否定记录在 discover rerank 的位置为 5 → 3 → 1。与此同时，**E2 的英文肯定记录在两种 intent 的 rerank 中都跌到第 6，被挤出前 5**；E0 中为第 3，E1 中分别为第 5、第 3。因此本次没有 Recall@5 改善，也不能只报告局部收益。

指标参考是脚本作者设定的合成相关性等级，未经过专家批准：肯定、否定、条件记录各 grade 3；培训背景 grade 1；两个无关记录 grade 0。Recall 将五条 grade 3 记录作为相关集；nDCG 使用 `2**grade - 1` 增益、`log2(position + 2)` 折扣。参考仅参与推理后的指标计算，既不用于构造查询，也不作为输入对内容。[独立核验](reference_and_cost_audit.json)逐条检查 6 条 embedding 查询和 48 个 reranker 输入对，确认它们仅由冻结查询和原始视图组成，不包含 grade、case ID 或参考字段。合成场景本身围绕本体别名设计，不能作为未见业务文档的泛化证据。

本次使用固定池，没有进行真实候选入池召回、调度/装配闭包、事实 precision/recall、完整路径或生产性能验收。无关行政/产品记录仍位于后部，但培训背景在多组排名靠前，条件与英文记录仍存在排序缺口。

## 成本与失败记录

成功运行耗时 **139.38 秒**。19 个私有调度请求全部 completed：2 次 `count_tokens_batch`、5 次 `embed`、12 次 `score_pairs`。其中 17 次为 embedding / rerank 推理请求。

| 成本归属 | 输入 tokens |
|---|---:|
| E0 查询 embedding 与两种 intent 的 rerank | 6,694 |
| E1 查询 embedding 与两种 intent 的 rerank | 7,684 |
| E2 查询 embedding 与两种 intent 的 rerank | 7,756 |
| 三组小计 | 22,134 |
| 三组共用的原始 record embedding 两批 | 340 + 325 = 665 |
| 总计 | **22,799** |

两次 tokenizer 计数请求不执行模型推理，适配器 `input_tokens` 记为 0。逐请求成本来自 [model_observations.json](results/model_observations.json)，分组与共享操作对账见[成本核验](reference_and_cost_audit.json)。E0 包含首次 reranker 权重加载，E1/E2 复用热模型，不能将各组耗时直接解释为词汇方案的延迟差异。

首轮脚本一次提交 8 条记录，超过适配器配置的 batch size 4，在 tokenizer 完成后收到 `ranking_batch_limit`，未获得排序结果。保留[首轮报告](failed_batch_limit/report.json)和[首轮原脚本](failed_batch_limit/run.py)。随后仅将临时脚本改为调用方分批，保留相同模型限制与实验设计，使用全新目录完成成功运行；未修改产品适配器或放宽限制。

## 代码身份、制品与复跑

[run.py](run.py)为成功运行的原始脚本，文件 SHA256 与报告 `harness_sha256` 完全一致。报告中的 `code_sha256` 是测量开始时读取的源码身份。运行中查询 builder 增加了拒用词不能绕过旧展示 label 通道的边界修复；结束后使用当前 builder 与相同冻结 IR、本体、主体重新生成查询，**6 条查询的完整 JSON、model_text、query ID 和 query_dependency_hash 均与实测输入一致**，见[兼容核验](current_query_compatibility.json)及[核验脚本](check_current.py)。这只证明本样本输入相同，不代表新边界场景已经执行真实模型评测。

原始报告按当时临时目录保留路径与制品 hash，未改写为归档路径。归档保留 JSON 制品与脚本，省略两轮私有 SQLite、合成 DOCX、共享 record 向量等运行文件；省略项和所有归档文件的 SHA256 见 [archive_manifest.json](archive_manifest.json)。原始完整成功运行目录为 `/tmp/ontology-label-semantic-3f9zewz6/`，失败目录为 `/tmp/ontology-label-semantic-CoBgqm/`；这些临时目录不承担长期保存保证。

在同一工作区复跑原脚本时，必须复制到新目录后执行，不能直接在本冻结目录写入结果：

```bash
S0_REPLAY_DIR="$(mktemp -d /tmp/ontology-label-replay.XXXXXX)"
cp docs/evaluations/ontology-label-retrieval-s0-20260913/run.py "$S0_REPLAY_DIR/run.py"
backend/.venv/bin/python "$S0_REPLAY_DIR/run.py"
```

脚本的 `BACKEND` 固定为本次工作区绝对路径，模型路径固定为本次校验的本地 revision；迁移工作区时需显式调整，并记录新脚本 hash。复跑会重新读取当前 TTL 和 builder，使用新合成 DOCX / 运行 ID / 调度库，并记录新身份；只有核对源码、模型、TTL、数值配置和实际查询/视图内容后才能作可比结论，不能仅因脚本退出 0 就宣称复现了原排序。脚本不会下载模型，缺失制品或限制失败会保存失败报告。
