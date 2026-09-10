# T017 验收材料资格审计

审计日期：2026-09-09。此页是供验收负责人复核的材料清单与门禁说明，不是专家审批、
阈值批准或质量通过证明。本次只读检查参考/协议元数据、文件哈希、既有说明与评分代码；
没有调用模型、修改历史制品或复制用户原文。

**在下列已审计仓库路径中，没有找到可用于 T017 正式验收的专家批准参考、独立文档
标注包或已批准质量/成本阈值协议。** 固定池三轮 A–D 和动态运行可以继续形成真实执行
及费用证据；缺少这些材料时，结果必须保留为诊断或未验收，不能通过增加运行次数改变
参考资格。此结论限于已检查的仓库材料，不表示受控外部标注库中不存在材料。

依据为 [T017](tasks.md)、[FR-013/FR-014 与 SC-005](spec.md)、
[正式验收口径](quickstart.md) 和 [活动评分契约](../../backend/app/evaluation/README.md)。

## 1. 已有参考与协议

读取范围包括 `backend/app/evaluation/fixtures/`、`docs/evaluations/` 的全部现有参考
及八份归档协议；另搜索 `evaluations/` 中非冻结 runtime 的参考、gold、协议和阈值文件。
冻结 runtime 中随代码复制的 fixture 不算新增标注包。

| 材料 | 核验结果 | 当前用途 |
|---|---|---|
| [活动 fixture](../../backend/app/evaluation/fixtures/cmc_upload_23c872fb_reference.json) | `schema_version=1`、`annotation_level=assistant_silver`，没有 `expert_review`；14 实体、31 属性、13 关系、15 禁止项、6 个局部 scopes、2 个未评分观察 | 有限范围诊断参考，不能用于正式 graph scorer |
| [参考说明](../../backend/app/evaluation/fixtures/reference_notes.md) | 明示由独立助手依据源文与本体标注，未看预测；仍未经人工或 CMC 专家复核；6 个 scopes 仅承诺局部穷尽性 | 标注来源与局限说明，不是专家审签 |
| [归档 02](../../docs/evaluations/cmc-23c872fb-20260907-02/protocol.json)、[03](../../docs/evaluations/cmc-root-guided-20260908-03/protocol.json) | 协议将参考等级冻结为 `assistant_silver`，登记有限任务/时间及观测口径 | 历史有界对照，非 022 正式质量协议 |
| [归档 04](../../docs/evaluations/cmc-quality-guided-20260908-04/protocol.json)、[05](../../docs/evaluations/cmc-quality-guided-20260908-05/protocol.json)、[06](../../docs/evaluations/cmc-quality-guided-20260908-06/protocol.json)、[07](../../docs/evaluations/cmc-quality-guided-20260908-07/protocol.json) | `reference_status=assistant_silver_not_expert_reviewed`，参考仅运行后评分；07 的 acceptance 是分别报告指标的要求，没有获批数值阈值 | 历史识别/证明诊断，不是专家验收 |
| [归档 08](../../docs/evaluations/cmc-product-api-staged-20260908-08/protocol.json)、[09](../../docs/evaluations/cmc-product-api-staged-20260908-09/protocol.json) | 固定 `describes → hasActiveIngredient` 焦点，继续使用未改 silver；09 明示非穷尽图谱评测 | 焦点回归与证据核验，不能覆盖当前整图或其他 focus path |
| [固定池说明](../../backend/app/evaluation/FIXED_POOL.md) | 给出可执行协议格式，示例 `reference_sha256=null`、`comparisons=[]`、`cost_limits={}`，明确是诊断模板 | 可生成真实三轮执行和成本；空参考/阈值不能通过质量门 |

活动 fixture 与八份归档 `reference.json` 共 **9 个文件字节完全一致**，本次独立重算的
共同 SHA256 为：

`8374e3b5a592729f40df05a940f99111bcffdd623576902d4fa1df5728989ee9`

全部八份归档协议的 `reference_sha256` 均指向这个值。它们绑定同一原件：

`e94822808601e73f4ba4c715c664812d4a01e42f0e4e84a6b08a3653b915daf7`

