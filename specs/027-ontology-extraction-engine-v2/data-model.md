# 数据与状态契约

“新增”均指待实现。现有类型主要在 `backend/app/services/extraction/ontology_guided/contracts.py`。模型字段未另行说明时必需；可空值显式传 null，禁止额外字段和宽松类型转换。[阶段 Schema](contracts/stage-schemas.json) 为机器可读模板。

## 1. 契约层次与引用

| 层 | 所有者 | 规则 |
|---|---|---|
| 工具参数、候选、核验 JSON | 模型生成、服务端校验 | 当前菜单/引用二次授权，JSON 合法不等于语义通过 |
| 冻结实体、声明、证明 | 唯一识别协调器 | 复用 VersionedRef、EvidenceAnchor 和稳定 ID |
| GraphSnapshot / GraphArtifactResponse | 服务端投影 | 精确版本、组和限定保真，默认视图不是完整交付范围 |

模型 `entity_id/claim_id/schema_card_id/graph_ref/mention_ref` 均通过当前上下文解析，不作为路径、URL 或数据库键直接执行。模型临时 ID 映射到持久 ID；新证据包不沿用旧短引用含义。`VersionedRef={id,revision}` 沿用现有类型。

`Quote={evidence_id:str,text:str,context_text:str|null}`，三个字段必需。服务端调用 `resolve_fragment_quote` 唯一定位；context_text 须为同源逐字片段且包含 text。重复文字缺少可唯一定位上下文时不猜测。模型不提供可信坐标，最终 EvidenceAnchor 由 IR 回填身份和半开区间。

## 2. SchemaCard（新增于 claim_protocol.py）

| 字段 | 类型和来源 |
|---|---|
| schema_card_id | 卡片内容 hash 的局部 ID |
| subject_ref、class_iris | 当前主体精确引用、合法类型列表 |
| ontology_snapshot_id、menu_id | 冻结本体与 LocalMenu |
| predicates | 裁选的 SlotSpec/EdgeSpec，保留 declared_by 与约束状态 |
| quantity_policies | QuantityPolicy 列表 |
| identity_keys | IdentityKeySpec 列表 |
| unsupported_constraints | `{predicate_iri,construct,reason_code}` 列表 |

`QuantityPolicy={predicate_iri,allowed_forms,endpoint_role,allowed_target_units,unit_requirement,declaration_ref}`：

- allowed_forms 为 scalar/interval/lower_bound/upper_bound；endpoint_role 为 lower/upper/null。
- unit_requirement 为 physical/dimensionless/count/not_declared；目标单位不能从字段名推断。
- 无数量形态声明时，已解析数值 datatype 只支持精确标量；区间保留观察/未决，不扩宽槽位。
- declaration_ref 指冻结本体声明或运行输入的声明式抽取 profile。profile 只描述表示、单位和身份映射，不放文档答案、章节偏好或领域算法。

`IdentityKeySpec={class_iri,property_iris:list[str],namespace:str|null,scope:document|dataset|global,declaration_ref}`。复合键须有全部分量、同一 owner 和适用域证明。名称不是默认唯一键；未支持的 OWL key 构造显式报告，不把读出语法当完整推理。

## 3. 候选与冻结声明（新增于 claim_protocol.py）

以下字段与 stage-schemas.json 的 discovery 对应；运行时仅可按菜单与目标收紧枚举。

| 类型 | 字段 | 不变量 |
|---|---|---|
| EntityProposal | local_id、class_iri、representation、mentions、record_components、identifier_claims | representation=mention/record；相应来源非空；类型须核验 |
| RecordComponent | role、quote | role=subject/field/value/context；不能拿关联对象名称替代主体 |
| IdentifierProposal | predicate_iri、value_quote | 使用本体属性，不预设编号字段 |
| PropertyProposal | local_id、subject_id、predicate_iri、value_quote、field_support、unit_support、qualifiers | 原表达完整，模型不填写可信规范化结果 |
| RelationProposal | local_id、subject_id、predicate_iri、object_ids、selection、bridge_support、selection_support、qualifiers | 对象唯一；单对象 selection=all；多对象保留组 |
| ExternalLinkProposal | local_id、subject_id、external_candidate_id、identity_support | 候选来自当前 query_instances 的真实返回 |
| ObservationProposal | subject_id|null、predicate_iri|null、quote、kind、reason | kind=missing/unknown/ambiguous/unbound；不补造主体 |

