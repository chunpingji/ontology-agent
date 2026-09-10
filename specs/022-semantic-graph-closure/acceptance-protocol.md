# T017 真实验收协议草案

状态：**可审阅草案，待专家参考、独立文档和预注册质量/成本标准冻结**。
本文命令是运行示例，所列三轮 A–D 尚未实际执行，也不批准尚未提供的阈值。
正式执行结果另存新评测目录；
本文不关闭 [tasks.md](tasks.md) 的 T017。

对应 [spec.md](spec.md)、[quickstart.md](quickstart.md)、
[固定池工具契约](../../backend/app/evaluation/FIXED_POOL.md) 和
[活动评分契约](../../backend/app/evaluation/README.md)。工程闭环与真实质量验收分开判定。

## 1. 验收对象与材料

必须包含用户指定的 `upload-23c872fb-3ab1-41de-a705-dd4b162dfa09` 对应 DOCX。
当前可回放原件位于仓库内
`evaluations/022-cpu-upload-23c872fb-20260908/input/source.docx`。
以内容 hash 绑定输入，不能只依赖文件名或上传引用。该文档已用于开发诊断，
不能充当独立留出样本；正式文档样本数和抽样方法仍须专家协议确定。

截至 2026-09-09，已核验的稳定输入如下。以下 hash 来自
`evaluations/022-independent-upload-23c872fb-20260908/prepared-04/manifest.json`；
`prepared-05` 的原件、IR、本体快照及 runtime hash 与之相同。

| 冻结项 | 已核验身份 |
|---|---|
| DOCX SHA256 | `e94822808601e73f4ba4c715c664812d4a01e42f0e4e84a6b08a3653b915daf7` |
| IR 文件 SHA256 | `858dddbb762217ede057ed85e659bb6d0349fc90e0c936fbf42756859c882134` |
| 本体 semantic hash | `25a0576f6f6d0efb002c44c5449dfba4329144da3868f9b6234a4038d3d68b6c` |
| 本体 snapshot ID | `24862a42ba8770c1e3a5786e2d16fd0d831e31dd159e63d739603a854d9a73db` |
| 本体快照文件 SHA256 | `38a1b6d5b3bfb573aac3b30c611d1d18d9df8465588fa7a49b467a50bdfd8311` |
| 当时 runtime hash | `52a6ada08de16a28a224be09e372014b1534f51dabbd776cb900539abb50e461` |
| 根类型 | `https://ontology.pharma-gmp.cn/slpra/drug-development/CMCReport` |
| 主识别模型 | `Qwen3.6-35B-A3B`；revision `0b21525e972670ed59e1812e170b27c26355381f0656ecc4e25617ece7dac58b` |
| 主模型身份 | `4aa883b9193693ca3e939427bfc40d55b43e859e7213dc781ca3d70eee33fb06`；适配器 `ontology-guided-model-adapter-v4.3-independent` |
| embedding 制品清单 SHA256 | `55020286f89e3f4aff275a70035f677605e15065921afe7832cad842f855993a` |
| reranker 制品清单 SHA256 | `505e20b19c5937824d54b3b91e2b64f3f023413d183e8d18ccdbd0f51f74197a` |
| CPU 数值环境 | `torch=2.12.1+cpu`、`sentence-transformers=5.6.0`、`transformers=5.6.2`；CPU、L2、完整输入不截断 |

正式运行若含后续代码修复，须重新 prepare，并在运行前登记新 runtime、prepared manifest
及 schema hash；不得混入上述历史 runtime 的轮次。正式三轮只能共用同一个 prepared。
`prepared-04/05` 的 `evidence_max_tasks=4` 仅够有界诊断，不能直接用于全文验收。

以下材料须在评分前隔离保存，不能传入 prepare、summarize、run 或排序请求：

- 专家批准的 `ontology-guided-reference-v1`：完整实体、属性、关系 tuple、方向、极性、
  条件、适用域、等价原文证据组、身份分区、局部实体映射及完整路径；记录批准身份、时间、
  受控标注包 hash 和参考文件 SHA256。
- 固定池查询—记录参考：每条 record 的 0–3 级相关性和 support/counterevidence/
  conditional/context 角色；完整池全裁决、`annotation_complete=true`，并附专家批准记录。
