# 五项运行诱因修复与前期验证对比

本次修复面向运行 `d231338c-f96f-436a-b759-85fc5610578c`。结论是实现接线、预算语义和验收范围出现了缺口；前期局部协议验证不能证明全文自主工具抽取已通过。原始调查见[运行诊断](../run-d231338c-20260917/README.md)。本次不修改历史冻结实验结果，不重启或改变原运行。

## 输入差异

只读核对运行冻结源后，发现当前运行使用《原料药 HRS-9267 临床备样生产信息.docx》，497 个原文单元、149 条逻辑记录，文件 SHA-256 为 `fec736f6293d67c9dc2e363db7a83750eaca83ff75e2d01222db4653bdd6b5c1`。前期 CMC Mock 实验文件 SHA-256 为 `e94822808601e73f4ba4c715c664812d4a01e42f0e4e84a6b08a3653b915daf7`，447 单元；并非同一冻结文件，不能直接比较图数量、耗时或 F1。

前期工具案例中的 RE64611 在当前报告中不存在；按已配置 Mock 名称/别名映射重新检查，当前报告有 14 个名称匹配条目，包括 PF64603。匹配只用于选择回归案例，不证明类型、全局身份或关系，不进入引擎领域规则。

## 五项原因、漏测与修复

| 诱因 | 为何前期验证未挡住 | 本次修复 |
|---|---|---|
| 上下文归属异常 | 工具运行时要求所有 evidence 都有唯一 record；实际上下文组装器可合法带入独立标题和共享表头。早期探针遇到标题问题后换合法数据行，未覆盖该组合；冻结样例也不是当前报告。 | 当前记录优先；无唯一归属的原始辅助上下文允许 record_id=null。保留原文、角色、跨度、事实资格；null 不能新增证据或提升资格。检索授权与冷继续共用规则，具体失败码透传。 |
| 输入/输出预算不匹配 | 小样本 Chat 实验使用 enable_thinking=false；Responses 工程 fixture 默认百万输入额度且计数器为 len。先前输出截断推动提高输出上限，但完整请求输入和总上下文未联合验收。 | max_input_tokens 独立限制完整输入，不再先减输出。可选 max_context_tokens 单独约束输入+输出。新运行冻结三项额度；保留完整 Responses reasoning/工具配对，不用删历史项省预算。 |
| 空候选与未完成混用 | Schema/协议通过只检查回答形状；未覆盖“合法空结果→记录覆盖→后续调度”。默认文案称完成核验，但实际 complete=false。 | 合法空声明（无阻断问题，观察为空或仅 missing）返回 record_no_claims、complete=true、semantic_outcome=not_checked；记录检查完成，无事实结论。unknown/ambiguous/unbound 仍未决。检索继续，暂停不提前记作该任务最终搜索反馈。 |
| 候选组合过多、强模型串行 | 在线新工具协议禁用旧 evidence_repair，而稀疏规划又强制依赖它；已测过的摘要/精排不等于候选规划已接通。早期每组只有 3 条记录，未覆盖全文运行。 | 允许新协议复用现有 sparse-candidates-v1 和增量当前状态；默认启用通用本体标签/定义查询，不带旧领域别名。首项候选后接入语义精排。离线薄入口用同样组合；未接纳记录仍是未覆盖，不能当否定。确定性预算/归属失败不原样重试。 |
| 工具启用未形成有效协作 | 早期工具由实验代码显式安排。后来的真实 Responses 工具链已记录 3 个 blocked、0 候选；启用验证只有工具预检，没有自主模型协作成功证据。 | 提示只操作当前谓词，优先对目标原文调用适用工具，引用必须取自已成功返回的 mention_ref/source_ids；独立调用可同批，并提示单对象selection必须为all这一既有契约。缺定义观察与相同 binding anchor 去重；模型仍有4096返回上限，纯控制器必检不受模型返回额度误阻断。 |

本次读取实际 Qwen 服务 `/props` 确认总上下文为 **102400 tokens**，模型别名 Qwen3.6-35B-A3B、并行槽位 2；线上输入配置 32768、输出 20480。旧检查只剩 12288 输入额度，和服务实际容量不一致。`local_llm_max_tokens=200000` 不是该服务上下文容量，不能用它推断。默认未擅自声称 reasoning=none 可关闭思考。

## 前期证据的真实边界

- [Mock 实验](../../cmc-mock-entity-validation-20260916/README.md)：3 记录 × 3 组、18/18 Schema 通过；已经披露漏失与归属误判，未证明全文 F1 改善。
- [摘要检索实验](../../cmc-summary-retrieval-validation-20260916/README.md)：已披露需求组累积、工具 JSON 膨胀及未完成端到端调用，字符预算未覆盖完整请求。
- [Responses 引擎验证](../README.md)：正式 T025/T026 尚未完成；真实工具链4次请求、38519 tokens、工具均 blocked、0 候选。提示修复后当时只有离线测试。
- [工具启用验证](../tool-enablement-20260917/README.md)：GLiNER 实际执行但0提及，Mock 人工查询1候选；这证明配置能加载和工具可调用，不证明 Qwen 自主调用顺序、独立核验与入图。

