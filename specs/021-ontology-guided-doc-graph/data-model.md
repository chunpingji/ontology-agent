# Data Model: 本体指引的文档分析运行与关系图谱

日期：2026-09-08。本文定义领域对象、不可变身份、状态机及建议的 SQL 持久化映射。字段名是 Phase 1 契约；具体索引长度等数据库细节在 Alembic 实现时以 PostgreSQL/SQLite 契约测试固定。

## 1. 通用约定

- 所有业务时间为带时区 UTC；所有内容 hash 为小写 SHA-256。
- `recognition_run_id` 是用户可见稳定运行 ID；`execution_token` 是一次领取的秘密 fencing generation，两者不得互换。
- 所有可变 head 带单调 `revision`；客户端控制操作提交 `expected_revision`，不匹配返回 409。
- 原始提议、模型答复、判定、证明、resolution 和运行事件追加式不可变；重验创建新对象/版本，不覆盖历史。
- `candidate_id@revision`、`entity_id@revision`、`proof_id@revision` 等引用必须完整保存。禁止把缺省 revision 解释为 1，禁止静默追随 current head。
- 模型生成内容不直接成为状态。只有服务端验证后的 event batch 能推进 head、资格或图快照。
- 标题、摘要和检索分数的 authority 固定为 `retrieval_only`；只有可回放原文或受控外部/人工/派生 provenance 可以支持语义事实。

## 2. 运行聚合

### 2.1 DocumentAnalysisRun

一次显式上传及本体根类型选择产生一个运行。

| Field | Type / rule |
|---|---|
| recognition_run_id | UUID；创建后永不改变 |
| contract_version | 当前为 `document-analysis-runs-v1` |
| owner_id | 认证用户稳定标识；参与所有授权检查 |
| request_key | owner 范围内幂等键，1—200 字符 |
| request_hash | 源字节 hash、规范文件名、root_class_iri、元数据/范围选项的 hash |
| source_filename | 只保存安全 basename；原名影响标题 fallback，属于输入身份 |
| source_artifact_ref | 指向本运行授权的原件；不得是客户端路径 |
| root_class_iri | 用户提交且经 RootSeedPolicy 验证的完整 IRI |
| root_class_label | 冻结快照的展示标签；不作身份 |
| metadata_mode | `cached_summary | generate_summary | structure_only` |
| scope_mode | `document_graph | focus_path`；在线页面默认 `document_graph` |
| focus_path | focus 模式的谓词 IRI 序列；document_graph 时为空 |
| status | RunStatus |
| stage | RunStage |
| revision | 每次合法控制/持久 head 更新递增 |
| event_head | 已提交的最后 event sequence，初始 0 |
| artifact_revision | 任一公开 artifact 水位变化时递增 |
| provisional_fingerprint | 上传验证后可计算的输入/配置身份 |
| final_fingerprint | IR、元数据、本体、模型及政策冻结后计算；此前为空 |
| ontology_snapshot_id / analysis_id / metadata_snapshot_id / graph_snapshot_id | 当前公开 artifact 精确引用；各自可为空 |
| progress | RunProgress 当前投影；不是 checkpoint |
| error | `{code, stage, retryable, safe_detail, occurred_at}` 或空；不得暴露原文/prompt |
| created_at / started_at / paused_at / finished_at | 生命周期时间 |
| retention_policy_version / expires_at | 运行中为空；终态或 paused 按配置计算 |
| deleted_at | 仅 tombstone 可见；普通读取删除后不再返回业务内容 |

唯一约束：`(owner_id, request_key)`。相同键且 `request_hash` 相同返回同一运行；相同键而 hash 不同冲突。文件 hash 相同但新 request_key 不被强行合并为同一运行。

### 2.2 RunFingerprint

采用两阶段冻结，运行 ID 不随阶段变化：

