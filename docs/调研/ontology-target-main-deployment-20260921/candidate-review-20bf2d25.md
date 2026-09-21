# 20bf2d25 运行增量原文审查

运行：`20bf2d25-98f3-4efe-99f0-1e5dbbae5d05`。原文：
`upload-a255fd30-192c-4f81-92df-5f76a56b8383`。本审查仅读取已保存的实际调用、
公开图谱及 source/source-selection，不调用模型、不控制运行、不修改图谱。
这是独立事后原文审阅，不是预先盲评或业务金标。

## 第一组：1 次发现、1 次核验

审阅的 lineage 为 `3ade30e97846dcf7932f909142038ceca00c52925202dbac26dee9be15ec3111`。
发现为 attempt 1，核验为 attempt 2；相应不可变结果键、输出摘要、原始核验结论、
已发布实体及属性的来源选择结果保存在
[本运行证据与增量清单](candidate-review-20bf2d25-evidence.json)。之后只追加新调用审查，
不重复把这两次调用计入新增样本。

**第一组实际发布了 6 个文档局部 DrugProduct 实体、2 个属性、0 关系，但实体类型/身份与重复粒度未通过独立核对。**
首个只读快照在核验回执尚未应用时只有文档根；公开继续后的快照
`run_revision=99 / event_head=9` 已包含上述结果，不能继续用先前空图描述最终落图。
系统总结果为 `semantic_outcome=supported`、`complete=false`，未完成原因为
`reference_binding_endpoint_invalid`；supported 汇总并不等于整组通过人工质量验收。

冻结本体的 DrugProduct 定义为：

> A finished dosage form drug product that bears a clinical drug role.

原文段落编号沿用全文清单的 1 基编号：P42 为“制剂剂型：口服片剂”，P44 为
“是否细胞毒药物：否”，P39 为“溶解性： DCM易溶，H2O、Heptane难溶”。
六个实体都没有 identifier claim；公开图谱均标为 `document_local`、`unreviewed`。
因此这里的问题不是错误授予全局唯一身份，而是把属性字段/记录重复当成已充分识别的成品实例。

| 发现项 | 原文与系统行为 | 独立核对结论 |
|---|---|---|
| e1，mention，剂型字段 | 整句引用 P42；type/referent/subject_role 全 supported，实际落图 | 剂型值有依据，但字段整句不是独立产品名称。单凭它不足以把新增成品实例及其粒度判为已识别。 |
| e2，mention，细胞毒否字段 | 引用 P44；模型用“排除法”推为 DrugProduct，实际落图 | 非细胞毒这一属性不证明 finished dosage form 或 clinical drug role；类型推理错误，实例依据也不足。 |
| e3，mention，溶解性字段 | 引用 P39；模型以“药物产品的物理性质”判 type supported，实际落图 | 溶解性是共有物理属性，不能证明此类型或独立实例；类型推理错误。 |
| e4，record，剂型字段 | 把 P42 整句标为 subject 组成，重复建立记录实体，实际落图 | 可视为产品的一条属性记录；没有充分证明应新增另一个成品实例。归属/身份与重复粒度未通过。 |
| e5，record，细胞毒否字段 | 把 P44 整句标为 subject，模型称符合“药物产品分类”，实际落图 | 同 e2：否定分类属性不是成品制剂类型证据，且重复实体化同一字段。 |
| e6，record，溶解性字段 | 把 P39 整句标为 subject，模型称符合“物理性质定义”，实际落图 | 同 e3：共有属性不能代替类型关键限定，且重复实体化同一字段。 |

e1/e4 的“口服片剂”确实比共有化学属性提供了更多剂型信息；本报告没有断言原文不存在制剂。
但全文同时描述原料药供样和结晶纯化，字段值不能独自解决具体受述对象、实例层级及临床角色。
核验本次可见范围主要是基本性质字段及其上下文，不能要求模型引用未提供的简介，
也不能据字段相邻就认定六个新实例已获充分证明。

## 三个属性与来源核对

