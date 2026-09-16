# 实施任务与依赖

本清单是待开发任务，全部未实施。编码按本规范推进，现有文档/冻结实验不替代验收。`og/tv/da` 前缀定义见 [plan.md](plan.md)；测试默认在 `backend/tests/test_extraction/`，标“新”的文件尚不存在。研发代理的工作环境与产品运行时 Qwen 工具循环分别遵循 [Harness 设计](harness.md)，共用明确契约和可执行反馈，不混成另一套 Agent 框架。

## A. 契约和单轮传输

- [ ] **T01（M02/M03/M10）**：新增 claim_protocol.py、tool_contracts.py 的模型；以实现类型和静态工具注册表导出 Schema，与本规范 JSON 制品及示例做契约漂移检查，不另写一套独立参数定义。同步 og/contracts.py 中共享 GraphRelationshipGroup、TraversalScope/ScopeMember、modality、node grounding 和多对象 VerificationTarget 类型及身份/hash/dependency 契约，供冻结、核验和构图共同使用；定义 context.py 的派生 ModelContextView、tool_model_adapter.py 的 TurnPlan，以及 ToolIssue.field_path:str|null。VerificationInput 以 discovery_ref 派生完整声明/实体/桥接/外部候选/scope 依赖，替代仅传 target ID/hash；ToolObservation 保留原始 ToolCall 与可空 parsed_arguments，分派前错误使用 ToolErrorResult。required-nullable、extra 禁止、动态菜单和目标集合校验；旧对象省略新增默认字段以保持冻结 hash/序列化。测试新 `test_tool_engine_contracts.py`，覆盖有效示例与缺字段、额外字段、错类型和重复/悬空引用反例；在同一原文下分别变更对象、selection、原单位和限定，断言 verification 请求随冻结内容准确变化，缺内容/hash 失配阻止请求。依赖：无。
- [ ] **T02（M05）**：local_client.py 新增单 HTTP attempt 的 `responses_create -> ResponseTurn`，复用调度/取消，保留完整 output 项、usage、response.status/incomplete_details/refusal。工具定义平铺，读取 function_call 的 name/arguments/call_id，验证 call_id 与输出项 id 的不同用途；不完整/拒绝响应不执行工具，也不当成功空图。新协议使用 text.format/json_schema、max_output_tokens、store=false 和每轮显式 instructions；不使用 previous_response_id/conversation，不回退 Chat。扩展 `test_model_scheduler.py`，新 `test_native_tool_protocol.py`；已有 chat 测试只证明旧调用回归。依赖：T01。
- [ ] **T03（M11）**：当前协议固定 api_protocol=responses，使用 active_input_items/active_instructions/pending_call_ids，完整保留续传所需 input/output 项及 reasoning；按具体端点能力请求并保存不透明 encrypted_content。扩展逐 attempt result_ref、persist_calls/hydrate/restore/版本核验，工具结果键为 attempt+call_id；暂停后直接加载完整本地项和已确认结果，不扫描历史、不依赖远端会话、不复制完整会话到每个结果。仅在当前协议保存 context_authorization_ref，指向已确认检索结果中的完整当前授权描述；确认结果和引用在同一协调器屏障提交，从冻结 IR/RecordIndex 重建权限，不依赖 worker 深拷贝或 active_input_items。扩展 `test_document_current_state.py`、`test_current_state_transactions.py`、`test_current_work_resume.py`。依赖：T01。

## B. 通用工具能力

