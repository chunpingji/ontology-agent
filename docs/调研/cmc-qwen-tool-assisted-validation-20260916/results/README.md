# 工具协作方案实施与实测

2026-09-16 已实施共享工具核心，并用指定 Word 报告和项目 Qwen 完成 A/B 验证。**工具编排可运行，但本轮未达到全面质量验收；没有证据支持 GLiNER 已稳定提升最终质量。** 共 47 次真实模型请求，46 次成功返回、1 次超时；A 完成 8/8 片段，B 完成 7/8。

本轮是独立评测实现，没有部署到线上识别流程，没有重启服务、恢复原暂停作业或提交业务事实。

## 已实现的范围

- `get_schema_card` / `inspect_evidence` / `check_claim_binding`：冻结菜单、原文坐标、逻辑行与字段/单位物理归属。重复 ℃ 可使用工具提供的 span ID 定位。
- `propose_mentions`：已有 GLiNER 的离线严格调用、160 字窗口和 24 字重叠、标签分组、候选坐标回映及显式失败。旧可选 NER 接口行为保持。
- `validate_metric`：复用已有单位注册表和精确换算；与 pySHACL 0.31.0 组合，分开原值、语义状态、规范化、原生 SHACL 结果及实际 focus 覆盖。
- 服务端最终门复用同一确定性核心。工具内部不调用 Qwen，不接受模型自带 shapes，不修改本体或提交事实。

## 验证协议

沿用报告 `upload-23c872fb-3ab1-41de-a705-dd4b162dfa09`，原件 `HRS-1597原料临床备样生产信息表_--DP-C-PI-S-X2419_2601_04-原 - PDE-冲突.docx`，SHA-256 `e94822808601e73f4ba4c715c664812d4a01e42f0e4e84a6b08a3653b915daf7`。CMCReport 根 IRI 为 `https://ontology.pharma-gmp.cn/slpra/drug-development/CMCReport`，冻结 329 张卡片。完整报告 447 个 IR 单元用于先前调查；本轮仍为 8 scopes / 87 个 scope 单元 / 86 个不同单元，**不是全文 precision/recall 评测**。

强模型为项目实际服务的 `Qwen3.6-35B-A3B`，声明 revision 为 `0b21525e972670ed59e1812e170b27c26355381f0656ecc4e25617ece7dac58b`。输入、本体及代码摘要见[汇总 JSON](summary.json)。声明 revision 与服务模型名核验不等于服务端权重的逐文件验算。

每片段最多三次 Qwen 请求：工具规划 → 固定主体槽位候选 → 冻结候选语义核验。第一阶段只申请类卡片和主体槽位，第二阶段根据工具结果填写锚点；空槽位不能成为关系端点。A 关闭 NER，B 可调用 NER，其余协议和预算相同；按片段交替先跑 A/B。使用既有 `chat_with_schema` 和共享调度；这是受约束 JSON 工具计划，未使用原生 `tool_calls`。卡片、原文和 NER 由计划选择，绑定与指标验证由服务端强制执行。

每请求输出上限 8192 tokens，temperature=0，max_attempts=1、timeout_retries=0、truncation_max_tokens=null，timeout_s=240、total_timeout_s=300。总请求上限 48。B 的 PDE 候选请求超时，第三阶段未执行，未自动重试；它保留在 8 片段分母中，质量状态为未评估。

SHACL 当前只覆盖单条字面量声明的表示契约，包括 predicate、raw、目标 datatype、单位与基数；关系方向/range 由既有卡片和绑定检查负责。本轮没有实现自动 OWL→SHACL 全图投影或业务阈值 profile。

## 工程检查

最终定向 pytest：**272 passed，4 条既有 warnings**；定向 Ruff 通过。覆盖新增工具/编排及受影响的旧 GLiNER、Schema、证据和依赖边界测试；不是全库测试或线上部署验收。

在 `backend/` 实际执行：