| 属性 | 原文核对 | 系统最终结果与独立结论 |
|---|---|---|
| p1：e4 → dosageForm → 口服片剂 | P42 的值在偏移 `[5,9)` 唯一可定位；字段、值和 subject-to-value 方向一致。原文无局部条件限制，肯定记录剂型合理。 | 实际发布；通过目标的 supported_assertion_refs 引用。值/字段核对通过，但 e4 的具体身份和类型尚未充分证明，整个主体—属性事实不能据此评为通过。 |
| p2：e5 → isCytotoxic → 否 | P44 末尾“否”表示布尔 false；同段“是否”的“否”也出现，单字引文在偏移 1、8 各一次，且未给 context_text。 | `ambiguous_source_quote` 拒绝，未进入本批语义核验或属性图。来源歧义拒绝有依据。尚未验证最终布尔归一及否定表达；不能把 false 当作缺失或否定实体存在。 |
| p3：e6 → solubility → DCM易溶，H2O、Heptane难溶 | P39 值在偏移 `[5,24)` 唯一可定位，三个溶剂的配对保留；字段及方向正确。 | 实际发布且有 supported_assertion_refs。字面内容通过，但主体 e6 的类型/身份不足；该整体事实仍未通过独立质量核对。 |

对 p1/p3 的 6 个 source-selection 请求均实际成功，文档 hash 与结构 hash 与当前运行
source 一致，主体、值和 predicate_bridge 都能回到上述原句。**可定位原文不等于语义整体成立。**
两个属性的局部 `polarity=affirmed`、`modality=asserted`、无条件列表与所引字段相符；
没有把字段值解释成已执行生产、已完成清洗或已验证合格。

本批没有提出 `CAS=N/A` 或其他缺失值属性，也没有缺失观察；因此尚未实际验证本运行的
N/A 门。旧运行的缺失值验收不能转记到本运行。

## 共指、关系与展示完整度

rb1=e4→e1、rb2=e5→e2、rb3=e6→e3 分别复用同一个物理字段；系统均记录
`reference_binding_endpoint_invalid`，未建立这些共指。一个字段被建成 mention 和 record
不证明存在两个独立业务对象，也不自动证明合并。模型在 p1/p3 的 subject_binding 理由中
仍声称相应端点“共指”，没有足够依据把已拒绝的绑定当作独立证明。

本组 `relations=[]`，最终 relationships/relationship_groups 也为空，**没有关系实线可验收**。
记录发现阶段未提出关系不能独自评为关系召回失败，也不能据两个属性已发布宣称关系合批已通过。
六个局部节点尚无与报告根相连的实证关系。

快照完整度为关系完成 `0/12`、实证 0；属性目标从初始 7 增至 169，实证属性 2、
完成项仍为 0，完整度 `0.0%`；待展开由 12 增至 18。分母变化反映新增实例目标，
而这些实例本身有类型/身份质量问题，因此分母增长和模型 supported 数均不是识别进步的证据。

## 第二组：基本性质中的名称与属性

lineage 为 `a9860bc85d4285b672d26fdc6b9abd5082f9486dea522af8805d66ef0c6791fa`，
新增审阅发现、核验各一次。不可变结果键、字段全文/偏移、原始模型与控制器判定差异及
第三组的覆盖矩阵保存在[第二、三组增量证据](candidate-review-20bf2d25-increment-02-03.json)。

这一组读取基本性质的 P31–38，新增了一个以 P33“项目名称：HRS-1597”为整句提及的
DrugProduct 节点（`095ae2bf…@1`），仍为 `document_local`、`unreviewed`，没有 identifier
claim。它比前组独立属性字段更明确地提供了对象名称，但模型将“分子式、化学名”“口服片剂、
粉末状”一并称为 DrugProduct 类型依据，仍未充分区分化学实体、粉末性状与成品制剂实例。
这不是全局身份合并错误，也不能只因同一段里存在剂型字段就消除前述类型/身份缺口。