PropertyProposal 和 RelationProposal 均另含 `bridge_kind`、`bridge_ref_ids`。bridge_kind 复用现有 explicit_assertion/owned_field_group/role_mapped_table/resolved_reference_chain/document_subject_description，并按当前结构和卡片裁选；same_document/same_name/adjacent_only/path_reachability 永不进入可用证明菜单。bridge_ref_ids 只引用上下文中已核验的桥接链；resolved_reference_chain 必须可解析为包含 predicate_assertion 步的完整链。新共指须先经既有局部共指核验形成桥接引用，不能模型编造 proof ID。普通显式叙述可用空数组，但仍需原文 support。桥接类别和引用一起进入声明内容 hash。

所有 `local_id` 在同一 DiscoveryEnvelope 内全局唯一，不能与本次上下文已登记 ID 冲突；subject_id/object_ids 只解析到本轮 entities 或已登记的上下文实体，不跨类型引用属性/关系 ID。碰撞、重复和悬空引用在冻结前逐项拒绝，依赖项保持未决；不重命名后静默猜测其指向。local_ref_map 是该唯一命名空间到精确 VersionedRef 的映射。

`Qualifiers={polarity,modality,condition_support,scope_qualifiers}`：polarity=affirmed/negated；modality=asserted/required/possible/planned/unspecified。unspecified 不默认转 asserted。scope_qualifiers 为 `{predicate_iri:str|null,quote:Quote}` 列表，保留本体允许属性或原文维度，不用旧业务枚举。旧产物 polarity=conditional/uncertain 原样保留；不能把历史不明模态自动改为 possible。

冻结函数生成 `FrozenClaimSet={assertion_generation,evidence_revision,content_hash,entities,properties,relations,external_links,observations,local_ref_map}`。服务端生成 claim_ref/hash，覆盖类型、端点、谓词、原值、极性、模态、selection、条件和继承 scope。证据包 hash 单独保存；只补证保持声明 hash，但使核验输入身份变化。record 候选在 freeze 时依据组成来源形成稳定局部候选 ID，核验只接纳/拒绝，不在核验后偷换 ID/hash；组成来源改变才产生新候选代。不以标签或拼接整行冒充物理提及。

## 4. 独立核验

程序构造目标 `{target_id,target_kind,content_hash,required_facets}`。模型逐目标返回 target_id、content_hash、facets；每个 facet 为 `{name,verdict,support,counterevidence_support,reason}`，verdict=supported/unsupported/undetermined。

| 目标 | 必需 facet |
|---|---|
| entity | type、referent、subject_role |
| property | subject_binding、field_role、predicate、bridge、value、unit、qualifiers、counterevidence |
| relation | subject_binding、object_binding、predicate、bridge、selection、qualifiers、counterevidence |
| external_link | identity_owner、identity_key、identity_scope |

服务端排除不适用 facet，如纯文本 unit、单对象 selection；程序指定的文档根类型/身份按输入来源单独登记，不生成伪造的模型核验目标，但其属性或关系的 subject_binding 仍须证明。模型不能自行跳过维度。响应的目标与 facet 集合须和请求完全相等；禁止重复、遗漏、增加和 hash 改变。supported 必须有合法原文依据，反证必须覆盖。实体组成证明属于 referent/subject_role，模态和条件属于 qualifiers；属性与关系均通过 bridge 核验其桥接类别及引用链。

现有 VerificationBundle/ProofGate 保留，新增选择和记录组成证明。accepted 要求机械门、必需语义维度、表示和依赖通过；缺证/表示能力不足为 unresolved，明确错配/反证为 rejected。被支持的 negated 声明可以进入图谱，不等于 rejected。

新版 ProofGate 使用本协议 required_facets 及实际结构生成菜单，不能继续调用按谓词 local name 分支的旧 PredicatePolicyRegistry。关系对象类型来自精确版本的实体核验依赖，不把另一目标的 type 判定直接换 target_id 复用。facet 的原文判定、程序根绑定和实体类型依赖分别验证，再组装 PredicateEvidence/VerificationBundle。

## 5. 数量、身份与来源

