# HRS-5592 跨记录互补证据独立审查

日期：2026-09-10。状态：**本轮 16 项实际派发任务已全部审查；结果未达到关系/属性完整识别要求**。

补充原文确实进入了部分关系证明，但所有已执行属性仍为未决或不支持，有效属性为 0。
本轮暴露出三类不同问题：对象提及与类型混淆、字段/条件角色误读，以及模型选用的桥接
不符合程序契约。单纯扩大检索范围不能解决后两类问题；应保留各层判定，避免把程序拒绝
误报成原文没有该事实。

## 审查边界

审查对象为 `/tmp/ontology-joint-evidence-hrs5592-20260910-r2` 的冻结 `source.docx`、
`ir.json`、`ontology/`、`runtime/`，以及随后产生的 `tasks/`、`graphs/` 和计时事件。
原件 SHA256 为 `2c1174bf616f30dd28c655fef4261c167762de807c8ad79e148fb8ef18e16436`。
下文 P、table、row 均为 IR 的零基索引；`E1` 等模型短引用仅在对应任务内有效。
独立审查未修改运行输入、提示、本体或模型输出，也未调用模型。

本实验是人工指定原文位置的诊断干预。current 已有字段组、表头及局部主体绑定；
joint 追加的原文保持 `fact_eligible=false`，不授予新对象或值的抽取权限。
它测到的是现有识别适配器和证明门对上下文变化的响应，未测自动检索、生产状态保存、
完整执行器的冲突失效/重验，也不能给出全文提速或正式 precision/recall。

运行 `joint-5081e1e747494f538e73528d3870a1cf` 正常结束，停止原因
`registered_tasks_finished`，无技术失败。原预登记最大可达 26 个案例/组任务，
实际派发 10 个父关系任务和 6 个属性任务，共 16 项、32 次本机模型调用；
其余 **10 个属性任务因父关系无效而未派发**，不是被成功验证或因预算耗尽省略。
它们包括产品 current 两项、计划两组各三项、路线两组各一项。

识别共 829.271 秒；其中发现 16 次累计 300.353 秒、核验 16 次累计 526.223 秒，
上下文装配累计 0.897 秒。父关系有效投影计时累计 0.004869 秒，但不含属性投影及
生产状态持久化，不能替代数据库开销。总实验耗时 832.390 秒。
任务语义状态为 4 supported、8 undetermined、4 unsupported；supported 是程序结果，
不能据此计算业务准确率。上述计时来自本轮事件与任务制品，没有与 r1 或历史 SQL 计时相加。

## 分案例审查

### 产品名称：补充证据确有贡献，但没有解决实体与属性问题

| 项目 | current | joint |
|---|---|---|
| 原文目标 | P33 `项目名称：HRS-5592` | 同一目标 |
| 已有上下文 | 19 片段，包含 P31–P49 字段组（不含目标的重复副本） | 同左，另加 P25/P26/P27，共 22 片段 |
| 装配计数 token | 7077 | 8946 |
| 发现/核验耗时 | 18.162 / 26.237 秒 | 43.356 / 113.031 秒 |
| 单任务耗时 | 44.745 秒 | 156.632 秒 |
| 模型候选 | 1 个 DrugProduct | 同一名称形成 9 个类型/极性候选 |
| 语义与程序结果 | 未决；0 条有效边 | 2 条 affirmed supported 边、1 条 affirmed unsupported 边、6 条 negated supported 边 |
| 系统有效投影 | 空 | DrugProduct 与 ClinicalTrialDrug 各 1 个同名实体、各 1 条 describes |
| 独立业务判断 | 未决理由存在任务理解错误 | 补充原文被用于证明；同提及重复建实体、本体类型张力及否定关系语义仍待解决 |

来源：`tasks/01-product_name-current/`、`tasks/02-product_name-joint/` 和
`graphs/product_name-{current,joint}.json`。

