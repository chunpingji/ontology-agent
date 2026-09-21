# v5 候选图谱运行的独立审阅

运行：`84956c8a-6988-43cb-bab6-e4ad7e3d026d`。输入：
`upload-a255fd30-192c-4f81-92df-5f76a56b8383`，根类型 CMCReport。
本轮目标是保留实体识别与类型区分，同时发现原文关系候选；关系不核验，全部虚线，
属性识别与校验关闭。本审阅只读公开 API 和该运行数据库结果，不调用模型或控制运行。

**交付截面为 revision 249 / event 15：18 个确认抽取调用，图中除文档根外有 1 个实体、
1 条带原文的未核验关系候选。关系核验、属性任务/核验、validate_graph 均为 0。**
运行仍在继续；本审阅在取得该截面后停止轮询。这是局部候选闭环验收，不是全文质量或
完整度验收。精确计数见本文末尾和[冻结截面](candidate-review-84956c8a-cutoff.json)。

从新运行 source API 重新取得 parser 9 原文；文档 hash 为
`1156ca7b694c5af10afa393a5df4158ad4f9528901eab560ae2cf96e55b6d44b`，结构 hash 为
`c5c3d60bf5347931486fa3127c909194b4fac44362754574e1ba21d508e94bb5`，本体 snapshot 为
`54c748b83397a74fd7268f96c8f72826c572bc0b5a457fec8e8e18da055831f2`。

每个新增确认回执核对：实体标签是否指称实体及其类型；候选关系主体、对象、方向、原文
引文与否定/条件；请求所属成员和谓词数；实体核验与关系核验分开计数；是否实际探索
非根主体的下层关系。候选边存在不等于该关系为真，实体模型 supported 也不等于独立接受。

初次 target-graph 快照 revision 11 / event 5 为 `phase=candidate_graph`；关系目标 12、
属性目标 0，两个完成百分比均为 null。数据库尚无抽取回执或协议。此时只确认展示阶段
配置已生效，不能据零调用验收后续模型行为。

## 前五组：实体来源未通过，尚无关系调用

[首三次回执](candidate-review-84956c8a-increment-01.json)与
[随后五次回执](candidate-review-84956c8a-increment-02.json)逐条保存模型输出、冻结提案、
实际核验和原文检查。第二份额外区分 quote 字面匹配与 context_text 是否确实属于同一
原文单元，不能因为短 quote 存在就忽略错误的 context_text。

| 组 / lineage | 原始输出与独立判断 | 实际处理 |
|---|---|---|
| 1 / `1154fc94…`，理化性质区域 | 整条 pKa、logD、熔点字段及“理化性质”标签被组合成 DrugProduct record。核验借片剂和粉末支持 type，借化学名等支持 referent；没有证明该属性记录就是成品实例。 | referent 未覆盖冻结 record 组成，`record_composition_source_coverage_missing`；0 节点。 |
| 2 / `c93af360…`，项目与化学字段 | 提出项目名、项目代码、两个化学名、N/A、粉末、分子式、分子量、片剂共 9 个 DrugProduct。数值/属性和项目字段不能自动变成 9 个成品实例。 | N/A 被缺失指称门拒绝，片剂挂错 evidence_id；另 7 个核验虽全 supported，但 referent 将化学名当作项目名等其他单元的 context_text，`source_excerpt_mismatch`，全部未登记。 |
| 3 / `a50d6a7d…`，残留物段 | 把 N/A 提为 Residue；它不是残留物指称。 | `entity_referent_missing`；没有付费实体核验，0 节点。 |
| 4 / `5f9ec57f…`，P96–97 降解段 | 把“本品”“降解产物”提为 DegradationPathway，仍混用物质/产物与途径。 | “本品”重复且未给上下文，“降解产物”引用错误单元；`ambiguous_source_quote` / `source_excerpt_mismatch`；没有付费实体核验，0 节点。 |
| 5 / `5a41a66a…`，清洗表 | 把“HRS-1597成品”提为 HeatInactivation，把反应釜及洗涤步骤组成 InactivationProcess。原文清洗操作不等于灭活。 | 第二个 record 拼接跨单元文字，冻结失败；第一个模型 type 明确 unsupported，指出回流洗涤不证明灭活，部分引文仍有原文不匹配。0 节点。 |

