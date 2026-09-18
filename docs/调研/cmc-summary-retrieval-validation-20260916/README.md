# CMCReport：接入现有摘要检索的真实验证

已将项目现有的结构解析、分层摘要元数据和记录检索接入 GLiNER2.5 + Qwen 工具协作评测。**工程接入检查通过，但本轮冻结配置的端到端验证未通过，也没有证明摘要改善事实质量。** GLiNER2.5 完成 8/8 组；真实 Qwen 共 9 次请求，7 组候选请求 HTTP 400，包装存放组候选通过 JSON Schema、核验输出截断，最终完整完成 **0/8** 组。失败未重试，未切换线上识别器。

摘要启用／屏蔽的独立对照确认：摘要在 7 组产生非零分数贡献，但 8 组入选记录集合及模型输入均不变。仅简介组的记录排序变化，随后按 IR 顺序组织的模型输入仍相同。本轮不能据 D/C 差异推导摘要收益。精确身份与结果见 [summary.json](summary.json)。

## 实施范围与输入

新增 [检索适配](../../../backend/app/evaluation/schema_card_summary_retrieval.py)和 [D 组 runner](../../../backend/app/evaluation/schema_card_summary_tools.py)，复用现有 `prepare_metadata`、`RecordIndex`、`plan_slot`，由选中的原文记录重建工具引用、Schema 卡片和 Qwen 输入。两份新增测试文件覆盖输入身份、预算、摘要隔离、完整记录及引用重建；线上服务和旧 C runner 未因本轮修改。

- 报告：`upload-23c872fb-3ab1-41de-a705-dd4b162dfa09`；Word SHA-256：`e94822808601e73f4ba4c715c664812d4a01e42f0e4e84a6b08a3653b915daf7`。
- 同一全文 IR：447 个证据单元，329 张冻结本体卡片；IR SHA-256：`858dddbb762217ede057ed85e659bb6d0349fc90e0c936fbf42756859c882134`。
- 强模型：项目 `Qwen3.6-35B-A3B`，声明 revision `0b21525e972670ed59e1812e170b27c26355381f0656ecc4e25617ece7dac58b`。
- NER：`fastino/gliner2.5-multi-v1`，revision `aaecfe45db1d828c963717054ccb868e8ad1f1d5`，权重 SHA-256 `c1ff4ec0bc00031c15530b8f3c33d3677f27949e6a0cb52e1247a6224b6c5395`；依赖、SKOS overlay 和抽取参数沿用 [C 组](../cmc-gliner2-skos-validation-20260916/results-2.5/README.md)。
- D Qwen run：`cmc-summary-tools-653cb0cb7f744de5`；复用 NER run：`cmc-summary-tools-ce183fde807a4dfb`。

摘要取自历史 `docs/evaluations/cmc-23c872fb-20260907-02/summaries.json`，SHA-256 `e5bf9a23060050d59155d1bd04410a7ad5b4131b00012db415da7c7e10afd15f`。Word 和 IR 相同，32 个节点内容哈希经重解析全部匹配；29 个完成的模型摘要、2 个部分摘要、1 个根节点抽取回退。这些是章节／子树摘要，没有独立页级摘要制品。历史摘要同用项目 Qwen，保留 `word-tree-summary-v1` 身份和 951.256618 秒历史生成成本；本轮新增摘要调用为 0。

摘要元数据快照 `fd782f2db11d28a01e27bcddcbc0f1a710c204541f4823eb5ade472fb9694ec8`，文件 SHA-256 `91ca5544d6f2c03bf4d7f51e6e5ce1cf9474906f127ba6295efbee65e0fedfe0`。见[准备脚本](../../../output/schema-card-qwen-cmc-summary-retrieval-20260916/metadata/prepare_metadata.py)及[身份校验](../../../output/schema-card-qwen-cmc-summary-retrieval-20260916/metadata/manifest.json)。历史制品没有重写。

## 冻结检索协议与实际贡献

保留旧 C 的八组类菜单以限定需求，各类实际谓词和允许目标类型形成查询，类／谓词去重。旧 section/text_prefix 选择器、旧候选、银标和原文窗口均不参与选择。尚未验证从 CMCReport 根类自主展开整个关系图；PDE 仍只有原菜单中的一个 `SharedLineAssessmentData` 槽位。

全文 `RecordIndex` 有 86 条记录（48 段落、38 表格行）。现有 `plan_slot` 逐查询得分为 `4 × 原文词项 + 2 × 标题 + 摘要`，本轮评测适配再对需求组内查询等权求和。每组最多选择 6 个正分完整记录，含原文上下文的去重文本预算 12,000 字符；同分按原位置稳定排序，超预算整条 deferred，不截断。此适配不是生产逐主体／谓词完整执行器，本轮没有 embedding／cross-encoder 精排。

记录的 source、header、note、parent、field-group、ancestor-heading 引用均保存；只从冻结 IR 重建原文。角色与检索分数保存在检索制品，不额外进入 Qwen；摘要也不作为事实证据或提示内容。现有 parser 会把部分工艺首行作为 header context，此标记本身不证明它在语义上就是表头。