current 核验认为 P33 是名称而非“existing DrugProduct entity in the ontology”，因此拒绝确认。
文档抽取不要求该实体事先作为本体实例存在，这个理由不能当成原文确实不足的业务判据。
joint 的 DrugProduct 和 ClinicalTrialDrug 核验均将新增 P25（任务内 E20）列入
`type_support` 与 `predicate_support`，因此可以确认新增证据进入了实际证明，
不只是占据上下文。但其 ClinicalTrialDrug 理由又把 P26 的“用于临床I期试验”写进
仅指向 P25 的解释，仍需分清实际逐字引用与模型叙述。

P25 明确写“规格为原料药”“属于肿瘤药产品”“目前项目处于I期临床阶段”，P39 又有
“制剂剂型：片剂”。冻结 `slpra-drug.ttl:20` 将 DrugProduct 解释为 finished dosage form；
`slpra-drug-development.ttl:1012` 的 describes 注释则允许 drug product / drug substance，
但 range 仍是 DrugProduct。不能以菜单允许及证明门通过，宣布 API/DrugProduct 解释张力已消除。
同一 P33 提及被按 DrugProduct 和其子类 ClinicalTrialDrug 建为两个实体，也不能算两个独立药物。

joint 发现阶段还提出“取得 IND 批件⇒CommercialDrug”，核验已正确拒绝该候选。
另六条否定候选未进入有效图，但不能因此忽略：

- P42“是否存在细胞毒性：否”是对产品性质/分类的否定，不能直接改写成报告“不描述”一个
  已正向定型为 CytotoxicDrug 的实体。HighActivityDrug 等否定分支有同样语义风险。
- BetaLactamDrug 的否定核验只引用 P46“是否是青霉素类药物：否”。非青霉素不能推出
  非所有 β-内酰胺药；核验理由自己也承认这个推断不必然成立，结构化 verdict 却仍为 supported。
  这是实质性误判，不能记作“否定对照通过”。

### 产品属性：原文、归属已进入证明，字段角色仍未决

current 没有有效父边，两项子任务均未派发，状态应为 `parent_not_effective`，不能记成属性失败。
joint 按预登记的源位置/稳定身份排序选择 ClinicalTrialDrug 父实体
`1c7d354950dbdfe12e02fea07b6cce364402af72a6f22370daf8e1612d091ee4`。

| 子任务 | 原文及值 | 发现/核验/总耗时（秒） | 模型判定 | 程序与投影 | 独立业务判断 |
|---|---|---|---|---|---|
| projectName | P33；`HRS-5592` | 13.547 / 24.770 / 38.433 | predicate、bridge、subject_binding supported；role undetermined | field_role 未决，属性未进入有效图 | 名称和值在原文中明确；父实体类型解释仍是上游问题 |
| appearance | P36；`类白色固体` | 14.970 / 23.135 / 38.251 | 同上 | field_role 未决；local_coreference supported；有效属性 0 | 是产品字段组的性状，未误挂 A14 中间体性状 |

来源：`tasks/03-product_name-child-1-value-joint/`、
`tasks/04-product_name-child-2-value-joint/` 的输入、原始核验与 `decision_payloads`。
属性路径不创建 type decision；不能把返回的 `type_verdict=undetermined` 单独说成程序阻断原因。
实际阻断是 `field_role=undetermined`。projectName 的模型理由还称“对象类型未在原文中显式定义”，
反映它混淆了属性字面值与关系实体，但改进必须针对任务语义并保留真正的归属检查。

### 肿瘤药/细胞毒混淆：否定影响了拒绝，但未留下结构化反证链

| 项目 | current | joint |
|---|---|---|
| 目标及增量 | P25 产品简介 | P25，另加 P33–P49 字段 |
| 发现/核验/总耗时（秒） | 16.346 / 33.125 / 49.596 | 24.250 / 37.776 / 62.300 |
| 候选 | `属于肿瘤药产品`→DrugProduct；`目前项目处于I期临床阶段`→ClinicalTrialDrug | `属于肿瘤药产品`→CytotoxicDrug；`HRS-5592项目`→DrugProduct |
| 模型语义 | 两候选各项 supported | 两候选 bridge unsupported，type/role/predicate undetermined |
| 程序与有效投影 | 两条 supported 有效边 | 两条 unsupported；0 条有效边 |
| 独立业务判断 | 把同一产品的性质和状态句错误实体化；不能算两项正确产品识别 | 正确拒绝 CytotoxicDrug，但也拒绝产品提及；不能算产品抽取成功 |

