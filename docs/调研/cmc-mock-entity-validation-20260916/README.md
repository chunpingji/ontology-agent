# CMCReport：静态 Mock 实例知识三组实测

2026-09-16 已完成实现与 E0/E1/E2 真实对照：**9/9 个 case 完成，18/18 份原始响应符合指定 JSON Schema；身份工具有效，尚未证明最终抽取质量提升。** E2 增加检索编号覆盖，却未正确保留过滤器备选实例，且放行一条证据不足的清洗设备使用关系。因此语义质量未全面通过，不据此切换线上识别器。

使用报告 `upload-23c872fb-3ab1-41de-a705-dd4b162dfa09` 对应 Word、完整报告的 447 单元 IR、329 卡片及同源分层摘要；按冻结预算每组选择 3 条记录。这不是全文所有关系的抽取验收。统计见 [summary.json](summary.json)，实际返回见 [return-samples.json](return-samples.json)，Schema 卡片见 [scope-cards.json](scope-cards.json)。原始 C/D 制品不变。

## 来源与实验边界

Mock 使用现有 `mock_equipment/equipment_archive` 静态档案，共 211 条；它与管理界面可编辑数据库不是同一来源，不做同步或播种。前端手工 CMC 示例和设备排期不作为上传报告证据。全部名称、编号、规格和辅助元数据来自当前档案冻结副本，不修改权威本体/SKOS。

范围为 CMCReport 的 usesEquipment 和 ProcessEquipment 的编号、名称、规格。GLiNER2.5 仍使用已有 Equipment 类型和三个属性的 SKOS 标签，NER 上位类提及不自动证明 ProcessEquipment 类型。Mock 名称不变成 NER 新标签。

| 组别 | 原文选择 | Mock 用途 |
|---|---|---|
| E0 | 现有单谓词摘要检索 | 不进入模型 |
| E1 | 与 E0 完全相同 | 按原文名称/编号查询身份候选 |
| E2 | 单谓词检索 + Mock 原文词项得分 | 同 E1 |

复用 RecordIndex 和 plan_slot；不使用旧窗口、手工设备编号名单或评分参考选记录。E2 仅在每条记录的 primary 原文计算 `4×不同精确编号 + 1×不同精确名称`，同名档案不重复加分，上下文不重复触发加分。按得分/原位置取前三条正分完整记录，每条独立处理；含表头、注释、父上下文和祖先标题，去重原文≤3,000字符，超预算整条deferred，不裁切。

原始 NER 全量保存，模型可见提及固定按分数与坐标排序取48条；完整原文保留。去除上一轮重复工具诊断、整份 source_spans 和无关类槽位；模型 messages 限60,000字符，超限不请求。全部组使用同一提示与候选Schema；E0无Mock候选，E1/E2只能选择实际返回的最多8条记录。每条候选/核验各一次，共最多18次Qwen，无重试；E0/E1交错运行，E2随后运行。

## 判定与验收

本体只限定合法字段，摘要只作检索；文档原文证明提及、角色和条件，外部档案证明自身字段。Mock身份工具只确认编号/记录/版本及引文对应，只有同名或相似规格不得确认。未知编号保留；“A或B”通过one_of组保留，不摊平成两条同时使用的事实。

Qwen候选经固定ID核验，再执行原文归属/身份/字段表示门。外部属性仅沿用设备编号、设备名称和规格型号三个既有映射，材质/位置等元数据只作未映射背景或差异观察。SHACL文本表示通过不等于数值单位校准、唯一身份或关系成立。实验结果不提交业务事实。

独立助手在预测前从全文冻结[参考](reference.json)及[口径说明](notes.md)。该参考不是专家金标或全图金标，不进入runner；身份、合并、备选条件、检索覆盖、技术缺测、实际用量和数值未评估分别报告。D组改变了更多协议且0/8完整，不作为本轮三组的质量基线。

## 实现及固定身份

| 模块 | 本轮实现 |
|---|---|
| [mock_entities.py](../../../backend/app/services/extraction/tool_validation/mock_entities.py) | 静态档案名称/编号查询、版本化候选、原文编号/身份核验与字段来源 |
| [schema_card_mock_retrieval.py](../../../backend/app/evaluation/schema_card_mock_retrieval.py) | 复用现有摘要检索，增加 Mock 词项对照、完整记录与上下文回放 |
| [schema_card_mock_protocol.py](../../../backend/app/evaluation/schema_card_mock_protocol.py) | 紧凑 Schema、原文锚点与属性归属、备选关系、外部身份及确定性终判 |
| [schema_card_mock_tools.py](../../../backend/app/evaluation/schema_card_mock_tools.py) | 冻结输入、GLiNER2.5 复用、三组候选与核验两阶段执行 |