```text
provisional = hash(
  contract_version, original_source_hash, safe_filename,
  root_class_iri, metadata_mode, scope_mode, focus_path,
  configured parser/model/budget/policy identities
)

final = hash(
  provisional, converted_document_hash, structure_hash, analysis_id,
  ontology_snapshot_hash, metadata_snapshot_dependency_hash,
  tokenizer_identity, model_identity, protocol_version,
  retrieval/scheduler/proof/eligibility/resolution policies,
  semantic and technical retry budgets
)
```

恢复必须精确匹配 `final_fingerprint`。本体、摘要、模型、tokenizer、预算或政策变化只允许创建新运行，不能把旧 checkpoint 套到新配置。

### 2.3 RunStatus 与 RunStage

RunStatus：

```text
queued -> running -> finished
   |         |  \-> retryable_failure -> queued
   |         |  \-> blocked_dependency
   |         |  \-> paused -> queued
   |         \----> cancelled
   \--------------> cancelled

finished | paused | retryable_failure | blocked_dependency | cancelled
    -> deleting -> deleted
paused | retryable_failure | blocked_dependency | finished | cancelled -> expired
```

规则：

- `finished` 仅表示声明范围的 ledger 已进入可解释停止状态，不等于客观全文无其他事实。
- `cancelled`、`deleted`、`expired` 不可恢复；取消可保留已有 artifact 到到期/删除。
- `paused` 仅在安全持久批次边界生效，可在 `expires_at` 前恢复。
- `retryable_failure` 是技术失败；不得投影成 semantic `unsupported`。
- `blocked_dependency` 保存具体缺失依赖；无新依赖时不得重复采样刷结果。
- `deleting` 之前必须撤销 execution token；迟到 worker 的 batch 被 fencing 拒绝。

RunStage：`accepted | storing_source | converting | parsing | preparing_metadata | freezing_inputs | planning | extracting | projecting | finalizing | complete`。状态与阶段正交；例如 paused 运行可停在 extracting，图谱失败时 metadata 仍可 ready。

### 2.4 RunProgress

```text
RunProgress:
  tasks_attempted
  model_calls
  records_planned
  records_examined
  records_incomplete
  records_unattempted
  phase_counts: {phase1, phase2}
  decisions: {supported, unsupported, undetermined, not_checked}
  pending_frontiers
  active_subject_ref / active_predicate_iri / active_record_ref | null
  stop_reason | null
  contract_version
  event_head
  artifact_revision
```

没有可靠总量时不返回伪造 percentage。计划记录数不是模型调用分母。

### 2.5 ExecutionLease 与 CheckpointEnvelope

`ExecutionLease`：`run_id、execution_token_hash、status、actor、worker_id、lease_expires_at、heartbeat_at、pause_requested、cancel_requested、generation`。每个运行唯一活动 row；claim/heartbeat/fence 通过数据库条件更新仲裁。

`CheckpointEnvelope`：`run_id、final_fingerprint、event_head、artifact_revision、scheduler_version、queue_refs、active_task_ref、completed_task_keys、retry_ledgers、frontier、dependency_head、candidate_heads、proof_heads、coverage_head、model_call_count、created_at`。

Checkpoint 只引用已提交对象或完整待处理 payload，不是另一个事实仓。公开 API 不返回其私有字段。

## 3. 冻结输入与检索产物

### 3.1 SourceArtifact 与 DocumentIR

`SourceArtifact` 保存 original/converted 两种 bytes 的内容 hash、size、media type、安全文件名、storage locator、malware/zip-resource validation 状态和 ownership。locator 只能由服务端解析。

`DocumentIR` 复用现有契约：原件/转换件 hash、parser/structure policy、analysis_id、章节 nodes、原序 blocks、递归 tables、原子 evidence units、pagination、diagnostics、structure_hash。新增运行引用不改变 EvidenceAnchor 的回放规则。

### 3.2 OntologySnapshot