来源：`tasks/05-cytotoxic_confusion-joint/`、`tasks/06-cytotoxic_confusion-current/`。
joint 核验理由明确提及新增 P42（该任务 E11）的“是否存在细胞毒性：否”，
说明否定信息参与了判断；但 `counterevidence_support=[]`、
`counterevidence_verdict=undetermined`，没有形成引用该原文的结构化反证链。
这与产品名称 joint 中将 P25 写入 type/predicate 引用的证据强度不同。

current 根本未提出 CytotoxicDrug，joint 则提出并拒绝了它。两组的候选集合不同，
因此不能报告为“同一细胞毒断言从错误挂接变为正确拒绝”的严格配对因果证据。
joint 对 `HRS-5592项目` 的拒绝又要求 productIdentifier 或已有 ontology 实例，
重复出现了把新提及识别误解为查找既存实例的问题。

### 生产计划：模型认可后仍被结构契约挡住

| 项目 | current | joint |
|---|---|---|
| 原文目标 | P26 完整生产计划段 | 同一目标，另加 P25/P27 |
| 发现/核验/总耗时（秒） | 10.944 / 18.183 / 29.212 | 11.424 / 19.148 / 30.682 |
| 候选 | ClinicalSampleProductionPlan；用整个 P26 作对象引用 | 同一实体引用、同一实体 ID |
| 模型语义 | type/role/predicate 未决，bridge unsupported | type/role/predicate/bridge/applicability 均 supported |
| 程序判定 | unsupported，structural_valid=false | undetermined，model_supported=true，structural_valid=false |
| 系统有效投影 | 0 条父边 | 0 条父边 |
| 子属性及日期反例 | 父边无效，均未执行 | 父边无效，均未执行 |

来源：`tasks/07-production_plan-current/`、`tasks/08-production_plan-joint/`。
P26 本身明确记载计划生产月份、车间、批次数、用途和批量，属于独立于产品类型张力的
业务阳性案例。current 以未命名独立计划实体为由拒绝，不能证明该文档没有生产计划。
joint 的 type/predicate 引用仍只有 P26，没有正式引用新增 P25/P27；不能把模型 verdict
变化归因为“新增片段补齐了缺失事实”。单次上下文变化也可能改变任务理解或生成结果。

joint 在语义决策均通过后，仍为 `structural_valid=false`。
该候选选择 `bridge_kind=document_subject_description`，但当前桥接注册契约仅为
describes 开放此种桥接，hasProductionPlan 的通用策略不允许它。这是桥接选择/契约不一致，
不是继续缺少跨段证据。冻结 `runtime/app/services/extraction/ontology_guided/verification.py`
的策略及 gate 分支与该 proof 对照，命中 `bridge_kind_forbidden_for_predicate`；
不能仅依据输出中的笼统 `candidate_undetermined` 隐去这个具体原因。

两组计划父关系都没进入有效图，因此 `plannedProductionDate`、`plannedBatchCount`
以及 P19 错误文档日期反例均没有真实模型结果；**不能宣称本轮已经验证日期误挂被拒绝**。

### 设备：新增操作原文进入证明，已有表格也可建立关系

joint 的 `1000L反应釜`→Reactor 候选通过语义、结构和有效投影，另一候选
`反应釜需要密闭性好，用于金属催化反应。` 被核验正确识别为要求描述而拒绝。
该任务发现 25.775 秒、核验 43.038 秒、总计 68.987 秒。
来源：`tasks/09-equipment-joint/`。

