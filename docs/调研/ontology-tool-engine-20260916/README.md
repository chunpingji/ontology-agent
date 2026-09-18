# Spec 027 实施与验收

本轮依据 [Spec 027](../../../specs/027-ontology-extraction-engine-v2/spec.md)、[模块计划](../../../specs/027-ontology-extraction-engine-v2/plan.md) 和 [任务清单](../../../specs/027-ontology-extraction-engine-v2/tasks.md) 实施。工程实现、真实模型协议、抽取质量和部署分别验收；本记录不表示正式 F1 已通过。用户随后要求重新部署，已于 2026-09-17 完成当前开发配置更新，见 [部署记录](deployment-20260917.md)。

## 实现范围

- 新建非模板文档分析运行接入项目 Qwen Responses；模板执行域和旧冻结运行保持原入口。每轮显式 instructions、store=false、完整 output 及 call_id 配对，发现与独立核验输入隔离。必需 Qwen 不可用时明确失败。
- 静态注册十个通用函数，由同一类型生成 OpenAI 工具定义。Qwen 按阶段/权限/能力最多可见八个；值与单位规范化、SHACL 由控制器执行必检，不能由模型跳过。GLiNER2.5、本体/SKOS 词表、摘要检索和冻结外部实例通过现有模块协作。
- 最终结果是本体约束关系图谱，保留实体、属性、单边和多对象关系组，以及极性、模态、条件、作用域和原文选区。组不展开成额外普通事实边；外部身份来源单独保留，不覆盖文档事实。
- 复用现有执行器、请求账本、当前状态与展示缓存，没有新增数据库表、Agent 框架或历史回放系统。pending 与请求预留同事务提交；工具结果和授权同屏障确认，暂停后加载当前状态继续。
- 新增 `probe/run/score` 薄 CLI，复用现有评测执行器与评分器；识别输入不读取参考答案。CPU/CUDA 依赖共同求解，离线模型清单校验由应用与 runner 共用。

| 编码入口 | 职责与输出 |
|---|---|
| [claim_protocol.py](../../../backend/app/services/extraction/ontology_guided/claim_protocol.py)、[claim_freeze.py](../../../backend/app/services/extraction/ontology_guided/claim_freeze.py) | 本体 Schema 卡、声明冻结与精确核验目标；通过证明和确定性检查的声明生成图谱节点、属性、边及关系组 |
| [tool_contracts.py](../../../backend/app/services/extraction/ontology_guided/tool_contracts.py)、[tool_runtime.py](../../../backend/app/services/extraction/ontology_guided/tool_runtime.py) | 十个标准工具契约、阶段/引用权限检查和类型化结果；NER/检索/外部元数据只提供线索 |
| [tool_model_adapter.py](../../../backend/app/services/extraction/ontology_guided/tool_model_adapter.py) | Responses 发现与独立核验循环、字段反馈、四次预算及控制器必检，返回 TaskOutcome |
| [execution.py](../../../backend/app/services/document_analysis/execution.py)、[current_state.py](../../../backend/app/services/document_analysis/current_state.py) | 在现有文档分析域装配新引擎，原子保存当前工作状态及调用预留，暂停后继续 |
| [public_projection.py](../../../backend/app/services/document_analysis/public_projection.py)、[document-relationship-graph.tsx](../../../frontend/src/components/analysis/document-relationship-graph.tsx)、[template-document-graph-panel.tsx](../../../frontend/src/components/analysis/template-document-graph-panel.tsx) | 公开 verified 图谱及证据；展示组、作用域、模态、数量和孤立节点 |
| [ontology_tool_engine.py](../../../backend/app/evaluation/ontology_tool_engine.py) | 独立 probe/run/score 入口，保存最终图、coverage、协议和成本；评分参考只由 score 读取 |

## 工程证据

