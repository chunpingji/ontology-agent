# 固定池 A–D 运行与协议聚合

`fixed_pool_benchmark.py` 补齐固定池排序子实验。它从活动运行中导出一个完整、已提交且
没有降级的 RankingEpoch，冻结两种意图的原始查询、全部原始 record 视图、池顺序、
视图 hash、模型制品及预算。它不调用主识别模型，不生成实体或事实。

所有命令在 `backend/` 的既有环境执行；B–D 使用已准备的离线模型制品。一次 CLI
调用只执行协议预登记的一个组，运行目录必须新建。不要修改历史运行来补充参考或成本。

## 1. 导出共同输入

源运行的 `result.json` 必须为 `execution_status=finished`，`run.json` 必须匹配最终
制品 hash。源图谱可以仍有未决任务；被选中的排序 epoch 必须完整。导出拒绝缺记录、
缺视图、遗漏原文、非完整视图、缺意图、模型/策略身份不一致及降级 epoch。

```bash
.venv/bin/python -m app.evaluation.fixed_pool_benchmark export \
  --run-dir /controlled/active-run \
  --epoch-id FROZEN_EPOCH_ID \
  --output /controlled/fixed-pool.json
```

命令输出 `content_hash`。将此值写入协议 `pool_hash`。运行时会同时核验内容 hash
与 query/pool/view 身份；只修改正文、只修改 hash、重排池或漏掉记录均不能继续使用
原协议。参考相关性标注不进入导出或排名执行。

## 2. 预登记至少三轮

先编写 `protocol.draft.json`。下面是待填入实际池 hash 的诊断协议；空比较/成本阈值
和空参考明确禁止质量通过，不是推荐的正式验收标准。

```json
{
  "schema_version": "semantic-fixed-pool-protocol-v1",
  "protocol_id": "fixed-pool-diagnostic-01",
  "pool_hash": "填写导出命令返回的 content_hash",
  "k": 10,
  "rounds": [
    {"round_id": "01", "run_ids": {"A": "r01-A", "B": "r01-B", "C": "r01-C", "D": "r01-D"}},
    {"round_id": "02", "run_ids": {"A": "r02-A", "B": "r02-B", "C": "r02-C", "D": "r02-D"}},
    {"round_id": "03", "run_ids": {"A": "r03-A", "B": "r03-B", "C": "r03-C", "D": "r03-D"}}
  ],
  "reference_sha256": null,
  "comparisons": [],
  "cost_limits": {}
}
```

正式检索验收应在运行前填入独立专家参考文件的 SHA256、预先批准的比较及成本门槛：

- `comparisons` 元素为 `{left, right, metric, minimum_mean_delta}`，增量定义为
  `right - left`；`metric` 仅允许 `ndcg_at_k`、`recall_at_k`、`mrr`。
- `cost_limits` 可绑定每次运行的 `reserved_tokens`、`elapsed_seconds` 上限。
- 参考沿用 [README 中的 query/record 格式](README.md)，另需
  `expert_review={status:"approved", reviewer:"...", reviewed_at:"..."}`。
  每个池记录必须裁决，且 `annotation_complete=true`。

```bash
.venv/bin/python -m app.evaluation.fixed_pool_benchmark freeze-protocol \
  --input /controlled/protocol.draft.json \
  --output /controlled/protocol.json
```

保留命令输出的 `content_hash`，将其设置为 `FIXED_PROTOCOL_HASH`，后续每轮使用同一个
值。该值是独立登记的协议身份，不要在每次运行时从可能已修改的协议文件重新生成。
参考或门槛变化需要新协议和新运行，不能回写旧协议后宣称原实验已满足验收。

## 3. 串行执行各组

```bash
for round in 01 02 03; do
  for group in A B C D; do
    .venv/bin/python -m app.evaluation.fixed_pool_benchmark run \
      --pool /controlled/fixed-pool.json \
      --protocol /controlled/protocol.json \
      --protocol-hash "$FIXED_PROTOCOL_HASH" \
      --round-id "$round" --group "$group" \
      --output "/controlled/results/$round-$group" || exit 1
  done
done
```

各组直接复用既有稀疏/metadata 通道、余弦、意图内名次和 RRF 函数：A 使用共同的确定性
初排，B 增加稠密输入，C/D 在同一原始池上增加联合编码精排。池成员不会重新选择，
主体验证/发现也不会运行。单个固定池中没有阶段前沿，C 与 D 可以得到相同排名；
manifest 明示 `phase_interleaving=not_exercised_in_single_pool`。阶段交错的实际收益
必须另用动态前沿运行验证，不能归因于这个固定池结果。

每组使用独立 `scheduler.sqlite3`，仅建立本地模型池和请求两张表。每次 tokenizer、
embedding、reranker 请求前写入 `cost_state.json`；失败和取消仍保留预扣及请求记录。
tokenizer 成本不被伪装为精排输入 token。两意图的精排调用分别计入每记录预算。
写入屏障失败立即阻断派发；取消不重试；普通技术失败仅按冻结预算重试。
任何整池缺项、非法分数、模型身份漂移、超时或预算耗尽都令该组失败，不回退成成功 A 组。

输出包括 `manifest.json`、`observation.json`（仅成功时）、`cost_state.json`、
`costs.json`、`scheduler_requests.json` 和 SQLite。manifest 保留实际代码、数值环境、
预登记 run ID、随机执行 nonce、共同池与最终输出 hash。成本区分预扣、实际已测输入、
实际已测输出、未知输入/输出请求；chat 的 `prompt_tokens/completion_tokens` 与
排序的 `input_tokens` 分别归并到正确方向，不将输入和输出相加。

## 4. 聚合及隔离评分

```bash
.venv/bin/python -m app.evaluation.fixed_pool_benchmark aggregate \
  --pool /controlled/fixed-pool.json \
  --protocol /controlled/protocol.json \
  --protocol-hash "$FIXED_PROTOCOL_HASH" \
  --runs /controlled/results/01-A /controlled/results/01-B \
         /controlled/results/01-C /controlled/results/01-D \
         /controlled/results/02-A /controlled/results/02-B \
         /controlled/results/02-C /controlled/results/02-D \
         /controlled/results/03-A /controlled/results/03-B \
         /controlled/results/03-C /controlled/results/03-D \
  --reference /controlled/expert-query-record-reference.json \
  --output /controlled/fixed-pool-aggregate.json
```

没有专家参考时省略 `--reference`；聚合仍保留全部运行和成本，质量门禁为阻断状态。
聚合先核验共同池的封存内容，并将每组共享输入、原始运行身份和查询/视图绑定到该池；
即使全部组一起修改本体等共享字段，也不能通过。聚合拒绝缺轮/缺组、混合输入或
代码/数值环境、因素漂移、重复执行身份、请求归属错误、
制品 hash 改变及排名漏记录。失败组不会静默排除；未裁决/未标注、未定义指标、未登记
门槛、成本不明/超标都不能通过。每个配对增量必须包含协议全部轮次，零相关查询保留
N/A，不以剔除这些轮次计算漂亮的均值。

`retrieval_protocol_gate=pass` 只表示该固定池检索协议达标；
`graph_quality_gate` 始终为 `not_evaluated`。它不能代替真实文档、完整断言与证据、
实体身份、多跳路径和动态调度的质量评测，也不能单独关闭 T017。