```bash
.venv/bin/python -m pytest -p no:cacheprovider -q \
  tests/test_extraction/test_schema_card_tools.py \
  tests/test_extraction/test_tool_metric.py \
  tests/test_extraction/test_tool_shacl.py \
  tests/test_extraction/test_tool_evidence.py \
  tests/test_extraction/test_tool_mentions.py \
  tests/test_extraction/test_gliner_strict.py \
  tests/test_extraction/test_gliner_extractor.py \
  tests/test_extraction/test_ontology_guided_boundaries.py \
  tests/test_extraction/test_schema_card_tightened.py \
  tests/test_extraction/test_schema_card_evidence.py

.venv/bin/ruff check --no-cache \
  app/evaluation/schema_card_tools.py app/evaluation/schema_card_tools_finalize.py \
  app/services/extraction/tool_validation app/services/extraction/gliner_extractor.py \
  tests/test_extraction/test_schema_card_tools.py \
  tests/test_extraction/test_tool_evidence.py tests/test_extraction/test_tool_mentions.py \
  tests/test_extraction/test_tool_metric.py tests/test_extraction/test_tool_shacl.py \
  tests/test_extraction/test_gliner_strict.py
```

新增反例在调用前冻结，原银标清单不变。包括缺单位、跨行、截断单位、同量纲错指标、区间/比较、专业类型、伪条件、空图/错误 target、真实超标值、NER失败和请求预算。语义准确性仍需对原文逐项复核，工程反例通过不替代真实模型评分。

## GLiNER 先验实测

单独预检实际版本 0.2.29（仓库原锁仍为 0.2.27），8/8 scopes 完成，37 个提及候选，总墙钟 55.276 秒。21 个本地文件逐个计算 SHA-256，文件清单身份为 `500ec47fe3f3390386c24255c9b289d2118221ed780ccd9b0aa89ee06123a2a3`；没有下载新权重。

命中包括日期和 24M；也出现“温度”“NOAEL”“PDE”等被误标计量单位，包装仅返回“双层聚乙烯薄膜袋”，简介未补回产品名。NER score 未经概率校准；长值仍依赖完整原文。所有窗口执行完成不代表编码器无截断或实体召回完整，旧模型接口不提供的截断状态保留 unknown。

宿主准备 pySHACL extra；容器仅把锁定纯 Python 依赖置于独立评测目录，通过本轮进程 PYTHONPATH 使用，未更改运行服务的全局依赖。Qwen 调用使用共享调度，会记录正常模型用量。

## A/B 结果与样例

下表“保留”是通过当前程序门的候选数量，**不是正确事实数量**。清单为调用前冻结的局部银标断言，缺少完整金标分母；不把清单通过率写成 precision/recall。

| 片段 | A 保留声明 / 观察 | B 保留声明 / 观察 | 冻结清单 A / B | 返回与卡片 |
|---|---:|---:|---|---|
| 简介 | 6 / 0 | 6 / 2 | 未通过 / 未通过 | [返回](samples/introduction.json) · [卡片](cards/introduction.json) |
| 产品属性 | 9 / 0 | 8 / 0 | 通过 / 通过 | [返回](samples/product_properties.json) · [卡片](cards/product_properties.json) |
| 工艺设备 | 2 / 0 | 2 / 0 | 未通过 / 未通过 | [返回](samples/process_equipment.json) · [卡片](cards/process_equipment.json) |
| 质量条件 | 1 / 1 | 1 / 1 | 通过 / 通过 | [返回](samples/quality_condition.json) · [卡片](cards/quality_condition.json) |
| 包装存放 | 4 / 3 | 4 / 0 | 通过 / 精确表达未匹配 | [返回](samples/storage.json) · [卡片](cards/storage.json) |
| 残留 N/A | 0 / 1 | 0 / 1 | 通过 / 通过 | [返回](samples/residue_missing.json) · [卡片](cards/residue_missing.json) |
| PDE 冲突 | 2 / 0 | 缺测 | 未通过 / 未评估 | [返回](samples/pde_conflict.json) · [卡片](cards/pde_conflict.json) |
| 毒性未知 | 2 / 4 | 2 / 4 | 通过 / 通过 | [返回](samples/toxicity_unknown.json) · [卡片](cards/toxicity_unknown.json) |