- 预注册质量标准：主指标、最小增量/非劣界限、置信水平、文档样本数、关键反例门、
  全图与路径要求、允许成本及失败处理。现有材料不足以填写数值。

不能根据模型输出反向标成金标；临床原料药/中间体不能为获得正图而放宽为 DrugProduct。
既有 `usesEquipment` 局部正例只能说明程序证明门曾通过，不代表专家已确认事实。

## 2. 实验矩阵与因素冻结

对每个登记的文档和 scope，至少执行 **3 轮 × A–D = 12 个动态新 run**；
对每个登记的共同查询/记录池，另执行 **3 轮 × A–D = 12 个固定池新 run**。
开发诊断、同一次输出复制、改名目录或单个固定池不能补齐动态轮次及独立文档。
运行串行，轮内顺序预先登记并保持一致；同机其他任务的资源竞争另行记录。

| 组 | 稠密召回 | 联合编码精排 | 动态阶段策略 |
|---|---|---|---|
| A | 关闭 | 关闭 | 同槽位 phase 1 优先，之后 phase 2 |
| B | 开启 | 关闭 | 同 A |
| C | 开启 | 开启 | 同 A |
| D | 开启 | 开启 | 同槽位首次 phase 1、第二次 phase 2，之后按现有 4:1 机会交错 |

所有组保留共同根/主体/种类/谓词/章节公平性、探索机会和独立原文验证。
B 的模型配置 `mode=dense`，其排序 policy 仍标记 `mode=semantic`；以实际
`enable_dense/enable_reranker`、模型身份和调度请求共同核验，不能仅按 mode 字符串判断。

动态 manifest 的 `shared` 必须完全相同，包括文档、本体、代码、metadata、根/scope、
主模型、所有 `execution_limits`、非排序识别设置、查询/视图政策及公共排序预算。
具体而言，C/D 只允许 `phase_interleaving` 改变；本字段未重复写入
`execution_limits`，因此无需修改 shared budget。`stop_file` 是 shared budget 的一部分：
全组使用 `null`，或预先登记完全相同的路径，不能为各组生成不同路径后删除差异。

允许因素集合在运行前登记：

| 配对 | `allowed_factor_changes` |
|---|---|
| A→B | `mode, enable_dense, embedding_identity, numeric_runtime` |
| B→C | `enable_reranker, reranker_identity` |
| C→D | `phase_interleaving` |

这些身份变化仅表示启用同一登记制品；CPU、包版本、权重或 tokenizer 更换必须另开协议。
固定池导出保留共同制品清单，其 `phase_interleaving` 明示
`not_exercised_in_single_pool`；C/D 同排名是正常结果，不能据此宣称阶段交错有效。

动态验收另检查 `events.json`、`retrieval-plans.json` 与 `ranking.json` 的真实派发：
当同一槽位两阶段均有待查记录时，D 是否实际派发了 phase 2；比较等预算下已读反证、
完整证据装配、事实/路径及成本。若根本未形成可交错机会，结果为“因素未被实际检验”。
不能通过修改 C 的章节限额、D 的 task 预算或跳数制造差异。

## 3. scope 与全文预算

当前活动 CLI 没有 `--max-hops` 或“只查一跳关系”的选项；实际
`max_hops=max(4, len(focus_path))`。单条 `--focus-path .../usesEquipment`
只限制关系序列，仍检查根和所有合格子主体的本体属性。不能把它称为全本体一跳验收。
省略 `--focus-path` 才是 `document_graph`，其合法多跳前沿也必须计入覆盖。

实际 IR 有 447 个 evidence units、32 个结构节点；RecordIndex 为 **86 条记录**。
覆盖任务的分母是“主体版本 × 合法谓词 × 记录”，不是 evidence units 数或候选池大小。
冻结计划将 phase 1/2 分成互斥记录集合，初次覆盖每个槽位仍为 86 条。

| 范围 | 当前可核验任务基数，不含重试/版本失效 |
|---|---|
| 只计算根 `usesEquipment` 槽位 | `1 × 86 = 86`；当前 CLI 会继续纳入合格子主体属性 |
| 已有 usesEquipment 诊断前沿 | `17 × 86 = 1462`：根关系 1 个，Reactor/FilterPress 各 8 个属性槽位 |
| 未聚焦 document_graph 的根层 | `12 × 86 = 1032`：根菜单 12 个关系、0 个属性；新主体另外增加 |

