# 027 Spec 的 Harness Engineering 相容性审查

日期：2026-09-16。审查对象为当前工作区的整个 027 设计包及关联总体方案，包含尚未提交的设计内容。结论针对设计，不代表运行代码、真实 Qwen 或图谱质量已经验收。

## 1. 结论与依据

**总体架构相容，但尚未形成完整可编码的 Harness 闭环。** 研发规范入口、模型与程序的决策边界、按需上下文、标准工具、有界循环、单一当前状态、图谱输出及独立评测方向一致。发现 2 项 P1 契约缺口和 3 项 P2 收紧项；其中核验输入与错误反馈的 P1 应先修订，再据此实现相关模块。无需推翻架构或增加 Agent 框架。

本次经 Context7 检索，并实际获取、阅读以下 OpenAI 官方页面（均 HTTP 200）：

- [Iterating Development Workflows with Codex](https://developers.openai.com/cookbook/examples/codex/iterating-development-workflows-with-codex)：明确目标、规范/计划/结果的归属，采用仓库已有约定，使任务和验收可发现、可执行，以观察结果推动改进。文中的目录是可调整的惯例，不是强制结构。
- [Codex as a platform: build on the open agent harness](https://developers.openai.com/blog/codex-as-a-platform)：Harness 管理上下文、工具交互、执行边界和跨轮工作，宿主应用保有业务数据、规则及用户界面。

原始 Harness engineering 文章全文的取证缺口仍见 [harness.md](harness.md) §1；本次不声称对该文逐条认证。以下是上述官方资料与本项目明确需求的设计审查，不是 OpenAI 的认证标准。Responses 是交互协议，采用该协议本身不能证明 Harness 相容或抽取正确。

读取范围：[需求](spec.md)、[模块计划](plan.md)、[数据模型](data-model.md)、[Harness](harness.md)、[任务](tasks.md)、[验收](quickstart.md)、[工具契约](contracts/tool-contracts.md)、三个 JSON 制品及[总体方案](../../docs/文档抽取引擎2.0设计方案.md)。同时核对既有上下文、识别 worker 和证明目标类型，区分当前代码与拟新增接口。

## 2. 按 Harness 职责逐项核对

| 职责 | 当前设计落点 | 判断 |
|---|---|---|
| 明确目标与可见输出 | FR-12/15；M10—M12；verified 图保留组、范围、限定及原文证据 | 相容；不把 JSON 合规、空图或模型自述当完成 |
| 仓库知识与任务可发现 | spec → plan/data-model/contracts → T01—T26 → quickstart；harness §7 划分维护位置 | 相容；当前设计检查入口尚未入库，见 R5 |
| 模型能理解当前任务 | M07 ModelContextView、卡片/目录/原文分层、发现与核验隔离 | 方向相容；核验缺少明确的声明内容输入，见 R1 |
| 工具反馈支持下一步行动 | 静态注册、类型化结果、field_path、按 call_id 回传 | 方向相容；错误调用的观察类型不闭合，见 R2 |
| 程序掌握权限与真理门 | 服务端授权引用/来源、冻结 hash、独立核验和 proof gate；模型不能写图 | 相容；不靠提示词授予或收回权限 |
| 有界且可调整的循环 | TurnPlan、实际 HTTP 预算、无进展判定、停止与未完成语义 | 相容；默认四次是本项目冻结预算，不是 Harness 通用要求 |
| 确定性能力可调用且必检 | M08 单位/数量/SHACL 与模型语义核验分工 | 相容；两项工具的模型可见阶段需明确，见 R4 |
| 暂停继续与当前状态 | 既有协调器唯一写入、精确结果引用、完整 Responses 项本地续传 | 方向相容；检索新增权限的重建未闭合，见 R3 |
| 架构约束可执行 | T01 类型/注册表与 Schema 同义；T23 导入、权限、预算和跨层检查 | 相容；是待实施验收，不是已经通过的运行能力 |
| 结果反馈改善开发 | M13/T22/T25；工程反例、真实质量和成本分开；金标不进识别 | 相容；不以单篇报告调参代替独立质量评测 |

追踪性检查覆盖 18 个 FR、13 个模块及 26 项任务，所有 FR 均有模块归属，任务依赖无环。没有发现需要增加第二个执行器、远端会话、历史回放或观察平台才能满足 Harness 的理由。四个拟新增线上文件和十个标准函数的规模也不是相容性障碍。

## 3. 按优先级列出的发现

### R1 · P1：独立核验缺少可消费的冻结声明内容

**依据**：[data-model.md](data-model.md) 第 62 行的目标仅为 `target_id/target_kind/content_hash/required_facets`；第 185—194 行的 ModelContextView 只有精确引用、这些目标及工具观察，没有冻结声明内容。[plan.md](plan.md) 第 201 行要求核验重新建上下文，第 220—224 行的构造接口却未明确接收或加载声明内容。[examples.json](contracts/examples.json) 第 130—164 行的目标也只有上述四项，完整 Responses 示例止于 discovery。

**触发与影响**：进入独立 verification 后，模型不能从 opaque hash 得知需要核验的对象、原值、selection、极性、条件和 scope。原文中存在同类多个候选时，核验器必须重新猜测正在检查哪条声明。工具上下文里的 frozen_claims 对模型不可见；既有工具也没有返回完整声明的能力。旧内部 VerificationTarget 虽然更完整，但新接口使用 VerificationTargetSpec，规范未定义二者的内容映射，不能视为已经补足。

**最小修订**：在 ModelContextView 中显式定义冻结声明及必要依赖的类型化视图，或让 VerificationTargetSpec 携带等价的声明内容；选择一处作为规范定义，不重复维护。由已确认 discovery_ref 加载并核对 hash，包含实际端点、实体指称、原值、限定、scope，以及核验所需的授权外部候选/桥接内容；继续隔离发现阶段 reasoning。

**落点与验收**：修订 data-model §4/9、plan M02/M07、T01/T11/T14，并补完整 verification request 示例。同一原文下仅改变对象、selection、原单位或限定时，核验请求必须准确反映被检查声明；缺少内容或 hash 不匹配不得发送核验请求。

### R2 · P1：错误工具调用无法进入反馈及恢复视图

**依据**：[data-model.md](data-model.md) 第 198 行将 ToolObservation 限为 `tool_name:ToolName` 和 `arguments:EvidenceModel`，并要求按注册表恢复。[tool-contracts.md](contracts/tool-contracts.md) 第 52、178—179 行却允许未知工具或非法 JSON/参数形成已确认错误反馈，再在预算内纠正。

**触发与影响**：Qwen 返回未知函数名或坏 JSON，服务端保存 function_call_output 错误后，下一轮或暂停恢复需要构造 ToolObservation。未知名称没有注册表项，坏参数不能构造 EvidenceModel，错误反馈因自身类型要求而无法进入 ModelContextView。这直接中断 FR-17 要求的反馈闭环。

**最小修订**：区分原始调用与已验证参数。原始 ToolCall 保留 `call_id/name:str/arguments_json:str`；观察中的类型化 parsed_arguments 可空。未知函数和参数错误使用统一的类型化错误外壳；合法调用仍使用注册表的具体参数/结果类型。不以宽松字典替代合法工具的契约。

**落点与验收**：修订 data-model §9、工具契约、T01/T06/T14。覆盖“未知函数／坏 JSON → 保存错误 → 暂停继续 → 同一批调用完整配对 → 预算内正确重试”；同时断言无非法 handler 执行、无退款、无丢失原始调用项。

### R3 · P2：检索新增证据权限缺少确定性恢复契约

**依据**：[tool-contracts.md](contracts/tool-contracts.md) 第 96 行按 TaskContext.fragments 授权读取，第 136—138 行的 RetrievalData 只有记录/证据 ID、hash 和覆盖信息，却允许确认新增上下文。materialized_refs 的四种类型没有上下文授权描述。[plan.md](plan.md) 第 307 行的恢复规则列出卡片、提及、外部候选和表示图，未定义新增证据的权限重建。

**触发与影响**：检索确认新记录后暂停，继续时仅加载结果 ID/hash，无法据此完整重建 fragment 的跨度、角色、fact_eligible、归属及相同上下文身份。现有 [RecognitionCall](../../backend/app/services/extraction/ontology_guided/recognition_execution.py) 会深拷贝 TaskContext，worker 内修改不自动成为协调器状态。实现者只能遗漏新证据，或不安全地从模型输入反推权限。

**最小修订**：在既有已确认检索结果中定义足以重建授权上下文的最小描述，当前协议仅持有相应 result_ref；从冻结 IR 和该结果确定性重建 TaskContext，并核验 context/evidence hash。它是检索结果的一部分，不增加全文副本、权威状态或存储服务；active_input_items 不承担授权来源职责。

**落点与验收**：修订检索结果内部契约、plan M07/M11、T03/T08/T17。覆盖“检索确认 → 暂停 → 恢复 → inspect 新证据”，断言文本、角色、范围、权限和 hash 一致；未确认或过期增量不能授予读取权。

### R4 · P2：校准工具目录与模型可见阶段未闭合

**依据**：[tool-contracts.md](contracts/tool-contracts.md) 第 165—172 行只在 finalize 列出 metric/graph，且 finalize 禁止 Qwen 请求；同时称“具备已核验输入的工具会话”可向模型开放，但没有对应阶段、TurnPlan 条件和预算路径。

**触发与影响**：严格按阶段表实现时，validate_metric/validate_graph 是标准可调用函数，但 Qwen 始终无法选择它们。这不影响控制器执行必检，也不能据此宣称十个函数都已实现强模型工具协作。工具注册、模型可见和实际执行三种状态应分别说明。

**最小修订**：明确本期这两项由控制器调用、模型可见工具是目录的子集；若要让 Qwen 消费校准结果，则在已有阶段内定义已核验输入、可见条件和剩余额度下的调用路径，并调整“阶段回答后不再请求”的规则。两种选择均可与 Harness 相容，但契约必须采用明确的一种；不得新增免费 finalize 模型轮次或跳过语义前置门。

**落点与验收**：同步阶段表、TurnPlan、T13/T14/T24 和相应协议示例。测试注册集合与各阶段可见集合，以及模型未调用时控制器仍执行适用必检。若宣称模型可调用，须有一条在冻结四次额度内实际可达的路径。

### R5 · P2：设计制品校验尚无仓库内可复现入口

**依据**：[quickstart.md](quickstart.md) §6 记录了 JSON、反例、引用与链接的通过数量，却没有当前设计检查的仓库命令。此次复用的检查器只在 `/tmp/validate_027_responses.py`，使用固定工作区绝对路径及系统 jsonschema；该环境不属于项目锁定依赖。

**触发与影响**：新的编码会话或干净 checkout 无法复现这些制品检查，修改契约后只能相信历史数量。T01 的未来实现类型/注册表测试有独立价值，但不能替代当前设计制品检查。

**最小修订**：将最小制品检查入口保存在仓库，采用相对仓库根定位并注明所需环境，在 quickstart 给出命令。实现后的类型/注册表同义检查仍按 T01 执行，不能把设计 JSON 自检宣传为实现验收。

**落点与验收**：quickstart 与设计检查入口；通过正常制品，拒绝已定义的缺字段/多字段/错类型、失配 call_id、悬空本地链接等变异。无需新增线上组件或审计系统。

## 4. 修订顺序与范围

先解决 R1/R2 的输入与错误观察类型，再补 R3 的授权重建；R4 同步明确工具可见阶段，R5 固化设计检查。修订对应现有 T01/T03/T06/T08/T11/T13/T14/T17/T24 的契约或验收说明即可，不增加开发阶段、线上文件或任务状态系统。

本次只写审查结果并从设计入口链接，不把上述建议静默变成已实施能力，不修改应用代码、依赖、本体、历史评测或运行数据。P1 是相关模块编码前应修订的契约缺口；其余已明确模块的独立工作无需因此停下。

## 5. 本次实际检查与限制

已重新执行现有临时设计检查器及 Python 接口示意语法检查：3 个 JSON 文件、10 个工具参数 Schema、2 个阶段 Schema、25 个封闭对象定义、12 份有效输入、36 个非法输入反例、18 个逐字引用、3 个核验目标集合、26 项无环任务依赖及一组 Responses 往返/错误配对均通过；11 段 Python 接口示意可解析。另核对 18 个 FR 全部映射至 13 个模块。结果仅证明这些制品的结构一致性；R1—R4 正说明结构检查通过不能替代上下文和控制流闭合审查。

包含本报告后，119 处本地文件链接目标存在，git diff --check 通过；链接检查不证明所指未来能力已实施。未执行应用工程测试、浏览器、真实 Qwen/GLiNER 或 F1 评测，未部署。R1—R5 当前均为待修订发现，不将审查报告本身视为修复完成。