A 保留 26 条声明、9 条观察，冻结清单通过 5/8；B 保留 23 条声明、8 条观察，清单通过 4/8，其中 1 片段技术缺测。B 的有效期返回 `有效期24M`，参考为 `24M`，独立原文复核认为这属于表达差异；保留原参考不事后改分，也不把此项解释为语义错误。

最终 A 未决 44 条、拒绝 5 条，B 未决 12 条、拒绝 8 条；这些分类包含声明、观察和被拒主体框架，不能与声明数量直接相加计算召回率。46 次有效返回全部通过对应 JSON Schema，超时的一次不记作合约通过。B PDE 的扩展验收明确为未评估，不因输出为空通过反例检查。

两组均保留完整包装、“不超过25℃”、三项联合 IPC 标准及真实重结晶条件；没有将杂质限度改为检测限，没有把 N/A 生成“无残留”事实，毒性四格 —/× 状态及图例保留，未生成 HighSensitizingDrug 专业类型。

仍有共同遗漏与错误：简介缺少 document→describes；工艺未保留 usesEquipment；PDE 未建立两条独立试验记录。独立复核还确认两组简介均把“中控过程杂质含量未达过控标准”作为 IPC 标准保留，毒性片段均把产品名称当作卡片要求的唯一 `productIdentifier`。简介 `producesFinalProduct` 以及若干唯一身份声明仍缺充分依据。

也存在过度阻断：原文批量区间可支持最小/最大批量属性，但当前通用数量完整性门没有处理端点属性的特殊含义，两组候选均未保留。这与字段语义误判、引用歧义造成的漏保留一起说明，减少输出本身不能证明质量提高。

### 独立原文复核

复核者仅查看原始候选、原文及卡片，未读取第三次 Qwen 结论或评分参考，未新增项目 Qwen 请求；这是助手复核银标，**不是领域专家金标**。四份逐项结果为[简介/属性](review.introduction-product.json)、[工艺/质量](review.process-quality.json)、[存放/残留](review.storage-residue.json)、[PDE/毒性](review.pde-toxicity.json)。[最终归宿匹配](review.matched.json)依据主体、字段和完整候选内容匹配，不仅依赖候选 ID。

49 条被程序保留的声明中，复核认为 37 条有依据、4 条不支持、8 条未决；其中 A 为 19/2/5，B 为 18/2/3。这表明第三次同模型核验仍会重复生成阶段的错误，SHACL 通过也不能证明专业语义。175 条复核记录全部匹配；2 条重复观察按原始位置消歧并显式标记，B PDE 单独保持未评估。

另有 17 条保留观察：复核认为 14 条有依据、2 条不支持、1 条未决。不支持项为 A 已映射有效期仍标 `unmapped`，以及 B 对 N/A 的状态与“无可用数据”解释不一致；这些状态错误不等于原始文字不存在。

N/A 虽符合冻结参考的 `not_applicable`，但原文无进一步图例时仍可能意为无可用数据；此解释不确定性在逐项复核中保留。

## 检查器修正与原始结果保留

初轮 36 次请求中，多片段因 span ID 解释不一致在候选后停止。修正后只补尚未使用的第三次语义请求 11 次，累计 47 次；已有语义响应必须与完整冻结目标相等才复用。没有重生成计划或候选，也没有重试超时请求。

1. **引用定位**：采用 `unique-nested-context-v2`。quote 与工具 span 相等时用精确坐标；否则要求 quote 在整个同源单元逐字唯一，并与 span 区间完整包含。跨 ref、部分相交、重复歧义或坏坐标均拒绝，raw/quote 不改写，坏引用隔离到候选。
2. **跨行主体**：共享 API 单元跨两条试验行，不能仅凭行交集拥有其中任一行的值。新增 `owner_record_ambiguous`，同物理 cell/同跨度和明确单行锚点仍允许。相同 A PDE 返回从 18 条保留降为 2 条，22 条试验属性保持未决；两条公共关联/成分声明的记录身份在独立复核中仍未决。
3. **数量完整性**：补齐含单位 raw 的边界，防止“不超过25℃→25℃”“50~55℃→55℃”“25℃→5℃”绕过比较符、区间或完整数字检查；完整字符串表达仍可保留。
4. **结果分类**：明确不支持的观察归拒绝，证据不足归未决；重算时写入新的绑定检查制品，保留原超时原因和成功阶段计数，失败片段不能获得空输出的扩展验收通过。

