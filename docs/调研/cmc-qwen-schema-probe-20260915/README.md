# CMCReport：Qwen 结构化 JSON 与 Schema 卡片前期验证

本次验证依据[分享方案](https://chatgpt.com/s/t_6aa9d5f78d1881919f576d20771fe071)，把其中的强模型角色替换为项目已部署的 `Qwen3.6-35B-A3B`，使用用户指定的 Word 文档。实验仅验证结构化协议、本体卡片和一次局部扩展的可行性，不实施新的线上识别管线。

**结论：指定结构化 JSON 返回和从现有本体生成 Schema 卡片可行；本次最小抽取方案尚未通过事实正确性验收。** 主验证及补卡片实验的 19 次调用全部通过 JSON 结构契约，但 11 次抽取响应仅 2 次通过卡片/引用检查；还观察到 PDE 跨行错绑、N/A 被解释为否定和未知毒性被解释为肯定。不能把“返回了合法 JSON”视为关系图谱识别正确。

实验于 2026-09-15 UTC 开始，2026-09-16 UTC 完成。汇总：[summary.json](summary.json)。

## 1. 输入与范围

| 项目 | 本次实际输入 |
|---|---|
| 文档标识 | `upload-23c872fb-3ab1-41de-a705-dd4b162dfa09` |
| 登记类型 | `https://ontology.pharma-gmp.cn/slpra/drug-development/CMCReport` |
| 原件 | `HRS-1597原料临床备样生产信息表_--DP-C-PI-S-X2419_2601_04-原 - PDE-冲突.docx` |
| 原件 SHA-256 | `e94822808601e73f4ba4c715c664812d4a01e42f0e4e84a6b08a3653b915daf7` |
| 模型 | `/v1/models` 实测返回 `Qwen3.6-35B-A3B`，`owned_by=llamacpp` |
| 配置声明的权重 revision | `0b21525e972670ed59e1812e170b27c26355381f0656ecc4e25617ece7dac58b`；本次未重新读取整份 GGUF 验算 |
| 本体 | 从当前项目 TTL 的私有物化库生成 329 张卡片；不修改权威 TTL |
| 解析 | 调用项目 `analyze_word_core()` 重新解析原件，共 447 个证据单元 |
| 取样 | 8 个片段，86 个不同证据单元；不是全文识别或完整召回评测 |

读取登记信息时数据库事务设为只读。该文档原有抽取作业处于 `paused`；本次不继续该作业、不提交事实、不启动或重启后端。真实模型调用使用既有共享调度，会新增模型请求和用量记录，因此整轮实验不是只读操作。

| 片段 | 原文范围 | 验证重点 |
|---|---|---|
| `introduction` | 简介 | CMCReport → DrugProduct；备样计划与工艺步骤不能混用 |
| `product_properties` | 产品的结构，选取 6 个原文单元 | 性状、剂型和否定标志 |
| `process_equipment` | HRS-1597 结晶纯化，步骤一 | 合成步骤、设备、物料的角色与关系方向 |
| `quality_condition` | HPLC 送检及不合格后重结晶 | 本体缺口、阈值和条件 |
| `storage` | 存放条件表格 | 表头/行归属、包装、温度和有效期 |
| `residue_missing` | 设备清洗残留物基本信息：N/A | 缺失不能变成肯定事实或全文否定 |
| `pde_conflict` | 产品 NOAEL、F 值表，两条试验记录及注释 | 100 与 7.5 不可丢失试验上下文或擅自裁决 |
| `toxicity_unknown` | 产品毒性信息及符号图例 | “×”与“—”不同，未知不能变为无毒性 |

## 2. 本次固定 JSON 协议

两个协议均在调用前指定，所有对象禁止额外字段；使用项目原有 `response_format.type=json_schema`、`strict=true`，随后用 Pydantic 严格验证。设置 `temperature=0`、输出上限 3,500 tokens、`max_attempts=1`、`timeout_retries=0`、关闭 thinking。每次 HTTP 请求限时 180 秒，总预算含排队为 240 秒。

- [retrieval-request.schema.json](retrieval-request.schema.json)：`scope_id`、`content`、`semantic_query`、`candidate_concepts`、`active_frames`、`open_slots`、`retrieval`。
- [extraction.schema.json](extraction.schema.json)：`scope_id`、`mentions`、`claims`、`open_slots`、`schema_gaps`。实际请求进一步把类 IRI 和谓词 IRI 限定为本次卡片的枚举集合。

这是分享方案的最小实验协议：`candidate_concepts` 暂用字符串提示，`active_frames` 固定为空；未实现分享示例的完整对象化 frame 状态。属性 `literal_value` 的传输类型统一为字符串。模型会将原文“否”输出成字符串 `"false"`，本次未完成各字段的数据类型/单位归一化验证，不能直接作为已校验的 RDF 数值或布尔值提交。

`document` 是用户指定的已知 CMCReport 根，不记为模型识别成功。所有输出 mention/claim 都必须携带真实 `evidence_id` 和该单元内的逐字引文。表格原始行列、表头和源单元随输入提供。模型不读取评分参考；局部 silver 断言只在模型调用完成后评分，不计算正式 precision/recall。

现有 `chat_with_schema()` 本身主要负责 JSON 解析和调用控制，不能替代应用侧 Schema 校验。本次明确关闭其提示词回退，避免把一次原生 Schema 失败后的修补返回计成原生成功。

## 3. Schema 卡片来源与检索边界

卡片直接复用 `ontology_snapshot_from_engine()` 和 `compile_local_menu()`，保留当前类型、父类、定义、直接或继承属性、关系范围、声明类、基数和约束状态。示例：

- [CMCReport.schema-card.json](CMCReport.schema-card.json)：0 个属性，12 条关系。
- [DrugProduct.schema-card.json](DrugProduct.schema-card.json)：产品字段及合法关系。
- [SharedLineAssessmentData.schema-card.json](SharedLineAssessmentData.schema-card.json)：NOAEL、F 因子、PDE、试验类型及来源字段。

卡片由确定性代码生成，Qwen 只消费卡片，不能生成权威卡片或发明正式 IRI。分享中的 `AssayTest`、`AcceptanceCriterion`、`hasAcceptanceCriterion` 在当前 TTL 中不存在；本实验不会把示例名称伪装为项目已有本体。

初始检索是有意限定的词法基线：原文和模型语义提示共同评分，`top_k=3`，再加入已知 CMCReport 卡片。候选关系的范围类可作为输出端点，但其自身属性卡片需要进一步检索。未接入向量模型、完整 AOR、跨段实体状态或历史回放；`include_siblings` 未实现，默认关闭。`max_depth=1` 仅代表一跳范围类型，不代表递归取回所有端点菜单。

后续补测只针对简介和产品属性两个已经完成的片段：从首次输出的合法 mention 类型中，确定性取回缺少的完整卡片，再请求 Qwen 抽取。扩展输入来自首次模型输出，不来自 silver 参考。

## 4. 卡片体积验证

最初直接使用完整 `LocalMenu` 元数据，单次简介抽取请求为 111,168 字节。去掉每条关系中重复的 `range_classes` 描述及空字段后，卡片保留全部正式 `range_class_iris` 和约束。

[card-fidelity.json](card-fidelity.json) 对 329 张卡片逐一比较：除上述预定删除的展示元数据外，差异为 0。整个卡片文件从 2,459,663 降至 1,030,294 字节。

| 同一简介原文 | 完整菜单卡片 | 紧凑卡片 |
|---|---:|---:|
| 抽取请求字节 | 111,168 | 55,668 |
| 实测 prompt tokens | 24,425 | 10,794 |
| 实测 completion tokens | 2,469 | 2,163 |
| 实测 HTTP 秒数 | 148.454 | 77.620 |

这是一轮实测，输出长度和缓存命中不同，服务也共享负载；不能据此声称统计显著的速度提升。首轮简介已完成，进入后续片段时中断了该试跑；原始响应及中断记录保留，未把它算作完整 8 片段评测。

## 5. 结果与判断

### 5.1 实际结果

| 指标 | 结果 |
|---|---:|
| 语义检索请求 JSON 契约 | 8/8 通过 |
| 抽取响应 JSON 契约 | 11/11 通过 |
| 原文内容原样返回及检索 anchor 落在原文 | 8/8 通过 |
| 抽取卡片/引用检查 | 2/11 通过 |
| 主验证调用 | 17 次：8 次语义请求、8 次首次抽取、1 次质量条件槽位扩展 |
| 完整主体卡片补测 | 2 次：简介、产品属性各一次 |
| 19 次调用输入/输出 tokens | 124,419 / 18,974 |
| HTTP 调用耗时之和 | 1,083.757 秒；存在并发，不是墙钟总耗时 |
| 全文 precision/recall、专家金标验收 | 未执行 |

19 次调用均 `finish_reason=stop`，没有 Schema 回退、自动重试或截断补救。主验证从 `23:50:29` 到次日 `00:03:31`，约 13 分钟；补卡片实验与其部分并发。模型为共享服务，不能把这些数值当成独占部署性能。

另有大卡片试跑的 3 次完整调用，以及 1 次主动中断后调度记录过期的调用，未纳入上述 19 次核心计数。最终只读核验确认原抽取作业仍为 `paused`。

| 阶段/片段 | JSON 结构 | 卡片/引用检查 | 复核结果 |
|---|---|---|---|
| 首次：简介 | 通过 | 未通过 | 计划使用了步骤的 `outputMassRange_kg`；文档根引用跨越两个证据单元 |
| 首次：产品属性 | 通过 | 未通过 | 字段值基本正确，但模型使用 `DrugProduct` 主体时未取回该主体完整卡片 |
| 首次：工艺设备 | 通过 | 通过 | 获得步骤 → 设备、设备编号等候选；不据此宣称整篇质量达标 |
| 首次：质量条件 | 通过 | 未通过 | 杂质限度误填到 HPLC 检测限/定量限，且有非法主体引用 |
| 槽位扩展：质量条件 | 通过 | 未通过 | 引用和关系 range 仍出错，扩展未解决语义错配 |
| 首次：存放条件 | 通过 | 未通过 | 温度和包装可读出，但关系引用了未定义的文档主体 ID |
| 首次：残留物缺失 | 通过 | 未通过 | 把 `N/A` 建成 Residue，并生成否定的残留关系 |
| 首次：PDE 冲突 | 通过 | 未通过 | 条件不是逐字引文；人工式原文复核另发现犬试验值错绑到大鼠主体 |
| 首次：毒性未知 | 通过 | 未通过 | 将“—”生成肯定的毒性声明，且谓词不适用于产品主体 |
| 补主体卡片：简介 | 通过 | 未通过 | 日期、批量字段改善，但 `describes`、`hasProductionPlan` 方向仍反转 |
| 补主体卡片：产品属性 | 通过 | 通过 | 6 条属性候选，预先设置的 3 项局部断言全部通过 |

主结果：[main-result.json](main-result.json)；补卡片结果：[expansion-result.json](expansion-result.json)。结果中的 `status=complete` 仅表示调用执行完毕，应同时检查 `checker_errors` 和语义复核，不能视为质量通过。

### 5.2 可核对的正例与反例

**完整主体卡片有效，但效果是局部的。** 初次产品属性输出已有正确性状、剂型等值，卡片中却只有其他类型和关系范围里的 `DrugProduct` 名称。确定性取回 `DrugProduct` 完整卡片后，再次返回的 6 条属性均通过本次结构与引用检查。原始输出：[product-properties.qwen.json](product-properties.qwen.json)。其中布尔值仍是字符串 `"false"`，本实验没有完成类型归一化。

**百分比限度不能因本体存在相近字段而强行映射。** 原文“单个杂质应不得过0.2%总杂应不得过2.0%”被填成 `detectionLimit_ug="0.2%"`、`quantitationLimit_ug="2.0%"`。JSON、谓词词汇甚至单一属性的 domain 检查都不能证明这种映射正确；指标含义和单位均不符。原始输出：[quality_condition.qwen.json](quality_condition.qwen.json)。

**值出现于原文不代表主体归属正确。** PDE 表中大鼠行是 100，犬行是 7.5。模型声明 `mention_pde_calc_2` 的原文提及为“大鼠14天亚急毒试验（试验1）”，却将 `calculatedPDE_mg_per_day="7.5"` 绑定到该主体。证据值 7.5 确实来自原文，但来自另一行。原始输出：[pde_conflict.qwen.json](pde_conflict.qwen.json)。

**缺失、否定和未知仍会混淆。** `N/A` 被建成 Residue 实体并生成 `polarity=negated`；毒性表中图例明确“—”表示研究数据不充分，模型仍针对生殖发育毒性、致癌性和致敏性生成 `polarity=affirmed` 的声明。原始输出：[residue_missing.qwen.json](residue_missing.qwen.json)、[toxicity_unknown.qwen.json](toxicity_unknown.qwen.json)。

**现有小型 silver 断言不能替代上述语义复核。** 例如质量条件用例只检查缺口保留和禁止发明某个谓词，其局部断言通过，却没有覆盖检测限错配；PDE 断言固定目标字段，不能独立评价所有替代建模。断言结果完整保留，没有事后修改参考来制造通过率。本次不将它们折算为抽取准确率。

### 5.3 前期方案决策

可保留：Qwen 作为强模型、原生 JSON Schema 返回、从权威 TTL 确定性生成卡片，以及按已识别合法类型补取完整主体卡片。

继续验证前只需收紧三个与本轮失败直接相关的点：

1. **按当前主体限定返回协议。** 已知文档根只允许固定 `document` ID；一轮只允许该主体卡片里的谓词和匹配的属性/关系值结构，避免把多个类型的谓词合成一个扁平大枚举。
2. **区分未取回卡片与本体没有定义。** 关系 range 给出类型后，先取回该类型的完整卡片再提取属性；缺少检索结果时保留未决。不能把模型的 `schema_gaps` 直接当成本体缺口。
3. **为事实增加具体的语义验收。** 核对表格行归属、关系方向、指标/单位、条件、N/A 与“—”的状态；不满足时保留未决，不提交为事实。优先用本轮原文反例验证这些检查。

这些是后续实验建议，本次未修改生产识别流程或本体。本轮检索采用简单词法基线，输出采用统一 claims 协议；结果说明这套最小组合的限制，不能外推为 Qwen 或完整 AOR 架构的质量上限。

## 6. 复现与制品

执行器：[schema_card_probe.py](../../../backend/app/evaluation/schema_card_probe.py)。取样定义：[schema_card_probe_cases.json](../../../backend/app/evaluation/fixtures/schema_card_probe_cases.json)。参考是独立的 assistant silver，不是业务专家金标。

在已配置项目 Qwen 端点及数据库的后端环境运行，输出目录必须不存在：

```bash
python -m app.evaluation.schema_card_probe \
  --document-ref upload-23c872fb-3ab1-41de-a705-dd4b162dfa09 \
  --output /app/data/evaluations/schema-card-qwen-cmc-new-run

python -m app.evaluation.schema_card_probe \
  --expand-from /app/data/evaluations/schema-card-qwen-cmc-new-run \
  --output /app/data/evaluations/schema-card-qwen-cmc-new-expansion
```

原始制品保存于工作区 `output/schema-card-qwen-cmc-20260915/`（被 Git 忽略）：`pilot/` 为大卡片试跑，`run/` 为 8 片段主验证，`expansion/` 为补卡片实验。包含原始请求/响应、实际动态 Schema、模型用量、Word/IR、卡片、TTL 和运行脚本副本。脚本记录内容哈希，运行仍依赖项目当前共享客户端和菜单编译器；不是完整封装的独立运行镜像。

本执行器的片段选择和 silver 参考固定用于上述 SHA-256 对应原件，若源文件变化会拒绝沿用参考；验证其他文档需另行准备片段与参考。

定向工程验证：`tests/test_extraction/test_schema_card_probe.py` 的 12 项测试通过；定向 Ruff 通过。测试覆盖错误关系方向、非法谓词/端点、伪造引用、错误证据单元、额外字段、未解析约束、卡片压缩保真及扩展不引入未知类型。既有测试环境产生 4 条弃用/字段命名警告。本次未执行全库测试、真实全文评测或部署。

接口文档通过 Context7 查询 [llama.cpp grammars](https://github.com/ggml-org/llama.cpp/blob/master/grammars/README.md) 及[结构化返回示例](https://github.com/ggml-org/llama.cpp/blob/master/scripts/server-test-structured.py)：Schema 只约束生成语法，需要同时在提示中描述协议；GBNF 仅覆盖 JSON Schema 子集，不支持的条件、唯一性等规则不能作为事实校验依据。本次最终以实际部署服务的响应为证据。