| 冻结项 | 身份 |
|---|---|
| Qwen | `Qwen3.6-35B-A3B`；配置声明 revision `0b21525e972670ed59e1812e170b27c26355381f0656ecc4e25617ece7dac58b` |
| GLiNER2.5 | `fastino/gliner2.5-multi-v1`；revision `aaecfe45db1d828c963717054ccb868e8ad1f1d5` |
| GLiNER 权重 SHA256 | `c1ff4ec0bc00031c15530b8f3c33d3677f27949e6a0cb52e1247a6224b6c5395` |
| Word SHA256 | `e94822808601e73f4ba4c715c664812d4a01e42f0e4e84a6b08a3653b915daf7` |
| 静态档案文件 SHA256 | `7cd6148df2575179441e44e68ed8d6f5c7bdcfa7dd7f10ac4391686fb1c9fa35` |
| 运行身份 | `cmc-mock-70bd86d8226d4d36` |

完整代码、输入、词表及依赖身份见 [result.json](../../../output/schema-card-qwen-cmc-mock-20260916/completed/result.json)。本轮 `temperature=0`、`enable_thinking=false`。已核对 E0/E1 三对原文、完整 NER 和模型 NER 视图逐字相同；E1/E2 共享离心机记录的候选阶段请求也相同。新摘要调用为 0。原始 NER 视图的 48 条上限本轮实际省略 **0 条**。

## 检索覆盖与真实结果

全文参考包含 6 个不同设备编号、18 次物理提及和 5 条设备需求记录：RE64611、RE64613、PF64216、PF64616、CT64610、DE64603。5 个编号在静态档案精确匹配，DE64603 不存在。参考不进入检索或模型输入。

| 组 | 实际入选记录（按顺序） | primary 原文编号覆盖 | 最终保留编号/本组已选编号 |
|---|---|---:|---:|
| E0 | CT64610需求、DE64603需求、RE64611需求 | 3/6；提及 3/18 | 3/3 |
| E1 | 同 E0 | 3/6；提及 3/18 | 3/3 |
| E2 | RE64611与两PF的清洗记录、CT64610需求、两PF的需求记录 | 4/6；提及 6/18 | 2/4 |

E2 替换了 2/3 条记录。三组都未覆盖 RE64613；E2 还失去 DE64603。E0/E1 去重源单元各 55 个（primary 40），E2 为 54 个（primary 35）。E2 候选词法上涉及全部 4 个编号，但过滤器候选的锚点/合并错误使其最终仅保留 RE64611、CT64610。检索覆盖提高没有转化为最终有效结果增加，详见 [reference-scores.json](reference-scores.json)。

| 指标 | E0 | E1 | E2 |
|---|---:|---:|---:|
| 完成 case / 原始 Schema 通过 | 3/3；6/6 | 3/3；6/6 | 3/3；6/6 |
| 程序 accepted / unresolved / rejected | 12 / 0 / 0 | 13 / 1 / 0 | 10 / 9 / 0 |
| accepted：实体/属性/关系/观察 | 3 / 6 / 3 / 0 | 3 / 6 / 2 / 2 | 2 / 4 / 2 / 2 |
| accepted 中独立整体支持 | 12/12 | 13/13 | 9/10；另 1 项未决 |
| accepted 关系中独立支持 | 3/3 | 2/2 | 1/2；另 1 项未决 |
| 成功外部身份关联 | 不适用（未输入Mock） | 2 | 2 |
| 文本属性验证通过/待验证属性 | 6/6 | 6/6 | 4/8 |

accepted 是实验门输出，不是人工确认。观察与事实类型分开计数，不能以 E1 accepted 总数较多宣称质量提升。所有结果 `fact_eligible=false`，未提交业务事实。

独立助手未读取程序检查/Qwen 核验结果，复核全部 9 包、45 个原始目标，再按包哈希、目标 ID 和完整 proposal 匹配最终去向。整体支持/不支持/未决分别为 E0 **12/0/0**、E1 **13/1/0**、E2 **9/9/1**；无漏评。只看文字含义而忽略引用/实体归属时，支持数分别为 12、14、17，故不能用“文字看起来正确”替代完整契约。详见 [reviews-matched.json](reviews-matched.json)。