第 3、4 项在模型调用全部结束后执行本地重算，不再访问模型。原始实验、补充核验和最终检查结果分别保留，检查器变化不计作模型生成能力改善；原值、候选及语义返回均不改写。

最后一次离线重算耗时 0.370 秒，保留声明/观察数量不变；B 的 3 条明确不支持观察从未决改列拒绝。各阶段 HTTP 账本逐项保持相等，总请求仍为 47。

## 单位与 SHACL 的实际验证边界

最终对 72 条字面量候选执行指标门：38 条运行 SHACL，实际 focus 覆盖完整且 conforms=true；34 条因归属、单位、语义或表示前提不足未运行，保留原因。**本轮实际执行 SHACL 的数值数量候选为 0**，18 个数值候选均在前置门阻断；不能声称真实报告上的数值换算效果已获验证。数值精确换算、缺单位、同量纲错指标、比较区间和空图边界由工程反例验证。

SHACL profile 固定为 `metric-representation-v1`，本轮结果证明表示校验和覆盖门可接入，未证明完整业务图约束或 PDE 正确性。真实超限值与业务是否合格是两种结论，工具不会为符合限度而改值。后续质量改进优先处理试验主体拆分和字段语义，不能靠补单位或放宽 shape 提高通过率。

## 调用成本

| 指标 | A | B |
|---|---:|---:|
| HTTP 请求 | 24 | 23 |
| stop 返回 / 超时 | 24 / 0 | 22 / 1 |
| 已报告 prompt tokens | 203,097 | 173,862 |
| 已报告 completion tokens | 34,654 | 22,192 |
| HTTP 累计秒 | 1,387.817 | 1,298.210 |

46 次响应提供用量，B PDE 超时的 token 用量未返回，不能按零成本计算。初轮模型阶段墙钟 2,148.196 秒，补充第三阶段 566.116 秒，共约 45.24 分钟；两阶段之间调查/修改耗时和先验 NER 预检不在该和中。

真实 B 组 8 次 NER 工具执行共返回 46 个 spans，推理合计 25.146 秒（与独立预检的标签不同，不能混用 37 个 spans），包括之后 Qwen 超时的 PDE 片段。独立先验预检 55.276 秒、本轮 runner 的重复预检 53.420 秒、B 组工具执行成本分别列于[汇总 JSON](summary.json)，不加入 Qwen token 成本。

## 制品与复现

- [冻结初轮](../../../../output/schema-card-qwen-cmc-tools-20260916/initial/result.json)：原始 36 次请求、原解析结果。
- [补充语义核验](../../../../output/schema-card-qwen-cmc-tools-20260916/completed/result.json)：复用原候选，补 11 次，累计 47 次。
- [最终离线检查](../../../../output/schema-card-qwen-cmc-tools-20260916/checked/result.json)：零新增模型调用，包含每候选 metric/SHACL、引用、拒绝与未决原因。
- [评测入口](../../../../backend/app/evaluation/schema_card_tools.py)、[补核入口](../../../../backend/app/evaluation/schema_card_tools_finalize.py)、[工具核心](../../../../backend/app/services/extraction/tool_validation/)、[运行说明](../../../../specs/026-cmc-tool-validation/quickstart.md)。

每组片段目录保留计划、候选、核验的请求/返回/Schema，完整原文、卡片、工具结果、冻结候选与最终检查；上表样例便于直接审阅，未展示的空字段也保留在原始返回中。模型和 GLiNER 权重均沿用本地服务，不下载新权重。
