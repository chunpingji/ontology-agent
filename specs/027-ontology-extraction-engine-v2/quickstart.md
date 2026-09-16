# 验收步骤

状态：编码后的目标验收入口；本轮仅检查设计制品，没有运行下列模型或工程验收。标注“拟新增”的命令须在相应任务完成后执行，不可按现有可用命令宣传。

研发任务从 [Harness 设计](harness.md)、[模块计划](plan.md) 和 [任务卡模板](tasks.md#每次提交的检查) 定位所需上下文。研发 Harness 的反馈是代码/契约/界面验收结果；运行 Harness 的反馈是 Qwen 工具结果、冻结声明和证明，两层不共用另一套任务状态或 Agent 框架。

## 1. 环境与输入

使用 backend/pyproject.toml、uv.lock 和已有环境。纯工程测试不下载权重；真实 GLiNER2.5 使用固定本地模型与兼容依赖，真实 Qwen 使用项目配置端点/模型。用户已确认 Qwen 支持 Responses，027 主协议固定为 Responses API；验收具体字段、严格参数、结构化输出和无状态续传，不把协议支持重新当成待猜测条件。旧 Chat Completions 仅做已有调用回归，新运行不建设双栈/回退。确认必需工具可用，缺项明确报告；不得把关闭 NER 作为“完整工具方案通过”。不重启共享后端来替代隔离验证。

GLiNER2.5 的 Python 包固定为 `gliner2[local]==2.0.0`，不是 gliner2==2.5。历史隔离验证采用 Transformers 4.57.6、HF Hub 0.36.2、tokenizers 0.22.2、protobuf 6.33.5，准确清单见已有 `backend/app/evaluation/fixtures/gliner2_runtime_requirements.txt`；应用 CPU/CUDA 当前锁中的 Transformers 为 5.6.2。T21 必须共同求解本能力与应用语义模型的兼容依赖，并回归受影响模型；不能仅添加 extra 或直接覆盖历史版本就宣称应用可用，也不通过 sys.path 混用实验 venv。隔离环境通过和应用环境通过分别记录，本轮不安装或更新依赖。

应用进程在导入 Hugging Face/Transformers/GLiNER 相关库**之前**设置 `HF_HUB_OFFLINE=1` 和 `TRANSFORMERS_OFFLINE=1`，核对实际进程环境及缓存状态；不能等工厂创建模型后才设置。按 plan M04 拟将权重清单校验统一为 `gliner2_extractor.verify_local_checkpoint(model_path,manifest)`，线上和离线 runner 复用，线上不导入 evaluation。验收本地权重/tokenizer 完整加载、错误 hash 和缺失文件均明确失败、全程无下载请求；历史准备环境和线上环境各自验证。

新评测 manifest 至少包含以下字段，路径由操作者指定，不硬编码历史报告：

| 字段 | 内容 |
|---|---|
| run_id、output_dir | 新运行与新目录；已存在且非空则拒绝覆盖 |
| document_path、document_hash、ir_path、ir_hash | 同一原文与 DocumentIR |
| ontology_path、ontology_hash、root_class_iri | 冻结本体及用户指定根 |
| metadata_path、metadata_hash | 同源摘要，可含 partial/回退状态 |
| vocabulary_path、vocabulary_hash、extraction_profile_path | 显式词表/表示和身份映射；无 overlay 可为空 |
| model_identity、tool_protocol_version、api_protocol、capabilities_path | 项目 Qwen 身份、ontology-tool-extraction-v1、固定 responses、具体字段验收结果 |
| store、tool_strict、stage_strict、reasoning_encrypted_content | 固定 false；工具与阶段 strict 各自以 false 为基线或冻结已验收 true；是否已验收可请求的加密续传项 |
| ner_model_path、ner_manifest_hash、ner_required | 离线 GLiNER2.5 身份，完整方案为 true |
| external_sources | 可为空；每源 ID、冻结版本、字段/身份映射和读取配置 |
| budgets | max_model_calls、max_input_tokens、max_output_tokens、max_calls_per_lineage=4、工具和补证上限 |
| arm | F0/F1/F2/F3/F4；固定输入和总额度，变化只对应总体方案的消融项 |

不得包含 gold/reference 文件路径、参考实体列表或原文答案白名单。评分命令另行读取金标。没有足够参考时仍可完成协议和工程验收，但不声明已证明 F1 提升。

## 2. 定向工程检查

按受影响路径选择下列层级，不因名称为 Harness 就每次运行全库检查。已有入口与待实施用例分开记录：

| 层级 | 反馈对象 | 入口与执行条件 |
|---|---|---|
| 契约与边界 | 类型/注册表/Schema 漂移、非法依赖、参数和引用反例 | 已有 boundaries；T01 完成后运行新增 contracts 测试 |
| 隔离行为 | 工具循环、预算、派生上下文、暂停与图谱语义 | 已有 coordinator/current_work/public_projection；新增协议用例完成后追加，不加载真实模型 |
| API 与展示 | 公开响应、原文选区、组/范围保真、GET 无副作用 | 已有后端 API 测试、前端 Node/类型检查及下方真实 API 浏览器入口 |
| 专用环境 | PostgreSQL 竞争/恢复、GLiNER 应用环境离线加载 | 按现有 fixture 和 T21 准备；缺环境列 skip/未完成 |
| 真实模型与质量 | Qwen 原生往返、最终图谱与固定参考下的质量/成本 | T24/T25，使用第 3—5 节入口与预算；不由工程通过推定 |

以下现有路径已核验；新增用例按 tasks.md 写入后再运行。从 backend/ 执行：

```bash
.venv/bin/python -m pytest -p no:cacheprovider -q tests/test_extraction/test_ontology_guided_boundaries.py tests/test_extraction/test_recognition_coordinator.py tests/test_extraction/test_current_work_resume.py tests/test_extraction/test_document_analysis_public_projection.py
```

新增文件完成后，执行：

```bash
.venv/bin/python -m pytest -p no:cacheprovider -q tests/test_extraction/test_tool_engine_contracts.py tests/test_extraction/test_native_tool_protocol.py tests/test_extraction/test_tool_runtime.py tests/test_extraction/test_relationship_groups.py tests/test_extraction/test_group_scope_scheduling.py
```

数量/SHACL、NER、实例源、事务等按 tasks.md 的受影响已有路径追加。pySHACL 依赖缺失造成的 skip 不算 SHACL 验收通过。专用 PostgreSQL 必须符合现有 fixture 的可销毁要求；未配置时报告 skip，不用 SQLite 声称锁/并发已通过。

T01 的契约检查从实现类型/静态工具注册表导出定义，对照 JSON 制品及有效/无效输入；不能只重复读取同一份 JSON 来证明实现一致。T23 扩展已有 AST 导入检查，错误须指出违规文件及导入路径。ModelContextView 不新增持久状态的边界、ToolIssue.field_path 的可定位错误，以及 TurnPlan 预算/无进展行为由定向测试覆盖。

新增 `test_native_tool_protocol.py` 的目标是 Responses：平铺 tools、call_id 配对、完整 output 项、text.format、max_output_tokens、response.status/incomplete_details/refusal 和本地无状态续传。现有 `test_model_scheduler.py` 等 chat 测试保留为**历史协议调用回归**，通过它们不能宣称 Responses 接通；共享调度、取消和 owner 隔离用例仍复用。

T17/T18/T23 必须同时覆盖旧 run 和新 run：旧 run 按原 slot key、hash、序列化及冻结投影冷恢复，旧 verified 请求明确拒绝；新 run 保留 scope 并输出 verified。不能仅验证新协议而把旧运行读回后默认补字段、改键或按新规则重新计算。沿用 `test_document_current_state.py`、`test_current_work_resume.py`、`test_document_analysis_public_projection.py` 和现有 API/前端用例扩展这些反例。

从 frontend/ 执行：

```bash
node --test tests/document-analysis-runs.test.mjs tests/template-document-performance.test.mjs
./node_modules/.bin/tsc --noEmit
```

只对改动 Python 文件跑 Ruff、改动前端文件跑 ESLint。以上为编码验收命令，不是本轮执行记录。

### 2.1 真实 API 浏览器入口

复用现有 `backend/scripts/document_analysis_browser_fixture.py` 与 `frontend/tests/document-analysis-browser.mjs`，在 T19 增加本规范组/范围场景。fixture 要求调用者已准备**专用空的 loopback PostgreSQL、库名以 `_browser_test` 结尾**、独立输出目录和对应前端；它关闭正常应用 lifespan/后台 worker，使用受控模型响应建立运行，不重启共享后端。现有脚本拒绝复用非空数据库。Playwright/Chrome 按已有本地路径配置；不在验收脚本中隐式安装或下载。

环境准备好后，从 backend/ 启动 fixture（变量由当前隔离任务设置）：

```bash
.venv/bin/python scripts/document_analysis_browser_fixture.py --database-url "$DOCUMENT_BROWSER_DATABASE_URL" --output "$DOCUMENT_BROWSER_FIXTURE" --port 58001 --frontend-origin http://127.0.0.1:53101
```

从 frontend/ 的另一个终端执行；`DOCUMENT_BROWSER_FIXTURE` 指同一输出目录，必要时配置已有 `PLAYWRIGHT_MODULE` 和 `DOCUMENT_BROWSER_CHROME`：

```bash
node tests/document-analysis-browser.mjs
```

验收关系组数量、selection/模态/条件、scope_resolutions、孤立实体和原文点击；切视图/刷新前后不新增模型请求或业务候选。沿用脚本既有截图、请求断言与测试结果，仅保存本次验收输出。该层证明真实 API 与页面协作；Node 源码断言/SSR、受控响应浏览器、真实 Qwen 质量评测分别列结果，互不替代。

## 3. 拟新增离线 CLI 契约

入口 `python -m app.evaluation.ontology_tool_engine`，放在 M13，当前尚不存在。

| 子命令 | 参数 | 行为/退出码 |
|---|---|---|
| probe | `--output DIR --max-model-requests N` | 使用项目 Qwen Responses、合成非业务文本验收具体字段与无状态往返；预算硬上限，输出 capabilities.json；0=声明的基础验收通过，2=字段兼容/具体能力验收未完成，1=技术失败 |
| run | `--manifest PATH --output DIR` | 仅共享核心识别；不读金标；0=任务范围执行完成，2=partial/未执行，1=技术失败；0 不等于质量通过 |
| score | `--prediction PATH --reference PATH --output DIR` | 不调用模型、不改预测；输出 P/R/F1、未决、coverage、成本；参考未决单列，不静默丢弃 |

`probe` 首轮建议预算 4，验证至少一个真实 `response.output` 中的 function_call、相同 call_id 的 function_call_output 和利用结果的后续回答；无调用输出不能算工具往返成功。工具定义为平铺 `{type:"function",name,description,parameters,strict}`；基线显式 `strict:false`，仅在当前端点验收严格参数生成后冻结为 true，不能把省略 strict 当成关闭。严格参数生成与阶段 `text.format={type:"json_schema",name,schema,strict}` 分别记录；使用 `max_output_tokens`，不以请求接受/一次合法 JSON 推断完整能力。其他具体能力测试需要额外请求时，显式声明其预算，不超出本次 probe 上限。

每轮使用 `store=false` 和显式 instructions；下一轮 input 包含当前阶段完整先前输入、原样 output 项和配对 function_call_output。验收 output 同时含 reasoning/function_call/message 项的保存与续传，不能只发送 output_text 或抽取出的函数调用。`id` 是输出项身份，`call_id` 才是调用与结果的配对键；测试必须包含两者不同的情况。只在端点已验收时请求 `include=["reasoning.encrypted_content"]` 并保留不透明返回项；它和普通 reasoning 都不进入事实证据。不得发送 previous_response_id/conversation，不依赖远端会话继续。

capabilities.json 固定 api_protocol=responses，记录实际工具 strict、阶段结构化输出、无状态完整项续传和可选加密项结果。具体字段不兼容、response.status 非完整、incomplete_details 或 refusal 如实报告；不执行该批工具，不把拒绝或缺回答记为成功空图，不切换旧 Chat 协议。

`run` 输出最小集合：manifest.json、final-graph.json、coverage.json、metrics.json、calls.json、protocol-checks.json。在线仍使用现有当前存储；离线制品不成为新的线上真理来源。calls 记录实际请求/用量，不为每次调用复制全部运行快照。

失败定位复用这些输出及现有引用：报告受影响 task/claim/target/facet/call_id、reason_code、可空 field_path 与证据 ref，并保留对应 output item 的 id；不混淆两种身份，不复制完整运行状态或新建日志服务。开发者将已确认失败整理成任务卡的最小反例，加入对应模块的定向回归；识别输入仍不得读取评分参考，回归也不把真实答案写进模型提示。

## 4. 必过场景与判据

| 用例 | 预期 |
|---|---|
| Responses 单调用、多调用、无 output_text 但有 function_call | 解析完整 output，每个 call_id 配对 function_call_output；不丢调用、不从 XML/正文猜测执行 |
| call_id 与输出项 id 不同 | 结果按 call_id 配对；id 仅保留输出项身份，不能用 id 代替 |
| 额外参数/错类型/越权 ref/未知函数 | 工具不执行；合法 call_id 获 function_call_output 结构化错误，可定位时返回 field_path，不能定位时为 null；不泄漏未授权来源，纠错续轮计费 |
| 未知工具／完整响应中的坏参数 JSON → 错误保存 → 暂停继续 | 原始 ToolCall 和 ToolErrorResult 可恢复，parsed_arguments=null；不强制按未知注册表解析，不执行 handler、不退款，配齐 call_id 后在预算内纠正 |
| 缺失/重复 call_id、缺失 arguments 字段或响应整体截断 | 协议失败/未完成，不执行残缺批次；不同 attempt 的结果键仍含 attempt；完整响应内的坏参数 JSON 另按 invalid_tool_arguments 反馈 |
| response.status=incomplete、incomplete_details、refusal | 不执行该批工具、不接纳部分回答；属于执行/协议层无可用回答，不能变成声明 semantic rejected、原文 negated 或空图成功 |
| store=false 的多轮 input/output 项与每轮 instructions | 原样续传当前阶段完整项，包括 reasoning；不发 previous_response_id/conversation，不依赖远端状态 |
| 加密 reasoning 项能力与来源边界 | 仅验收可用时 include；返回项不透明保存/续传，不作原文证据、不作 verifier 的发现理由 |
| 两个阶段中的工具续轮 | 实际调用次数守恒，最多四次；不够时不跳核验 |
| 无需工具与存在顺序依赖的工具 | plan_model_turn 可直接候选→核验，也可在额度内执行工具→工具→候选→核验；下一工具只读取已确认前轮结果，不固定一轮或强迫调用工具 |
| 重复工具/同一证据与同一缺口无进展 | 不能靠重排或重复相同调用延长循环；有足够证据则进入阶段回答，否则保留未完成/未决，必检真理门不变 |
| 按需 ModelContextView | 摘要/索引帮助选取完整授权证据，范围/角色不丢；完整请求放不下时明确未完成，不裁切为误导片段或新增持久状态 |
| 独立核验包含冻结声明及授权依赖 | 从 discovery_ref 派生完整 verification_input，不继承发现 reasoning；对象、原单位、selection 或限定变化准确体现在输入，缺内容/错 hash 不发请求 |
| discovery/verification 阶段内 recovery mode | 由真实缺口和 recovery_kind 派生白名单，只用一次恢复机会；不新增运行阶段或重置请求/工具额度 |
| 保存完整 ResponseTurn/函数结果后暂停 | 恢复 active_input_items/active_instructions/pending_call_ids，复用 attempt+call_id 已确认结果和完整输出项；不丢 reasoning、不重复模型或工具，无 owner 写权限时停止 |
| 检索确认新证据 → 暂停 → 恢复读取 | 从当前 context_authorization_ref 和冻结 IR 重建授权上下文；文本、跨度、角色、归属、fact_eligible、scope 和 hash 与暂停前相同；未确认/过期描述不授予权限 |
| 旧 run 冷恢复和原投影视图 | 原键、hash、序列化和冻结语义不变；旧 verified 请求拒绝，不补 scope 后重算 |
| GLiNER 应用环境与离线加载 | 共同求解固定包和应用依赖，通过受影响模型回归；导入前 OFFLINE，共享清单校验，缺文件/错 hash 明确失败 |
| 无编号或无名称记录主体 | 有合法组成和类型证明可交付；不伪造 span 或全局键 |
| one_of、alternatives、all | 三种语义独立；多个对象一个规范组，不新增普通事实边 |
| 同实体在两个组、嵌套组、范围冲突 | scoped task/slot/coverage 不碰撞，后继保留范围，冲突不展开 |
| verified 图含孤立、否定、条件/模态声明 | 所有语义/证据保真；effective 只作为子图 |
| conditional 过滤父选择组而保留子声明 | scope_resolutions 仍含精确版本派生的选择/限定与证据；不计新增事实 |
| 区间/比较与端点 | 普通 scalar 不截值，显式 endpoint 保留完整来源；边界和比较符不丢 |
| mg→g 等真实尺度换算及已注册偏移 | 精确数值正确；未知/错量纲/单位 owner 错误未决或拒绝，不补单位 |
| SHACL 空图、错 focus、异构声明 | 非空/完整 focus 才完成；每声明适用 profile，不拿单一 slot 校验整图 |
| 标准工具目录与模型可见集合 | 十项已注册；metric/graph 的 model_callable=false，所有模型阶段均不发送二者，主动请求返回 tool_not_allowed；控制器仍按可信前置条件完成适用必检，finalize 不增模型轮次 |
| 来源关闭、未知键、同名冲突、外部值差异 | 局部实体仍保留；身份不伪造，值不覆写 |
| 新本体/IRI 重命名/等义表达 | 同一算法，无领域分支；不支持构造显式报告 |

## 5. 真实质量与完成报告

先冻结独立标注、文档/模板分割、本体与预算，再运行总体方案 F0—F4。F0—F2 固定主记录，补证只服务当前缺口；F3 才改变主记录覆盖；数值 F4 单独统计真实转换与 SHACL 的有效样本。单文档回归不称跨领域提升。

按最终交付图评分：实体、属性、单边/关系组及完整范围；默认视图遗漏、技术未执行、核验失败未交付均纳入端到端召回。one_of {A,B} 预测为两个普通边时计 2 FP、1 FN。工具协议、JSON 合规和 NER span 召回均不能替代该评分。

最终报告列明实际修改、已执行测试/skip、真实模型请求及 token、图谱/数值正确路径、未决与限制。工程通过、原生接口接通、质量改善和部署分别写；不因报告生成就标为已部署。

## 6. 设计制品检查记录

**历史 Chat 协议、Harness 修订前记录**：2026-09-16 使用系统 Python 与 jsonschema 检查：3 个 JSON 文件可解析；10 个工具参数 Schema、2 个阶段 Schema 合法，25 个对象定义均禁止额外字段并列全 required；12 份有效输入通过，36 个缺字段/额外字段/错类型反例被拒绝。合成消息的调用 ID/参数/结果配对、18 个逐字引用及 3 个核验目标集合一致；26 项任务依赖无环，当时本规范包及关联设计/README 的 85 处本地链接可解析，git diff --check 通过。上述数量和结果不作为本次 Responses 修订的检查结论；本次结果须在完成检查后另行追加。

这仅验证设计制品的结构和一致性，没有执行新增运行代码、工程测试、GLiNER 或 Qwen；示意 hash 不代表已经实现并验收冻结算法。

**历史 Chat 协议、Harness 修订后记录**：10 个工具参数/2 个阶段 Schema、12 份有效输入、36 个非法输入反例、18 个逐字引用及 3 个核验目标集合仍通过；96 处本地链接可解析，26 项任务依赖无环。另检查 11 段 Python 接口示意的语法，以及新增错误反馈的调用 ID、参数、field_path 与阻断结果配对。git diff --check 通过。以上均为当时设计制品检查，没有执行目标运行循环、工程/浏览器测试或真实模型，也不证明本次 Responses 契约已通过。

**本次 Responses 协议修订记录**：2026-09-16 使用系统 Python 与 jsonschema 检查：3 个 JSON 文件可解析；10 个工具参数 Schema、2 个阶段 Schema 合法，25 个对象定义均禁止额外字段并列全 required；12 份有效输入通过，36 个缺字段/额外字段/错类型反例被拒绝。18 个逐字引用、3 个核验目标集合、98 处本地链接及 26 项无环任务依赖通过检查。另核对 1 组 Responses 合成请求、完整 output 项、function_call_output 与本地完整 input 续传，阶段 text.format 的 Schema 与制品一致；1 组错误反馈的 call_id、参数、field_path 与阻断结果配对通过。11 段 Python 接口示意可解析，git diff --check 通过。本次只验证设计制品，未实施运行代码，未执行工程/浏览器测试或真实 Qwen、GLiNER 调用；项目 Qwen 支持 Responses 以用户确认为设计前提，具体字段兼容仍须按上述接通验收执行。
