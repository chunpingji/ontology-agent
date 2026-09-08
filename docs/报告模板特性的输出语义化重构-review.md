# 报告模板特性的输出语义化重构评审

复审日期：2026-09-06。

评审对象：[报告模板特性的输出语义化重构.md](报告模板特性的输出语义化重构.md) 的 2026-09-06 修订稿。

复核范围：逐项检查初评 R01～R04 的修订，交叉检查新增契约与存储、接口、迁移及验收的衔接，并核对新增源码、本体和 fixture 证据。

复审基线：分支 `019-evidence-semantic-extraction`，HEAD 为 `ce41f8b75c727d5db4c0343732c0ec1a92f44028`；被评审设计为该提交上的未提交修改。设计文件原始字节 SHA-256 为 `5d5c189deb2ea3904ca8f9a2e4463efb8a0acb7b9e45c07c0de00e773980218c`。初评记录的 `6f28526e87092ab2e78afbef1f69bb204a85ad67` 是包含上述提交的合并提交，已核对祖先关系；两个基线不混用。

**复审结论：R01、R02、R03、R04 均已在设计层面闭合。本轮未发现需要阻断设计进入实施的新问题，建议接受本次修订，按文档的 Phase 0/Phase 1 推进基线冻结和可执行协议建设。**

本结论仅表示修订已经回答初评提出的设计问题。代码替代、数据库规则纠错、实际签署流程、真实业务数据准备及发布验收仍待实施；不能据此标记 V2 已实现、规则已修正或生产发布就绪。

**逐项复审结果**

| 初评项 | 复审状态 | 修订依据及闭合理由 | 实施验收 |
|---|---|---|---|
| R01 · P1：规则输出含无依据声明 | 设计闭合 | §5.6 区分 planned_control、observed_fact、risk_decision，逐声明校验前提；FALSE 不再隐含低风险，postconditions 不证明实施。§10.3 将同一门禁用于 ClaimCatalog、composed 和 assisted。§17.6 增加 R-RA1～5、两条执行链及种子升级的迁移台账；§13 明确独立规则修订存储。 | AC-06、AC-31～AC-33；新规则须完成实际语义审核，不能直接注册旧文本。 |
| R02 · P1：签署与不可变快照衔接 | 设计闭合 | §5.4、§17.2 将当前报告未来签名移出正文 Input。§12.4 定义正文版本、签名、签署快照和封装，固定 hash 边界、签署位置、CAS、幂等、封装冻结及失败恢复；§13、§14 增加对应存储和接口。正文材料状态与签署状态分别计算。 | AC-20、AC-34～AC-35；追加、撤销和恢复均须保持正文及原输入快照不变。 |
| R03 · P1：条件断言消费缺少证明 | 设计闭合 | §7.4 增加 ConditionDefinition、ConditionalAssertionBinding、ConditionEvaluation，区分条件表达证据、关联等价审核和发生证明，规定 TRUE/FALSE/UNKNOWN 的消费边界。条件结果保留极性及计划/实际性质；§9、§11、§14 衔接 DAG、Coverage、缓存、冻结和诊断。 | AC-07、AC-13、AC-30、AC-36～AC-38；不放宽原 Candidate 的正向资格，不产生无条件事实。 |
| R04 · P2：状态传播和 block 范围 | 设计闭合 | §7.5 明确主状态仅作摘要、问题原始影响范围、节点级 block、UseResolution、逐层传播和报告阻断集合；规定确定/未知守卫、部分集合及强制要求的处理。§16 用“规格冲突、编号有效”的例子说明不同消费者的结果。 | AC-05、AC-08、AC-14、AC-39～AC-40；不能仅按父 Input 的主状态调度。 |

以上闭合判断基于以下具体变化：