这里的新增关系证明确实来自辅助原文，但要按实际输入表述：选择 table0 row12/13
后，`RecordIndex.source_units` 同时带入 table0 row0 的“投料1”及操作正文，joint
实际比 current 多 6 个片段。核验 `predicate_support=E29` 指向的是 **table0 row0**，
evidence `460b5549e9884fbaaaa63c873a0acecd90a26f60ee0311cfffd2ecde9df2ae7b`：
“于1000L反应釜（RE64615/RE64215）中加入230kg的1，4-二氧六环……”；
不是登记行 row12/13 本身。该辅助段有明确设备类型及相同编号，能支持同一设备的实际使用。
目标名仍来自 table9 row1，辅助段均为 `fact_eligible=false`，没有新增端点权限。

current 的同一 `1000L反应釜`/Reactor 候选也通过全部门控，实体 ID 与 joint 相同。
其发现 21.597 秒、核验 30.576 秒、总计 52.317 秒；`predicate_support` 引用同一目标行的
“SM5592-A14制备”（任务 E14）。结合现有岗位、设备名称/编号/用途表头及对应行，这一
关系在原文中可成立，新增操作原文提供更直接证明，但本例没有从未决到有效的父关系收益。
这也说明设备 current 不能称为仅有一个孤立设备词的上下文。

两组父实体相同，因此设备属性可按同一主体作定向比较：

| 子任务 | current | joint | 独立核对 |
|---|---|---|---|
| equipmentID | 发现 15.590、核验 24.684、总计 40.426 秒；5 个语义 decision 全 supported，但 structural_valid=false，未决 | 发现 16.342、核验 26.431、总计 42.917 秒；field_role 未决，其他 4 项 supported，structural_valid=false | 两组均抽到原文 `RE64615/RE64215`，并保留斜杠原值；该值属于设备编号列 |
| modelSpecification | 发现 14.070、核验 21.253、总计 35.433 秒；bridge unsupported，role/predicate 未决；模型 subject_binding supported 但未引用主体，程序 local_coreference 降为未决；structural_valid=false，最终 unsupported | 发现 21.483、核验 35.345、总计 56.998 秒；两候选 field_role 未决、structural_valid=false，最终均未决 | `1000L` 是规格型号列；current 错称它在搅拌体积列，joint 第二候选又把设备编号 `RE64615/RE64215` 错配规格型号表头 |

来源：`tasks/10-equipment-current/` 至 `tasks/14-equipment-child-2-value-current/`。
两组编号发现均选 `adjacent_only`；joint 型号发现选 `same_document`，均不是现有
证明门接受的独立谓词桥接；冻结 gate 对其检查同时命中
`retrieval_hint_is_not_predicate_proof` 和 `bridge_kind_forbidden_for_predicate`。
不能把上述 structural_valid=false 都归成模型语义否认或没有证据。
joint 型号还将设备名放进 `condition_support`，程序据此把两个候选改为 conditional；
设备名在此用于主体归属，原文没有相应生效条件，这是另一项角色混淆。
这些错误候选被门控挡住，但只看有效图会漏掉错误原因及额外调用成本。

### 路线：current 已有步骤上下文，joint 未形成完整联合证明

| 项目 | current | joint |
|---|---|---|
| 原文目标 | P53 `3.1.1合成路线图` | 同一目标及同一实体 ID |
| 实际上下文 | 24 片段，已含 P57/P61/P64/P68 步骤标题和各工艺表部分操作行 | 29 片段；仅 P123–P127 五片段是实际新增内容 |
| 装配计数 token | 8699 | 13190 |
| 发现/核验/总耗时（秒） | 15.173 / 23.054 / 38.344 | 17.326 / 26.439 / 43.937 |
| 模型语义 | bridge unsupported，type/role/predicate 未决 | type/role/predicate/bridge/applicability 全 supported |
| 程序及有效投影 | unsupported，structural_valid=false，0 有效父边 | undetermined，model_supported=true，structural_valid=false，0 有效父边 |
| 工艺描述属性 | 父边无效，未派发 | 父边无效，未派发 |