`QuantityValue={form,raw,source_unit,target_unit,scalar,lower,upper,lower_inclusive,upper_inclusive,comparator,endpoint_role,datatype_iri,dimension,conversion_record}`。

- 数值使用十进制字符串或 null，不经过 float；边界为 bool|null。
- scalar 仅 scalar 非空；interval 保留 lower/upper 及开闭；bound 保留对应端点与比较符。
- 端点属性保留完整区间 raw/引文，endpoint_role 来自卡片和核验。
- 复用精确比例/偏移与受控 registry；别名、恒等、真实换算分开统计。目标单位为空不擅自去掉原单位。
- 未支持的复合量、需假设或无法精确表示的换算未决；不承诺任意单位代数。

`ExternalCandidate={candidate_id,source_id,system,dataset,record_key,record_version,class_iri,matches,mapped_fields,metadata}`。

- matches：`{predicate_iri|null,document_quote,record_field,record_value,match_kind}`，match_kind=exact_key/key_component/name/alias，只是候选依据。
- mapped_fields：`{predicate_iri,raw_value,datatype_iri|null}`；metadata：`{name,value}` 字符串列表。未映射元数据不扩展本体。
- 外链须独立核验，并复用原外部 provenance/版本检查。文档值和外部值不互相覆盖。

## 6. 图谱契约

保留 GraphEdge/公开 GraphRelationship 的单个 object_ref；新增独立 **GraphRelationshipGroup**，不把单对象字段改成 union。

| 位置 | 扩展 |
|---|---|
| LocalReferent | kind=mention/record、record_view_refs、composition_decision_ref；record 不伪造 SourceMention |
| GraphNode | referent_ref、type_decision_ref、referent_decision_ref、dependency_refs、grounding_kind=document_root/mention/record；record 另绑定 composition_decision_ref，用户指定根由 root_origin 区分 |
| GraphRelationshipGroup | 复用 GraphEdge 的主体、谓词、证明、状态和来源字段；object_refs 至少两个唯一精确引用；selection、selection_evidence_refs |
| GraphProperty/GraphEdge/组 | modality、scope:TraversalScope；原 conditions/applicability 与证据保留，新限定不受业务维度枚举限制 |
| VerificationTarget | 新增与 object_ref 互斥的 object_refs/selection；身份 hash 覆盖组；check_kind 增加 selection/record_composition/modality |
| PredicateEvidence | selection_support_refs、modality_support_refs |
| GraphSnapshot/TaskOutcome | relationship_groups 集合 |
| 公开 schema、前端 | 同构组类型/集合；VersionedRef 经现有公开映射，不混内部 id 和公开 entity_id |

构图规则固定：单对象进 edges，多对象进 relationship_groups，不同时造重复单边。one_of 要求恰选一个证明，仅“或”不够；undetermined 仅进诊断候选。所有组成员须准确解析且证明完整。绘图虚拟连接点不是本体实体，不能作为新节点计分。

候选限定的落图映射固定：condition_support 的逐字文本进入 conditions，解析后的锚点进入 condition_evidence_refs；scope_qualifiers 进入 `applicability={"qualifiers":list[GroundedScopeQualifier]}`，其中 `GroundedScopeQualifier={predicate_iri:str|null,text:str,evidence_refs:list[EvidenceAnchor]}`。没有适用性限定时仍用空对象 `{}`，不以含空数组的对象制造“有条件”结果。公开映射将 EvidenceAnchor 转为原文选区；旧协议 applicability 仍按旧结构读取，不将旧业务键重新解释为新版限定。

新增 verified 投影：保留当前证明合格的属性、边、组和独立核验节点，包含限定/否定/模态，不要求根可达。非根节点不能仅凭默认 decision_status=supported 进入 verified，须有 type/referent 证明。新版 effective 须同时要求 modality=asserted、空 scope、无 conditions/applicability；只纳入根可达的普通肯定边及 selection=all 的合格组，不把 one_of/alternatives 展成边。新版 conditional 视图按实际条件、适用性或继承限定选择，不能只判断旧 polarity=conditional。all 仍为含未决的诊断视图。