| 属性 | 1 基段落及值偏移 | 原文检查与实际结果 |
|---|---|---|
| projectCode | P34 `[5,16)` | `HRXS-P-2419` 及“项目代码：”逐字唯一匹配；最终 rejected_mapping。 |
| molecularFormula | P31 `[4,15)` | `C23H28N6O5S` 及“分子式：”匹配；最终 rejected_mapping。 |
| molecularWeight | P32 `[4,10)` | `500.60` 匹配，原文无单位；候选解析为 decimal `500.6`，没有虚构单位；最终 rejected_mapping，另含 quantity_representation_missing。 |
| chemicalNameEn | P35 `[7,157)` | 150 字符英文名称完整匹配，立体/几何构型字符保留；最终 rejected_mapping。 |
| chemicalNameZh | P36 `[3,90)` | 87 字符中文名称完整匹配；字段“中文：”须与前一化学名字段联合理解；最终 rejected_mapping。 |
| casNumber | P37 `[4,7)` | `N/A` 匹配，但不是 CAS 实值；冻结阶段以 attribute_value_missing 拒绝，未进属性核验。 |
| appearance | P38 `[3,11)` | `类白色到白色粉末` 及“性状：”匹配；最终 rejected_mapping。 |

六个非缺失属性的模型原始 predicate facet 全部写 supported，却没有任何 `support` 引文，
只写“属性为 X，在 DrugProduct 类型定义中”。控制器正确将这些判定降为
`predicate:support_missing`、undetermined；最终候选还带有 `field_column_mismatch`、
binding/metric 未通过和 SHACL 未执行。这里逐字核对确认原文字段存在，不能把上述机械拒绝
解释为原文没有属性，也没有独立证明 `field_column_mismatch` 是领域语义否定。
本组最终是 **1 个新增实体、0 个已发布属性、0 关系、6 个 rejected_mapping 属性候选**。

各值的原文局部均为肯定陈述，无条件列表，subject-to-value 方向未倒置；整体主体类型/归属
未因此通过。N/A 门首次在这个运行中实际被验证，不能将第一组未覆盖该情形改写成已覆盖。
模型另以 `fact_eligible=false` 为唯一理由为 10 个背景属性提出 missing 观察，这混淆了本轮
事实引用权限与原文缺失；例如剂型、溶解性和多个明确“否”字段本身都有内容。这些只是发现
输出中的观察，本审阅没有将其视为已发布的缺失事实或布尔归一结果。

本组没有对象关系候选，也没有可验证多谓词关系合批的端点对。公开 revision 129 / event 10
为 8 个实体（含文档根）、2 个已发布属性、6 个拒绝映射候选、0 关系；属性目标增至 196，
实证仍为 2、完成仍为 0。新增数量不能被报告为质量提升。

## 第三组：首次 describes 候选及端点链拒绝

lineage 为 `49da0e1c56be84ced7720e23f061df41b51d7e717e24b61f61fa860b2dcb3285`，
新增审阅发现、核验各一次。区域仍为基本性质 P31–49，只有一条候选：
当前 CMCReport 文档根 `c5b309b1…@1` → describes → 第二组的项目名称 mention 实体
`095ae2bf…@1`。它不是 record 实体，也不是一个包含多个谓词的批次。

模型关系核验的 subject_binding、object_binding、predicate、bridge、qualifiers 和
counterevidence 六个 facet 均称 supported；预冻结工具的 source_binding 和
exact_mention_identity 也通过。最终控制器仍返回 `semantic_outcome=undetermined`、
`complete=false`、`source_assertion_endpoint_chain_not_closed`，没有发布关系。

独立原文检查确认，19 个对象支持引文都能逐字定位。报告对 HRS-1597 基本性质的描述，
在文档根到所述对象这个方向上合理；局部不是否定或计划性描述。但对象的 DrugProduct
类型缺口没有被关系核验修复，`CAS：N/A` 和否定分类字段也不能补强其身份或成品类型，
不能据模型 supported 把整体关系判为实证成立。

链拒绝的具体原因与类型问题不同：

- [source_assertions.py](../../../backend/app/services/extraction/ontology_guided/source_assertions.py)
  的 `document_description` 分支明确允许当前文档根没有正文提及，直接令主体已落地；
  本次 `source_assertion.subject_support=[]` 不是拒绝原因。
- 冻结对象是 P33 `[0,13)` 的“项目名称：HRS-1597”提及；它包含在本次 object_support 中，
  因而对象端点绑定也不是拒绝原因。
