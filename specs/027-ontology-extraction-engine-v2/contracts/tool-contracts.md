# 通用工具的可编码契约

标准请求定义见 [tools.json](tools.json)，包含 10 个 **Responses API function 工具**。用户已确认项目 Qwen 支持 Responses，新 027 运行固定 `api_protocol=responses`；旧 Chat Completions 仅服务已有调用，不为新协议建设双栈或失败回退。制品不由在线代码从 specs 目录加载；实现时由 `tool_contracts.py` 的类型生成同义定义，以契约测试核对。

这是完整能力目录，不是每次请求的 tools。当前协议中 `validate_metric`、`validate_graph` 固定由控制器调用，Qwen 可见集合从另外 8 项按第 3 节阶段、恢复模式及预算裁选；标准函数定义不附加非 OpenAI 字段。必检与模型可见性分别验收，不能将十项注册等同于十项模型调用。

每个工具为平铺 `{type:"function",name,description,parameters,strict}`，不嵌套 function 对象。基线显式 `strict:false`；具体端点的严格参数生成验收通过后可冻结为 `strict:true`，本次运行冻结后不因失败动态改变。**省略 strict 不等于关闭严格模式**；无论该值为何，参数/结果和语义前置条件均由本地契约复核。

## 1. 类型、上下文与分派

拟新增于 `ontology_guided/tool_contracts.py`：

```python
ToolName = Literal[
    "get_schema_card", "inspect_evidence", "resolve_source_anchor",
    "propose_mentions", "check_claim_binding", "query_instances",
    "retrieve_evidence", "propose_repair", "validate_metric", "validate_graph",
]
ToolStatus = Literal["ok", "no_match", "blocked", "error"]

class ToolIssue(EvidenceModel):
    code: str
    field_path: str | None       # 必填可空；参数 JSON Pointer，非字段问题为 null
    message: str                 # 固定可公开说明，不带异常堆栈或秘密配置
    evidence_ids: list[str]

class ToolResult(EvidenceModel, Generic[T]):
    status: ToolStatus
    data: T | None
    evidence_refs: list[str]
    issues: list[ToolIssue]

class ToolErrorResult(ToolResult[None]):
    status: Literal["blocked", "error"]
    data: None

@dataclass(frozen=True)
class ToolDefinition:
    name: ToolName
    description: str
    args_type: type[EvidenceModel]
    result_type: type[EvidenceModel]
    allowed_stages: frozenset[str]
    model_callable: bool         # metric/graph 为 false；不进入 OpenAI 工具定义
```

每个参数类型按 tools.json 一一声明，`extra="forbid"`；nullable 字段使用无默认值的 `str|None`，仍是必填。入参以 `model_validate_json(..., strict=True)` 校验；模型不可传执行器、semantic_status、owner、租约、任意文件或 URL。运行时 schema 只收紧有效 ID/IRI 枚举，不能把本地检查取消。

Harness 的错误反馈必须可定位和行动：field_path 仅指本次模型参数，如 `/quote`、`/evidence_ids/0`；message 说明违反的约束及当前允许的读取/修正方法，不直接提供正确业务答案。阶段 target/facet 反馈由编排器转为 data-model 的 ContextFeedback。不要向模型返回原异常、未授权引用或额外状态写入口。

实现后由同一类型/静态注册表导出函数定义及本地结果 Schema，与本规范参数和示例检查同义；description 可调整，字段、required、nullable、枚举和额外字段限制不得静默漂移。ToolCall/ToolObservation 的权威字段见 data-model 第 9 节；它们保留原始 name:str、arguments_json:str 及可空的类型化 parsed_arguments，不增加持久化实体。

完整 Responses 批次通过调用身份检查后，先保存原始 ToolCall，再查注册表/白名单和解析参数。未知函数或非法 JSON/字段产生 ToolErrorResult：data=null、issues 非空；无法定位到参数字段时 field_path=null。这类调用的 parsed_arguments=null，恢复时加载原始调用和统一错误类型，不再次强制将未知名称转为 ToolName 或将坏 JSON 转成参数模型。合法调用及其执行失败仍按注册表保留已验证参数和对应具体结果类型。ToolErrorResult 不替代所有工具结果的类型检查。