独立复算和[完整输入等同性检查](masked-equivalence-completed.json)确认：

- 7 组摘要分量非零，存放组为零；所有 enabled-minus-masked 差值均与摘要分量一致。
- 8 组入选记录集合不变，仅简介组的记录顺序变化；按 IR 顺序重建的 source、uN、计划、卡片不变。
- 核对 NER 全输入相同后复用同一冻结 NER，重建模型可见工具、Schema，并对照实际请求。没有额外运行屏蔽摘要的 NER/Qwen；这是输入等同性验证，不是两轮独立随机推理一致性的证明。

等同性检查 293 项通过，覆盖八份实际候选请求；另复用同一冻结候选核对存放核验请求的原文、卡片、Schema 和参数。这个条件性重建不代表屏蔽摘要后又独立生成了一轮候选。

以下仅是 D/C 原文 ID 的事后覆盖差异，不是全文召回率，也没有用于调参或回补：

| 需求组 | 保留 C 单元 | D 新增单元 | C 未再入选单元 |
|---|---:|---:|---:|
| 简介 | 0 | 103 | 3 |
| 产品属性 | 6 | 97 | 0 |
| 工艺设备 | 0 | 94 | 3 |
| 质量条件 | 3 | 72 | 0 |
| 包装存放 | 10 | 1 | 0 |
| 残留 | 0 | 94 | 2 |
| PDE | 45 | 1 | 0 |
| 毒性 | 0 | 103 | 15 |

D 使用 190 个不同原文单元，C 为 86；交集 66、D 新增 124、C 未再入选 20。具体偏移见[独立检索复核](retrieval-review.json)：

1. 简介日期／用途／批量自然表述未匹配完整属性标签，相关记录得分 0、排 38/39；毒性表及图例得分 0、排 82/83。
2. 产品需求中的“规格／分子式／分子量”等词经目标类型标签反复命中设备表，设备表行占据前五。
3. 工艺组的 `usesEquipment` 在三个类中出现，某条记录单项 25 分累计三次为 75 分，总分 99；组级求和放大了重复需求。
4. 残留 N/A 的标题与摘要贡献 6 分，排第 10，仍超出六记录预算。

这些结果表明当前组级聚合有相关性偏移，不能把设备表主导或上下文扩大当作召回改善。冻结实验未事后改词、调权重或补回旧片段。

## 真实 Qwen 与独立原文复核

| 需求组 | D 原文单元 | NER 带标签 span | Qwen 结果 |
|---|---:|---:|---|
| 简介 | 103 | 543 | 候选 HTTP 400 |
| 产品属性 | 103 | 1,251 | 候选 HTTP 400 |
| 工艺设备 | 94 | 798 | 候选 HTTP 400 |
| 质量条件 | 75 | 425 | 候选 HTTP 400 |
| 包装存放 | 11 | 24 | 候选 stop；核验 length |
| 残留 | 94 | 446 | 候选 HTTP 400 |
| PDE | 46 | 403 | 候选 HTTP 400 |
| 毒性 | 103 | 1,189 | 候选 HTTP 400 |

共 9 次请求，低于 16 次预算，未重试。包装存放候选原始返回通过保存的 JSON Schema，产生 4 条声明和 2 条观察；核验请求输出 8,192 tokens、`finish_reason=length`，没有完整可解析 JSON，runner 保留 `model_output_truncated`。其余 7 次的 HTTP 400 正文没有被现有 Recorder 保存，不能断言具体错误原因。

总 run 的 `status=completed` 表示所有预定组已尝试结束；每组 `status=failed`，完整验证为 0/8。程序保留声明／观察为 0，是核验没有完成，不能解释为所有候选被语义拒绝、无事实可抽取或质量通过。[原始返回与身份检查](artifact-checks-completed.json)保留真实 Schema 失败和未执行项，不将“制品一致”与“模型通过”混用。

制品检查共 379 项通过、31 项跳过、2 项失败；失败为存放核验的 `length` 和原始 JSON 字符串未终止。原始返回 Schema 为 1 通过、1 失败、7 缺测，制品身份／三级账本一致性失败为 0。该检查如实返回非零退出码，不把正确记录失败改写为模型验收通过。

独立助手仅阅读原文、卡片及原始候选，没有阅读 Qwen 核验、最终检查器或参考集。覆盖唯一有候选的包装存放组 2 个主体、4 条声明、2 条观察；其余 7 组明确缺测。见 [Schema 卡片](storage-subject-cards.json)、[候选 Schema](storage-candidate-schema.json)、[完整返回样例](storage-candidate-proposal.json)、[原文](storage-source.json)、[独立审阅](review-storage.json)和[精确候选匹配](source-review-matched.json)。

4 条完整声明均不支持：3 条属性内容（包装、有效期和温度上限）有原文依据，但 `s1` 把“中间体／成品”列的物料名 `HRS-1597` 锚成 `StorageCondition`，主体角色不成立；关系 `hasStorageCondition` 又仅引用“工艺”，且目标角色错误。2 条 unknown 观察的局部缺证判断合理，但 value 为空、仅结构化引用表头，整体未决。六条均属于“模型核验失败、尚未最终处理”，不能称为程序保留错误或已成功拦截。