| 范围 | 本次证据 |
|---|---|
| 当前状态、工具协议、范围与事务 | [工程记录](engineering.md)，含独立 PostgreSQL、旧协议/冻结 hash 与冷恢复反例 |
| API 与浏览器 | [浏览器记录](browser/README.md)：53 次真实 GET；组、孤立实体、范围说明、原文跳转、刷新和旧运行切换通过 |
| GLiNER2.5 与受影响语义模型 | [依赖记录](dependency-check.json)：独立 CPU 克隆环境真实 GLiNER2.5、BGE embedding/CrossEncoder 离线推理通过；共同求解不等于已部署 CUDA 锁 |
| 真实抽取完整依赖 | [隔离运行环境](runtime-complete-environment.json)，与原共享应用环境分开 |

此前受影响后端大集合 754 项通过、6 项 PostgreSQL 跳过；专用 PostgreSQL 批次另行 17 项通过、无跳过，已覆盖这些事务/恢复路径及新增身份落库反例。后续真实运行反馈修复分别通过 207 项、127 项及最终 129 项定向回归；这些批次有重叠，不相加。完整命令和各次范围见工程记录。

工程用受控 Responses 返回验证边界，浏览器用持久化图契约制品验证 API/展示，均不能代替真实模型质量验收。PostgreSQL 事务测试采用当前 ORM schema 并 stamp head；旧迁移链存在重复建表问题，本次没有修改它，也不声称空库迁移链通过。

## 真实 Qwen 验证

指定报告为 `upload-23c872fb-3ab1-41de-a705-dd4b162dfa09`，根类型 CMCReport；原 Word SHA256 为 `e94822808601e73f4ba4c715c664812d4a01e42f0e4e84a6b08a3653b915daf7`。真实输入保留完整 447 个 IR 单元和 32 个摘要节点；有界小跑不等于全文图谱验收完成。

最新分项结果见 [真实运行汇总](latest-run-summary.json)。数量正例 result05 已完整通过：真实 Qwen 2 次请求、18,412 tokens、约 208.6 秒；8 个必需核验 facet 均有原文支持，`1500 mg` 精确转换为 `1.5 g` 并以 claim_verified 进入最终图谱。该轮 Qwen 没有主动调用可选工具，绑定、metric、SHACL 由控制器分别执行 1 次。原始完整返回的独立零请求复核进一步确认 SHACL evaluated=true、conforms=true、focus coverage 完整，共 7 个执行 shape。此结论仅覆盖该数量正例，不表示 CMC 全文或正式 F1 通过。

数量反例 result03 使用 4 次真实请求、38,171 tokens、约 437.6 秒，两代合法候选与核验均实际到达控制器。质量属性的 `1500 m` 被 `unit_missing_or_incompatible` 阻断，未生成可信数量或图谱属性；binding/metric/SHACL 入口各调用 2 次，SHACL 因校准前置失败而未求值。结果保持 partial/incomplete，不能把属性拒绝行为写成整轮完整通过，也不能把独立实体的 supported 当成该属性通过；无适配器技术异常。

完整 CMC + 系统 Mock 的 cmc-mock03 使用 3 次请求、38,803 tokens，触及 1,032 项计划中的 2 个任务，保持 partial。单对象关系返回 selection=one_of 被严格拒绝，保留完整 output 继续纠错时超出上下文预算；另一任务完成 inspect_evidence 往返后返回合法空候选及原文缺失未决。没有接纳关系或属性。GLiNER2.5 已离线加载，NER、Mock 查询和摘要检索工具均可见，但本轮 Qwen 未调用这三项，不能宣称它们已完成真实工具协作。该限制与后续显式工具调用的集成探针分开报告。

- [首次 probe](probe-01/capabilities.json)：2 次请求，工具调用成功，但带 Markdown 围栏的回答被严格解析器拒绝。
- [第二次 probe](probe-02/capabilities.json)：4 次请求；strict=false 的工具往返、完整项续传及随机工具结果使用通过；strict=true 的回答约束仍失败。未请求或验证 encrypted_content。
- [首轮抽取记录](first-run-summary.json)：CMC 6 次请求、70,155 tokens；数量正例 3 次、21,525 tokens；反例 3 次、17,159 tokens。均未形成合法候选，不能将空图当成正确拒绝或 SHACL 成功。