拟新增 `ToolContext` 为只读运行时依赖集合：`task, context, index, ontology, menu, cards, frozen_claims, semantic_decisions, materialized_refs, mention_index, external_candidates, representation_graphs, instance_reader, mention_extractor, vocabulary, limits, check_cancelled`。其中 index 等读取对象使用隔离副本或不可变视图；不包含 SQLAlchemy Session、运行仓库写入口或业务提交服务。模型看不到该对象。

`materialized_refs` 是当前协议中已确认结果的引用映射，供上述 cards/mention_index/external_candidates/representation_graphs 视图解析，不是另一份实体或图谱存储。每项为 `{kind,id,result_ref,content_hash,context_hash,dependency_refs:list[VersionedRef]}`，kind 为 schema_card/mention/external_candidate/representation_graph；id 只在当前运行、lineage 和依赖下有效。handler 可计算确定的结果 ID，但不能自行登记权限。协调器核对工具结果、引用来源及版本，通过现有结果存储和协议屏障确认 result_ref 与映射后，才回传结果并构造下一轮 ToolContext。映射只引用结果中的对应对象；表示图按规范化结果和冻结 slot/profile 重建，不再持久化一份 RDF 图副本。

`ToolLimits={max_calls_per_response:8,max_calls_per_lineage:16,max_external_candidates:8,max_evidence_units_per_call:16,max_result_tokens:int}`。这些是初始资源上限，运行创建时冻结，可按模型上下文预算调整；不改变事实语义。max_result_tokens 从该请求剩余空间分配，不能默认无限大；完整记录不能保真容纳时返回 blocked/result_budget_exceeded 并保留 deferred 覆盖。

工具调用额度针对模型发出的调用，`tool_calls_used` 在每次获准分派前由协调器预扣并保存；参数错误、未知函数及执行失败的已尝试调用也消耗额度。超出单响应上限的批次不部分执行，额度不足时不进入 handler。控制器必做的 binding/metric/SHACL 不消耗模型选工具的额度，仍受任务执行预算约束并单列本地执行次数/耗时；不能因模型已耗尽工具额度而跳过必检。模型 HTTP 请求继续独立计入[总体方案第 8 节](../../../docs/文档抽取引擎2.0设计方案.md)的四次上限。

一次调用由当前 lineage 内的 `(request_attempt,call_id)` 标识。call_id 取自 Responses 输出的 `type="function_call"` 项；同项的 `id` 是输出项身份，不能替代 call_id。已确认结果恢复时直接加载，不重新扣工具额度、不重复 NER/查询/计算；只有预扣但未确认结果的调用保持未完成，若在原运行契约下重新尝试，则再次预扣并受剩余额度限制。恢复时同时加载 materialized_refs，不能让已返回的 mention/candidate/graph ID 失去解析入口。声明或上下文依赖改变时拒绝不再适用的旧引用；不靠重新执行工具掩盖版本不匹配。

拟新增于 `tool_runtime.py`：

```python
def build_tool_definitions(ctx: ToolContext, stage: str, *, strict: bool = False) -> list[dict]: ...
def parse_tool_arguments(name: ToolName, arguments_json: str) -> EvidenceModel: ...
def dispatch_tool(call: ToolCall, ctx: ToolContext) -> ToolResult: ...
def to_function_call_output(call_id: str, result: ToolResult) -> dict: ...
```

分派用静态 `dict[ToolName,handler]`；不 eval、不动态 import、不提供 execute_tool 万能接口。每个 handler 签名固定为 `(args: XxxArgs, ctx: ToolContext) -> ToolResult[XxxData]`。模型分派先校验 model_callable 和阶段/mode 白名单，再执行 handler；控制器以内部入口调用同一参数校验和 handler，不能由模型参数选择调用者身份。工具输出 data 为下节对应类型；禁止通用 `dict[str,Any]` 吞掉未定义输出。内部已有算法返回自由 dict 时在薄适配出口转成类型化结果。

`status=ok` 表示执行完成，data 必须存在；检查结论放在 data.validation_status/conforms。no_match 只用于正常完成的召回/查询，返回空集合及覆盖；blocked/error 的 data 可为空，但 issues 非空。`to_function_call_output` 接受具体 ToolResult 或 ToolErrorResult，返回 `{type:"function_call_output",call_id,output:result.model_dump_json()}`；output 是该外壳的 JSON 字符串，外壳不是 OpenAI API 自带业务结构。