`integration-03` 实际只完成 4 个任务，尚有 1458 个未尝试。1462 是该次已发现前沿的
基数，新主体、不同身份划分、迟到反证、重新验证和新版本都可能增加任务，不能当成完成上界。
一般预算估算为 `86 × Σ(所有实际合格主体的纳入槽位数) + 重试/重开任务`。
每记录初次发现无候选通常用 1 次主调用，有候选独立验证通常用 2 次；当前 lineage
总上限 6 次包含续验证，不是每次恢复重新获得 6 次。

扩大预算必须在新 prepare 前完成；所有组使用相同冻结值。`max_tasks=2048` 可作为
覆盖诊断起点，但无法保证本例完整，更不能作为未经批准的质量/成本门槛。
完整验收必须实际满足 `completion=in_scope_complete`，并核对未尝试、未完成、
未展开前沿和冲突状态；队列耗尽与低相关拒绝都不代表全文事实不存在。
若任一组触顶，保留失败/未完成结果；扩大预算后需新协议及全组新运行，不能只补跑低分组。

### 已测成本与容量推算

以下实测仅用于安排资源，不是验收阈值或未来耗时承诺：

| 来源 | 已测结果 |
|---|---|
| `integration-03` | 618.611 秒；4 次 inspection、7 次主 LLM 请求（4 discovery、3 verification）；主适配器耗时合计 265.346 秒 |
| 同一运行 CPU 排序 | 90 tokenizer、24 embed、8 score_pairs 请求；各耗时合计 41.669/121.605/156.074 秒；排序预扣 63802 tokens |
| 同一运行完整调度 | 129 请求，0 无归属；实测输入 108822、实测生成输出 5469 tokens；122 个排序请求未提供生成输出计量，单列未知 |
| [CPU 文档实测](cpu-ranking.md) | 同文档另一登记查询的 86 条完整记录，172 对精排输入；两池 CPU 排序 618.26+193.05 秒，实测排序输入 212100 tokens；整轮 843.29 秒，最大单进程 RSS 约 4.80 GiB |

按 7 次请求/265.346 秒的极小样本粗估，1462 个初次任务的 1462–2924 次主调用约
需 15–31 小时；按该 86-record CPU 查询的每槽位约 13.5 分钟粗估，17 槽位另需约
3.8 小时排序。查询长度、上下文、缓存与候选比例不同会显著改变成本；不同模型的时间
不能相加成精确 SLA。三轮四组的动态整范围验收可能达到数百小时，不能按 4-task
冒烟的十分钟直接安排。预算、资源时段和独立文档规模须据此登记。

8-record 固定池、batch 4、两意图、无文本重复及重试时，每个 B 预计 10 个 tokenizer
请求及 3 个 embed 批次，C/D 各再加 4 个 score_pairs 批次；三轮四组约 141 个调度
请求，其中 51 个权重批次，主 LLM 请求为零。实际时间以新运行计量为准。
8 条共同池的 Recall@Pool 不能解释成全部 86 条记录的召回率。

## 4. 动态运行命令

以下在已加载受控主模型/CPU 配置的 shell 执行，不输出环境文件或凭据。
路径与 `CMC_TASK_BUDGET`、`CMC_DEADLINE_SECONDS` 必须来自本次登记协议；它们是执行
限额，不替代专家质量/成本阈值。当前有界配置见 [independent-verification.md](independent-verification.md)。