- [ ] **T04（M01）**：卡片按当前菜单裁选；明确 quantity/identity profile 和未支持构造。新协议绕开具体类型/谓词范围特判，不能放宽并列 range。扩展 `test_ontology_guided_core.py`，在 T01 新测试覆盖 IRI 重命名。依赖：T01。
- [ ] **T05（M01/M04）**：词表移除默认领域 overlay 和强制人工条目；加入 field_label 角色，保留来源；GLiNER2.5 显式注入。扩展 `test_tool_vocabulary.py`、`test_tool_mentions.py`、`test_gliner2_extractor.py`。依赖：T04。
- [ ] **T06（M04）**：tool_runtime 静态注册与分派，card/inspect/anchor/binding 四类工具；只从当前授权上下文解析输入，核对完整引用权限、真实根角色、自关系、本体语义和结果类型。工具不持有 owner Session、不自行请求模型、不写图；ModelContextView 仅为现有状态派生输入，不新增权威状态。ToolIssue 保持既有错误字段并补 field_path，参数错误指出可定位字段，语义缺口使用真实 reason_code/引用，无法定位时 field_path=null；不得泄漏未授权来源。扩展 `test_tool_evidence.py`、新 `test_tool_runtime.py`，验证未知函数/坏 JSON/非法参数形成错误结果、保存后暂停继续，再在完整 call_id 配对后预算内正确重试；断言非法 handler 未执行、原始调用未丢失、额度未退款。合法调用仍按注册表参数/结果类型校验，越权/过期 ref 和伪造状态仍不能执行。依赖：T01/T04。
- [ ] **T07（M09）**：ExternalInstanceReader 的最小 query/resolve；冻结源/映射输入、名称与复合键候选，去除设备默认源。旧 Mock 实验模块不删除。扩展 `test_external_evidence.py`，新 `test_tool_instance_query.py`。依赖：T04/T06。
- [ ] **T08（M07）**：retrieve_evidence 接现有摘要/结构检索；在 context.py 按当前任务、scope、缺失 facet 和请求预算构造 ModelContextView，先提供卡片/来源索引，再按需开放原文及工具返回，保持完整记录、反证、coverage 与引用身份。内部 ConfirmedRetrievalResult 保存 data-model 定义的完整 ContextAuthorization（位置/角色/权限/绑定及 hash，不存全文），模型仅收 result；由冻结 IR/Index、精确 subject 依赖与同一上下文策略重建 field_bindings/subject_label，校验任务/scope/来源及 evidence_hash/context_hash 后开放新增证据。派生视图不复制整份运行状态，不把摘要提升为事实；补证无新增来源不重问，无法保真容纳时明确未完成。扩展 `test_schema_card_summary_retrieval.py`，覆盖按需读取、来源权限、完整请求空间和不同 scope 隔离，以及“检索确认→暂停→冷恢复→inspect 新证据”的文本、范围、角色、权限、绑定/hash 一致性；未确认或过期授权拒绝；新增共享核心用例不得 import evaluation。依赖：T04/T06。
- [ ] **T09（M07）**：propose_repair 与统一恢复路线，移出领域 issue/目的词；引用修复不改原文，变化声明增加 assertion_generation，单次恢复与预算共享。recovery 是现有 discovery/verification 阶段内由 recovery_kind 派生的 mode，不新增运行阶段或状态机；修复工具白名单随该 mode 收紧。扩展 `test_evidence_repair.py`、新 `test_tool_engine_recovery.py`。依赖：T06/T08。

## C. 声明、数量与阶段编排

- [ ] **T10（M02）**：实现多实体/记录组成主体冻结；mentions 新增 record referent；无编号与共享名称不误合并。扩展 `test_ontology_guided_core.py`、`test_ontology_guided_node_revisions.py`。依赖：T01/T06。
- [ ] **T11（M02）**：基于 T01 共享类型实现逐目标逐 facet 核验、精确 hash、桥接链、选择/模态/条件证据和通用 ProofGate；新路径不走旧按谓词名分支的政策表，实体类型作为精确目标依赖。构建完整 VerificationInput 并校验 claim_set_hash/精确依赖；实体指称、关系端点、原值/单位、selection、条件、scope 与原候选一致，发现 reasoning 不进入独立核验。一个目标失败只阻断依赖声明；冻结实现与核验实现分别依赖公共契约，不互相阻塞。扩展 T01 新测试及 `test_source_bound_units.py`。依赖：T01。
- [ ] **T12（M08）**：数量 policy、interval/bound/endpoint；同步 literal_normalizer、value_constraints、evidence._raw_span 和 metric.normalize_metric；精确尺度/偏移，保持比较/精度。扩展 `test_literal_normalizer.py`、`test_tool_metric.py`。依赖：T04/T11。
- [ ] **T13（M08）**：固定 quantity SHACL profile、单声明 graph_ref、完整非空 focus；规范化/SHACL 分工及 stable errors。validate_metric/validate_graph 在本期注册 model_callable=false，作为 controller-only；finalize 无论模型是否用尽工具额度都运行适用必检，不满足可信前置条件时保留真实缺口，不能强行生成校准值。扩展 `test_tool_shacl.py`。依赖：T12。
- [ ] **T14（M06）**：ToolModelRecognitionAdapter 实现 inspect/_run_stage 与 TurnPlan/plan_model_turn，先从 task/context/protocol、可用工具和已预留账本计算 TurnPlan，再构造 ModelContextView；10 个注册函数中的 validate_metric/validate_graph 对 Qwen 永远不可见，其余 8 个按阶段/recovery mode/授权引用裁选，finalize 不调用 plan_model_turn、直接执行控制器必检；依据真实缺口、剩余预算和完成候选/独立核验的底额选择工具或阶段回答，不固定为一轮工具。仅采用 Responses 标准 function_call/function_call_output；每轮重发 instructions、store=false，并续传当前阶段完整 input/output 项，工具结果按 call_id 配对，reasoning 不作为事实或核验证据。工具依赖前轮结果时逐轮执行；无新增可用引用/证据、候选变化或缺口改善的重复请求停止，不用重排内容冒充进展。核验先加载 discovery_ref 并校验完整 VerificationInput；未知/非法调用保留原始 ToolCall 与错误结果进入观察和恢复，不以宽松 dict 放松合法类型。保留发现/核验阶段输入隔离、必检最终门和每 lineage 四次上限；必检缺口仅可通过已有 evidence/reproposal 恢复返回原阶段，不新增校准会话或免费模型轮次；按阶段内 recovery mode 调用已有恢复能力，并用 T15 构图函数生成 TaskOutcome。不继承旧领域 adapter 全体。扩展 `test_recognition_coordinator.py` 和新 `test_native_tool_protocol.py`，覆盖零工具、顺序依赖工具、多工具、无进展、预算不足、incomplete/refusal 无执行及完整项续传后暂停；断言注册集合/各阶段可见集合精确一致、控制器必检不可跳过，工具选择不改变语义真理门。依赖：T02/T03/T05—T13/T15。