RunResponse 与 GraphArtifactResponse 新增 `extraction_protocol:str|null`，从冻结运行身份读取，新协议值为 ontology-tool-extraction-v1，旧缺省 null。前端据此选择 verified；旧运行请求 verified 返回 unsupported_projection，不重新解释旧事实。新字段在旧对象序列化时按现有 preserve_legacy_* 方式保持原形，不以新默认值改写旧 hash。project_graph 的 relationship_groups 参数默认空，旧调用保持原 policy。

公开 GraphArtifactResponse 另含 `scope_resolutions:list[ScopeResolution]`，仅按本次返回声明实际使用的 scope_id 派生；`ScopeResolution={scope_id,steps:list[ResolvedScopeMember]}`。每步为 `{relation_ref,member_ref,selection,polarity,modality,conditions:list[str],applicability:list[PublicScopeQualifier],evidence_selection_ids:list[str]}`，单边 selection=null，组使用其原 selection；`PublicScopeQualifier={predicate_iri:str|null,text:str,evidence_selection_ids:list[str]}`。精确引用仍是权威，public_projection 从当前关系版本读取限定并生成原文选区，不能让模型填这个摘要。即使 conditional 等过滤视图未显示父组，前端仍能解释继承范围；这些派生摘要不计作新事实、不另存权威状态。依赖无法精确解析时相关声明不能进入 verified，禁止输出看似无限定的替代声明。

## 7. 组成员与限定关系的展开

`TraversalScope={scope_id,members:list[ScopeMember]}`；`ScopeMember={relation_ref:VersionedRef,member_ref:VersionedRef}`，relation_ref 可指单边或组。空集合代表无路径附加限定；scope_id 为规范排序成员列表的 hash。具体条件、模态和 selection 由精确关系版本解析，不由模型重填。

1. 新增 frontier_eligibility，与 effective_proof_gate 分开：肯定且证明完备的组/带限定关系可在 scope 下继续，不提升为普通无条件边。
2. negated、selection=undetermined、modality=unspecified、依赖失效或表示不完整不驱动展开，保留未执行原因。
3. 单对象无限定实际肯定边无需新 scope；组/限定关系逐成员追加范围。相同关系成员不重复追加，避免循环造成无限 scope。
4. 同一 one_of 组的不同成员不能合成同时成立的 scope；冲突记 scope_conflict 并停止该路径。
5. task_id、lineage、registered_subjects、slot/plan/search/coverage、检索缓存和 lazy frontier 去重键均含 scope_id。主体键 `(entity,revision,scope)`，slot 键 `(entity,revision,scope,predicate)`。
6. 后继声明继承范围；只有另行在无范围任务取得独立原文证明，才可生成无限定声明，不能删除 scope 去重。
7. 组修订/成员变化通过 DependencyIndex 使依赖失效，不直接重指向新版。max_hops 和任务/请求预算沿用，未展开成员进入覆盖缺口。

新 scope tuple 只用于新协议的任务和恢复；旧运行按原键和冻结投影读取，不把三元旧 slot key 静默补成新键后重算。

## 8. 唯一当前协议状态

新协议 `ontology-tool-extraction-v1` 只用于明确创建的新运行，同时冻结 `api_protocol="responses"` 并进入 fingerprint，不与旧 Chat 协议结果混用。当前 `protocols[lineage_id]` 承载以下字段，不新增运行状态机：

| 字段 | 含义 |
|---|---|
| version、lineage_id、base_target、scope_id | 协议值 ontology-tool-extraction-v1 和精确任务身份；外层调用账本 version 仍为数字 2 |
| api_protocol | 固定 responses；新运行创建时冻结，不在恢复时切换协议 |
| stage | discovery/verification/finalize，当前局部阶段 |
| assertion_generation、evidence_revision、evidence_hash、context_hash | 前三者沿用既有读写名称；evidence_hash 为冻结来源集合/权限 hash，context_hash 为含卡片、字段归属和范围的完整模型上下文 hash |
| request_attempt、completed_attempts | 沿用请求序号和已确认响应收据；工具/阶段不重置。确认收到与 response_status=completed/语义通过不同，incomplete/failed 的收到也有精确收据 |
| active_instructions | 当前阶段的可信执行规则；每轮 Responses 请求显式发送，阶段切换重新装配 |
| active_input_items | 当前阶段完整有序 input 项，包括初始材料、已返回的完整 output items 和配对的 function_call_output；阶段完成后释放，不保存逐轮整段会话副本 |
| pending_request | `{attempt,stage,request_hash,reservation_key}` 或 null，request_hash 是完整请求 hash，沿用现有预留读取字段；先保存后请求 |
| turn_refs | 当前阶段按 attempt 引用 ModelTurnResult；直接恢复，不扫描历史 |
| pending_call_ids、completed_tool_results | 当前 function_call 批次未完成的 call_id 与已确认结果引用；output item.id 不能替代 call_id |
| tool_calls_used、materialized_refs | 当前 lineage 模型请求的工具尝试数及当前可用卡片/mention/candidate/graph 引用；每项 kind/id/result_ref/content_hash/context_hash/dependency_refs，类型见[工具契约](contracts/tool-contracts.md)；只保存继续所需映射 |
| discovery_ref、verification_ref、outcome_ref | 当前已提交业务阶段结果引用 |
| recovery_kind、recovery_used | none/evidence/reproposal；共享一次恢复机会 |

