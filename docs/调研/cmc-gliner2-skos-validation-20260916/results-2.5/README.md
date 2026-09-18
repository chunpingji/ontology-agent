# GLiNER2.5 + SKOS + Qwen 真实验证

已用指定 CMCReport 和项目 Qwen 完成 GLiNER2.5 工具协作实验。**接入可运行，质量未通过，不建议据此切换线上默认识别器。** NER 8/8 完成；Qwen 共 15 次新请求，14 次返回通过 JSON Schema，PDE 候选请求 HTTP 400，7/8 片段完成。失败请求未重试。

程序保留 21 条声明、6 条观察。独立助手按包含主体绑定的严格口径复核，21 条声明中 **3 条支持、9 条不支持、9 条未决**；若只看字段／关系文字内容，则 17 条支持、2 条不支持、2 条未决。差异主要来自药名被当作计划／存放条件主体，以及产品与 API 层级缺证。**数值 SHACL 覆盖 0/3，实际尺度换算 0 次**，本轮没有证明真实数值校准或最终质量得到改善。详细身份与统计见 [summary.json](summary.json)。

## 输入与实验边界

- 报告：`upload-23c872fb-3ab1-41de-a705-dd4b162dfa09`；Word SHA-256：`e94822808601e73f4ba4c715c664812d4a01e42f0e4e84a6b08a3653b915daf7`。
- 根类：`https://ontology.pharma-gmp.cn/slpra/drug-development/CMCReport`。冻结完整 447 单元 IR、329 张卡片，实际识别仍限 8 scopes／87 个 scope 单元／86 个不同原文单元。这不是全文 precision/recall 验收。
- 强模型：项目实际服务 `Qwen3.6-35B-A3B`；声明 revision 为 `0b21525e972670ed59e1812e170b27c26355381f0656ecc4e25617ece7dac58b`。服务名和声明身份核验不等同于服务端权重逐文件验算。
- NER：`fastino/gliner2.5-multi-v1`，revision `aaecfe45db1d828c963717054ccb868e8ad1f1d5`；权重 SHA-256 为 `c1ff4ec0bc00031c15530b8f3c33d3677f27949e6a0cb52e1247a6224b6c5395`。6 个下载文件共 1,165,523,156 bytes，完整清单见 [ner-summary.json](ner-summary.json)。
- C 组复用旧 B 组的计划与主体槽位，仅新执行候选和冻结语义核验，每片段最多 2 次，共最多 16 次 Qwen。temperature=0，输出上限 8192，单请求 timeout=240s、总 timeout=300s，无重试或截断后扩容。
- 这仍是受 JSON Schema 约束的工具编排，工具由控制器执行，不是 Qwen 原生 `tool_calls`。未部署、未重启服务、未恢复暂停作业、未提交业务事实。

与旧 B 同时变化的因素包括 NER 模型、SKOS 词汇输入和工具提示组织，不能单独归因于 2.5 或 SKOS。旧 B 的 PDE 计划仅分配一个 `SharedLineAssessmentData` 槽位，即使请求成功，也不足以验证两条独立试验记录的完整拆分。

## 2.5 接入与 NER 实测

Python 包仍为 `gliner2==2.0.0`；隔离环境固定 Transformers 4.57.6、huggingface_hub 0.36.2、tokenizers 0.22.2、protobuf 6.33.5，复用 PyTorch 2.7.1+cu126。补入 protobuf 后，官方 tokenizer 兼容加载分支可运行；未修改原始 tokenizer/config 或线上依赖。

适配器使用 `AutoExtractor` 并检查 boundary 架构，保留原文、不自动补句点，构建官方 boundary metadata；解码前核验有效候选分数、坐标和批次，返回后按原文回放。模型无固定 `max_width`，但仍受 160 字窗口、24 字重叠、每批一个标签和共享候选池限制：最多 32 起点、32 终点、192 候选。本实验保守 encoder 输入门为 512，实际最大 406；不把它解释为模型架构绝对上限。

| 片段 | 带标签提及数 |
|---|---:|
| 简介 | 67 |
| 产品属性 | 139 |
| 工艺设备 | 78 |
| 质量条件 | 22 |
| 包装存放 | 24 |
| 残留 N/A | 0 |
| PDE 冲突 | 403 |
| 毒性未知 | 119 |
| 合计 | 852 |

2694 个文本×标签窗口全部执行完成，NER 墙钟 60.321 秒，加载合计 30.710552 秒、推理合计 27.020862 秒。Qwen 阶段复用这些制品，没有再次推理 NER。