## D. 图谱与跨层接入

- [ ] **T15（M10）**：使用 T01 共享图类型，实现 accepted 声明到节点/属性/单边/关系组的构建及精确证明依赖；保留 modality、scope 和 grounding，不将选择组摊平成普通事实边。单边契约保留，不在本任务重新定义公共类型。新 `test_relationship_groups.py`。依赖：T10/T11。
- [ ] **T16（M10）**：使用 T01 的 TraversalScope 实现 frontier_eligibility；将 executor/scheduler/lazy_frontier、slot/search/coverage/cache 键全部加入 scope；循环、one_of 范围冲突和版本失效。新 `test_group_scope_scheduling.py`，扩展 `test_ontology_guided_node_revisions.py`。依赖：T15。
- [ ] **T17（M10/M11）**：verified 投影、组/独立节点保真；current_work/当前候选/证明/展示 collections 白名单与冷恢复更新。回归旧 run 的原 slot key、hash、序列化及冷恢复，不把旧状态补成新 scope 后重新计算；新 run 直接加载当前作用域状态。冷恢复先据 context_authorization_ref 和冻结 IR 重建 TaskContext，再恢复其他物化引用；显式传入服务端 run_fingerprint 和由初次目标工厂派生、尚未绑定 context_hash 的 target_seed，断言不用已返回 base.target 重算且恢复 target_id/hash 一致；覆盖检索后暂停与错误工具结果暂停，未确认/过期授权不得授予 inspect 权限。扩展 `test_document_current_state.py`、`test_current_work_resume.py`。依赖：T03/T15/T16。
- [ ] **T18（M12）**：公开 GraphRelationshipGroup、scope、scope_resolutions、modality、extraction_protocol、证据选区和 counts，API 只读行为不变。覆盖过滤父组后仍可解释子声明继承范围，派生摘要不计新事实；回归旧 run 的冻结投影视图与公开结果，旧 run 请求 verified 返回 unsupported_projection，不按新版语义重解。扩展 `test_document_analysis_public_projection.py`、`backend/tests/test_api/test_document_analysis.py`。依赖：T17。
- [ ] **T19（M12）**：api.ts 与两个图组件、组成员展示/证据/孤立节点、作用域标签；新协议选择 verified，旧运行按冻结契约读取。扩展 `frontend/tests/document-analysis-runs.test.mjs`、`template-document-performance.test.mjs`；复用 `backend/scripts/document_analysis_browser_fixture.py` 与 `frontend/tests/document-analysis-browser.mjs` 增加真实 API 浏览器用例，核验组/范围、原文跳转及只读视图切换。受控响应的界面验收与真实 Qwen 质量验收分开，不把 Node 源码断言或 SSR 当浏览器结果。依赖：T18。
- [ ] **T20（M06/M11）**：da/execution 的新适配器装配与新运行 fingerprint，冻结 api_protocol=responses 和具体字段能力；旧冻结结果不混用，旧 chat 入口仅保留已有调用，不为新运行建设协议双栈/回退。未选择模板的本体引擎按本规范接入，模板专用执行域不误切换。依赖：T14/T17/T18。

## E. 环境、工程和真实验收