```text
OntologySnapshot:
  snapshot_id
  ontology_hash / release_ref
  module_hashes
  classes[]:
    iri, labels, description, declared_by, ancestors, inheritance_paths
    data_properties[], object_properties[], restrictions[]
  property declaration:
    iri, kind, labels, description, declared_by
    domain_expression, range_expression, constraints, source_hash
    inherited_via[], constraint_status
  unresolved_constraints[]
  created_at
```

`constraint_status = resolved | constraint_unresolved`。不支持的 union/限制表达式不展开为宽松并集；受影响 Slot/Edge 进入 blocked/undetermined，其他菜单继续。

### 3.3 LocalMenu、SlotSpec 与 EdgeSpec

`LocalMenu` 由 `ontology_snapshot_id + subject EntityInterpretation@revision` 确定：

- `SlotSpec`：当前主体合法数据属性、datatype/unit/cardinality/identity_key、declared_by 和政策 ref。
- `EdgeSpec`：当前主体合法对象谓词、精确 range 表达式、合法子类闭包、最大基数、declared_by 和政策 ref。
- `retrieval_definitions` 可含当前 edge range 类的一层字段/身份定义，仅用于排序和角色区分。

对象边支持后才为具体对象编译下一 LocalMenu。未关联对象的二跳类型不得进入全文 allowed output menu。

### 3.4 MetadataSnapshot 与 RetrievalHint

| Field | Rule |
|---|---|
| snapshot_id | 内容寻址 ID |
| analysis_id / document_hash / structure_hash | 必须与运行 IR 一致 |
| summary_version / summary_model_identity | 无摘要时显式为空/`none` |
| generation_source | `model_summary | extractive_fallback | structure_only | mixed` |
| source_record_refs | 仅指向真实 IR 记录；摘要自身没有 fact evidence ID |
| node_summaries | node_id、status/source、content、content_hash、generated_at |
| dependency_hash | IR、摘要配置和实际输出的 hash |
| frozen_at | 识别计划开始前冻结 |

`RetrievalHint`：`hint_id、snapshot_id、node_id/record_ids、subject_type_iri、predicate_iri、object_type_iris、hint_kind、support_origin、score_components、policy_version、authority=retrieval_only`。未知 IRI 或超出 LocalMenu 的标签拒绝。

### 3.5 SourceRecord、RecordView 与 FieldGroup

- `SourceRecord`：一个段落、完整逻辑表格行或明确标题记录，保存 source/binding/heading/note/parent ranges、section、table path、row 和物理 membership。标题默认不作为 mention target。
- `RecordView`：同一物理 cell/段落在一个逻辑行/字段上下文中的观察，保存 `source_cell_id、logical_row_ids、column_ids、header_refs、rowspan/colspan、unit/legend/note refs、parent_table_refs`；不创建新原文。
- `FieldGroup`：同章节连续的带标签字段块，保存完整成员和边界、候选 owners、反证与结构依据。标题、表格和章节边界阻断无条件扩展。

超 token 的完整记录标 `context_budget_exceeded` 并留在 ledger。只有能保留 owner/谓词角色、表头、单位、合并关系、条件和竞争主体完整闭包的显式投影才可分块。

### 3.6 RetrievalPlan

计划键：

```text
hash(subject_ref@revision, predicate_iri, ontology_snapshot_id,
     analysis_id, metadata_snapshot_id, retrieval_policy_version, scope_mode)
```

每个 `PlannedRecord` 保存 `record_ref、phase、section、rank_score/components、rationale、discovery_ranges、binding_ranges、authorized_claim_ranges、authorization_state`。Phase 1/2 不重叠；并集必须等于 scope 内全部非标题目标记录。路由阴性、零分或前一记录拒绝均不能从 Phase 2 删除记录。

## 4. 提及、实体解释与值

### 4.1 SourceMention

```text
mention_id = hash(analysis_id, source_cell_id | evidence_id, exact_source_spans, text)
```