这些实测揭示兼容端点虽接受 text.format，仍可能不按 Schema 输出。修复将同一模型类型生成的 Schema 显式加入阶段 instructions；保留原始非法 output，由本地校验派生带字段位置的纠错输入，再在剩余额度内重新回答。没有剥围栏、放宽类型或免费重试；正常和冷恢复纠错路径均有行为反例。

[修复后第二轮](second-run-summary.json) 使用原有输入和预算：CMC 2 次请求、28,575 tokens，形成一个合法空候选/未决结果，主关系任务因输出截断和后续完整上下文超额而 partial；数量正例 3 次、30,131 tokens，仍未形成合法候选；数量反例 4 次、24,921 tokens，已进入独立核验及原生工具调用，但最终 JSON 非法且目标覆盖不足。两例均未到达 controller metric/SHACL，不能视为校准通过。

[第三轮数量正例](third-run-summary.json) 保持四份冻结输入、4 请求/1 任务与 32,768 输入预算，只将输出上限从 8,192 提至 16,384；1 次请求、7,392 tokens 得到完整合法 JSON，但候选重复使用已登记实体 ID，冻结器拒绝。此问题已以 0 次新模型请求离线复现，并加入阶段字段级纠错反馈；对应正常执行、冷继续与严格边界回归 82 项通过。桥接提示同时明确记录 ID 不能充当桥接证明。该轮仍未到达 metric/SHACL，且预算变化不能用于原同预算质量比较。

[第四轮数量正例](fourth-run-summary.json) 4 次请求、34,848 tokens，完成两代候选与独立核验。第二代属性主体正确，但 counterevidence 缺少原文支持，被判为未决；控制器随后读取不存在的 `ShaclData.issues` 导致异常。该集成缺陷已修复，4 个完整适配器回归覆盖数量通过、单位不兼容、反证证据缺失、SHACL 技术失败，更广定向集合 207 项通过。[原始返回的零请求复验](fourth-run-offline-check-summary.json) 确认异常消失并保存 binding/metric/SHACL 各 1 次调用；由于语义仍未决，metric 不返回可信数量，SHACL 未实际求值，属性没有入图。调用次数不能当成校准通过。