- [ ] **T21（M04）**：复用固定 GLiNER2.5 隔离环境验收，新增最小 gliner2 extra，以 `gliner2[local]==2.0.0` 及历史 Transformers 4.57.6 兼容组为求解依据，与应用 CPU/CUDA 锁中 Transformers 5.6.2 及受影响语义模型共同求解并回归；不把历史版本直接覆盖应用锁，不以 sys.path 混用实验 venv。按 plan M04 将校验下沉为 gliner2_extractor.py 的 `verify_local_checkpoint(model_path,manifest)`，线上与 runner 共享，保留本地权重清单。应用进程导入相关库前设置 `HF_HUB_OFFLINE=1`、`TRANSFORMERS_OFFLINE=1`，验证缓存/加载路径和缺权重行为；运行期不下载。只改必要依赖，不安装全部模型。依赖：T05。此任务可与 C/D 并行。
- [ ] **T22（M13）**：新 evaluation/ontology_tool_engine.py 的 probe/run/score 入口及冻结 Responses manifest；识别不读金标，输出最终图、coverage、协议和成本，历史目录不覆盖。失败结果以既有 task/claim/target/facet/call_id、reason_code、field_path 和证据引用定位受影响模块及最小复现，输出项 id 单独保留且不代替 call_id；由开发者将确认的失败加入对应定向回归，复用已有结果，不新增日志库/观察平台或自动改写测试。依赖：T14/T18。
- [ ] **T23**：定向工程测试；扩展 `test_ontology_guided_boundaries.py` 的机械导入边界，禁止线上 import evaluation、共享核心持有持久化/业务提交依赖、工具契约/运行时依赖 OpenAI SDK 类型，SDK 转换只在 local_client。专用 PostgreSQL 事务/暂停恢复（未配置报告 skip）；旧 run 冷恢复、冻结 hash/序列化和原投影视图回归，新 run 的 scope/verified、派生上下文和阶段内 recovery mode 验收；前端类型、目标 Node 用例及 T19 的隔离真实 API 浏览器验收。依赖：T19/T20/T21/T22。
- [ ] **T24**：基于用户已确认的项目 Qwen Responses 支持执行 probe，验收具体字段兼容、function_call/call_id 往返、text.format 结构输出及 store=false 完整项续传；每轮 instructions 和无 previous_response_id/conversation 独立检查。probe 只在对应阶段暴露 8 个模型可选函数的子集，断言任何 Qwen 请求不含 validate_metric/validate_graph，并由控制器执行适用必检；不将“注册 10 个标准函数”描述为“Qwen 可调用 10 个”。工具基线显式 strict=false，具体端点支持严格参数生成后才冻结 strict=true；encrypted_content 的请求/回传按能力验收。超预算、incomplete/refusal 和字段不兼容明确报告，不回退 Chat。依赖：T02/T22；正式全流程要求 T23。
- [ ] **T25**：冻结同预算 F0—F4 与独立跨文档/本体参考，执行真实抽取、单位正反例、图谱质量评分；分别报告协议完成和 F1。依赖：T23/T24。
- [ ] **T26**：交付模块差异、工程结果、真实模型报告和未决项；只有证据满足才标相应任务完成，不自动部署、发布或清理。依赖：T25。

## 并行归属

T01 先合入共享图/scope/VerificationTarget 和模型协议类型；T02 与 T03 可并行，T10/T11 可基于同一契约独立实现；T05/T07/T08 可按文件归属并行；T12/T13 由同一责任人串行。T15 构图完成后再集成 T14；T15—T17 由同一责任人维护跨层引用一致性。T18 完成契约后前端 T19 可与 T20 并行。多个分支修改 contracts.py/executor.py 时先合入公共类型，再独立实现，不能各自发明同名契约。

## 每次提交的检查

当前设计变更先按 [quickstart.md](quickstart.md) 运行仓库内 [check_design.py](check_design.py)，校验正例、非法输入、Responses 配对、引用和依赖；这不是未来实现类型/注册表同义检查或运行行为验收。只运行受影响路径；无理由不全库格式化或跑全部测试。所有新声明、原文/身份/单位失败和暂停继续行为都须有行为反例。测试不得依赖真实报告答案进入模型上下文，不能删掉未执行范围改善指标。任务名称或待建路径本身不代表文件已存在。

研发任务使用以下短卡片，附在当前开发说明中即可，不进入产品运行状态或新建管理服务：

```text
任务：Txx / FR-xx / Mxx；预期用户行为：…
权威输入：spec/data-model/具体契约；改动文件：…
最小复现：输入、触发步骤、当前结果、期望结果
机械检查：受影响边界/契约命令；行为验收：定向测试或浏览器场景
完成证据：实际命令/结果/skip；未完成项与下一处定位：…
```

代理先用卡片和模块地图取所需上下文，再按 [quickstart.md](quickstart.md) 的层级执行反馈；一次失败优先形成最小反例、修复并重跑受影响检查。模型自述成功、计划中的命令或其他代理意见都不能替代执行结果。
