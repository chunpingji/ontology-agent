# gpt-6-sol 与 Qwen 的同协议对照实测（2026-09-28）

后续状态：本报告保留当时对照结果；其中“编号属性受全局身份范围要求阻塞”的问题已作独立修复，新的同输入对照、反例和当前协议完整运行见[语义边界修正及 GPT 复测](文内编号与全局身份语义边界修正及GPT复测-20260928.md)。

用户提供 Responses 网关并授权对照。评测使用独立进程和新产物目录，不切换线上默认模型，不提交业务事实。

本次小样本中，GPT 的编号候选协议更稳定，并成功采信明确的共同参与、并行生产关系；但两例中的编号属性均未决。Qwen 在原句中采信了两个编号，在明确并行例中因生成非原文引文而中止。**直接换模型尚不能完成整体识别目标**；需要分别处理文内编号与全局身份的核验边界，以及正文生产计划的发现覆盖。

## 固定范围与方法

沿用[第五版上线验收](竞争指称分组上线验收-20260928.md)的原始输入、本体快照和精排结果：

| 用例 | 原文 | 根类型 |
|---|---|---|
| members | 计划于644/642车间完成本次临床样品的生产。 | CMC 报告 |
| parallel_plan | 本次临床样品生产计划同时在644车间和642车间并行生产。 | 临床备样生产计划 |

1. 同阶段对照：复用真实 `referent_candidates` 请求中的原文、草案、类型定义和 Mock 键候选，双方每例各重复三次。核对逐字引用、完整竞争分组及编号边界，不将候选正确等同最终实体采信。
2. 完整流程：双方每例各从空状态执行一次正式 `Engine`。复用相同精排结果前核对原精排输入哈希；发现、类型、编号、属性、关系、证据和共指调用由各模型独立回答。没有复用 Qwen 的识别结果作为完整流程的中间状态。
3. 每轮双方的来源查询共用同一 PostgreSQL `REPEATABLE READ READ ONLY` 事务快照及正式映射查询服务。查询结果、完整性和来源版本独立保存。模型调度仍会产生既有调用用量，不把整个试验称为只读操作。

比较的是两条固定单句，完整流程每格只有一次，不宣称完整文档或跨领域的模型排名。参考只进入推理后的开发回归评分，无业务专家金标；完整 H01/H02 不据此自动通过。

## 模型与接口约束

- Qwen：`Qwen3.6-35B-A3B`，revision `0b21525e972670ed59e1812e170b27c26355381f0656ecc4e25617ece7dac58b`。
- 对照：请求及网关返回模型名均为 `gpt-6-sol`；网关没有给出可冻结的权重 revision。
- 用户地址为 `http://0.0.0.0:31080/v1`。宿主机请求使用 `127.0.0.1`；容器评测通过 Docker 网关 `172.22.0.1` 访问同一端口。认证键从隐藏终端提示读入，仅保留在进程内存中。
- 双方请求 reasoning effort=`none`，输出上限 16384、输入预算 32768。Qwen 配置 temperature=0.1；GPT 请求同为 0.1，但网关返回的 temperature 为 **1.0**，因此不能声称实际采样温度完全一致。该差异随原始返回记录。
- 首轮正式对照上限 90 次调用、2400 秒，每个完整运行最多 30 次；后续接口修正重跑的预算另列。接口预检单独计数；未决、失败、未尝试的阶段均保留。