```bash
REPO=/opt/dev/chen/ontology-agent
CMC_CASE="$REPO/evaluations/022-acceptance-upload-23c872fb-01"
CMC_PREPARED="$CMC_CASE/prepared"
CMC_PYTHON="$REPO/backend/.venv/bin/python"
: "${CMC_TASK_BUDGET:?须先登记相同的各组任务预算}"
: "${CMC_DEADLINE_SECONDS:?须先登记相同的各组软截止秒数}"

cd "$REPO/backend"
EVIDENCE_MAX_TASKS="$CMC_TASK_BUDGET" "$CMC_PYTHON" \
  -m app.evaluation.cmc_benchmark prepare \
  --source-docx "$REPO/evaluations/022-cpu-upload-23c872fb-20260908/input/source.docx" \
  --root-class-iri https://ontology.pharma-gmp.cn/slpra/drug-development/CMCReport \
  --output "$CMC_PREPARED"

# 先登记 prepared manifest、runtime、本体/IR 及模型身份，之后不再改变。
# 离开 backend，避免 cwd 中的 app 遮蔽冻结 runtime。
cd "$CMC_CASE"
export PYTHONPATH="$CMC_PREPARED/runtime"
export OMP_NUM_THREADS=4 MKL_NUM_THREADS=4

for CMC_ROUND in 01 02 03; do
  for CMC_GROUP in A B C D; do
    "$CMC_PYTHON" -m app.evaluation.cmc_benchmark run \
      --prepared "$CMC_PREPARED" \
      --output "$CMC_CASE/dynamic-$CMC_ROUND-$CMC_GROUP" \
      --run-id "022-acceptance-upload-23c872fb-01-$CMC_ROUND-$CMC_GROUP" \
      --mode quality_guided --ranking-ablation "$CMC_GROUP" \
      --max-sections-per-predicate 3 \
      --timeout 600 --timeout-retries 0 \
      --deadline-seconds "$CMC_DEADLINE_SECONDS"
    # 失败制品保留；不要使用 --resume、复用目录或覆盖失败输出。
  done
done
```

这里展示 document_graph 全范围。若执行一条焦点路径的独立协议，应为所有组加入同一个
`--focus-path https://ontology.pharma-gmp.cn/slpra/drug-development/usesEquipment`，
使用新 case/run ID，并将专家参考 scope 同步冻结；其结果不关闭全图/多跳验收。
若选择 `quality_guided_summary`，在各组运行前对同一个 prepared 执行一次 summarize，
冻结同一 `summaries.json`，记录摘要模型、hash 和成本，所有组一律使用该模式。

上述 timeout、线程及章节配置来自当前 CPU/主模型实测起点；正式登记若另选值，须整组
一致。活动 CLI 的 deadline 为请求前软检查，单个正在运行的请求/排序可能越过墙钟截止，
实际总成本必须记录，不能按配置截止值代替实际耗时。

逐个运行独立评分；无专家参考时可省略 `--reference`，工具输出
`status=pending_expert_reference`、`formal_quality_gate=not_run`：

```bash
"$CMC_PYTHON" -m app.evaluation.cmc_benchmark score \
  --prepared "$CMC_PREPARED" --run "$CMC_CASE/dynamic-01-C" \
  --reference /controlled/gold/approved-graph-reference.json
```

动态配对使用已有 `validate_ablation_pair(..., fixed_pool=False)`，对三轮分别执行
第 2 节三组允许因素校验并保存结果。还须核验 12 个新目录、独立 run ID/fingerprint、
请求身份与制品 hash，以及实际因素和冻结值完全一致；该函数本身不执行三轮登记、
参考文件封存或跨文档统计。当前无动态质量聚合 CLI，不能把固定池 aggregate 用于动态图。
正式汇总应把所有单轮 metrics、覆盖、身份/路径、成本及协议比较列入同一报告，
任何缺组、不可比或未评分项均保留，并令总 gate 阻断。

## 5. 固定池运行命令

诊断协议可导出已存在的完整首池；它来自开发运行，不是独立全文样本：

```bash
"$CMC_PYTHON" -m app.evaluation.fixed_pool_benchmark export \
  --run-dir "$REPO/evaluations/022-independent-upload-23c872fb-20260908/integration-03" \
  --epoch-id 1891f4076c8144834dbb250baefa8eb33af62c7e0f657b31af0530340886228d \
  --output "$CMC_CASE/fixed-pool.json"
```

正式协议的源 epoch 选择规则在查看质量分数前登记；若覆盖多个槽位/关键反例，分别
导出各池并登记各自完整三轮，不挑选排序提升最明显的池。完整已提交且未降级的 epoch
才可导出，源图谱局部未完成不会自动使完整池无效，但其来源及范围必须公开。

依 [FIXED_POOL.md](../../backend/app/evaluation/FIXED_POOL.md) 编写
`fixed-protocol.draft.json`：固定 `pool_hash`、`k`、三轮各组唯一 run ID、专家参考 SHA256、
`comparisons` 和 `cost_limits`。未给专家材料时使用该文档的诊断模板：
`reference_sha256=null`、`comparisons=[]`、`cost_limits={}`，质量 gate 必须保持阻断。
不要填入助手自行批准的精度、增量或成本数值。