1. **规则治理已经覆盖事实语义。** [§5.6](报告模板特性的输出语义化重构.md#L279) 明确“有版本和 lineage”不足以证明声明成立；[§17.6](报告模板特性的输出语义化重构.md#L1194) 要求逐条审查原前提、陈述性质、适用范围及业务映射。新增不可变规则修订表也回应了当前 `rule_key` 全局唯一、同一行版本可变的实际限制。规则、Claim、模板和种子升级之间已有一致的迁移边界。

2. **签署不再造成正文自依赖。** [§12.4](报告模板特性的输出语义化重构.md#L719) 将 body_ast 与 final_ast 分开，签署封装引用正文、签署及审核快照；未来签名不回填 ResolvedInput。正文预览与签署工件预览分别固定版本，同时要求各自的预览、下载一致。会话 open → finalizing → sealed 的流程及续会话规则，为并发追加、撤销和封装恢复提供了可实施的边界。

3. **条件求值具备独立可信链。** [§7.4](报告模板特性的输出语义化重构.md#L426) 没有把一个布尔结果直接当作激活依据，而是要求定义、断言关联、参数映射、主体/时间范围及证明共同成立。特别是“条件 TRUE 也不能把计划升级为实际已使用”，避免了只解决逻辑条件却改变业务性质的问题。

4. **状态摘要与使用资格已分开。** [§7.5](报告模板特性的输出语义化重构.md#L455) 对规格冲突的处理是：有效编号仍可消费，整表草稿保留冲突格，实际展示冲突字段会阻止正式发布；未消费且非强制的可选字段问题仅保留诊断。这与共享定义、逐字段来源和独立完整性要求保持一致。

**证据复核及初评表述限定**

| 核查项 | 本次复核结果 |
|---|---|
| 设计与代码基线 | 当前分支和 HEAD 与修订稿 §2.1 一致；已核对 `ce41f8b` 是初评合并提交 `6f28526` 的祖先。设计文件 hash 单独记录，避免仅用 HEAD 标识未提交文档。 |
| 模板 fixture | 重新计算 schema hash，仍为 `78721d33afb40f12e77d2e048b5d613945c3442fafe759256561fbb4bce6b8d1`；结构仍为 3 个 Section、6 个 Group、32 个 semantic Slot、全部 required=false、3 个独立 Prompt。 |
| R-RA1～5 初始台账 | 与 [defaults.py](../backend/app/services/reasoning/defaults.py#L449) 对照一致：R-RA1～4 以前提 hasSharedLineData 存在触发，R-RA5 以前提 describes DrugProduct 存在触发；后件确有能力不足、设备确认、指定供料、已建 SOP 及处置/排放等缺少独立证明的陈述。该核查针对仓库默认定义，不代表数据库中所有人工修改版本。 |
| 规则加载及默认升级 | [snapshot_report.py](../backend/app/services/reporting/snapshot_report.py#L64) 和 [RiskReportGenerator._load_rules](../backend/app/services/reporting/risk_report_generator.py#L686) 均未按 published 过滤。[种子升级](../backend/app/services/reasoning/seed_declarative.py#L267) 只对匹配旧默认 hash 的行原位替换 consequent，人工修改行会跳过；修订稿对此描述准确，不能概括为无条件覆盖所有规则。 |
| 规则版本存储 | [OntologyDecisionRule](../backend/app/models/ontology_meta.py#L345) 的 rule_key 全局唯一，继承的 version 是同一记录上的并发版本；新增修订存储有实际依据，不能把原 version 字段视为不可变历史版本能力。 |
| 条件保存与选择 | [条件提交测试源码](../backend/tests/test_extraction/test_fact_commit.py#L252) 明确断言条件记录保留、positive_eligible 为 false 且不写正向边；[Candidate](../backend/app/schemas/evidence.py#L288) 和 [FactSelector](../backend/app/services/fact_selector.py#L86) 的当前边界与修订稿一致。本轮未运行这些测试。 |
| 补充本体定义 | [processName/processBasis/processDescription](../ontology/slpra/slpra-drug-development.ttl#L1141)、[basedOnSourceDocument](../ontology/slpra/slpra-risk.ttl#L242)、[documentName](../ontology/slpra/slpra-document.ttl#L126) 均存在，所述定义域/值域一致；它们仍只证明 schema 定义。 |
| 数据库与部署状态 | 本次未重新查询数据库、调用部署接口或生成报告。默认来源关联、作业状态、67 个候选以及 R-RA2 published/v1 均保留为初评时的历史观察。修订稿已明确此时间边界，未冒充本轮实时核查。 |

初评中“FALSE 直接映射为低，再映射为可接受”的表述应按执行路径和 postconditions 加以限定。修订稿 §2.3、§17.6 的补充准确，本次采纳该限定：

| 执行路径与条件 | 代码表现 |
|---|---|
| 当前冻结报告，规则 FALSE 且 postconditions 非空 | 控制前为低，控制后与风险状态仍为待评估；不能说该路径必然生成可接受。 |
| 当前冻结报告，规则 FALSE 且 postconditions 为空 | 控制前为低，控制后继承低，状态为可接受。 |
| 旧 RiskReportGenerator，控制前等级已知且 postconditions 非空 | 控制后直接降为低，状态为可以接受；控制前未知时仍保持待评估。 |

依据为 [render_snapshot_report](../backend/app/services/reporting/snapshot_report.py#L256) 及 [旧规则求值/控制后求值](../backend/app/services/reporting/risk_report_generator.py#L697)。R-RA2 默认定义具有非空 postconditions，因此在当前冻结报告中属于第一种情况。它的 control_measure 文本仍会写入风险行，规则求值真假不影响该字段的复制；初评关于无依据肯定句的发现仍成立。

**实施与验收仍需保留的要求**

以下是修订稿已经规定、尚待执行的门禁，本轮不再将其登记为未闭合的设计问题：

- 规则迁移必须覆盖实际数据库修订及预览/报告/种子入口，完成逐声明业务审核和 AC-31～AC-33 的反例验收；增加表或版本字段本身不构成语义纠错完成。
- 条件定义、参数映射、三值逻辑及 UseResolution 必须落实为可执行模型；用跨主体/时间、条件自证、可选字段冲突、未知守卫和开放集合验证 AC-36～AC-40。
- 签署必须验证正文不变、逐值签名来源、CAS/幂等、冻结时追加和文件写入中断恢复，完成 AC-20、AC-34～AC-35；既有结论签批不等于报告内容签署。
- 正式验收仍需真实来源、实际审核提交、独立质量与性能记录及 V2/019 联合退役证据；设计闭合不提前解除这些条件。

本次完成了修订差异与相关源码阅读、本体定义核对、fixture 统计/hash 复算，以及文档的 32 个本地引用、2 个 JSON 示例语法和 AC-01～AC-40 编号完整性检查。JSON 语法检查不表示示例已经通过尚待实现的 V2 编译器。未运行业务测试、模型质量/性能评测、数据库规则迁移或发布验收。

本次仅更新本评审文件，被评审设计及业务代码保持用户修订时的内容。以下保留初评原记录，其中“本轮”“当前”和数据库状态均指初评时点；原四项问题的最新状态以以上复审结论为准。

---

**初评记录（历史；R01～R04 已于本次设计复审闭合）**

初评日期：2026-09-06。

评审对象：[报告模板特性的输出语义化重构.md](报告模板特性的输出语义化重构.md)。

评审范围：设计合理性、引用证据的真实性与边界、关键契约的完整性。

核查基线：评审时工作区 HEAD 为 `6f28526e87092ab2e78afbef1f69bb204a85ad67`；原设计记录的基线为 `1ae442363bc82ba5df030cb783b47f6e16b0447b` 及当时未提交的 019 实现。数据库结论来自本次会话中的只读查询，表示查询时状态，不保证后续状态不变。本次落档未重新执行数据库核查。

**总体判断：架构方向合理，主要现状证据可信；但规则语义、条件断言消费和签署生命周期还需要补齐，才能作为完整实施设计。**

`Binding → Input → Render` 的分工直接对应现有问题：稳定引用解决标签错配，记录列表解决风险矩阵列间错位，冻结输入与输出 AST 解决预览、下载各走一套链路。共享定义、逐值来源、三个独立状态轴，以及承认 LLM 重跑不保证逐字一致，也都值得保留。

以下问题建议优先修订。P1 表示应在相关能力实施前补齐的关键契约；P2 表示应在协议定版时明确的执行语义。这些意见评审的是设计缺口，不表示 V2 已经实现或已经出现对应运行故障。

1. **R01 · P1：规则结果仍可能携带无依据声明，迁移不能只处理 Prompt。**

   设计 §5.5 将风险判断交给版本化规则，§17.3 要求移除 Prompt 中“设备均在确认有效期内”等无依据肯定句，但没有具体处理现存规则中的同类问题。

   本轮只读数据库核查发现：**已发布、启用的 `R-RA2 v1`，前提仅检查存在共线评估数据，输出的控制措施却包含“设备……均在确认有效期内”**。仓库 [defaults.py](../backend/app/services/reasoning/defaults.py#L470) 中的规则定义与此一致：

   ```json
   {
     "rule_key": "R-RA2",
     "status": "published",
     "version": 1,
     "is_disabled": false,
     "antecedent": {
       "op": "some_values_from",
       "property": "hasSharedLineData",
       "filler_class": "SharedLineAssessmentData"
     }
   }
   ```

   其 `control_measure` 包含：“生产使用的设备均按照生产工艺进行了选型、安装及确认，设备、仪器、仪表使用前均在确认有效期内，能够满足{{药物名称/代号}}工艺需求。”该前提并没有检查设备确认记录或有效期。

   另外，[snapshot_report.py](../backend/app/services/reporting/snapshot_report.py#L261) 仍将规则求值 `FALSE` 直接映射为“低”，再映射为“可接受”。这属于报告代码中的业务决定，也应纳入规则语义迁移。

   因此，版本、类型和 lineage 齐全，仍不足以保证声明成立。仅把旧文本迁入规则输出或 ClaimCatalog，会保留无依据声明的入口。

   **建议：** 增加逐条规则迁移台账，明确区分计划措施、已实施事实、风险决定，把等级映射和 Claim 的成立前提纳入审核。至少增加“有共线数据、无设备确认记录”的验收案例，确保确定性输出也不会生成上述肯定句。设计已有的规则版本和输出 schema 要求应保留，同时补上现存规则的语义纠错。

2. **R02 · P1：两阶段签署与不可变快照之间缺少明确的数据契约。**

   设计 §12.4 的“先固定正文、后签署”方向正确。但 §17.2 又把当前内容版本的签署记录作为输入；签署发生时，原输入快照和已完成输出已经不可修改。

   需要明确：签署追加后哪些对象产生新版本、最终预览使用哪个 AST、签名来源如何进入追溯，以及材料状态和签署状态分别依据哪份快照。现有 [ElectronicSignature](../backend/app/models/reasoning.py#L119) 绑定的是推理结论 ID，不能直接充当报告内容 hash 的签署证明；现有 [sign_conclusion](../backend/app/api/compliance.py#L132) 接口也围绕推理结论的生命周期执行。

   如果签署记录仍作为同一次运行的普通 ResolvedInput，追加记录就会与输入不可变约束冲突；如果签署阶段另读最新工作流，则需要显式定义其冻结和追溯边界。

   **建议：** 单独定义“签署工件封装”，引用不可变正文 hash、独立签署记录快照及最终工件 hash，并补上签署提交、并发追加和最终工件生成协议。明确正文预览与带签署证明的最终工件之间的版本关系，避免修改原快照或重新生成正文。

3. **R03 · P1：条件断言“可以保存”与“条件满足后可以使用”之间仍有断层。**

   设计 §7.3 要求条件匹配时允许投影，但引用的 019 实现主要保存条件证据位置。当前 [Candidate.positive_eligible](../backend/app/schemas/evidence.py#L288) 明确排除条件断言；[FactSelector](../backend/app/services/fact_selector.py#L86) 的适用时间检查也只是匹配时间值。

   文档尚未定义条件的稳定身份、条件与输入的绑定、满足条件的证明，以及这些依赖如何进入 Coverage 和缓存。例如“若转产则使用设备 E”，不能因为本次输入出现某个 `true` 就认定条件成立。条件表达有证据，也不等于条件已经发生。

   **建议：** 增加版本化的条件定义及求值结果契约，保留条件断言、满足条件的事实、主体范围和求值规则的完整关联；暂不支持的条件应明确返回未完成。验收应覆盖条件满足、不满足、未知，以及同一条件在不同主体或时间范围下的隔离。

4. **R04 · P2：状态清单完整，但状态传播和 `block` 的作用范围不够明确。**

   设计 §7.3 区分了九种状态，也保留并存问题，这是优点。但 `block` 究竟阻断单元格、整个 Input、OutputUnit，还是正式发布，需要形成唯一协议。§16 中“conflict/invalid 阻断该值”的默认规则，也需要与集合主状态、消费者调度及报告材料状态衔接。

   例如设备记录的可选规格字段冲突，而另一个消费者只需要设备编号：如果只根据记录主状态调度，可能连有效字段一起阻断；如果只看字段值，又可能遗漏应当影响报告的冲突。

   **建议：** 补充“字段 → 记录/列表 → 使用位置 → 规则/输出 → 报告”的状态合成表，以及条件分支、可选字段和部分数据的执行规则。明确哪些问题影响当前字段的可用性，哪些影响整个输出或正式发布；不能依靠各模块自行选择状态优先级。

**证据核查记录**

| 证据类别 | 本轮结论 |
|---|---|
| 指定模板结构和 Prompt | 核实一致：v18、3 个 Section、6 个 Group、32 个 semantic Slot、全部非必需、3 个独立 Prompt；`{{设备}}` 错配及无依据肯定句确实存在。数据库 schema 与仓库 fixture 完全一致。 |
| 本体引用 | 核实准确：`constructedOf` 是对象属性；`projectCode` 是 DrugProduct 的字符串属性；两个会签关系的定义域是 RiskAssessmentReport。它们证明模型定义，不能证明业务实例已经存在。 |
| 当前代码问题 | 基本准确：正式报告消费冻结清单，旧预览仍读取 annotation cache；标签取值、集合拼字符串及业务表格分派仍可找到对应代码。 |
| 默认来源元数据 | 已发生变化：当前 `default_source_job_id` 已有关联，更新时间为 `2026-09-05T13:43:35.603644+00:00`；关联作业仍无发布快照。因此“来源未关联”应更新，“尚不能消费正式事实快照”仍成立。这是基线漂移，不能据此认定历史实测错误。 |
| 019 与历史评审 | 能支撑需求来源和复用方向；任务记录仍有覆盖、报告接入、退役和发布验收未完成项，不能作为新增方案有效性的实测证明。本文对此总体保持了正确区分。 |

指定模板只读核查的定位信息：

- 模板 ID：`dea037a2-f4b5-478a-8e0e-d5471fc45cbc`。
- 名称与文号：风险评估文档 / `QS-A-020F05`。
- 数据库版本与状态：`v18 / published`；schema revision：`v1`。
- schema SHA-256：`78721d33afb40f12e77d2e048b5d613945c3442fafe759256561fbb4bce6b8d1`。计算使用 UTF-8、保留 Unicode、键排序及紧凑分隔符的 JSON 序列化。
- 对照 fixture：[risk_template_dea037a2.json](../backend/tests/fixtures/risk_template_dea037a2.json)，与查询所得 schema 相等。
- 默认来源作业 ID：`19e291e5-f344-42eb-b5e0-ff40ae3e760b`。
- 默认来源文件：`原料药 HRS-5678 临床备样生产信息.docx`。
- 该作业查询时为 `reviewing`，`snapshot_id=null`；候选表中有 67 个待审核实体候选。这些候选不能作为正式报告事实。

上述数据库读取通过 `BEGIN TRANSACTION READ ONLY`、`SELECT` 和 `ROLLBACK` 完成，未执行审核、提交、模板更新或报告生成。实施前应重新保存实际环境的查询结果及 hash，不能直接沿用历史元数据状态。

主要交叉核查位置：

- [slpra-drug-development.ttl](../ontology/slpra/slpra-drug-development.ttl#L1012)：`describes`、`usesEquipment`、`hasProductionPlan`。
- [slpra-drug.ttl](../ontology/slpra/slpra-drug.ttl#L463)：`projectCode`。
- [slpra-equipment.ttl](../ontology/slpra/slpra-equipment.ttl#L280)：`constructedOf`；同文件的 `equipmentID`、`equipmentName`、`modelSpecification` 数据属性。
- [slpra-risk.ttl](../ontology/slpra/slpra-risk.ttl#L274)：`hasAssessmentTeam`、`hasApproverTeam`。
- [ast_templates.py](../backend/app/api/ast_templates.py#L850)：旧行文预览读取 annotation cache 并调用旧评估、叙述链。
- [extraction.py](../backend/app/api/extraction.py#L1402)：后台报告任务消费已冻结清单。
- [snapshot_report.py](../backend/app/services/reporting/snapshot_report.py#L162)：标签索引及集合字符串投影。
- [docx_renderer.py](../backend/app/services/reporting/docx_renderer.py#L533)：业务 Group 类型分派。
- [019 tasks.md](../specs/019-evidence-semantic-extraction/tasks.md#L73)：T040、T042、T044 及后续退役、发布门禁尚有未完成项。
- [019 validation.md](../specs/019-evidence-semantic-extraction/validation.md#L156)：已有回归记录及明确保留的未完成范围。该记录中的测试数量没有在本轮重新运行，不作为本次验收结果。

全项目正则退役的依据是此前明确的需求；当前证据直接支持消除标签取值、业务 finder 和隐式回退，不能单独证明全面替换正则会改善质量或性能。应继续保留独立的退役、质量和性能验收，避免把“机制符合要求”当作“效果已经改善”。

本轮评审进行了文档、源码、本体及数据库只读核查，未修改业务数据，也未执行报告生成与发布验收。本文件用于保存评审意见，不表示原设计已经修订或上述问题已经解决。