字段：`mention_id、analysis_id、source_spans、source_cell_id、text、record_view_refs、proposal_refs、structural_status`。同一合并 cell 被多行观察只追加 `record_view_ref`；task、检索阶段和模型类型不参与 ID。

### 4.2 LocalReferent

字段：`referent_id、revision、mention_refs、coreference_proof_refs、alternative_refs、qualifiers、state`。`state = proposed | supported | undetermined | invalidated | split`。同名不自动共指；允许没有全局身份。

### 4.3 EntityInterpretation

字段：`entity_id、revision、referent_ref、class_iri、type_decision_ref、field_role_refs、qualifiers、level_evidence_refs、identity_origin、state`。

`identity_origin = document_local | verified_key | verified_external`。Product/API/批次等竞争解释分别保存；未通过类型解释不能形成肯定节点或驱动递归。

### 4.4 IdentityClaim

字段：`identity_claim_id、revision、entity_ref、key_predicate、value_span_ref、field_role_refs、identified_role、namespace、uniqueness_scope、qualifiers、decision_ref、state`。

本体 identity property 只是必要条件。角色、实体对应关系和所请求唯一性范围都 supported 后才可将 `identity_origin` 升级为 verified。试验来源号不能被当 API 全局身份；错误的 model true 也不能绕过门禁。

### 4.5 FieldRoleClaim 与 ValueObservation

`FieldRoleClaim`：`claim_id、span/header refs、record_view_ref、owner_ref/owner_hypothesis、role_kind、ontology_slot_ref、decision_ref、verdict`。`role_kind` 至少覆盖 `entity_name、property_value、project_code、study_source_id、batch_id、document_id、composition_member、condition、unknown`。

`ValueObservation`：`observation_id、slot_ref、owner_hypothesis、raw_text、source_refs、presence、interpretation、normalizer_version`。

- `presence = present | placeholder | not_mentioned`
- placeholder interpretation：`unknown | not_applicable | unspecified`
- `否` 可以是 present boolean false；不得被缺失过滤。
- `N/A` 默认 placeholder/unspecified，不生成 CAS 或其他业务值。

## 5. 验证与证明

### 5.1 VerificationTarget

由服务端冻结：

```text
target_id = hash(
  final_fingerprint, claim_ref@revision, check_kind,
  root_ref, subject_ref@revision, predicate_spec_ref,
  object_ref@revision | literal_hash, polarity, applicability,
  source_scope_hash, context_hash
)
```

`check_kind = mention_location | type | field_role | local_coreference | global_identity | predicate_entailment | applicability | dependency`。目标分别保存 `document_context` 与当前 `subject_context`，不再用一个 effective_class 混用文档和当前主体。

### 5.2 SemanticDecision

字段：`decision_id、target_id、attempt_id、check_kind、verdict、reason_code、reason、reason_status、support_refs、counterevidence_refs、searched_context_refs、verifier_version、model_identity、raw_response_ref、created_at`。

- `verdict = supported | unsupported | undetermined | not_checked`
- `reason_status = supplied | missing_legacy | system_prerequisite`
- 协议/超时/token/模型不可用是 execution event；不伪装为 unsupported。
- 模型拒答产生 `model_refusal` event 和 undetermined。
- 前置检查失败后，后续检查由程序写 not_checked 及 prerequisite reason；继续同批其他提议/记录。

### 5.3 PredicateEvidence

```text
PredicateEvidence:
  proof_id / proof_revision / target_id / predicate_iri
  subject_role_refs
  object_role_refs | value_role_refs
  predicate_support_refs
  bridge_kind
  bridge_steps[]:
    step_kind, input_refs, output_role, source_refs, decision_ref
  competing_owner_refs / counterevidence_refs
  applicability_refs / dependency_refs
  verdict / proof_policy_version
```