852 条带标签提及只对应 **112 个片段内物理跨度、跨片段 111 个唯一跨度**。80 个物理跨度被赋多个标签，66 个跨角色；有 82 条表头提及、70 条标题提及。坐标全部正确不代表语义正确：实际出现 `mg→微克`、`5→11种单位`、`DCM/H2O→API`、温度／湿度表头被当作属性值、毒性图例被当作产品等错配。38 条人工示例保存在 [ner-review.json](ner-review.json)，这不是全量精确率评分。

超过旧 8 个 char 分词单元限制的仅 2 条提及，均来自同一“**大鼠14天亚急毒试验**”（9 tokens），其中一条还被错标动物种属。因此本轮证明 boundary 模型能够返回超过旧宽度的跨度，但没有证据证明长属性质量已改善。合成长工艺条件冒烟没有命中，也未据此调参。

当前所选类／属性在权威本体中的直接 `skos:altLabel` 命中为 0，实际使用明确标为 `ManualGeneralKnowledge` 的[实验 overlay](../../../../backend/app/resources/cmc_extraction_vocabulary.ttl)：15 类、75 属性、18 单位词条。词表不包含报告具体值或银标，未修改权威本体。词表与检查器从 NER 冻结到 Qwen 完成保持不变。

## Qwen 返回与 Schema 卡片

| 片段 | 状态 | 原始声明／观察 | 程序保留声明／观察 | 保留声明复核：支持／不支持／未决 | 样例与卡片 |
|---|---|---:|---:|---:|---|
| 简介 | 完成 | 7 / 2 | 5 / 0 | 0 / 4 / 1 | [返回](samples/introduction.json) · [卡片](cards/introduction.json) |
| 产品属性 | 完成 | 7 / 2 | 7 / 0 | 0 / 0 / 7 | [返回](samples/product_properties.json) · [卡片](cards/product_properties.json) |
| 工艺设备 | 完成 | 7 / 3 | 2 / 0 | 2 / 0 / 0 | [返回](samples/process_equipment.json) · [卡片](cards/process_equipment.json) |
| 质量条件 | 完成 | 2 / 5 | 1 / 0 | 1 / 0 / 0 | [返回](samples/quality_condition.json) · [卡片](cards/quality_condition.json) |
| 包装存放 | 完成 | 4 / 2 | 4 / 2 | 0 / 4 / 0 | [返回](samples/storage.json) · [卡片](cards/storage.json) |
| 残留 N/A | 完成 | 0 / 2 | 0 / 0 | — | [返回](samples/residue_missing.json) · [卡片](cards/residue_missing.json) |
| PDE 冲突 | HTTP 400 | 缺测 | 缺测 | 未评估 | [失败记录](samples/pde_conflict.json) · [卡片](cards/pde_conflict.json) |
| 毒性未知 | 完成 | 2 / 4 | 2 / 4 | 0 / 1 / 1 | [返回](samples/toxicity_unknown.json) · [卡片](cards/toxicity_unknown.json) |

共 29 条原始声明、20 条观察。保留 21／6，未决 12，拒绝 11；拒绝中包括 1 个主体框架，因此不能把这些总数直接与 49 条声明／观察相加。冻结局部清单仅产品属性和毒性通过，即 2/8；PDE 保持未评估。清单不是完整金标，也不是 precision/recall。

14 份原始 HTTP 响应使用标准 `jsonschema 4.23.0` 重新校验，全部符合当次 Schema 且与保存的 proposal 一致；不是仅校验已经解析的结果。请求文件、阶段账本、片段账本和根账本均为 15 次，冻结输入／代码／工具摘要一致。独立[制品检查](artifact-checks.json)为 497 项通过、5 项缺测、0 项一致性失败；整体标为 incomplete，因为 PDE 原始返回及核验没有发生。[反例](artifact-negative-checks.json)确认能检出额外响应字段、候选漏项／重复、账本重复和源文件变化。

PDE 仅保留 `BadRequestError`、HTTP 400、耗时 0.720 秒；记录器按既有策略不保存任意服务错误正文。因此本轮不能确认失败是上下文长度、Schema 限制或其他原因，更不能把缺测视为空结果正确。未发起诊断性模型重试。

## 独立复核与具体问题

独立助手仅依据原文、Schema 卡片和原始候选复核，不读取 Qwen 语义核验输出或评分参考；这是助手复核，**不是领域专家金标**。复核者已知此前问题，不能称为完全盲法实验。