这证明当前归档副本和协议声明一致。notes 声明第 2/3 版标注修正在任何模型运行之前完成，
本次保留该来源陈述；**文件 hash 本身不能证明专家在预测前完成审批**。参考没有复核人、
批准时间、受控专家标注包 hash 或独立审签记录，不得把 notes 的“独立助手”换成“独立专家”。

这份参考也没有活动 `ontology-guided-reference-v1` 要求的本体 hash、显式根/scope 绑定
和专家批准结构，不能仅修改 `annotation_level` 或添加 `approved` 就转换为合格参考。
旧六个局部 scopes 不自动等于 `document_graph`，也不自动等于当前 `usesEquipment`
焦点及全部到达主体直接属性的范围。14 个实体包含外部给定文档根，不能都计为识别成果。

## 2. 独立文档与模型材料

本次对 `docs/evaluations/` 和 `evaluations/` 的非 runtime DOCX 仅计算文件 hash：
八份真实 CMC 原件/副本均为上述同一个 hash。另有四份 CPU 冒烟 `synthetic.docx` 和
两份浏览器制品 DOCX；[浏览器 fixture 生成器](../../backend/scripts/document_analysis_browser_fixture.py)
明确使用程序生成文档与受控模型响应。它们不能作为独立真实业务文档质量样本。
已搜索范围没有找到与另一份独立真实文档绑定的专家参考。

现有本地排序模型制品及实际主模型链路已就绪，见 [CPU 核验](cpu-ranking.md) 与
[独立真实运行核验](independent-verification.md)。这不再是 T017 的缺口。
后续正式协议仍需冻结当轮实际模型、tokenizer、适配器、runtime、本体、原件/IR 和
配置身份；旧运行的多份 preparation 或私有运行 ID 不增加独立文档样本数。

## 3. T017 的接受与拒绝门

以下条件来自现有 Spec Kit 契约和工具实现。数值主指标、非劣/增量界限、置信水平、
样本规模与成本上限尚无合格来源，必须由独立质量协议明确；本页不自行填入 95%/99%
或根据已见结果选择阈值。

| 门 | 接受条件 | 拒绝或未验收条件 |
|---|---|---|
| 材料来源 | 真实业务文档获准使用；独立专家在正式预测前完成参考及协议审签；保留审签身份、时间、内容 hash、版本与受控来源 | assistant silver、工程生成文档、无实际审签、预测后按结果调整阈值或参考 |
| 文档/范围 | 原件、IR、本体、显式根、`scope_mode/focus_path` 与运行精确相符；独立文档数和覆盖规模达到批准协议 | 同一文档换目录冒充独立样本；局部 scopes 冒充整篇；原件或 scope 不符 |
| 图谱参考 | `ontology-guided-reference-v1`；完整 tuple、方向、极性、条件、适用域及允许原文证明；必需的局部身份、mention 分区和多跳路径有专家裁决 | 缺证据集合、关键路径未标注、未决或范围外接受项被静默排除；给定根充当提取成功 |
| 检索参考 | 绑定冻结 `query_id/document_hash/query_content_hash`；每条共同池记录有完整 grade/role 裁决；`annotation_complete=true`；专家审签 | 只标相关记录、漏反证/条件项、未裁决池记录；将检索参考提供给执行器 |
| 预登记 | 预测前冻结参考文件 SHA256、全部轮次/run ID、A–D 允许差异、共同预算、数值环境、质量比较与成本上限；版本变化另建协议 | 结果后补门槛；复制执行冒充新轮；未注册因素或预算变化 |
| 固定池执行 | 至少三轮，每轮 A/B/C/D 齐全；共同查询、原始记录集合、视图及池顺序一致；B–D 有真实模型/费用证据；协议聚合 `retrieval_protocol_gate=pass` | 缺组/缺轮、哈希漂移、漏记录、失败轮被排除、语义组降级冒成功、未测成本或超预算 |
| 动态图执行 | 至少三个真实新运行，按批准 A–D 动态协议从同一显式根重新执行；检查实际前沿/阶段、必要原文装配、有效主体展开及证明路径 | 仅固定池排名、仅计划未执行、单跳局部结果代替完整多跳、技术未完成当语义完成 |
| 事实/路径质量 | 分列完整关系/属性 P/R/F1、精度上下界、重复与未评分、身份错误、多跳连续路径、证明回放、覆盖；达到批准主指标和比较门 | 空有效图“高精度”、含实体 overall 代替完整断言指标、借用其他路径证据、路径枚举截断或遗漏未评分 |
| 全部成本 | 汇总 tokenizer、embedding、精排、发现、独立验证、摘要、重试、排队及失败预扣；输入/输出分开，未知显式保留；满足批准成本门 | 只计成功调用、输入输出混加、未知当零、预算/范围不同直接称效率提升 |
| 最终裁决 | 上述材料、执行、质量与成本全部通过，结论可追溯冻结制品及独立审批 | 任一材料缺失保持 `blocked/not_evaluated`；已执行指标不达标记失败，不能解释为通过 |