允许的初始 bridge：`explicit_assertion、owned_field_group、role_mapped_table、resolved_reference_chain、document_subject_description`。`same_document、same_name、adjacent_only、path_reachability` 只能成为线索。`document_subject_description` 仅适用于经政策批准的文档描述谓词，不能证明产品组成或任意属性。

### 5.4 PredicatePolicySnapshot

不可变版本记录按精确 predicate IRI 保存允许 bridge、必需 owner/object/value/condition 角色、禁止推论和审核身份。无专门政策时使用通用保守政策：只接受明确断言、已映射 owner 的字段组/表格或经证明指称链。政策不是 TTL 修改，不含文件名/项目号专用规则。

### 5.5 四层判定

每个候选分别投影：

1. `structural_valid`：目标、引用、span、端点、版本、bridge 结构合法。
2. `model_supported`：当前语义 Decision supported。
3. `policy_eligible`：ProofGate、极性/条件、依赖和本体政策允许。
4. `independent_review`：`unreviewed | accepted | rejected`；首期无编辑/审核 UI，默认 unreviewed。

任何一层不得自动填充后一层。分析图的有效结果不等于已发布业务事实。

## 6. 调度、覆盖、依赖和归并

### 6.1 Task 与 RecallLedgerEntry

工作键含 `entity@revision + predicate + record + scope/proof dependency hash`；路径是见证，不替代防重键。

每个 ledger entry 保存四个正交维度：

- `execution_state = queued | running | finished | retryable_failure | blocked_dependency | paused`
- `semantic_outcome = supported | unsupported | undetermined | not_checked | mixed`
- `assertion_polarity = affirmed | negated | conditional | uncertain | not_applicable`
- `coverage_state = unattempted | attempted_incomplete | examined`

另存 phase、实际 call/attempt IDs、proposal/decision/proof refs、失败/停止原因、新证据重验次数。路由分数不是 coverage。第一阶段全部拒绝后必须继续第二阶段。

### 6.2 FrontierScheduler

Frontier item：`subject_ref、incoming_proof_path、local_menu_ref、next_predicate_cursor、record_plan_ref、priority_class、dependencies`。根 seed 是唯一允许无入边启动的对象；普通实体必须有当前有效肯定路径。

默认公平政策沿用实验初值：两次子记录后给一次根记录机会；主体内关系/属性与章节分别 round-robin。`max_hops` 和 `max_tasks` 是安全上限，截断的前沿写入 `pending_frontiers/stop_reason`，不得标全文 complete。

### 6.3 EligibilityPolicy

`seed_eligible(root)` 要求 root 的 document hash/IR、用户 root_class_iri、RootSeedPolicy、来源角色和撤销/审核状态有效。

`expandable(entity,path)` 要求：类型与局部指称 supported；incoming path 每条关系 proof 当前有效、肯定、无条件；精确 revision 与所有必要依赖匹配；无 conflict/审核拒绝。全局身份不是必要条件。

所有服务端 projection、scheduler 和评测适配使用同一政策；前端不得自行实现另一套 `positive_eligible`。

### 6.4 DependencyIndex

依赖类型：实体解释、局部共指、身份、谓词 proof、范围授权、路径、审阅、原文/元数据/本体版本、读取范围与竞争 owner 集合。

- 单 proof 的必要依赖为 AND。
- 同一断言的独立替代 proof 为 OR。
- 新 owner/qualifier 进入订阅范围时，旧 supported/unsupported/undetermined 均标 stale 并安排重验。
- 上游拒绝不删除历史；无独立替代 proof 的下游变为 invalidated。
- 另一条完整有效路径可恢复根可达性，但不能只改 revision 使旧 proof 复活。

### 6.5 ResolutionEvent

字段：`resolution_event_id、kind(merge|split|reassign|invalidate)、expected entity/mention revisions、proof_refs、canonical_choice、alias_changes、new_revisions、affected_claim/proof/task refs、before/after、actor、event_sequence`。