接口依据通过 Context7 查询并读取 [Responses 官方文档](https://developers.openai.com/api/reference/resources/responses/methods/create)和[结构化输出说明](https://developers.openai.com/api/docs/guides/structured-outputs)。官方页面用于请求结构，不用于证明用户网关后端的模型身份或定价。

### 实际遇到的网关差异

1. 非流式请求返回 `400: Stream must be set to true`，改为 SSE。
2. 原生产 Schema 的带说明 `$ref` 被网关拒绝。双方统一展开这类引用、要求默认字段显式返回、补全 enum 类型并去除 default 注解；业务指令和来源数据保持不变。原生产 Schema 及阶段模型继续在本地验证，不放宽原文约束。
3. 网关终态 `response.completed.output=[]`，完整文本位于 `response.output_item.done`。首个成功返回因此被旧解析器记作 `gateway_invalid_json`；保留原产物，另保存从完成事件解析的回答并修正评测传输层。该次回答不是模型 JSON 失败，未混入正式重复矩阵。

上述共同 Schema 适配使本次比较能够在同样约束下进行，但也使它与上一轮线上原样 Schema 存在差别。例如 `relation_groups` 必须显式回答。不能把前后结果变化全部归因于换模型；本次新 Qwen 对照用于控制这一因素。

## 工程验证

新增入口：[harness_model_comparison.py](../../backend/app/evaluation/harness_model_comparison.py)。线上模块没有新增依赖此入口，也没有改动生产提示词或默认模型配置。

容器缺少 JSON Schema 测试依赖，评测只在私有 `PYTHONPATH` 复用宿主机已安装的校验包，没有安装到服务环境。依赖版本随产物保存。

定向 Ruff 通过；[传输与评分回归](../../backend/tests/test_document_harness/test_model_comparison.py) **7 passed**，4 项既有警告。覆盖空终态输出的 SSE、未完成响应不可采信、错误中的认证信息去除、原 Schema 不被改写、空类型选择、编号串换、空编号集合和不完整运行不能通过评分。

离线复核修正了辅助指标 `identifier_ownership` 对空采信集合返回 true 的问题。原始运行摘要保留不动，以终态重新生成 `final-evaluation.json`；全部 `identifiers_ok` 和 `full_target_ok` 与原评分一致，没有将失败改成成功，也没有再次调用模型。

## 实测结果与产物

原始产物根目录：`backend/data/evaluations/harness-model-comparison-20260928-01`。候选重复矩阵使用 `paired-02`；修正网关类型声明后的完整流程使用 `paired-03`。`paired` 在读取认证前停止，无模型调用，未计入对照。

### 同输入的编号候选复测

| 输入 | Qwen | gpt-6-sol |
|---|---:|---:|
| `644/642` 原句：整体与成员竞争解释 | 3/3 | 3/3 |
| 明确“同时、并行”句：两个独立编号 | 2/3 | 3/3 |
| 合计 | 5/6 | 6/6 |

Qwen 唯一失败为 `paired-02/call-011.json`：两个编号、两成员解释都正确，但 `anchor=null`，因此触发 `identifier_partition_missing`。这次不是模型未识别两个编号，而是完整输出契约未满足。GPT 的六次均给出合法逐字定位和所需分组；此阶段尚未做实体、属性或关系最终采信。

候选阶段调用耗时中位数：Qwen **15.35 秒**，GPT **6.42 秒**。每模型只有六次，不据此推断普遍延迟或服务 SLA。

首轮完整流程还暴露了 `const:null` 未显式声明 `type:null` 时，网关在类型对齐前返回 400。该项属于接口兼容，不计模型语义错误。保留 `paired-02` 全部结果，仅增加这条类型声明；双方完整流程在 `paired-03` 新建状态重跑，上限 50 次/1800 秒。编号候选的 Schema 不涉及该差异，因此不重复已完成的三次矩阵。

已逐一核对 12 次候选请求，修正前后的传输 Schema 完全相同，记录于 `candidate-schema-equivalence.json`。

### 完整流程最终结果（paired-03）

| 输入 | Qwen | gpt-6-sol |
|---|---|---|
| `644/642` 原句 | 15 次调用，执行完成；两个车间及各自编号采信；未发现正文生产计划，无关系组 | 13 次调用，执行完成；两个车间采信；两个编号均未决；未发现正文生产计划，无关系组 |
| 明确并行句 | 3 次调用，在编号候选处发生 `source_quote_mismatch`；关系阶段未尝试 | 11 次调用，执行完成；两个车间采信；两个编号均未决；共同参与 `all` 和并行时间 `parallel` 均采信 |

这里的“执行完成”只指 `scope_complete=true`。四格的完整目标 `full_target_ok` 均为 false；不能以无异常结束替代识别质量通过。明确并行例的计划根类型由输入预先提供，不算模型成功发现正文计划。

Qwen 并行例提出原文不存在的编号引文 `644和642`，见 `paired-03/call-031.json`。原文是“644车间和642车间”，程序按既有逐字证据约束拒绝；保留失败，不修补回答后重计成功。

GPT 并行例的关系组从计划根指向两个车间，谓词为 `producedInArea`，`participation=all`、`timing=parallel`，两项核验均 `accepted`，极性为正且无条件。理由直接引用“同时”“并行生产”，没有用编号命中推导并行。终态见 `paired-03/parallel_plan-sol-state.json`。

### 已定位的判定差异

GPT 在原句中已经采信两个生产区域，却在属性核验中把两项编号判为 `unresolved`，理由包括“未给出编号体系或适用范围”。原始证据为 `paired-03/call-012.json`。这表明它没有丢失 `644`、`642`，而是将身份范围条件用于约束文内编号属性的采信。按本轮“识别文内编号，来源全局身份仍未核对”的验收口径，该结果未达标；不能把候选阶段 6/6 当成最终属性正确率。

Mock 返回确实保留 `identifier_namespace=urn:mock:production_areas`、`business_scope_status=unspecified` 和 `identity_status=not_checked`。因此不能由来源键命中直接确认全局身份；改进目标是使这个限制不阻止有原文依据的局部编号值及归属判断，也不能反过来删除身份范围约束以换取通过。

原句还有一个双方共有的上游限制：冻结精排中 `ClinicalSampleProductionPlan` 已进入种子候选，却因 `card_exceeds_remaining_budget` 没有进入发现菜单；实际菜单包括药物产品、洁净区、取样过程和生产区域。最终 Qwen 与 GPT 都未发现正文生产计划。菜单遗漏是有证据的诊断线索，但本次没有改变菜单做消融，不能单凭相关性宣称它是唯一原因。

共指也不能混入编号评分：GPT 对原句两车间的全局同一性保留未决，对明确并行例则依据并列叙述判为不同。Qwen 原句判为不同，但理由含“不同编号通常指向不同对象”的推断；编号属性通过不代表全局来源身份已完成核验。

下一轮建议固定模型，分别做两项单因素试验：其一，在属性核验中明确“文内编号值及成员归属”与“外部来源全局身份”是不同判断，保留来源身份所需的完整键组及作用域约束；其二，在同等菜单预算下确保生产计划卡进入发现菜单，检验正文计划能否被发现。继续保留完整复合编号、别名/改号、来源无记录和仅有斜杠却无并行证明的反例。上述为后续建议，本轮未改生产提示词或精排策略。

## 调用与成本

| 轮次 | 模型 | 请求数 | 返回完成响应数 | 输入 token | 输出 token | 调用耗时累计（秒） |
|---|---|---:|---:|---:|---:|---:|
| paired-02 | Qwen | 24 | 24 | 86,038 | 7,483 | 407.06 |
| paired-02 | GPT | 10 | 8 | 45,726 | 2,512 | 109.12 |
| paired-03 | Qwen | 18 | 18 | 69,474 | 4,869 | 318.85 |
| paired-03 | GPT | 24 | 24 | 119,236 | 4,562 | 167.11 |

正式两轮共 **76 次请求、74 次完成模型响应**。另有接口预检三次请求：两次 400、一次完成响应（输入 4,368、输出 199 token）。全任务共 **79 次请求、75 次完成响应**；400 未报告 usage，单列接口失败，不能据此断言零计费。完成模型响应也不代表业务回答通过。

最终完整流程的成本分开列示，防止把提前失败当作更低成本完成：

| 输入 | 模型 | 调用数 | 输入 / 输出 token | 调用耗时累计（秒） |
|---|---|---:|---:|---:|
| 原句 | Qwen | 15 | 58,193 / 3,345 | 259.35 |
| 原句 | GPT | 13 | 68,153 / 2,689 | 100.42 |
| 明确并行 | Qwen（提前失败） | 3 | 11,281 / 1,524 | 59.50 |
| 明确并行 | GPT | 11 | 51,083 / 1,873 | 66.69 |

耗时来自每次调用观测，不是端到端耗时或纯推理时间。Token 为各服务报告口径，包含的结构化输出约束及分词方式不同；没有网关价格，未估算金额。两模型调用顺序部分交替，但未做充分随机化，实际温度也不一致；两条输入和每格一次完整流程不足以给出稳定的总体优劣结论。

## 可复核产物

- [最终离线评分](../../backend/data/evaluations/harness-model-comparison-20260928-01/final-evaluation.json)：修正辅助空集合指标，主评分与原始运行一致。
- [产物一致性检查](../../backend/data/evaluations/harness-model-comparison-20260928-01/artifact-integrity-check.json)：两轮冻结的 17 个 Harness 文件、13 个本体文件一致并匹配当前工作区；12 份基线文件一致。来源映射版本及同记录版本一致，最终四个运行返回的记录集合一致；182 个文本产物/本轮文件未匹配到 API key 格式。
- [原始结果及成本汇总](../../backend/data/evaluations/harness-model-comparison-20260928-01/aggregate.json)：保留全部正式轮次，不排除失败。
- [候选 Schema 一致性](../../backend/data/evaluations/harness-model-comparison-20260928-01/candidate-schema-equivalence.json)及[首次 SSE 回答离线复核](../../backend/data/evaluations/harness-model-comparison-20260928-01/preflight-recheck.json)。
- [Qwen 候选缺 anchor](../../backend/data/evaluations/harness-model-comparison-20260928-01/paired-02/call-011.json)、[Qwen 非原文引文](../../backend/data/evaluations/harness-model-comparison-20260928-01/paired-03/call-031.json)、[GPT 编号未决原始回答](../../backend/data/evaluations/harness-model-comparison-20260928-01/paired-03/call-012.json)。
- [GPT 并行例终态](../../backend/data/evaluations/harness-model-comparison-20260928-01/paired-03/parallel_plan-sol-state.json)及[Qwen 原句终态](../../backend/data/evaluations/harness-model-comparison-20260928-01/paired-03/members-qwen-state.json)。
- 各轮 `manifest.json`、`probe.py`、`runtime/`、`ontology/`、`baseline-*`、`calls.json` 和逐模型 `*-queries.json` 保存配置、运行代码、冻结输入、原始调用及来源查询；最终评分器另存为根目录 `final-scorer-and-runner.py`。

本轮只增加隔离评测入口、测试和文档。H02f 表示完成模型对照执行；完整 H01/H02 仍未通过，线上默认模型继续使用 Qwen。