- 文档描述分支随后要求 `_covered(predicate_sources, object_sources)`，即谓词引文须覆盖
  提交的**全部**对象角色引文。本次 predicate_support 仅 P33 `[0,13)`，object_support
  却加入 P31–49 的全部 19 个字段；因此只覆盖 1/19，其余 18 个字段触发链未闭合。
  `bridge_support` 虽含全部字段，当前这个覆盖分支不使用它替代 predicate_support。

补查实际持久化输入后，原先仅把这一点归因于“模型把背景属性放入对象角色”不完整。
[来源角色证据附件](entity-source-role-review-20bf2d25.json)确认以下链条：

- 原始 `EntityProposal.mentions` 和已保存 `reference_resolutions.source_refs` 均只有
  P33 `[0,13)` 一个物理提及。
- 该实体 type facet 引用了 14 个字段，referent 引用 2 个，subject_role 引用 1 个。
  当时节点把原始来源和各实体 facet 的支持引文取并集，`GraphNode.evidence_refs` 变为
  14 个；这是**实体类型等 facet 中使用的化学/属性背景**，不是直接复制六条属性候选的证明。
- 实际保留的关系核验输入中，`registered_entities` 的 proposal 仍只有一个 mention，
  `source_refs` 却已是这 14 个字段。`executor.tool_dependencies` 直接从 node 复制这些来源；
  发现指令又要求 object_support 完整覆盖登记的 source_refs。保留的请求是 verification
  阶段；全覆盖发现指令由源码核对，不能声称取得了原 discovery 请求的完整文本。
- 模型的 19 个对象引文覆盖登记的 14 个，又增加 P37、39、40、41、49 五个单元；
  P42 还多包含一个末尾空格。即使没有额外五个，仅按指令覆盖输入 14 个，谓词仍只覆盖
  1/14，机械门仍会拒绝。