拆分先失效旧解释，再按各自 proof 重归属；不得把旧属性复制到所有新节点。冲突节点不能通过第三个模糊节点的传递闭包被合并。

## 7. GraphSnapshot 与 SourceSelection

### 7.1 GraphSnapshot

每个公开水位是不可变完整快照：

```text
GraphSnapshot:
  snapshot_id
  run_id / run_revision / event_head / artifact_revision
  analysis_id / metadata_snapshot_id / ontology_snapshot_id
  root_ref
  entities[] / properties[] / relationships[]
  proof_refs / decision_refs / dependency refs
  invalidated_refs
  projection_policy
  coverage_summary / unresolved_summary / pending_frontiers
  generated_at
```

默认 `projection=effective_affirmed` 仅包含当前 ProofGate 通过的无条件肯定图；另有 `all_candidates、unassociated、negated、conditional、undetermined、rejected` 只读过滤。过滤不创建新 snapshot，不改变事实资格。失效必须在新完整快照中显式表达，不能只追加节点。

根节点标 `seed_origin=user_selected` 且不计识别成功。`independent_review=unreviewed` 必须展示，不能因 finished 变成 confirmed。

### 7.2 SourceSelection

```text
SourceSelection:
  recognition_run_id
  analysis_id
  section_node_id
  source_record_ref / record_view_ref
  source_cell_id
  span_refs[]
  selection_role
```

关系详情分别提供 subject、object/value、predicate bridge、condition 和 counterevidence selection。选择必须能通过运行授权的 source API 回放；跨 Tab 保留同一 selection。

## 8. 事件批次与建议 SQL 映射

### 8.1 RunEventBatch

worker 提交：`batch_id、run_id、execution_token、expected_event_head、expected_object_heads、content_hash、events[]、artifact_manifest`。

事务顺序：fence token → 比对 event/object heads → 验证闭包 → 分配 revision/sequence → 追加事件/immutable revisions → 更新 heads/progress → 登记完整 artifact/snapshot。相同 batch ID/hash 幂等返回；相同 ID 不同 hash 409；任一步失败整体回滚。文件先写临时位置，校验 hash 后原子登记；未登记孤儿由维护任务清理。

### 8.2 SQL tables

| Table | Purpose / key constraints |
|---|---|
| document_analysis_runs | 运行 head；unique(owner_id, request_key)，revision/event/artifact heads |
| document_analysis_executions | 每 run 一个 lease/fencing generation |
| document_analysis_artifacts | 不可变 payload/path/hash/kind；内容不得就地改 |
| document_analysis_run_artifacts | run→artifact 授权、kind/revision、exclusive/shared；删除做 ref 检查 |
| document_analysis_event_batches | unique(run,batch_id) 与 content hash、first/last sequence |
| document_analysis_events | primary(run_id, sequence)，event_id 唯一，追加式 |
| document_analysis_objects | mention/referent/entity/identity/value/claim/proof 的 current head |
| document_analysis_object_revisions | primary(object_id, revision)，不可变类型化 payload |
| document_analysis_decisions | immutable decision/attempt/target；target+attempt 唯一 |
| document_analysis_tasks | 任务当前执行/coverage head、稳定 dedupe key、预算 |
| document_analysis_dependencies | from/to 精确 revision、AND group/OR alternative/subscription |
| document_analysis_graph_snapshots | snapshot/event head 唯一，完整图 payload/hash |
| document_analysis_tombstones | 删除/到期后的最小 run ID、owner hash、token generation、deleted_at/reason；若源自公开 DELETE，另保留 request key/hash 与经过字段白名单约束的原始 202 控制响应/hash，用于删除后幂等重放；不含正文、候选、proof、artifact 或其他可恢复业务内容 |

所有结构通过一个新 Alembic revision 创建，并在 `app/models/__init__.py` 注册。SQLite 覆盖 schema/基本 CAS；真实 lease、并发 claim、`SKIP LOCKED`/隔离和清理闭包必须用 PostgreSQL 集成测试。