### 1.1 Responses 往返与无状态续传

`llm/local_client.responses_create(...) -> ResponseTurn` 负责单轮传输，复用原调度、预留和取消；工具层只解析已完成响应中 `function_call` 项的 name、arguments、call_id。arguments 是 JSON 字符串。服务端先校验整批调用身份、响应完成状态和拒绝，再逐项执行；缺失/重复 call_id、非完整响应或 refusal 不能触发部分工具执行。

新运行每轮发送 `store=false`、当前阶段 instructions 和完整 input 项；本地保留输入项与原样 output 项，包含模型返回的 reasoning 项。续轮将前轮完整 output 加入 input，再加入对应 function_call_output，不能只截取调用或 output_text。仅在端点能力验收通过时请求 `include=["reasoning.encrypted_content"]`，并将返回内容作为不透明续传项保存；reasoning、摘要或加密项均不作为文档事实证据。禁止使用 previous_response_id 或 conversation 代替本地当前状态。

当前协议使用 `active_input_items`、`active_instructions`、`pending_call_ids` 和 `api_protocol=responses`，字段归属见 [data-model.md](../data-model.md)。暂停后先恢复完整项和当前未完成 call_id，已确认结果直接组装 function_call_output；不依赖远端会话或重新生成前轮响应。每轮 instructions 都显式重发；独立 verification 重新构造阶段输入，不继承 discovery 的理由或 reasoning。

阶段回答使用 `text.format={type:"json_schema",name,schema,strict}`，输出预算参数为 `max_output_tokens`；阶段 strict 基线同样显式 false，具体能力验收后才冻结 true，与工具 parameters 的 strict 分别配置和验收。编排器检查 response.status、incomplete_details 及输出中的 refusal；非完整响应或 model refusal 属于执行/协议层没有可用回答，保留未完成及原因，不生成声明的 semantic rejected，也不生成原文 negated，不能解释为空候选或空图成功。工具只在通过这些响应检查后分派，语义核验和最终证明门不因协议改变而省略。

## 2. 各工具输入与输出

下列类型均定义于 tool_contracts.py。`Quote`、`SchemaCard`、`QuantityValue`、`ExternalCandidate` 等引用 [data-model.md](../data-model.md)。命名空间、版本和原文身份通过上下文冻结，不在模型参数里重复传入。

### get_schema_card

- Args：`subject_id:str, predicate_iri:str|null`；null 表示当前主体的已授权菜单，不表示任意本体查询。
- Data `SchemaCardData`：`cards:list[SchemaCard], unsupported_constraints:list[ConstraintIssue]`。
- 先核对主体版本和任务 scope，再 `compile_local_menu()`，按当前任务裁选；由内容生成 schema_card_id。没有合法目标返回 blocked，不扩大主体类型。
- 已有 `tool_validation.evidence.get_schema_card()` 只是类卡片复制，不能直接替代此编译行为。

### inspect_evidence

- Args：`evidence_ids:list[str]`，非空、唯一且不超过额度。
- Data `EvidenceData`：`units:list[EvidenceUnit], omitted_ids:list[str], coverage_complete:bool`。
- `EvidenceUnit={evidence_id,record_id,text,span_start:int,span_end:int,role,fact_eligible:bool,table:EvidenceTable|null}`；role 为 target/header/note/parent/binding/counterevidence。坐标为原文半开区间，text 与该区间严格对应。
- `EvidenceTable={table_path:list[str],source_cell_id:str|null,logical_rows:list[int],logical_columns:list[int],column_header_refs:list[str],cell_structure_valid:bool}`。字段来自冻结 IR/RecordIndex 的结构展开；表格结构缺失时保留空列表/null 和 false，不按文字位置猜行列。column_header_refs 仅列当前已授权且可解析的表头；不能为省略表头而丢失列与值的对应。
- 按 `TaskContext.fragments` 权限读取 RecordIndex；补带表头/注释须由上下文组装器授权。缺引用/越权在读取前拒绝；预算不足不截正文或否定。

### resolve_source_anchor