因此这不是“必须在正文出现报告名称”的错误要求，也不是缺少一个已命名端点；存在可以
确定修正的**输入来源角色混合**。物理指称、类型辅助证据与关系对象角色不应共享同一个
必须完整覆盖的来源集合。最小修复是节点 evidence_refs 保留原物理提及或全部记录组成，
各 facet 支持仍留在独立决策制品，并由既有决策/依赖引用关联。这个修复不证明 DrugProduct
类型或关系语义成立，也不要求放松 `_covered`。更多调用方影响见
[类型与来源绑定补查](type-binding-review.md#补查首次-describes-的输入来源角色混合)。
本次审阅没有改写运行、冻结证据或将候选补成事实。

最新读取的公开投影 revision 139 / event 11 仍为 8 实体（含根）、2 属性、0 关系。
关系完整度 `0/12`，属性完整度 `0/196`，无关系实线可独立验收。

## 第四组：共线毒理评估表核验未完成

lineage 为 `1b1f54b4ba4fc9feb49025a21158f156131466347698d370f320f8ea1da3da4a`。
本组及后两组的新调用、原文检查与冻结类型定义见
[第四至六组增量证据](candidate-review-20bf2d25-increment-04-06.json)。表坐标采用 IR 的
0 基编号：`table:10`，表头行 0、数据行 1。

第一次发现把 `p1` 同时用作一个记录实体及 10 个属性的 local_id，出现身份键冲突。
第二次发现改为一个 SharedLineAssessmentData 记录，识别 11 个属性：活性成分 HRS-1597、
大鼠 14 天亚急毒试验（试验 1）、NOAEL 200、F1–F5 为 5/10/10/1/1、PDE 100、
来源 19191-25030-NG、OEB 1。值、对应表头与同一数据行均可逐字核对，记录型毒理数据
候选有原文基础；不能把该行药品代号另行当作独立“共线评估数据”实体。

第三次调用的 JSON 可以解析，模型响应状态也是 completed，但仅返回 4 个 target，
其中 NOAEL 的核验 facet 也未完整；并没有完成冻结的一个实体与 11 个属性核验。
最终系统返回 `model_parse_error`，新增节点、属性、关系均为 0。因此这组是执行契约未完成，
不能据部分 supported 结论登记类型或宣称 11 属性已识别。

原表 PDE 列另有明确单位“(mg/天)”，NOAEL 表头只写 NOAEL。核验中已有“从属性定义要求
mg/kg/day”推单位的倾向，但未形成有效最终结果；不能用本体规范单位替代缺失的原文单位。
本组没有对象关系候选，也没有可验收的多谓词关系成员。

## 第五组：N/A 被发现为残留物，核验拒绝

lineage 为 `4699b75fa6d2bd5afca2d91e87660ccb102465cc47907a9c932acb9642c9ec08`，
发现、核验各一次。P79 标题为“3.5.2设备清洗残留物基本信息”，P80 只有 `N/A`。
发现阶段仍提出一个以 N/A 为提及的 Residue，并附三条有关溶解性、温度、溶剂的 missing
观察；核验以 type、referent、subject_role unsupported 拒绝，最终没有新增实体或属性。

原文 N/A 不指称留存在设备或表面的物质，因此拒绝实体类型与指称有依据。核验理由中的
“无数据或无残留”不能被视为同一个结论：该段不足以证明没有残留物。三个属性缺失观察
依赖的残留物主体未成立，也不能计为已证属性缺失或质量达标。

## 第六组：降解条件发生语义错配

lineage 为 `b4cd1148fe8f1df8ba207fa9faea84d1878236d3aab102d5f3564e4adb935955`，
发现一次、核验两次。P96 原文为：

> 本品在酸、碱条件下比较稳定，在氧化、光照、高温均有降解，说明本品在生产和贮运过程中应避免氧化、光照、高温环境。

P97 为“降解产物结构及清洁方法暂未明确。”冻结本体将 AcidDegradation / AlkalineDegradation
分别定义为在酸性/碱性应力条件下**发生的药物降解过程**，不能把测试条件本身等同已发生的过程。

| 候选 | 实际处理 | 独立核对 |
|---|---|---|
| 氧化、光照、高温 → 三个降解子类 | 各短词在 P96 出现两次，未提供 context_text；冻结为 ambiguous_source_quote，未登记 | 字面歧义拒绝有依据，但前半段明确指出这些条件有降解，当前三种真实明示类型没有形成已登记实体。 |
| 碱 → AlkalineDegradation | 两次核验均 supported，实际发布 `3ca0c80d…@1` | 原文说碱条件下比较稳定；只有条件词，不能证明碱降解发生。类型/指称层级未通过独立核对。 |
| 酸 → AcidDegradation | 两次核验均 supported，实际发布 `a27d6e5a…@1` | 同上，原文稳定条件不能据后半句的“有降解”转为已发生酸降解。 |
| 降解产物 → DegradationPathway | type undetermined，未登记 | 产物与途径不是同一层级；结构和清洁方法尚未明确，不支持把该词确认为已表征降解途径。 |

第一次核验理由尚复述“酸/碱条件下比较稳定”，却只因有酸/碱条件而支持降解过程类型。
第二次核验理由改写为“酸…条件下…有降解”和“碱…条件下…有降解”，把另一分句的谓语
转移给稳定条件。支持引文虽真实，语义解释发生条件错配；这不是由原文完全缺失导致的未决，
也不是可以靠增加 supported 计数解释的改善。

发现输出中还出现“在光照”“在...高温”“在...碱”等不是原文连续文字的 missing 观察引文。
这些不能当作可回到原文的证据。本组最终 `complete=false`，原因包含
`ambiguous_source_quote` 和 `type_not_supported`，但仍发布了酸、碱两个未经独立接受的节点。
两者均为文档局部 mention、unreviewed，没有关系实线。

本次公开快照 revision 198 / event 14 为 10 实体（含根）、2 属性、0 关系；关系完成
`0/12`，属性完成 `0/202`。新增的两个降解节点是明确的语义质量问题，不能计作识别进步。

## 第七组：酸/碱降解关系未落图

lineage 为 `b05af9879f3fbf9e2739a0d8bb2096a88908ea9c7e49d5c78a958d936a26dd05`，
发现一次、核验两次。证据仍为 P96。r1 指向酸和碱两个节点，却没有 object_support，
冻结为 `source_assertion_object_set_mismatch`；r2 仅指向碱节点，predicate_support 也只有
“碱”这个条件词。两者都属于 hasDegradationPathway，并非多谓词合批。

第一次核验重复返回同一 target，仍将“均有降解”解释为涵盖碱，甚至以“摘要确认”支持。
修正核验最终对 object_binding、predicate、qualifiers 给出 undetermined；控制器拒绝落图，
原因还包括“本品”重复出现导致的 subject_binding 引文歧义。该组新增实体、属性、关系均为 0。
“本品”指药品而非报告，文档根桥允许文档描述对象，不能据此声称药品和文档指称相同。

独立判断不需要断言稳定就等于零降解；关键是不能从“比较稳定”推导酸/碱降解已经发生。
原文明确“有降解”的条件范围是氧化、光照、高温。本组将这些实体“未登记”记作 missing，
同样混淆了识别状态与原文缺失。

## 第八至十组：范围说明、封面字段与毒性表

这三组及第七组的逐调用/原文证据保存在
[第七至十组增量证据](candidate-review-20bf2d25-increment-07-10.json)。

| 组与 lineage | 原文及输出 | 独立结论与最终状态 |
|---|---|---|
| 8：`5777cf3c…` | P74：“（包括中间体、中间态 以及成品的存放条件、包装方式、有效期/复测期）”。1 次发现，没有实体，只有 unknown 观察。 | 该句是范围说明，不提供具体存放条件、包装或期限。record_undetermined 与局部原文相符；不能扩写为全文不存在存放信息。保存的空 verification 不算额外模型调用。 |
| 9：`cb1f1879…` | 2 次发现、1 次核验；将 P8 封面编号 `DP-C-PI-S-X2419 2601 04` 提为 ClinicalSampleProductionPlan，把 P20“时间：2026年05月”提为 plannedProductionDate，并提出 e1→e1 共指。 | 引文真实，但报告封面编号/时间不足以证明独立生产计划及计划生产月。标题“临床备样生产信息”不能自动赋予封面日期 planned 语义。最终 reference_binding/type/datatype 等拒绝，0 登记、0 属性、0 关系。 |
| 10：`a9927fb6…` | 1 次发现、1 次核验；从 `table:11` 毒性表提出以 HRS-1597 为主体的 ProductionRiskAssessment 记录及以 `×` 为提及的同类实体，试图共指到 `unknown`。 | `×` 位于基因毒性列，是阴性状态，不是风险评估实例；产品名也不独自证明生产风险评估类型。记录组成覆盖、类型/指称/主体角色及无效端点均拒绝，0 登记、0 属性、0 关系。 |

第九组的值引文还包含“时间：”字段标签，最终有 datatype_mismatch；修正日期格式也不能
自动补齐主体和日期角色证据。模型以缺少全部类描述字段拒绝类型，不应被误读为这些字段
都属于本体必填要求；独立拒绝依据是当前引文没有证明所选对象类型及计划日期角色。

第十组 P117 备注明确：“×”代表没有相应毒性，“—”代表相关研究数据不充分。数据行中
生殖发育毒性、致癌性、致敏性为“—”，基因毒性为“×”；未把缺数据当成无毒。
模型观察还自行构造 `ProductionRiskAssessment#...` 字段 IRI，并将不包含符号的表标题
作为 context_text；这些不构成合法已识别属性或有效来源上下文。

公开快照 revision 279 / event 20 仍为 10 实体（含根）、2 属性、0 关系；关系完成
`0/12`，属性完成 `0/202`，没有新增关系实线。

## 第十一至十四组：评估问题、错引与风险类型混淆

本增量新增审阅 6 次调用，证据见
[第十一至十四组增量证据](candidate-review-20bf2d25-increment-11-14.json)。

| 组与 lineage | 原文与最终结果 | 独立核对 |
|---|---|---|
| 11：`eb16bbd1…`，发现/核验各一次 | `table:8` 第 2 行问题为“曾经发生过的反应异常事件，若有，请描述。”，回答为“否”。候选将问题提为 SafetyRiskAssessment；type 拒绝，0 登记。 | 问项不是已发生异常事件或评估实例。拒绝有依据；referent/subject_role supported 只能描述问项主题，不能证明实体事实。观察中的 example.com 谓词也不是合法属性。 |
| 12：`1c0af9d6…`，发现一次 | 将多级毒性表标题拼接成一个 ProductionRiskAssessment 提及，却绑定到 P117 符号说明；另将表标题挂到“致敏性”表头。source_excerpt_mismatch，0 登记。 | 两处都非所引 evidence_id 的原文，拒绝正确；不是毒性信息在报告中缺失。 |
| 13：`17d5a7ff…`，发现/核验各一次 | P88“通过反应安全风险评估，得出以下结论：无危险反应步骤，风险可以接受。”中的“反应安全风险评估”，实际登记为 QualityRiskAssessment：`7e702fdd…@1`。 | 评估实例有原文，但反应安全风险不自动等于质量风险。模型只核对制造环节，忽略 quality 限定，所选类型未通过独立审查。 |
| 14：`1d57fd35…`，发现一次 | P87“安全评估”标题和 P88 结论，只生成 missing/unknown 观察，record_undetermined，0 登记。 | 原文确实存在评估结论；引用权限 fact_eligible=false 或未识别到主体不等于原文缺失。观察把 evidence_id 当主体、自造 isFactual/hasConclusion，不能计为合法本体事实。 |

第十三组还将“安全评估”引用在 P88，实际连续原文只在 P87；共指支持含人为省略号，
因此相应引文/绑定被拒绝。已发布的 e1 虽能定位到 P88，也不能因此接受错误的风险类型。
“无危险反应步骤、风险可以接受”保留为原文反应安全结论，不能扩大为无质量风险或全部风险安全。

公开快照 revision 317 / event 22 为 11 实体（含根）、2 属性、0 关系；完成度仍为
关系 `0/12`、属性 `0/202`。新增风险节点同样不能当作实证质量提升。

## 第十五组与首次 34 调用预算边界

第十五组 lineage 为 `d34fbe0668217ba00b6a758e03ecaff17642d8c62643cb060a1a896b6a393a3f`，
发现一次、核验两次。P52“工艺描述”和 P53“3.1.1合成路线图”被提出为两个 SynthesisRoute
实体以及 processBasis、processDescription、processName 三属性。标题引文是真实的，
但不能同时充当完整工艺描述和具体工艺名，也不证明应新增两个路线实例。

第一次核验将同一 target 拆成多条，重复返回且有相反 field_role 判定；第二次仅返回
3 个 target，属性 facet 仍不完整。最终 `model_parse_error`，新增实体、属性、关系均为 0。
核验中的类型未决有局部证据依据；本审阅没有由模型只见标题推断全文不存在路线图或工艺正文。

第十六组 lineage 为 `07b11ded29ac54db00f87e1e01961fc903e69e5338588079f984206bf7048029`，
在第 34 个模型调用之后遇到预算暂停。两组证据保存在
[第十五、十六组及预算边界](candidate-review-20bf2d25-increment-15-16.json)。

该组发现来自 `table:6` 数据行 4（0 基）：设备名称“离心机”、编号“CT64610”、用途“甩滤”。
这种同一设备记录比此前属性值、问题标题的实体化有更明确的局部类型和归属依据；没有提出
全局 identifier claim。另一个候选将“设备用途以及工艺要求／甩滤”提为 CleaningEquipment，
缺乏施加清洗介质或清洗作用的依据，不能把需要清洗的生产设备自动当作清洗设备。

第 34 号核验回执给离心机三个 facet supported、给 CleaningEquipment 的 type unsupported，
但后者缺少 referent、subject_role。此时没有保存有效 verification 或最终 outcome，
当前工作仍为 active、outcome_ref=null。因此离心机尚不能计为已登记或完成识别。
600、材质、主要残留、丙酮、分子式及分子量 500.18 等有原文字段，却因“本阶段未映射”
被标为 missing；这同样不是原文缺失。500.18 应按该表引用保留，不能按基本性质中的
500.60 擅自改写，归属与一致性仍待属性核验。

预算暂停时公开 status 为 paused、revision 333 / event 24，34 次调用、0 未确认调用，
停止原因为 `execution_model_call_budget_exhausted`。**已有 15 组最终 outcome（32 次调用），
另有 1 组的 2 次调用等待处理；两种完成状态分开计数。**这一边界未出现关系实线或真实
多谓词关系批次，不能用已有模型 supported 数量补成验收通过。

## 继续后：第十六组最终未登记

继续后的只读快照新增了第十六组最终 outcome，见
[第十六组继续后的最终回执](candidate-review-20bf2d25-increment-16-final.json)。
没有新增模型回执，系统将该组结束为 `semantic_outcome=not_checked`、`model_parse_error`，
新增实体、属性、关系均为 0。此时公开运行已为 running、revision 340 / event 26，调用数仍为 34。

所以具有较明确原文依据的离心机候选最终也没有完成登记。先前保存的两个模型回执不重复
计为新调用，原始 type supported 不能用作已获得真实设备端点或已满足关系合批前置条件。

## 最终暂停：37 次调用，关系实证仍未通过

运行在 `2026-09-21T05:47:42.791735Z` 最终暂停，公开 revision 359 / event 29，
`stop_reason=operator_pause`、`completion=incomplete`，没有未确认调用。
最终全量调用键、outcome 清单、图谱摘要及最后三次调用原文核对保存在
[本运行最终审查证据](candidate-review-20bf2d25-final.json)。

第十七组 `15f22abb…` 新增发现/核验各一次，再次提出文档根 describes 项目名称节点。
它以 P34“项目代码：HRXS-P-2419”为主体/谓词支持，以 P33“项目名称：HRS-1597”为对象支持。
模型 predicate 未决、qualifiers 没有支持引文，控制器也指出两处不同段落锚点的端点链未闭合，
最终 0 关系。报告描述关系不必机械要求原文含有“描述”动词，但项目代码本身不等于报告身份，
提交的证据角色也不能修复既有 DrugProduct 对象类型问题。这仍然只有一个谓词、一个对象。

第十八组 `347a80aa…` 只有最后一次发现回执，未保存冻结 discovery 或最终 outcome。
回执实体/属性/关系为空，观察涉及 P88 安全结论以及冷却失效、气体产生等安全问项；
问项有原文不等于风险事实已经发生。观察使用未定义的 e1/unknown 主体和自行构造的
has-assessment-* 谓词，不能视为已识别事实，也不能把这次空输出算作区域分析完成。

最终核对结果如下：

| 验收项 | 本运行实际结果 |
|---|---|
| 模型调用 | 37 次：20 次发现、17 次核验，逐个不可变结果键均已审阅。 |
| 最终处理结果 | 17 组有 outcome；第十八组只有一个尚未应用的发现回执。 |
| 图谱实体 | 11 个含文档根：7 个 DrugProduct、2 个酸/碱降解实体、1 个 QualityRiskAssessment。十个非根实体的类型、指称或实例粒度都有前述独立审查缺口，不能当作十个正确识别样本。 |
| 图谱属性 | 发布 2 个；字段/值可回原文，但主体身份/类型仍不足。另有 7 个属性候选，不能计为已证事实。 |
| 关系实线 | 0 条；无法验收虚拟关系经证据验证成为实线的完整正向过程。 |
| 真实多谓词批次 | 未完成。已执行关系发现分别是两组 describes 和一组 hasDegradationPathway，每组单谓词。 |
| 完整度 | 关系完成 `0/12`，属性完成 `0/202`，待展开 21；运行未完成。 |

本轮实证发现了类型/指称混淆、稳定与降解条件错配、N/A 和符号被实体化、观察与原文缺失
混淆、引文歧义以及核验输出集合不完整等具体问题。它证明这些路径确实执行并暴露缺口，
没有证明识别精度提高或关系实证验收通过。这里是事后原文复核，没有固定盲评金标，不据此
给出全文 precision/recall；工程测试和界面运行结果也不能替代事实质量验收。

## 审查边界

本文件及附件冻结上述 v3 运行的最终暂停结果。剩余设备/生产/清洗范围与全文召回仍未评分；
新输出契约下的新运行应使用独立审查文件，不能把后续修正回写为本次已经通过。
未修改本运行状态、证据或图谱，也未覆盖 `8b97fa57` 的历史审查。