每条声明分别记录 `content_verdict` 和 `overall_verdict`：字段值或关系文字有依据，但主体锚点指向错误角色时，整体声明仍不成立。明确错角色／矛盾记 unsupported，只有证据不足记 undetermined。最终保留质量按整体判断；字段内容判断另列，避免把二者混为一谈。

声明匹配校验 scope、原始 ID/path/主体/字段/种类、完整原始 proposal 及冻结候选内容；只排除引用坐标、span_id 和 replay_issue 元数据。保留观察按完整 proposal 匹配，重复时按原位置消歧。技术缺测保持未评估，空候选不算质量通过。

三份逐项复核为[简介／产品](review.introduction-product.json)、[工艺／质量／存放／残留](review.process-quality-storage-residue.json)、[毒性与 PDE 缺测说明](review.pde-toxicity.json)。[最终匹配](review.matched.json)覆盖全部 49 条声明／观察及 26 个主体槽位的独立审阅；PDE 没有候选可审阅。

主要发现如下：

1. **主体错角色未拦住。** 简介把 u1 开头的新分子名称 `HRS-1597` 锚定为 `ClinicalSampleProductionPlan`；生产日期和用途文字正确，但当前主体锚点错误。存放片段同样把“中间体／成品”列的物料名称锚定为 `StorageCondition`。Qwen 核验仍支持这些类型和归属，机械／SHACL 门也未识别专业角色错误。
2. **名称仍被当作唯一标识。** 简介和毒性都把 `HRS-1597` 写入 `productIdentifier`，卡片明确不允许以名称代替具体实例身份；毒性原表头还明确为“产品名称”。这些声明被程序保留。
3. **关系校准有拦截，也有漏判。** 工艺原始候选把“加入 HRS-1597 粗品”写成 `producesIntermediate`，最终拒绝；但简介的“结晶纯化步骤”不足以证明最终 API 产出，`producesFinalProduct` 仍被保留。`usesEquipment` 有候选却因引用歧义未保留。
4. **内容可读不等于实体层级确定。** 产品属性保留外观、溶解性和口服片剂文字；局部原文未充分区分具体 `DrugProduct` 和 API 的主体归属，整体保持未决。复核没有把这些文字本身判错。
5. **完整值与观察状态分开检查。** 完整包装、含“不超过25℃”的存放描述及三项联合 IPC 标准保留。有效期返回“有效期24M”，与冻结参考“24M”存在表达差异，不为通过而改参考。另有同一有效期已映射到 `shelfLife` 却又标为 `unmapped` 的观察，状态不支持。
6. **缺失和条件仍有漏保留。** 质量的重做条件虽进入原始观察，但 value 引用为空，最终被 `source_quote_missing` 阻断；没有进入正式保留观察。N/A 未生成“无残留”事实，但两个观察都未保留；没有释义时 `not_applicable`／`not_available` 的选择仍未决。毒性四格及图例均正确保留为三项 unknown、一项报告中的 negated，没有生成高致敏专业类型。

6 条保留观察的整体复核为 4 支持、1 不支持、1 未决。检查器也有过度阻断：批量 `3.8–6.6 kg` 的上下限内容有依据，但通用标量完整性门尚不能表达端点字段语义；不能靠减少输出宣称提高质量。

本轮明确把主体锚点角色计入整体声明，旧 B 报告的历史人工复核未以同样方式逐项分离该维度。因此不把旧 B 的 18/23“支持”与本轮 3/21 直接计算质量升降；需要用同一口径重新审阅双方，才可作该比较。

## 单位与 SHACL 实际覆盖

对 21 条唯一属性候选保存 21 次预检、21 次最终指标门结果，共 42 条调用记录；两阶段不重复计为 42 个候选。预检没有执行 SHACL，最终 16 次执行均有非空、完整且匹配候选 ID 的实际 focus，conforms=true；这些都是文本／日期表示。

按卡片 datatype 识别的 3 个数值候选——批量最大值 6.6、最小值 3.8、步骤顺序 1——全部在前置门阻断，数值 SHACL **0/3**、数值规范化 0、真实尺度／偏移换算 0。两条批量遇到 `scalar_value_required`、`unit_source_missing`；这不表示原文没有 kg，而是当前绑定过程未形成可供后续使用的单位。顺序把原文“一”输出为 raw `1`，且步骤层级未决，遇到 `raw_value_not_in_quote` 和 `semantic_undetermined`。

因此，**单位解析、量纲／尺度检查和 SHACL 适合继续放在 deterministic engine 并由工具接入 Qwen，但本轮没有验证真实数值校准的成功路径**。16 次 SHACL 通过只能证明 `metric-representation-v1` 的字面量表示约束被执行，不能证明主体角色、PDE 科学语义或完整业务图正确。SHACL 不负责替 Qwen 补造单位或消解试验归属。