因此，不应归因为“模型突然变差”。验收从局部样例扩展到全文在线运行时，没有覆盖输入差异、真实预算、调度组合和工具自主协作，且已知失败探针未作为完成验收的阻断项处理。

## 本次验证

- [冻结上下文复查](context-regression.json)：原8项全部重查；取新快照时累计11项，11/11均能读取，0归属异常，0 Qwen调用。只修改辅助归属表达，不绕过原文/角色/事实权限。
- [工程回归](tests.txt)：473 passed、9 skipped，84.64秒；跳过项需要专用 PostgreSQL 测试库，本轮未配置。定向 Ruff 与 git diff --check 通过。沙箱内完整组卡在 TestClient 事件循环；停止该进程后，在相同 SQLite/临时目录隔离 fixture 下解除进程限制重跑通过。没有用历史通过数量替代本次结果。
- [规范检查](design-check.json)：4个JSON制品、10工具Schema、2阶段Schema、22项变异反例通过；使用系统Python的已有jsonschema环境，未安装依赖。
- [初始规划对比](planning-comparison.json)：同一冻结输入、max_hops=1、第一项任务前停止，旧稠密1788项、新通用稀疏63项，0模型请求。未入选记录保留未覆盖；该数字不代表完整运行最终任务数，也不与旧在线2533项直接等同比较。
- [工具结果大小](tool-result-budget-comparison.json)：对历史冻结返回去除完全重复的缺定义观察，25项变1项，4660→3868 tokens，保留全部7个提及及coverage，不改4096上限。本项只用实际tokenizer重新计数，未重新执行GLiNER，不作质量结论。
- [首轮脚本失败](initial-script-failure.json)：2次真实请求在回归脚本的保存回调处失败，合计352.125秒；该脚本误将result_changes一并校验成协议状态，已修正。返回未完成确认，usage未捕获，不补0、不计为引擎质量结果；修正后新身份每例仍最多4次请求，本轮合计上限为10次。
- [真实Qwen回归](real-regression.json)：当前冻结原文、本体及摘要，启用现有GLiNER2.5、Mock、实验词表；两例独立身份，各最多4次请求。实际7次请求、已捕获97262 tokens，加首轮脚本失败2次，本轮共9次请求（另2次usage未知，不并入已捕获值）。模型未调用GLiNER或Mock；禁止将“已配置”计为“自主协作已验证”。

| 真实案例 | 结果 | 请求/耗时 | 本次能证明什么 |
|---|---|---|---|
| 此前合成路线关系记录 | record_supported；1个新节点、1条边，独立核验与适用binding门禁通过 | 4次，692.223秒 | 修复后的真实读取→发现→独立核验→控制器→关系输出可完成；只保存在隔离验证输出，未提交在线图谱 |
| 含PF64603的真实记录 | identity_property_outside_menu；0节点、0边，未完成 | 3次，603.326秒 | 不把模型发明的标识属性带入图谱；单次实际输入达13118 tokens，能在修复后的输入额度内继续；不证明实体/关系召回改善 |

两例首轮均带Markdown围栏，随后还消耗字段纠正请求。针对单对象selection=all和未声明identifier_claims须为空的提示在本轮启动后补入，[追加77项适配器/轮次回归](final-prompt-tests.txt)通过；这些追加提示**未再用真实Qwen重测**，不把当前样本当作它们的实际效果证据。两例均无模型侧工具调用，因此第五项的返回预算/引用提示虽已修正，**Qwen自主工具协作仍未证实改善**。本轮不是完整图F1验收，也不能从人工选定关系记录的耗时推算全文提速倍数。

## 使用边界

新候选策略只进入新运行的冻结配置，不能把旧运行的2533项计划静默改成新策略。当前服务进程没有自动 reload；代码与工程测试通过不等于已部署，也不意味着原运行已重新执行。没有新增数据库表、历史快照或并发执行器。

预算语义参考已查阅的[Responses会话上下文说明](https://developers.openai.com/api/docs/guides/conversation-state)与[推理模型输出预算说明](https://developers.openai.com/api/docs/guides/reasoning)：输入额度、输出额度和服务总上下文容量分别核验，推理属于输出，完整协议项不能靠裁切隐藏。

## 修改入口

- [上下文与授权](../../../../backend/app/services/extraction/ontology_guided/context.py)、[工具运行时](../../../../backend/app/services/extraction/ontology_guided/tool_runtime.py)：辅助归属、具体错误码、去重与模型/控制器额度边界。
- [模型适配器](../../../../backend/app/services/extraction/ontology_guided/tool_model_adapter.py)、[声明协议](../../../../backend/app/services/extraction/ontology_guided/claim_protocol.py)：完整输入预算、工具提示、空候选完成语义。
- [执行器](../../../../backend/app/services/extraction/ontology_guided/executor.py)、[通用候选检索](../../../../backend/app/services/extraction/ontology_guided/heuristic_search.py)、[在线装配](../../../../backend/app/services/document_analysis/execution.py)：稀疏规划、冻结配置及暂停继续。
- [离线薄入口](../../../../backend/app/evaluation/quality_guided_variant.py)、[验证清单](../../../../backend/app/evaluation/ontology_tool_engine.py)、[Spec 027 计划](../../../../specs/027-ontology-extraction-engine-v2/plan.md)：在线/离线策略一致、总上下文配置与规范同步。