第一组还输出 27 条属性 missing observations，多条明明引用“否”却说未提及相应属性。
这些是被忽略的发现观察，没有变成属性任务、属性核验或图属性，不计为属性调用。后续
记录依然 properties=[]；不能把候选阶段仍显示的本体属性说明或观察文本混算成属性任务。

截至 revision 170 / event 10，共 **8 个确认抽取调用：5 次实体发现、3 次实体核验**；
另有 1 个请求进行中。五组都有最终 outcome，均未登记实体；图仍只有文档根。
实际协议中没有关系成员，关系 verification、validate_graph tool_result、属性任务/核验
均为 0，属性候选也为 0。这个零计数说明禁用路径没有运行，同时意味着关系候选闭环、
多谓词合批和非根下层关系探索**尚未得到真实验收**。

下一已发请求是基本性质区域的 ActiveSubstanceResidue 类型卡。它是在没有上游关系
实线时继续其他可达类型的实体发现；这不等同已经执行了非根实体的下层关系任务。

## 后续实体回执与首个实际登记

[第 9–14 个回执](candidate-review-84956c8a-increment-03.json)保留以下三组原始输出。

| lineage / 区域 | 原始输出与独立判断 | 实际处理 |
|---|---|---|
| `105de741…` / 基本性质 | 把整条溶解性字段提为 ActiveSubstanceResidue。原文没有生产或清洗后物质留在设备表面的证据；不能以药物性质代替残留物。 | 实体核验 type unsupported，`type_not_supported`，未登记。其 11 条 unbound observations 含虚构谓词和 unknown_binding，保留原输出，但没有变成属性任务。 |
| `3687f111…` / 安全评估表 | 把表格序号 4、3、2、1 提为 5 个 SafetyRiskAssessment，其中 4 同时按 mention 和 record 提出。序号本身不能证明独立评估实例。核验还曾用序号 2 支持序号 4 的 target，错误原样保存。 | type / referent 未决，`type_not_supported` / `referent_not_supported`，未登记。 |
| `787821d6…` / P88 | 提出“反应安全风险评估”和“安全评估”两个 SafetyRiskAssessment。前者确为原文连续指称，且句中给出反应安全评估的结论；后者不是“安全风险评估”的连续子串。 | 前者三个实体 facet 均 supported，登记实体 `772cbed9…@1`；后者 `source_excerpt_mismatch`。整组仍 complete=false，不能因为已有节点就改写失败记录。 |

首个登记实体的唯一物理来源是 P88（原文物理页 6）：

> 通过反应安全风险评估，得出以下结论：无危险反应步骤，风险可以接受。

独立判断：它可作为报告内安全评估信息的局部实体，类型与这句内容匹配；不据此宣称
存在全局唯一的评估记录。模型核验理由写了热失控“隐含于危险反应”，但这不是原文
明说的事实，本审阅不接受这个扩展，也没有将其写入图属性。

[第 15–18 个回执](candidate-review-84956c8a-increment-04.json)还包括一次
ActiveSubstanceResidue 发现与实体核验（`709d90fc…`）：把项目、化学名、分子式、分子量、
CAS 和粉末性状等 8 个字段当作同一残留物的 mentions。核验 type unsupported、
subject_role undetermined，未登记。该拒绝有原文依据；模型关于 API 身份的附带判断
不作为本审阅独立确认的事实。

截面时另有 `048f8d04…` 的实体发现回执：将共线评估表中的 HRS-1597 及 10 个
“主体—字段—值”组合提为 11 个 SharedLineAssessmentData。各引文能逐字定位，
但实体粒度是否正确仍需核验；不能将 NOAEL、F1–F5、PDE 等字段逐一实体化就算正确
识别评估资料。该组的实体核验仍在进行，无最终 outcome、无登记节点。
这些提案的 properties=[]，是实体建模质量问题，不能计成已执行属性抽取任务。

## 首条关系候选：原文、端点与运行状态