详细分母、focus 和阻断原因见 [C 指标／成本](cost-metric-c.json)及[统计口径](metric-shacl-counting-notes.md)。旧 B 同样为数值 SHACL 0/3、换算 0，没有真实数值改善证据。

## 与旧 B 的可比成本

下表均排除已经复用的计划请求，只比较候选＋语义核验；两组均有 14 次报告用量，失败请求用量未知，不能计作零。

| 指标 | 旧 B | 新 C：2.5 + SKOS |
|---|---:|---:|
| 新 HTTP 请求 | 15 | 15 |
| stop／失败 | 14／1 超时 | 14／1 HTTP 400 |
| 已报告 prompt tokens | 151,064 | 225,457 |
| 已报告 completion tokens | 20,482 | 20,434 |
| 已报告总 tokens | 171,546 | 245,891 |
| 全部 HTTP 累计秒 | 1,180.328 | 1,326.010 |
| 双方完成的相同 7 片段 HTTP 累计秒 | 940.330 | 1,325.290 |
| 程序保留声明／观察 | 23／8 | 21／6 |
| 冻结局部清单通过 | 4/8 | 2/8 |
| 数值 SHACL／真实换算 | 0/3／0 | 0/3／0 |

已报告输入 tokens 增加 49.246%，总 tokens 增加 43.338%；相同完成片段的 HTTP 累计耗时增加 40.939%。全请求耗时包含旧 B 的 239.998 秒超时和新 C 的 0.720 秒失败，不能据其差额代表成功推理速度。新 C Qwen 阶段墙钟 1,326.906 秒，另有此前 NER 墙钟 60.321 秒；不计准备、调查和复核耗时。[旧 B 同阶段基线](baseline-b-cost-metric.json)保留精确统计。

本轮组合方案提高了提及数量和输入成本，没有提供最终质量收益证据。后续应先验证主体／记录锚点、名称与唯一标识区分、观察状态及区间端点语义，再另设实验评估标签数量与提示体积；这些是本次发现后的建议，没有在冻结结果上调参重算。

## 工程验证与复现

本次 13 个定向测试文件 **403 passed，4 条既有 warnings**；定向 Ruff 通过。覆盖 GLiNER2.5 适配器、词表、编排、证据、指标、SHACL 和依赖边界，不是全库测试或线上验收。pytest 命令与检查范围见 [engineering-checks.json](engineering-checks.json)。

运行使用独立 venv 和两项 OFFLINE 开关；权重下载只发生在准备阶段。Qwen 请求通过项目共享调度，会正常记录模型用量，不能称整轮实验为只读。

```bash
docker exec \
  -e PYTHONPATH=/app/data/evaluations/cmc-tool-shacl-deps-20260916:/app \
  -e HF_HUB_OFFLINE=1 -e TRANSFORMERS_OFFLINE=1 \
  -e OMP_NUM_THREADS=4 -e MKL_NUM_THREADS=4 \
  ontology-agent-backend-1 \
  /app/data/evaluations/gliner2-runtime-20260916/venv/bin/python \
  -m app.evaluation.schema_card_gliner2 \
  --baseline /app/data/evaluations/schema-card-qwen-cmc-tools-checked-20260916-01 \
  --output /app/data/evaluations/schema-card-qwen-cmc-gliner25-skos-20260916-01 \
  --ontology-dir /app/ontology/slpra \
  --model-path /app/models/gliner2.5-multi-v1-20260916 \
  --device cuda:0 \
  --prepared-tools /app/data/evaluations/schema-card-cmc-gliner25-skos-ner-20260916-01
```

以上为本轮已执行命令，不能用同一输出路径重跑覆盖。新实验须另设运行目录；本轮没有重试失败片段。

完整[Qwen 制品](../../../../output/schema-card-qwen-cmc-gliner25-skos-20260916/completed/result.json)包含所有请求、原始响应、JSON Schema、冻结候选、工具输出和检查器快照；[NER 制品](../../../../output/schema-card-qwen-cmc-gliner25-skos-20260916/ner/result.json)与[冒烟](../../../../output/schema-card-qwen-cmc-gliner25-skos-20260916/preflight/smoke.json)单独保留。只读复核工具保存在 [scripts](scripts)，不调用模型：`check-artifacts.py` 核验原始制品，`summarize-metric-baseline.py` 统计成本／SHACL，`make-review-packs.py` 和 `match-reviews.py` 分离审阅输入及最终归宿匹配。
