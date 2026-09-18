# 文档运行 eeacc490 长时间无关系诊断

运行：`eeacc490-241a-4df2-87e6-f4ed0c2a22a6`。页面：`/analysis?tab=document&documentRun=eeacc490-241a-4df2-87e6-f4ed0c2a22a6`。

## 结论

主要原因是文档根节点关系的发现表示与最终原文主体校验不匹配，导致模型已经发现并核验的候选关系仍被拒绝；串行、多轮模型执行放大了耗时。数据库权威工作状态和展示缓存均为零关系，足以解释页面现象。

本次只读查询运行、调用账本、冻结输入和结果；使用已保存输入做纯内存校验复现。未修改业务代码、运行数据或配置，未额外调用模型、暂停运行或重启服务。新增本报告及统计摘要；没有执行修复或部署。

## 观测口径

[snapshot.json](snapshot.json) 来自 PostgreSQL `REPEATABLE READ READ ONLY` 一致性事务，时间为 **2026-09-18 02:07:12 UTC**。运行仍在变化。

| 指标 | 观测值 |
|---|---:|
| 创建时间 | 00:57:09 UTC |
| 状态 / 阶段 | running / recognition |
| 自创建经过 | 约 70 分钟 |
| 已有暂停区间 | 01:29:34–01:41:09 UTC，约 11 分 35 秒 |
| 节点 / 关系 / 属性 | 6 / 0 / 0 |
| 节点组成 | 配置的文档根节点 + 5 个识别实体 |
| 已尝试 / 完成检查 / 未完成处理项 | 17 / 7 / 10 |
| 当前计划 / 未尝试处理项 | 71 / 54 |
| 已完成强模型调用 / 在途调用 | 39 / 1 |

处理项是主体、谓词与记录的组合，不等同于独立原文记录数。`supported=5` 对应目前接纳的实体，不能读作已经接纳 5 条关系。7 个完成检查项均为 `record_no_claims`，只表示当前记录未提出声明，不表示全文没有相应事实。

17 个处理项中保存了 16 份语义 outcome，另一个因 `required_relation_validation_missing` 未形成语义 outcome；统计同时读取执行结果，未漏掉该项。

## 1. 文档根节点与原文主体的桥接表示不匹配

根节点由用户选定的 CMC 报告类型创建，实际保存为 `grounding_kind=document_root`、`root_origin=user_specified`、`evidence_refs=[]`。它代表文档，不是正文里某个药品名称、清洗方法或降解途径的提及。

本快照的 10 条已发现关系候选全部使用 `bridge_kind=explicit_assertion`，主体均为文档根节点。部分候选把对象描述、药品名称甚至整段正文放进 `source_assertion.subject_support`。

最终 `validate_source_assertion()` 对显式关系要求：主体引用必须覆盖主体实体自身的原文锚点。文档根节点没有这样的正文锚点，这条路径不能完成绑定。已保存的 9 份含候选语义结果中，7 份包含 `source_assertion_subject_endpoint_unbound`；另有主体证据缺失和事实来源不合格的结果。

代码已经支持 `document_subject_description`，允许用户指定的文档根节点通过文档描述关系连接对象，仍核验对象与完整谓词原文。当前发现提示词却写着“无已登记桥接时填写空数组，原文显式陈述使用 explicit_assertion”，没有说明文档描述关系的适用条件。模型因而持续选择了不适用的表示。

定位依据：

- [根节点创建与依赖视图](../../../../backend/app/services/extraction/ontology_guided/executor.py)：`root_node()`、`EntityDependencyView` 构建。
- [发现提示词](../../../../backend/app/services/extraction/ontology_guided/tool_model_adapter.py)：`DISCOVERY_INSTRUCTIONS`、`_stage_instructions()`。
- [主体来源校验](../../../../backend/app/services/extraction/ontology_guided/source_assertions.py)：`validate_source_assertion()` 中 `document_description`、`role_grounded()`。

对 `describes`、`hasCleaningMethod`、`hasDegradationPathway` 的 3 个样本做纯内存复现：使用冻结声明、已保存独立核验决定及其已检索原文范围，原始表示全部复现 `source_assertion_subject_endpoint_unbound`；仅把桥接类型换成已有的 `document_subject_description`，3 个样本的这项校验均返回空问题列表。

这是对原文角色校验环节的定位验证，不是完整模型重跑或最终事实质量验收。不能把所有根节点关系无条件改成该类型，也不能让普通实体或正文业务关系绕过主体归属要求。

## 2. 最终校验问题没有进入本轮纠正流程

`validate_graph` 工具核验本体范围、身份与来源绑定，并返回 `semantic_status=not_checked`；它没有完成上述最终原文断言闭合校验。因此工具显示通过不代表关系可以入图。