`ModelTurnResult={attempt,stage,input_hash,response_id,output_items,response_status,incomplete_details,error,usage}`，使用现有 DocumentRunResult。response_id 是本次响应身份，不是继续会话的依赖；output_items 是完整有序 output，保留 message、function_call 和实际返回的 reasoning 等项。response_status 保留 completed/incomplete/failed；非预期状态显式报告协议异常。incomplete_details/error 为原协议对象或 null，refusal 保留在对应 message 内容项中。usage 保留可空的 Responses 嵌套结构，包括 input_tokens/output_tokens/total_tokens、input_tokens_details.cached_tokens 与 output_tokens_details.reasoning_tokens；分项不重复累加，缺报不补 0。每条 DocumentRunRequest.result_ref 指向该请求精确轮次；工具结果以 attempt+call_id 独立保存于既有结果域，当前协议引用它们。每个轮次结果不包含全部 input 历史。

请求采用无服务端会话依赖的 `store=false`，每轮发送 active_instructions 和完整 active_input_items，不用 previous_response_id/conversation。工具 output 项按 call_id 配对，工具结果作为 `{type:"function_call_output",call_id,output:JSON字符串}` 加入当前 input。工具调用项自己的 id 保留原值但不作配对键。完整 output 先保存再判定是否可消费；incomplete/failed/refusal 不执行其中部分调用，也不生成通过的阶段业务结果。

实际返回的 reasoning/encrypted 内容仅作为不透明协议项保存和续传，不转为事实引用、不展示给用户或解析其推理；只在冻结端点能力支持时发送相应 include。新核验阶段只依据授权原文和冻结目标重建 input，不继承发现阶段 reasoning。`store=false` 不免除本地保存当前恢复所需协议项，也不要求把这些内容复制到展示图。

ModelTurnResult.input_hash 对应 pending_request.request_hash，覆盖 api_protocol、instructions、完整 input items、工具定义、text.format、store/include 等实际请求参数，不引入另一个含义不同的请求 hash。reservation_key 在预留前按现有 call_request_key 对 `[lineage_id,request_attempt]` 的规范 hash 预计算；把同算法纯 helper 放入 current_work.py 供共享核心和存储调用，不从共享核心导入 document_analysis/current_state.py。模型请求的工具每次获准分派前保存 tool_calls_used，校验或执行失败不返还；已完成 call_id 恢复不重复计数，未完成尝试若重做仍受剩余额度约束。控制器必做的 binding/metric/SHACL 单列本地执行次数与耗时，不消耗模型选工具的额度，也不因该额度用完而跳过。

提交顺序：预留→模型请求→提交完整 response→确认可消费状态并登记完整 output items→逐工具执行/提交并追加 function_call_output→下一次预留。没有持久化确认不继续。保存响应后暂停可继续待执行工具；保存工具结果后不重复调用。预留后结果未知保持未知费用和未完成，不假定未发出或自动零成本重发；已经收到 incomplete/failed 与未收到结果分别记录。

DocumentRunCandidate.kind、当前状态 domain 为字符串，body 为 JSON，现有约束没有 kind 枚举；新增关系组与协议结果预计无需数据库迁移。必须修改 collection 白名单和序列化，验证 PostgreSQL 事务/租约路径；不新建表或历史快照树。