- Args：`evidence_id:str, quote:str, context_text:str|null`。
- Data `AnchorData`：`anchor:EvidenceAnchor, text:str, mention_ref:str`。
- 用 `source_citations.resolve_fragment_quote`/`resolve_source_anchor` 精确回放；只有唯一合法匹配才 ok。
- mention_ref 由精确物理跨度确定，并按第 1 节经协调器登记；它只表示原文提及，不预置实体类型、全局身份或关系。query_instances 可使用该引用，因此 NER 漏报不会阻止 Qwen 对其他已定位原文查询。相同跨度的 NER 标签是候选注释，不另造物理实体。
- 不支持相似纠错、跨记录搜索或自动更换 ref。修复须经 propose_repair 独立提案与核验。

### propose_mentions

- Args：`evidence_ids:list[str], schema_card_id:str`。
- Data `MentionData`：`mentions:list[MentionSuggestion], coverage:MentionCoverage, limits:MentionLimits, omissions:list[ToolIssue]`。
- `MentionSuggestion={mention_ref,evidence_id,start:int,end:int,text,class_iris:list[str],predicate_iris:list[str],role,score:float}`；role=entity/field_label/field_value/unit。score 仅为提及模型分数。
- `MentionCoverage={requested_units:list[str],processed_units:list[str],unprocessed_units:list[str]}`；processed 仅包含全部请求标签/窗口均完成的单元，失败或未执行单元进入 unprocessed，并在 omissions 说明原因。
- `MentionLimits={labels_per_batch:int,window_chars:int,overlap_chars:int,word_limit:int,encoder_token_limit:int,candidate_pool_limit:int,max_returned_mentions:int}`。依次映射 mentions 的标签批次、字符窗/重叠、extractor 的 max_len/encoder_input_limit/shared_candidate_budget 及本次结果预算；不把 160 字符窗记为 160 tokens，不把单窗口共享候选池误称全文候选上限。返回裁选须在 omissions 给出实际省略数量/范围，不能把裁选后空集合记为无命中。
- 显式注入当前 GLiNER2.5 `Gliner2Extractor`，不走旧 GLiNER 默认工厂；标签由本体/SKOS/显式 overlay 编译。返回 mention_ref 可供后续查询，不授予实体事实资格。
- 提及 ID 与 AnchorData 使用同一物理跨度规则；提交结果后登记其引用及标签映射。同跨度的不同标签可共用 mention_ref 返回不同建议行，每行 score 保留该标签原始分数，不把其中最高分赋给全部类型。
- 只有全部声明范围执行完成且无提及时才 no_match；未加载、部分执行或 span 失败按 blocked/error 保留缺测。Qwen 仍看完整原文。

### check_claim_binding

- Args：`claim_id:str`。
- Data `BindingData`：`claim_ref:VersionedRef, validation_status:passed|failed|incomplete, resolved_role_refs:list[EvidenceAnchor], source_unit:str|null, issues:list[ToolIssue]`。
- 从冻结声明、实体、卡片及字段组取值；关系组逐个对象检查，不拆组。根身份读 root_ref，允许的自关系由本体/原文决定，不能按 "document" 或固定正则触发证明。
- 仅机械归属检查，passed 不等于语义成立。完整区间引用不能被旧 scalar 门提前拒绝；其表示资格由 QuantityPolicy 决定。

### query_instances

- Args：`mention_ref:str, class_iri:str, source_ids:list[str]`。source_ids 必须显式列出授权源，不用空数组表示全系统搜索。
- Data `InstanceData`：`candidates:list[ExternalCandidate], searched_sources:list[str], incomplete_sources:list[str], excluded_count:int|null, identity_status:Literal["not_checked"]`。
- 来源层返回 `InstanceSearchResult={candidates:list[ExternalCandidate],searched_sources:list[str],incomplete_sources:list[str],excluded_count:int|null}`，再由 handler 增加 identity_status；来源实现提供真实搜索范围和排除计数，不能从返回列表长度猜测。searched_sources 记录已实际搜索的源，incomplete_sources 记录请求中未完成的源，两者可交叠；未知的排除数量为 null，不得伪装为 0，并将该源标为未完成、返回 blocked/error。已知数量须非负。
- 查询键来自当前原文及显式字段/身份映射，支持名称和复合键候选；来源版本固定。只有全部请求来源完成、无排除且确无候选时返回 no_match；部分失败保留已有候选/缺测并返回 blocked/error。外部源关闭返回 blocked/external_source_disabled，不改原文求匹配。
- 返回候选 ID 按来源/键/版本生成，经协调器登记后才能用于 ExternalLinkProposal；暂停后从已确认结果恢复映射，不重新读取可变来源。
- 模型只能提出 ExternalLinkProposal，最终身份确认单独核验。外部字段不混为文档属性。