固定池的三轮 A–D 共 12 次执行只覆盖排序实验。A 是确定性组，不应伪称发生了排序模型
调用；C/D 的单池排名可以相同，因为单池不执行阶段交错。三轮固定池不替代动态图运行，
三个新运行也不自动满足独立文档规模或统计质量要求；以批准协议登记的文档、轮次和
比较矩阵为准。

## 4. 当前程序门禁的边界

[图谱评分器](../../backend/app/evaluation/ontology_guided_scorer.py) 已拒绝未批准参考，
并检查文档/IR、本体、根和 scope。`ExpertReview` 要求 `status=approved`、`reviewer`、
`reviewed_at`、`reference_hash`；这是字段与绑定校验，不能代替对真实专家身份和审签
来源的核验。实际参考文件 SHA256 还需与预登记协议相符，不把标注包 hash 与含审签
封装的参考文件 hash 混为一谈。

当前 `QualityThresholds` 直接门禁的是 **包含实体的 overall P/R/F1**、禁止断言接受
上限和可选完整覆盖要求。`metrics.assertions`、路径及身份另有输出，但没有独立的
完整断言增量、路径质量、独立文档样本规模或总成本比较门。因此单轮
`formal_quality_gate=pass` 不能单独关闭 T017；最终验收须按批准协议核对这些指标，
且完整闭环不能选择关闭必要覆盖条件以掩盖未执行任务。

[固定池聚合器](../../backend/app/evaluation/fixed_pool_benchmark.py) 核验预登记参考
文件 hash、专家批准字段、完整轮次、比较与成本。无参考返回
`missing_expert_reference`；无比较或成本阈值返回
`quality_or_cost_thresholds_not_preregistered`。即使检索协议通过，
`graph_quality_gate=not_evaluated`，阶段交错仍要求独立动态前沿协议。

## 5. 最小外部材料包与可继续工作

需要的外部材料可集中为一个受控版本包，避免零散人工确认：

1. **独立文档清单**：获准真实 DOCX 或其受控路径、SHA256、根类型、目标 scope；标明
   与调试文档的隔离关系，以及由质量协议确定的样本数量与覆盖类别。
2. **专家参考包**：对应文档/本体/根/scope 的完整图谱参考和 query/record 参考；包含
   正反例、条件/未决、局部实体归属、必要身份分区、多跳路径及可回放来源。真实专家
   审签要有复核人、时间、标注包 hash 和可核对来源，不能由执行助手代签。
3. **已批准的预测前协议**：冻结参考文件 hash、主指标及最小增量/非劣界限、置信水平、
   文档规模、至少三轮 A–D 固定池及动态矩阵、共同运行预算和全部成本门槛。批准主体、
   时间及协议内容 hash 一并保存。

材料就绪前可以完成新的真实固定池三轮 A–D、协议聚合、失败/成本审计和当前同一文档
的动态诊断，并把机器门禁如实输出。若诊断协议没有合格参考或阈值，后续补齐专家材料
时应另建正式预测前协议和新运行；不能追认旧诊断为预注册质量验收。

本页只新增验收材料审计说明，未改变 T017 需求、未批准阈值、未勾选任务，也未修改
历史参考或实验结果。