## 9. Harness 的派生上下文和本轮计划

以下对象服务请求装配和控制决策，不增加 protocol stage，不新增数据库状态。模型阶段回答仍严格使用原 discovery/verification Schema，不能在回答中填写这些控制字段。

`TurnPlan` 是 tool_model_adapter.py 的冻结 dataclass：

| 字段 | 类型/含义 |
|---|---|
| stage | discovery/verification；finalize 不调用模型 |
| mode | tools/answer/stop |
| allowed_tool_names | list[ToolName]；answer/stop 为空，tools 也须受阶段与模式白名单限制 |
| model_calls_remaining | int；来自协调器已预留账本的当前余量，包括未知结局已占额度 |
| reserved_model_calls | int；discovery/tools 留 2、discovery/answer 留 1、verification/tools 留 1、verification/answer 留 0，stop 为 0；这是本轮请求之后必需轮次的底额，不另扣一笔预算 |
| reason_code | str；说明允许工具、无工具可用、重复无进展、额度不足或其他当前约束 |

plan_model_turn 在当前工具批次处理完毕后调用；已有持久化阶段回答则不调用此函数重开生成。发起本轮请求必须满足 `model_calls_remaining >= 1 + reserved_model_calls`，未知结局已在余量中扣除。`batch_progress=None` 表示尚无可比较的完成批次，不能解释为 false；false 只用于同工具/规范参数和上下文/来源/声明依赖下重复执行且整批无新材料的情况。参数首次失败不因此剥夺仍有预算的纠正机会。

`ModelContextView` 是现有 context.py 的冻结 dataclass，字段如下：

| 字段 | 类型/来源 |
|---|---|
| task_id、subject_ref、predicate_iri、scope | 当前 RecognitionTask 身份及 TraversalScope |
| stage、context_hash、evidence_hash | 当前协议阶段和已冻结上下文/证据身份 |
| schema_card | SchemaCard，类型/谓词和表示约束 |
| source_catalog | list[SourceCatalogEntry]，按任务裁选的定位目录 |
| evidence_units | list[EvidenceUnit]，授权完整原文及必要结构/反证 |
| registered_refs | list[VersionedRef]，本轮允许引用的当前精确实体/声明；不授予新权限 |
| verification_targets | list[VerificationTargetSpec]，核验阶段的精确目标，否则为空 |
| tool_observations | list[ToolObservation]，当前阶段已确认调用/结果，由现有 result_ref 加载 |
| feedback | list[ContextFeedback]，已发生的检查缺口 |
| turn | TurnPlan，展示额度与可用动作的只读副本 |

`SourceCatalogEntry={record_id:str,title:str|null,summary:str|null,summary_source:str|null,authorized_evidence_ids:list[str]}`。目录摘要永远是定位线索；列表只包含已有权限的 evidence_id，未读取记录通过现有 retrieve 路径取得。目录本身不产生 EvidenceAnchor。

`ToolObservation={request_attempt:int,call_id:str,tool_name:ToolName,arguments:EvidenceModel,result_ref:str,result:ToolResult}` 是 tool_contracts.py 中的冻结 dataclass，arguments/result 按同一静态注册表解析为具体类型。恢复从原 ModelTurnResult 的 function_call 项与已确认工具结果组装，不重复存储这些数据。渲染调用实际具体类型的序列化方法，不用基类字段裁掉工具数据；对应当前 input 中的 function_call/function_call_output 配对项，不能把同一完整结果又复制到 user 内容。其他 output 项继续按原序保存，不因工具视图不使用而丢弃。

`ContextFeedback={target_id:str|null,facet:str|null,code:str,field_path:str|null,message:str,evidence_ids:list[str]}` 定义在 context.py。字段错误使用 JSON Pointer，非字段错误为 null；target/facet 从服务端检查结果读取，不接受模型自报已通过。反馈说明受违反的约束及允许的下一步，不包含未授权来源或金标答案。

context.py 对 SchemaCard、ToolObservation、TurnPlan 等仅作 TYPE_CHECKING 类型引用；纯派生函数操作已传入的类型化对象，禁止导入编排器执行逻辑造成 context↔adapter 或 context↔tool_contracts 的运行时循环。持久化仍使用第 8 节的 active_instructions、active_input_items、turn_refs 和 materialized_refs。