### retrieve_evidence

- Args：`subject_id:str, predicate_iri:str, missing_facets:list[FacetName]`。发现阶段可为空表示任务初始定位；补证阶段必须对应冻结目标实际缺口。
- Data `RetrievalData`：`record_ids:list[str], evidence_ids:list[str], context_hash:str, new_evidence:bool, coverage:{examined_records:list[str],unattempted_records:list[str],stop_reason:str|null}`。
- 复用 SubjectSlotQuery、RecordIndex、摘要/精排及 EvidenceWorkQueue；不按章节名或具体谓词决定目的地。保持完整逻辑记录、已知反证及竞争 owner。
- 新记录必须先通过协调器确认权限、上下文 hash 和引用表更新，再向后续工具/模型开放。工具不直接改变全局 coverage；同记录补验不重复增加 examined。无新增来源时 no_match。

### propose_repair

- Args：`claim_id:str, issue_code:str`，issue_code 必须出现在当前声明真实检查结果中。
- Data `RepairData`：`claim_ref:VersionedRef, action:none|rebind_quote|reproposal|supplement, proposed_quotes:list[Quote], required_facets:list[FacetName], changes_claim:bool, reason_code:str`。
- 引用修复仅限原授权 owner/字段的唯一匹配；对象/选择/条件变化走新 generation 的重提。函数只提案，协调器选择一个恢复分支；不主动调用模型、覆盖声明或循环重试。

### validate_metric

- Args：`claim_id:str,target_unit_id:str|null`。非 null 须为卡片允许目标，由 handler 解析为受控单位字符串；null 优先使用 slot 已声明的 canonical_unit，没有声明目标时保留源单位，不能从允许集合任挑一个或删除物理单位。单位必需但原文缺失仍为 incomplete。
- Data `MetricData`：`claim_ref:VersionedRef,validation_status:passed|failed|incomplete,quantity:QuantityValue|null,normalized_literal:str|bool|null,graph_ref:str|null,issues:list[ToolIssue]`。
- 原值/单位来源/语义判定均从冻结上下文读取。语义未支持时可返回解析诊断，但不得返回可信 normalized_literal/graph_ref。
- `normalize_metric(...,target_unit:str|null)->MetricNormalization` 为纯计算；`MetricNormalization={claim_ref,validation_status,quantity,normalized_literal,issues}`，字段类型与 MetricData 对应但不含 graph_ref，也不捕获 ToolContext。handler 传入可信绑定/核验和已解析目标，成功后构建单声明表示图，计算 graph_ref 并交协调器登记；只有登记确认后才向后续调用开放图引用。数值字面量使用精确字符串，boolean 保留 bool，不能直接将 Python int 混入该返回类型。
- SHACL 由 validate_graph 检查，控制器保证执行；MetricData.validation_status=passed 仅表示规范化前置条件和表示生成通过，不表示 SHACL 已完成或事实已接纳。当前 metric.validate_metric 混合规范化与 SHACL：实施时提取 normalize_metric，旧函数作为现有调用兼容组合，不把新路径重复校验两次。

### validate_graph

- Args：`graph_ref:str,shape_profile_id:str`，只能指当前声明的服务端图和适用的固定 profile。
- Data `ShaclData`：`profile:str,evaluated:bool,conforms:bool|null,validation_status:passed|failed|incomplete,report:list[ShaclIssue],coverage:FocusCoverage`。
- `ShaclIssue={focus_node,path:str|null,value:str|null,source_shape,constraint_component,severity,messages:list[str]}`；`FocusCoverage={expected:list[str],actual:list[str],missing:list[str],executed_shapes:list[str],complete:bool}`。
- 本期 graph_ref 指一个声明的表示图；混合谓词逐声明调用各自 profile 后由控制器汇总，不拿第一个 SlotSpec 校验整图。
- 复用 literal 表示 profile，新增 quantity 表示 profile；禁用导入、JS、推理和模型自定义 shapes。focus 必须非空且完整相等；正常执行但不合格为 ok/conforms=false，异常为 error/shacl_execution_failed。

## 3. 阶段白名单与必检