实际请求 `971ad259…` 只有 1 个成员 `8a51d556…`，谓词为
`hasSafetyRiskAssessment`，主体为报告根（hop 0）。确认回执提出：

`CMCReport — hasSafetyRiskAssessment → 反应安全风险评估`

发现输出的 bridge_support 和 predicate_support 都精确引用上面的 P88 全句，
引用跨度为 `[0, 33)`；端点引用均指向已登记实体。对象的原始指称也在 P88，
但本次关系输出 object_support=[]、subject_support=[]，不能把实体先前核验的
facet 偷换成该关系已获证明。报告根来自用户选择，发现输出使用
`document_subject_description` 桥接类型。

独立判断：原文确实给出了这份报告中的安全评估结论，因此“报告含安全风险评估”
是有来源且方向合理的候选。“无危险反应步骤”否定的是危险步骤存在，不是否定评估
存在；该候选使用 affirmed、conditions=[] 与这一点相符。此处只复核候选与原文的
对应，不进行模型关系核验，也不授予图谱事实证明。

系统发布的候选 ID 为 `ffaa993487d254aad53a254332eb3c49d76d3161dd21cdf2dd15b9c0a3313519@1`：
decision_status=not_checked、proof_ref=null、decision_refs=[]，structural_valid、
model_supported、policy_eligible 全部 false。当前记录的 outcome complete=true
只表示完成这段原文的候选发现，semantic_outcome 仍为 not_checked。图谱 target
状态为 undetermined、completed=false，关系与属性实证百分比均为 null。
这些状态与虚线候选语义一致，不能解读为完整关系识别或原文质量达标。

## 冻结截面计数及验收边界

状态和图谱 API 均为 revision 249 / event 15 / artifact 4；同期只读数据库导出
包含 18 个确认 model_turn。四份增量逐一保存这些不可变 key，合并后无遗漏、无重复。
API 与数据库为相邻读取，未宣称跨请求原子快照；二者在确认调用数、节点和关系数上
一致。运行保持 running，另 1 个实体核验请求进行中，未计为确认回执。

| 口径 | 截面结果 |
|---|---:|
| 实体发现任务 / 确认发现调用 | 10 / 10 |
| 实体核验确认调用 | 7 |
| 实体发现最终 outcome | 9（另 1 组核验进行中） |
| 关系发现任务 / 确认发现调用 / outcome | 1 / 1 / 1 |
| 总确认抽取调用 | 18 |
| 关系 verification 调用 | 0 |
| 属性任务 / 属性核验调用 / 图属性 / 属性候选 | 0 / 0 / 0 / 0 |
| validate_graph 调用 / tool_result / proof_generations | 0 / 0 / 0 |
| 已登记非根实体 / 原文关系候选 | 1 / 1 |
| 关系工作单元实际最大成员数 / 谓词数 | 1 / 1 |
| 实际执行非根主体的关系任务 | 0 |

表中“总确认抽取调用”不含预处理/排序。图谱另报告排序 model_calls=42，不得把
18 当作整个运行全部模型请求数。数据库 9 条 verification 结果行中有 2 条空的
确定性结果；只有 model_turn 才计入 7 次已确认实体核验。

work 中 12 个 predicate/search/plan 都属于报告根的关系，属性执行入口为 0。
根的本体菜单仍保留 8 条属性定义，控制状态还有 layer_phase=property 这个字段，
它们不代表发生属性任务；须以实际协议、回执及产物计数。

非根 SafetyRiskAssessment 已进入 registered_subjects，hop=1、空 scope；其当前
本体关系菜单为 0，因此没有下层关系可执行。现阶段真实证明的是“实体登记后可进入
主体集合，并形成根到实体的带来源候选”，**没有证明多谓词合批或非根下层关系探索**。
对其他可达类型进行实体发现，也不能填补这两个尚未发生的验收项。

完成上述截面归档后本审阅停止轮询，没有暂停、取消、继续、修改运行或再次调用模型。
后续仍在执行的结果不属于本次截面。浏览器虚线与原文跳转验收由主代理另行记录；
本文不把其他代理的浏览器操作计为本审阅执行。