来源：`tasks/15-route-current/`、`tasks/16-route-joint/`。current 的核验理由称
“标题本身不足”，但实际输入已有步骤标题和操作片段，不能照抄为“标题单独失败”的
实验结论。joint 同样选择 hasSynthesisRoute 不允许的 `document_subject_description`
桥接，模型赞成并未通过结构契约。

joint 的类型和谓词正式引用均仅有 E25，即 P123“总工艺：”；理由虽然提及
E26–E29 的四段合成摘要，却未把这些正文列入 type/predicate 引用。
只有“总工艺：”加目标标题，不能作为完整步骤链或 P53 图示对应某个整体路线的完整证明。
应区分“输入中有足够可调查信息”和“该候选已经给出充分、可回放的联合证明”。
本轮没有构造有效路线、hasStep/nextStep 链或 processDescription 属性，也未测试完整合成顺序。

## 结论及解释限制

| 观察 | 可以得出的结论 | 不能据此声称 |
|---|---|---|
| 产品名称 joint 引用新增 P25，产生两个有效边 | 装配器允许辅助原文参与类型/谓词证明 | 两个同名实体都业务正确、API/DrugProduct 问题已解决 |
| 设备 joint 引用新增实际操作原文 | 跨记录同设备原文可以形成更直接的关系证明 | 原有表格不足，或 joint 使该关系从失败变成功 |
| 计划/路线 joint 语义支持但结构失败 | 桥接选择与程序契约不一致会阻断有效父边及属性派发 | 再多召回几个段落必然解决 |
| 产品属性字段角色未决，设备属性另有错列/非法桥接 | 需要区分实体端点、字面值、表头映射、主体与条件角色 | 属性缺失都因为没有抽到值或不知道主体 |
| 细胞毒否定出现在 joint 拒绝理由 | 模型注意到了新增否定字段 | 已获得可回放结构化反证，或全面通过否定质量门 |

本轮每个任务只执行一次，输入变化也改变了部分候选集合；产品子属性只有 joint 派发，
设备子属性才是同一父实体的双组比较。没有专家参考、重复采样、自动召回对照或生产
冲突重验，不能报告正式准确率、稳定因果收益、全文提速倍数或生产完成度。
没有有效父边的 10 个属性任务，尤其两组 P19 错日期反例，始终保留为未执行。


## 原文核对要点

| 原文位置 | 可核验内容 | 使用边界 |
|---|---|---|
| P19，evidence `b218ea74973afc8f393ed40e938e65154b5e352ac9a99df19971039df29fc163` | `时间：2026年07月` | 文档日期，不能当计划生产时间 |
| P26，evidence `35b4f5d593ad2e86b491943dc9494dd6d03bba9671f779c9982282cd50b8ee3d` | 2026 年 08 月、计划生产 1 批、临床 I 期用途 | 本段自身已足以构成生产计划阳性对照 |
| P42，evidence `453da29a3769da785bf18095f78ff6dd9b695893e2b7852f462c510bbc60b613` | `是否存在细胞毒性：否` | 肿瘤药不是细胞毒药的充分证据 |
| table9 row1 | `1000L反应釜`、`RE64615/RE64215`、`1000L`、`搪玻璃` | 编号原值应保留；斜杠不证明全局设备身份；搪玻璃不可自动当 BorosilicateGlass |
| table0 row13，evidence `81a0345fd0247e364ce71828c5cbea353e4d7627191d6e6acc450c5fd873d6ce` | `向1000L反应釜（RE64615/RE64215）中加入800L的纯化水……` | 与目标设备的实际操作语义匹配；区别于 RE61104 的 100L 高压反应釜 |
| P53 | `3.1.1合成路线图` | 标题可提供定位，不能替代完整步骤链证明 |
| P57/P61/P64/P68 | A14、A15、HRS-5592 粗品、HRS-5592 成品的合成标题 | P64 与 P68 原文均为“步骤3”，不得自行修成步骤4 |
| P123–P127 | 总工艺及 A14、A15、粗品、成品工艺摘要 | P124 只描述 A14 合成，不能冒充整条路线的完整工艺 |

所有判断均以冻结原文和本体为依据；没有专家金标，不给正式准确率。