## 9. Artifact 可用性

metadata 与 graph 独立状态：`pending | ready | partial | failed | deleted`。

- IR/结构 ready 后 metadata API 可返回结构，即使摘要仍 pending。
- 摘要失败时按冻结政策转 structure_only 或 retryable failure；Tab 切换不重试。
- graph partial 必须绑定完整 event watermark，并列出未完成 ledger；空图不能推断全文无关系。
- GET、刷新、filter 和 SSE subscribe 不创建模型请求、候选、decision 或 event batch。

## 10. 保留、删除与旧域边界

### 10.1 新运行

运行中不自动到期。终态从 `finished_at`、paused 从 `paused_at` 起按版本化设置默认保留 7 天。显式删除或到期清理：

1. CAS 把 run 置 deleting，递增 lease generation 并撤销 token。
2. 等待/拒绝所有旧 generation batch，追加删除审计事件。
3. 删除仅本运行引用且未发布的对象、artifact 和源文件；共享 artifact 仅删引用。
4. 写 tombstone 后置 deleted/expired；不可恢复。公开 DELETE 的最小幂等 receipt 在清理控制表前复制到 tombstone：相同 `(run_id, delete, request_key)` 与相同请求内容仍重放首次 202，内容不同返回 409，且不会再次调度清理；没有公开 DELETE receipt 的到期 tombstone 只提供 410。

新运行不自动提交业务事实，因此正常删除目标均应是未发布分析产物。若未来增加发布功能，必须另立特性及 Constitution 合规设计。

### 10.2 旧域清理分类与 G-C03

Manifest 每项保存 `source job/object ID、table/file absolute resolved path、hash、ownership、reference closure、publication state、action、post-check`。

- `unpublished_exclusive`：撤销 worker/commit lease、闭包核验后可物理删除。
- `shared_or_unknown`：不删；先解决所有权/引用。
- `published_or_confirmed`：**禁止物理删除**。只能追加撤销/失效批次并保留历史；新系统不导入、不显示、不恢复。

若 manifest 的物理删除集合包含 `published_or_confirmed`，Constitution gate G-C03 失败，清理及单次切换阻断。只有排除该集合，或先完成正式 Constitution 修订/治理决定并重新审批 manifest 后才能继续。任何清理工具都不得提供跳过此门的 force 参数。

全局 `audit_log` hash 链、用户/权限、系统配置、本体、模型池、共享报告/模板和离线 08/09 评测归档不属于可删除运行数据。

## 11. 关键不变量

1. 两个结果 Tab 的 run、analysis、metadata snapshot 和 graph event watermark 一致。
2. 同一物理 span 只有一个 SourceMention；多个 RecordView 不自动增加实体数。
3. 类型、局部共指和全局身份 verdict 独立；无全局身份不否定局部实体。
4. 关系/属性必须有当前精确谓词的 PredicateEvidence；双端存在或父路径不是 proof。
5. summary/heading/hint 不出现在 fact-eligible source refs。
6. 新 `sparse-candidates-v1` 的 phase1∪phase2 等于已准入候选集合，且交集为空；共享全文搜索域独立保留，不逐槽位复制为任务或台账。旧冻结运行仍要求其并集等于 scope 内全部目标记录。拒绝不删除已入台账的记录，未入选不作否定证明。
7. 只有当前有效的 affirmed、unconditional proof path 可递归；根 seed 是唯一无入边例外。
8. event/object/proof 引用不静默升级 revision；合并/拆分通过 ResolutionEvent。
9. 技术失败、模型拒答、原文否定和人工拒绝分别保存。
10. 每个公开图是一个事务水位的完整快照；不拼接新节点和旧边。
11. lost lease、cancelled/deleting/deleted/expired generation 的任何晚到写均被拒绝。
12. 已发布旧事实不进入物理删除集合；违反即阻断切换。