这些比例仅描述本轮有限样本，不能解释为全文图谱 precision/recall。编号评分只证明源文定位与词法覆盖，完整语义另由独立复核判断；该复核也不是专家金标。

## 返回样例揭示的收益与缺口

**已知身份关联成立，未知编号保留。** E1/E2 均关联 CT64610 与 RE64611。RE64611 文档名称为“200L反应釜”，档案名称为“搪玻璃反应釜”；基于编号建立版本化链接，两份名称保持各自来源。DE64603 原文实体保留，`external_key=""`，没有错配为同名 DE64203。新增外部字段没有变成文档引文。

**E1 错误条件引用被拦住，但损失了一条本可成立的关系。** 干燥箱候选把“干燥”引用为 `u10`，实际在 `u23`。Qwen 核验仍判 supported；确定性原文门以 `citation_quote_not_in_source` 留作 unresolved。E0 同源关系有效，因此本轮添加 Mock 没有带来同源抽取质量提升。

**两个锚点编号冲突和一次双编号合并被拦住。** E2 清洗记录中，PF64216/PF64616 两实体的 anchor 都包含“（RE64611）压滤器”，触发 `anchor_equipment_id_conflict`，连带阻止属性/关系。E2 需求记录又将 `PF64216或PF64616` 放入一个实体的 `equipment_id.quote`，只给一个 object 却设置 `one_of`，触发 `equipment_id_must_be_single_identifier` 等门。没有把二选一摊平成两台同时使用后放行，但最终也未保留任何一组 PF 备选关系；说明性 observation 不能替代它。

**仍有语义漏门：清洗对象被当成 usesEquipment 的充分证明。** E2 清洗 case 的 `r1` 用“用纯化水冲洗反应釜内壁5~10分钟”作证据，用“HRS-1597成品”作 condition。Qwen 核验支持、程序 accepted、issues 为空；独立复核认为该证据包不足以证明所要求的使用关系，产品名称也不足以限定清洗/工艺条件，结论为 undetermined。此项保留在分母，既不计正确，也不称为已证伪。真实引文、精确外部身份和 SHACL 通过仍不足以保证关系语义。

**元数据差异没有校准成文档事实。** CT64610 文档材质为“不锈钢衬HALAR”，档案为“316L不锈钢”。这是来源值差异，316L 也可能描述基材，尚不能证明严格语义矛盾。本轮材质不在三项映射内，未覆写文档、未自动生成材质属性；模型也未输出对应差异观察。因此没有证明系统能自动识别和解决这类差异。

原样候选及最终去向摘录见 [return-samples.json](return-samples.json)，含已知关联、未知编号、清洗漏门、过滤器备选四个 case。完整原文、请求、响应及终判保存在对应[运行目录](../../../output/schema-card-qwen-cmc-mock-20260916/completed)。

## JSON Schema、SHACL 与成本

独立制品审计采用严格 JSON 解析（拒绝重复键/非有限数）与标准 `jsonschema==4.23.0`，核验原始响应与各自冻结 Schema：18/18 通过，18 次 `finish_reason=stop`，未记录模型失败或截断。账本没有显式 HTTP 状态码，因此不记作“18 个 HTTP 200”。文件身份、预算、同源性、版本及字段映射等 **1,006 项完整性检查全部通过**，见 [artifact-audit.json](artifact-audit.json)。

真实 pySHACL 为 `0.31.0`。E0/E1/E2 文本属性通过数分别为 6/6、6/6、4/8；真正执行且覆盖预期 focus 的 SHACL 分别为 6、6、4 项。E2 另 4 项在前置主体/语义检查失败，未进入 SHACL，不能记为 SHACL 合格或违规。预检查因尚未语义核验而 incomplete，也不计成功。`600`、`200L`、`24盘` 本轮均为规格字符串，**未验证数值类型、量纲或单位换算**；数值校准结论为未评估。

| 成本 | E0 | E1 | E2 | 合计 |
|---|---:|---:|---:|---:|
| Qwen 请求 | 6 | 6 | 6 | 18 |
| Prompt tokens | 38,566 | 52,350 | 51,525 | 142,441 |
| Completion tokens | 2,443 | 2,719 | 4,151 | 9,313 |
| Total tokens | 41,009 | 55,069 | 55,676 | 151,754 |
| HTTP 阶段累计秒 | 190.346 | 250.859 | 193.650 | 634.855 |