这些是独立助手的原文复核，不是领域专家金标，也不是全文 precision/recall。原 C 局部银标没有用于评分 D。

## 单位、SHACL 与成本

仅保存了 3 条属性预检查，均因 `semantic_not_checked` 为 incomplete，未执行 SHACL；最终 metric 记录为 0。按冻结卡片 datatype，数值候选分母为 0，数值 SHACL 通过率为 null，实际尺度／偏移换算为 0。**本轮数值／单位校准未评估**，不能把空分母写成通过率 100% 或宣称质量改善。详细口径见 [cost-metric-d.json](cost-metric-d.json)。

D 的 NER 共 5,079 个带标签 span，C 为 852；更多标注不是准确率／召回率提升。完整原文预算没有限制工具 JSON、引用列表和 Schema 的总体体积。八组候选请求的实际体积见 [request-footprint.json](request-footprint.json)：

| 指标 | C：固定窗口 | D：全文记录检索 |
|---|---:|---:|
| 8 份候选请求 JSON 文件字节合计 | 982,599 | 4,355,563 |
| 解码后 messages 内容字符合计 | 684,514 | 3,307,953 |
| 原文单元跨组累加（未去重） | 87 | 629 |
| NER 墙钟秒 | 60.321 | 161.286 |
| 新 Qwen 请求 | 15 | 9 |
| stop／HTTP 失败／length | 14／1／0 | 1／7／1 |
| 完整完成组 | 7/8 | 0/8 |
| 已报告用量的请求 | 14 | 2 |
| 已报告 prompt tokens | 225,457 | 18,324 |
| 已报告 completion tokens | 20,434 | 9,383 |
| 已报告 total tokens | 245,891 | 27,707 |
| 所有 HTTP 累计秒 | 1,326.010 | 229.299 |
| Qwen 阶段墙钟秒 | 1,326.906 | 230.223 |

字符／字节不是 tokenizer tokens。D 的 7 次失败请求用量未知，不能当作零；C/D 没有共同完整完成的组，且输入覆盖不同，不能用较短耗时或较少已报 tokens 宣称成本或效率提升。历史摘要生成成本另计，本次复用没有新增摘要推理；表中墙钟也不包括调查、元数据准备和人工／助手审阅。

## 结论与下一轮边界

现有摘要检索能够接入原文和工具协作链，但当前“需求组累加 + 大量候选工具 JSON”配置未完成端到端验证。证据支持的下一步是：按主体／谓词缩小检索任务并检查目标记录排序；在保持原文完整的前提下控制模型可见候选和引用体积；继续校验材料、产品、工艺步骤与条件记录的主体角色。请求失败原因需要错误正文或服务端证据才能确认，不能由体积增长直接推出。

下一轮应先固定上述变化及请求预算，再用新运行目录验证。只有摘要启用／屏蔽确实造成不同证据输入时，才有条件继续测量其事实质量贡献。本轮没有实施这些后续调优，也没有因失败重写已冻结结果。

## 工程检查与复现

本轮新增路径组合检查 **77 passed，4 条既有 warnings**，定向 Ruff 通过；详见 [engineering-checks.json](engineering-checks.json)。容器 `--prepare-only` 完成 8 组输入构造、Qwen 调用 0。工程测试通过不替代真实模型验收。

本次 Qwen 实际执行命令如下；重跑必须使用新输出目录，不能覆盖本轮：

```bash
docker exec \
  -e PYTHONPATH=/app/data/evaluations/cmc-tool-shacl-deps-20260916:/app \
  -e HF_HUB_OFFLINE=1 -e TRANSFORMERS_OFFLINE=1 \
  -e OMP_NUM_THREADS=4 -e MKL_NUM_THREADS=4 \
  ontology-agent-backend-1 \
  /app/data/evaluations/gliner2-runtime-20260916/venv/bin/python \
  -m app.evaluation.schema_card_summary_tools \
  --baseline /app/data/evaluations/schema-card-qwen-cmc-gliner25-skos-20260916-01 \
  --metadata-dir /app/data/evaluations/schema-card-summary-metadata-20260916 \
  --output /app/data/evaluations/schema-card-summary-retrieval-qwen-20260916-01 \
  --ontology-dir /app/ontology/slpra \
  --model-path /app/models/gliner2.5-multi-v1-20260916 \
  --device cuda:0 \
  --prepared-tools /app/data/evaluations/schema-card-summary-retrieval-ner-20260916-01
```

完整 [Qwen 制品](../../../output/schema-card-qwen-cmc-summary-retrieval-20260916/completed/result.json)、[NER 制品](../../../output/schema-card-qwen-cmc-summary-retrieval-20260916/ner/result.json)与 [scripts](scripts)保留原始请求、响应、Schema、工具输出、检索排序和只读复核工具。Qwen 经项目调度器产生正常用量记录，本轮不是全程只读操作；没有重启服务、修改权威本体或提交业务事实。