| 阶段 | 模型可请求 | 服务端必做 |
|---|---|---|
| discovery | card、inspect、anchor、mentions、instances、retrieve | 引用回放、合法菜单与候选冻结 |
| verification | card、inspect、anchor、binding、retrieve | 冻结目标逐维度核验；新证据改变核验包身份 |
| finalize | 无；不新增 Qwen 轮次 | 控制器按同一 handler 契约执行 binding、metric、graph，完成规范化、SHACL、证明/依赖门与图谱输出 |

recovery 是 `recovery_kind=none|evidence|reproposal` 与 recovery_used 表示的处理模式，不新增 stage。evidence 模式保留声明，在 verification 阶段补证/重验；reproposal 模式回到 discovery 生成新声明，再进入 verification。propose_repair 在这两个阶段仅对已由控制器选择恢复模式、存在真实问题的冻结目标开放；其提案不能改变已冻结的恢复路线或另开周期。allowed_stages 仅包含 discovery/verification/finalize，实际可见集再按模式、缺口、可信前置条件和剩余额度收紧。

本期 metric/graph 的 model_callable=false，所有 discovery/verification tools 列表均排除二者；模型即使主动返回这些名称，也得到 tool_not_allowed，不执行 handler。控制器在语义前置条件满足后先 metric 再 graph；必检结果进入既有声明检查与 ContextFeedback，若存在可修复缺口且还有共享恢复额度，按既定 evidence/reproposal 路线反馈，不增加模型可调用阶段。工具注册、模型可见、实际执行分别统计；不宣称 Qwen 已调用这两项。

## 4. 错误与副作用

| 情况 | 对外 code / 行为 |
|---|---|
| 非法 JSON/字段/类型 | invalid_tool_arguments；有合法 call_id 则回 function_call_output 错误，不执行 |
| 未登记/当前阶段不可用 | unknown_tool / tool_not_allowed |
| 未授权/过期对象 | reference_outside_scope / reference_version_mismatch |
| quote 缺失/歧义/不匹配 | citation_ref_missing / citation_quote_ambiguous / citation_quote_not_in_source |
| 语义前置条件未满足 | semantic_not_checked / semantic_undetermined，不伪造规范化结果 |
| 请求/工具/结果额度不足 | model_budget_exhausted / tool_budget_exhausted / result_budget_exceeded |
| Responses 响应未完成、拒绝或调用项身份非法 | 记录 response.status/incomplete_details/refusal 及稳定协议错误；不执行该批调用，不返回成功空图，不回退 Chat |
| 未配置必需依赖 | ner_required_unavailable / shacl_unavailable；Qwen Responses 支持以用户确认为前提，具体字段兼容问题独立报告 |
| 实际执行异常 | tool_execution_failed，具体组件稳定 code；不传原异常字符串 |

响应整体已完成、调用项字段完整且 arguments 为字符串，但该字符串不是合法参数 JSON，属于可反馈的 invalid_tool_arguments；不要与 response.status=incomplete 或缺失 call_id/arguments 字段混为整批协议残缺。错误观察与完整调用项同样保存并可恢复，有预算时才允许模型纠正。

取消、ExecutionLost 和持久化失败是运行控制异常，原样传播，不包装为 no_match/error 后继续。同批调用只使用已绑定引用；依赖前一结果的新调用必须下一轮生成。工具可读取冻结输入并产生提案/结果，不持久化业务事实；当前引用表和结果的登记统一通过现有协调器屏障。每个结果确认后检查暂停，恢复时先处理剩余合法 call_id；整批完成并配齐 function_call_output 后才决定是否关闭可选工具或发阶段回答，不能因第一项 no_match 而跳过后续有用调用。

## 5. 契约来源

OpenAI Responses 与项目 Qwen 字段契约的依据沿用[总体方案](../../../docs/文档抽取引擎2.0设计方案.md)。Pydantic V2 的 required-nullable、extra=forbid、model_validate_json 和 model_json_schema 依据 [JSON Schema 文档](https://github.com/pydantic/pydantic/blob/main/docs/concepts/json_schema.md)、[V2 迁移文档](https://github.com/pydantic/pydantic/blob/main/docs/migration.md)，2026-09-16 经 Context7 核对。运行代码仍以项目锁定版本验证，不升级依赖来迁就文档示例。