[标准 reasoning 参数探针](reasoning-effort-probe/summary.json) 共 2 次请求：baseline 266 个输出 tokens，effort=none 171 个；两次仍有非空 reasoning。该样本只证明接受字段和返回合法小 JSON，未证明关闭思考，应用默认保持省略。依据为实际获取的 [OpenAI Responses 文档](https://developers.openai.com/api/reference/resources/responses/methods/create) 与 [llama.cpp 官方转换源码](https://github.com/ggml-org/llama.cpp/blob/master/tools/server/server-chat.cpp)；当前官方源码支持 effort 转换不代表部署端点完整支持其语义。

[系统 Mock 冻结说明](system-mock-source-notes.json)：只读取得 211 条设备和 5 条角色，仅使用原有 class IRI 和数据属性 IRI 映射，不编造身份键。没有明确类型映射的部门/区域未纳入。该快照已准备，第二轮 CMC 尚未使用它，不能据此声称真实 Mock 协作验收完成。

[冻结实例 reader 检查](system-mock-reader-check.json) 已逐条验证全部 216 条记录的名称召回与精确版本解析；没有模型调用，测试查询不是文档证据，也没有授予全局身份。这证明真实系统快照可接入既有通用 reader，仍与真实 Qwen 是否调用该工具分开。

[指定报告字面重叠检查](system-mock-document-overlap.json) 进一步确认，5 条设备记录的名称/编号在 10 个原文单元出现，1 条角色记录的名称/代码在 1 个原文单元出现。此检查允许子串及同名多条记录，仅说明存在可定位线索，不证明类型、身份或关系；未命中也不作否定。扫描文件未并入上述 CMC 识别 manifest 或模型输入，扫描本身未调用模型。单独工具集成探针已明示人工选定的原文与步骤，不能用于自主发现或 F1 结论。

工具链探针的零请求预检另发现：table:0,row0 中一个工艺步骤原文单元，在当前冻结 IR/RecordIndex 中属于表头，records_by_evidence 没有可作为 target 的记录。探针改用同一报告中已登记的合法数据行，保留原本体菜单、IR 和权限；没有将该表头强行提升为事实原文。这一观察提示当前结构输入仍有质量限制，不代表已修复解析器。

[工具链探针终态](tool-chain-probe01/summary.json)：4 次真实 Qwen 请求、38,519 tokens，完整项续传、真实 wire/hash、call_id 配对与最终严格 JSON 解析通过，但三个工具均 blocked，候选为 0。propose_mentions 超出结果预算；resolve_source_anchor 的 quote 正确但 context_text 为空字符串；随后 query_instances 使用未登记的原文编号充当 mention_ref。没有外部身份核验或图写入。这是明确引导的工程集成失败记录，不能算自主工具协作通过。

[零 Qwen 请求诊断](tool-chain-probe01-diagnostics/diagnostic.json) 保持同一参数、词表和预算，确认真实 GLiNER 执行 4 个批次，处理 14/14 单元，返回 7 个跨度和 25 条 definition_missing；完整结果为 4,660 tokens，超过 4,096 上限。重复 coverage/evidence_refs 和缺定义信息占据较多结果预算；本次没有截断结果、提高预算或把预算前结果返给 Qwen。有效实体词条仅 CMCReport，编号也被误标为该类，因此实际 NER 执行不等于类型识别正确。Schema 卡已包含全部 25 个合法关系对象类，缺口在定义/受控词输入，不在 range 传递。

据此补充通用工具参数说明和错误反馈：不需要消歧时 context_text 必须为 null；空串或不包含 quote 的上下文反馈精确字段；实例查询必须使用成功返回的引用；结果超预算提示缩小原文或谓词范围。保留原始参数及权限门禁，对应 129 项回归通过，未追加真实 Qwen 请求验证这些提示的效果。

按用户此前授权另行编写 [25 类手工定义/同义词 overlay](manual-vocabulary-notes.md)，未加入引擎领域规则或权威本体。零请求编译验证由 2 个有效词条/25 个缺定义变为 27 个有效词条/0 个缺定义。随后按明确启用指令接入当前部署，见 [三项工具启用记录](tool-enablement-20260917/README.md)：补齐完整定义后发现并修复 NER 分批超限，134 项定向测试通过；实际 GLiNER 单元执行完整但无命中，Mock 查询返回 1 个未核验身份候选。新配置尚未用于真实 Qwen/F1 运行，不代表召回改善，原有评测结果保持不变。

## 未完成的正式质量验收

现有 CMC 参考为旧格式，与本次冻结根/本体/范围的正式参考契约不匹配，且缺少 `expert_review`。评分命令明确拒绝，未改写参考身份或伪造专家批准。T25 所需的独立跨文档/本体参考及同预算 F0—F4 比较尚未完成，不声明 F1 提升。

当前新执行路径尚未提供经独立核验的局部共指桥接链；桥接契约与端点闭合核验已实现，无可用链时不接受模型自造链。已证明的显式叙述和字段归属仍按各自证明规则处理。

前述实施与评测阶段没有部署、重启共享服务或提交 Git；后续按用户明确要求进行的部署单独记录。原始上传与历史冻结评测未覆盖；临时服务与专用测试库按各验证记录管理。

Spec Kit 的 before/after implement 钩子均仅含可选 `/speckit-git-commit`，本轮跳过自动提交；实现保留在工作区供审查。