`tool_model_adapter.py` 先根据缺失核验维度及工具检查结果决定是否补证，然后才调用 `finalize_claims()`。上述 3 个样本的模型核验维度均为 supported，直到最终 ProofGate 才发现主体端点未绑定；这个新问题没有反馈到前面的本轮补证选择。样本最终保存实体及未决关系证明，继续处理下一个任务。

相关入口：[工具关系校验](../../../../backend/app/services/extraction/ontology_guided/tool_runtime.py) 的 `_validate_relation()`；[适配器](../../../../backend/app/services/extraction/ontology_guided/tool_model_adapter.py) 的补证选择及 `finalize_claims()` 调用；[最终门禁](../../../../backend/app/services/extraction/ontology_guided/verification.py) 的 `evaluate_frozen_claim()`。

## 3. 其他已确认的候选问题

| 首个结果原因 | 处理项数 | 含义 |
|---|---:|---|
| `record_no_claims` | 7 | 当前记录未提出声明 |
| `source_assertion_subject_endpoint_unbound` | 3 | 唯一问题为主体端点未绑定 |
| `source_assertion_predicate_not_reviewed` | 1 | 核验引文未覆盖冻结谓词断言，另有主体未绑定 |
| `predicate:source_excerpt_mismatch` | 1 | 谓词核验引用不逐字匹配，另有主体未绑定 |
| `type:source_excerpt_mismatch` | 1 | 类型等多个核验维度的引文解析失败 |
| `source_assertion_fact_source_missing` | 1 | 用辅助表头代替当前记录的事实来源；另有记录组成覆盖不足 |
| `subject_role:support_missing` | 1 | 主体角色等核验依据缺失 |
| `subject_role_not_supported` | 1 | 实体主体角色未获支持，另有主体未绑定 |
| `required_relation_validation_missing` | 1 | 未取得协议要求的关系工具校验 |

生产计划样本的核验引用实际写成“计划于...进行临床样品的生产”，而原文中间是具体日期和地点，并没有省略号。逐字引文解析因此失败。清洗残留物的另一个结果也记录了多个 `source_excerpt_mismatch`；本次未把它进一步归因为相同的省略号问题。

这些问题不能通过放松原文证据校验来消除。应修正模型输出及所引用证据，保留真正缺证项。

## 4. 耗时主要在串行强模型请求

冻结配置是 `recognition_inflight=1`，每个任务至多 4 次模型调用。发现、工具往返、独立核验通常需要多轮。

| 阶段 | 已完成请求数 | 平均请求耗时 | 累计请求耗时 |
|---|---:|---:|---:|
| discovery | 21 | 83.50 秒 | 1,753.42 秒 |
| verification | 18 | 79.00 秒 | 1,422.08 秒 |

39 次请求累计执行约 **52.9 分钟**，排队累计 **1.26 秒**。已保存模型响应状态均为 completed，合计输入 391,662、输出 130,213 tokens。主要成本在串行模型执行及多轮输出，不是全局调度排队。

摘要阶段另有 6 次成功、1 次失败，合计请求约 169 秒，元数据随后完成。token count、embedding 和 reranker 累计请求约 67 秒。各操作可能重叠，累计请求时间不能直接当作墙钟时间相加。

本运行已经启用 `sparse-candidates-v1`，不能套用此前其他运行“尚未接入稀疏规划”的结论。当前 71 项仍只尝试了 17 项，且没有关系能进入下一层扩展。已记录工具调用包括 `validate_graph=8`、`propose_mentions=3`、`resolve_source_anchor=3`、`check_claim_binding=1`。

01:29:34 的暂停原因明确为 `execution_time_budget_exhausted`，对应冻结的单次执行 1,800 秒预算；01:41:09 有继续排队事件。约 70 分钟总历时包含这段暂停，不能全部算作连续计算。

## 修复顺序与验收建议

1. 明确区分文档描述关系和正文业务关系，修正发现提示词及桥接选择。保留普通实体的严格主体归属、谓词、否定和条件核验。
2. 让最终原文断言问题在现有调用预算内成为可见、可处理的反馈；优先复用已有校验和纠正入口，不新建独立恢复体系。
3. 修正非逐字引文和辅助表头事实权限问题；核对必需关系工具调用的预算预留，避免消耗完整轮次后因协议缺项失败。
4. 先用本次 3 个明确阻断样本验证完整发现—核验—入图链路，并保留主体错配反例；再评估输出长度和串行调用成本。只提高并发或继续等待不能解决当前主体绑定错误。

本次验证范围是实时数据诊断与 3 个局部门禁复现，没有执行模型质量评测、完整测试套件或部署。