```bash
"$CMC_PYTHON" -m app.evaluation.fixed_pool_benchmark freeze-protocol \
  --input "$CMC_CASE/fixed-protocol.draft.json" \
  --output "$CMC_CASE/fixed-protocol.json"

# 将 freeze 输出的 content_hash 登记为 FIXED_PROTOCOL_HASH，后续不从可变文件重算。
: "${FIXED_PROTOCOL_HASH:?须提供独立登记的协议 content_hash}"
for CMC_ROUND in 01 02 03; do
  for CMC_GROUP in A B C D; do
    "$CMC_PYTHON" -m app.evaluation.fixed_pool_benchmark run \
      --pool "$CMC_CASE/fixed-pool.json" \
      --protocol "$CMC_CASE/fixed-protocol.json" \
      --protocol-hash "$FIXED_PROTOCOL_HASH" \
      --round-id "$CMC_ROUND" --group "$CMC_GROUP" \
      --output "$CMC_CASE/fixed-$CMC_ROUND-$CMC_GROUP"
  done
done

"$CMC_PYTHON" -m app.evaluation.fixed_pool_benchmark aggregate \
  --pool "$CMC_CASE/fixed-pool.json" \
  --protocol "$CMC_CASE/fixed-protocol.json" \
  --protocol-hash "$FIXED_PROTOCOL_HASH" \
  --runs "$CMC_CASE"/fixed-0[123]-[ABCD] \
  --output "$CMC_CASE/fixed-aggregate.json"
```

上述 aggregate 有意不传参考，适用于缺材料的诊断；正式验收必须增加与登记 SHA256
匹配的 `--reference /controlled/gold/approved-query-record-reference.json`。
每次 run 都使用独立 scheduler、成本预扣及 nonce，不沿用上一轮缓存输出；记录权重冷加载
和宿主页缓存状态，不因输出恰好相同认定执行重复，也不以复制输出当独立执行。

## 6. 判定与不能静默排除的结果

| 层次 | 必须输出的判定 |
|---|---|
| 执行 | 正常结束、失败、暂停、任务预算耗尽、排序失败及未知计量分别记录；`execution_status=finished` 仍可能是 `partial/incomplete` |
| 排序 | Recall@Pool/Recall@K、nDCG@K、MRR、四类角色召回、完整证据集合入池/读取/装配；未裁决、零相关和未定义指标保留 |
| 图谱 | 完整 tuple P/R/F1、precision 上下界、实体错误合并/拆分、全局身份、禁止断言、原文回放、路径与覆盖；无预测 P=N/A，有正例漏出 R/F1=0 |
| 成本 | discovery/verification、tokenizer/embed/rerank 各自请求与输入/输出，预扣及未知成本、重试、摘要、排队、墙钟、缓存及实测内存；inspection 数不替代实际主调用数 |
| 协议 | 固定池与动态比较分别判定；所有文档、轮次、组别均列入，不静默剔除失败组或只汇总有分数的查询 |

实际工具失败口径：

- 固定池缺参考/阈值：`retrieval_protocol_gate=blocked_or_failed`，理由包含
  `missing_expert_reference`、`quality_or_cost_thresholds_not_preregistered`；
  `graph_quality_gate` 始终为 `not_evaluated`。
- 固定池失败或未完成组：`failed_or_unfinished_executions`；成本超标/不明：
  `cost_threshold_exceeded_or_unmeasured`。缺组、身份/内容漂移或参考 hash 错误直接拒绝聚合，
  CLI 错误也须保存，不能修剪输入后当完整协议。
- 图谱无参考：`formal_quality_gate=not_run`；参考未经专家批准会被拒绝；
  未配置阈值则为 `not_configured`。仅在有效参考和阈值存在时输出 `pass/fail`，
  覆盖、原文证明、未评分/身份/路径问题不能通过改小评分范围掩盖。
- 单轮图谱 `pass` 或固定池 `pass` 都不足以关闭 T017。总体验收还要求独立样本、
  预注册增量/非劣统计、三轮动态 A–D、关键反例和成本门同时完成。

现阶段可以完成真实固定池/动态工程运行、原文和制品身份核验、覆盖与费用报告；
缺失专家参考、独立样本及正式门槛时，总体结论必须为**研发待验/质量门阻断**。
本协议不把部署、业务事实提交、权威 TTL 修改或历史评测重写包含在验收执行中。