所有 18 次 usage 均有报告；Qwen 整轮墙钟 635.888 秒。E1 比同源 E0 多 14,060 tokens，约 34.3%。E2 共享离心机请求有服务端缓存命中，顺序/缓存影响时延，因此不能据此宣称 E2 更快。未配置价格，未计算货币成本。

GLiNER2.5 推理 5 份不同源输入，墙钟 36.841 秒；去重输入共 207 个带标签 span，按 9 个 case 重复计为 359 个。span 数不是召回率。NER 与 Qwen 阶段合计 672.729 秒，不含输入准备、历史摘要生成和独立评审；新摘要调用为 0。

## 验证与复现

新增四模块及对应测试、共享核心依赖边界共 **126 项工程测试通过**（4 条既有警告），八个新增 Python 文件定向 Ruff 通过，命令和耗时见 [engineering-checks.json](engineering-checks.json)。测试中的 metric stub 只验证控制流；实际 SHACL 证据来自真实运行。末次只读实现复核未发现上述已披露缺口之外的阻断问题。

本轮实际 Qwen 命令如下；已经执行完毕，重现时必须改为全新输出目录，不能覆盖本轮结果：

```bash
docker exec \
  -e PYTHONPATH=/app/data/evaluations/cmc-tool-shacl-deps-20260916:/app \
  -e HF_HUB_OFFLINE=1 -e TRANSFORMERS_OFFLINE=1 \
  -e OMP_NUM_THREADS=4 -e MKL_NUM_THREADS=4 \
  ontology-agent-backend-1 \
  /app/data/evaluations/gliner2-runtime-20260916/venv/bin/python \
  -m app.evaluation.schema_card_mock_tools \
  --baseline /app/data/evaluations/schema-card-summary-retrieval-qwen-20260916-01 \
  --output /app/data/evaluations/schema-card-mock-qwen-20260916-01 \
  --mock-file /app/app/resources/equipment_archive.json \
  --ontology-dir /app/ontology/slpra \
  --model-path /app/models/gliner2.5-multi-v1-20260916 \
  --device cuda:0 \
  --prepared-tools /app/data/evaluations/schema-card-mock-ner-20260916-01
```

参考构建、独立复核包生成、审计、评分及匹配脚本保存在 [scripts](scripts)。以下命令从仓库根目录执行，只读冻结运行、零模型调用，输出写入新的临时目录：

```bash
CMC_CHECK_DIR=$(mktemp -d /tmp/cmc-mock-check-XXXXXX)
CMC_RUN=output/schema-card-qwen-cmc-mock-20260916/completed
CMC_REPORT=docs/调研/cmc-mock-entity-validation-20260916
python "$CMC_REPORT/scripts/audit-results.py" "$CMC_RUN" \
  --prepared-ner output/schema-card-qwen-cmc-mock-20260916/ner \
  --output "$CMC_CHECK_DIR/artifact-audit.json"
python "$CMC_REPORT/scripts/score-reference.py" "$CMC_RUN" \
  "$CMC_REPORT/reference.json" --output "$CMC_CHECK_DIR/reference-scores.json"
python "$CMC_REPORT/scripts/match-reviews.py" "$CMC_RUN" \
  output/schema-card-qwen-cmc-mock-20260916/review-inputs \
  "$CMC_CHECK_DIR/reviews-matched.json" \
  "$CMC_REPORT/review-e0-e1.json" "$CMC_REPORT/review-e2.json"
```

精确返回约束见[候选 Schema](../../../output/schema-card-qwen-cmc-mock-20260916/completed/E1/record-c54c3238c67b/candidates/schema.json) 与[核验 Schema](../../../output/schema-card-qwen-cmc-mock-20260916/completed/E1/record-c54c3238c67b/verification/schema.json)。编号评分脚本不读取语义复核，其 `pending_separate_assistant_review` 表示该脚本职责边界；本轮语义复核已由 reviews-matched 全部完成。

## 当前判断

可保留 Mock 按需查询、精确编号核验与外部字段来源隔离；它们在本轮已知/未知编号场景中有效。E2 的 Mock 加权检索尚不宜作为默认策略；下一次验证应先收紧关系所需角色/条件证据，以及同单元多设备的锚点与备选实体拆分，再以新协议和新运行比较。后续修正不能回填本轮预测。

本轮未接入可编辑数据库 Mock，未验证其他外部系统、数值单位校准或完整图谱闭环；没有重启后端、修改本体/SKOS、提交事实或部署。**工程与结构验证通过，真实语义质量仍有明确缺口。**
