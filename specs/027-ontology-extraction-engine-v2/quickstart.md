# 验收步骤

状态：设计检查与离线 `probe/run/score` 入口已实现；下列各层验收独立记录，不由单层通过推定其余通过。历史设计检查记录保留在末尾，实施结果另列。

研发任务从 [Harness 设计](harness.md)、[模块计划](plan.md) 和 [任务卡模板](tasks.md#每次提交的检查) 定位所需上下文。研发 Harness 的反馈是代码/契约/界面验收结果；运行 Harness 的反馈是 Qwen 工具结果、冻结声明和证明，两层不共用另一套任务状态或 Agent 框架。

## 0. 当前可执行的设计制品检查

仓库入口为 [check_design.py](check_design.py)，从脚本位置定位仓库根，不依赖当前工作目录或 `/tmp` 文件。环境为 Python 3.11+、`jsonschema==4.23.0`，依赖写在脚本的 PEP 723 元数据中；使用预先准备好该依赖的设计检查环境。脚本不联网、不自动安装依赖、不调用模型，不导入应用运行模块。本工作区实际使用已有系统 Python；backend/.venv 未安装此依赖，不能将其当作已准备好的设计检查环境。

从仓库根运行：

```bash
python specs/027-ontology-extraction-engine-v2/check_design.py
python specs/027-ontology-extraction-engine-v2/check_design.py --self-test
```

第一条检查本地 Schema、样例、完整 Responses 往返、核验声明/依赖/hash、授权恢复描述、工具可见性、链接、需求归属及任务依赖。第二条另在内存副本上验证非法输入和制品变异会被拒绝，不改仓库文件。错误须指出具体制品/字段；缺依赖必须明确失败，不能跳过后报告通过。外部 `$ref` 不触发网络读取，只允许显式注册的本地契约。

控制层输入契约见 [context-schemas.json](contracts/context-schemas.json)，覆盖 verification_input、context_authorization 与分派前错误观察；它们不是新的模型输出阶段。下面的工程测试仍负责验证实际实现与制品同义，设计自检不能代替运行、暂停恢复或真实模型验收。

## 1. 环境与输入

使用 backend/pyproject.toml、uv.lock 和已有环境。纯工程测试不下载权重；真实 GLiNER2.5 使用固定本地模型与兼容依赖，真实 Qwen 使用项目配置端点/模型。用户已确认 Qwen 支持 Responses，027 主协议固定为 Responses API；验收具体字段、严格参数、结构化输出和无状态续传，不把协议支持重新当成待猜测条件。旧 Chat Completions 仅做已有调用回归，新运行不建设双栈/回退。确认必需工具可用，缺项明确报告；不得把关闭 NER 作为“完整工具方案通过”。不重启共享后端来替代隔离验证。

应用入口：新建非模板文档识别运行读取 `settings.ontology_extraction_options`（环境变量 `ONTOLOGY_EXTRACTION_OPTIONS` 为 JSON），并将选项冻结进该运行 policy。可配置 `profile`、`vocabulary_overlay`、`gliner2`、`external_sources`、`responses`，结构与下方 manifest 的 `options` 相同。每个声明 lineage 最多 4 次实际模型请求，工具续轮与独立核验共用额度。

当前端点使用 `responses={"strict_tools":false,"strict_answers":false}`；真实 probe 发现 strict 答案仍可能带 Markdown 围栏，不能声明服务端强制 Schema 已验收。本地严格解析与独立核验始终执行。`options.responses.reasoning` 默认省略；显式设置时仅透传标准 `effort` 枚举并冻结进请求 hash。当前端点的两请求对照中，`{"effort":"none"}` 仍返回非空 reasoning，只证明字段被接受，未证明关闭思考，不能据此默认启用。GLiNER/Mock 未配置时不向模型公开对应工具；配置后若必需依赖、权重或来源不可用，明确失败，不能静默替换为成功空结果。`gliner2.manifest` 必须是完整冻结清单对象；`external_sources` 使用显式 records/name_fields/alias_fields，不在通用核心默认绑定设备源。

本次实际环境、协议、小预算图谱与限制见[实施验证记录](../../docs/调研/ontology-tool-engine-20260916/README.md)及[最新分项结果](../../docs/调研/ontology-tool-engine-20260916/latest-run-summary.json)。工程测试、原生协议通过、质量评估与部署分别记录。数量正例已由真实 Qwen 发现与独立核验后将 `1500 mg` 规范化为 `1.5 g` 并入图；负例 `1500 m` 已由单位门禁拒绝，范围仍未完成。`controller_checks` 是检查调用数，须分别核对数值结果和 SHACL 的 `evaluated/conforms/coverage.complete`；工具被调用不等于求值或通过，空图也不等于正确拒绝。

完整 CMC 输入与 216 条系统 Mock 的最新有界运行只实际调用了 `inspect_evidence`；GLiNER2.5、`query_instances` 与 `retrieve_evidence` 虽可见但未调用。主关系回答因单对象选择 `one_of` 被严格拒绝，完整续轮上下文超预算，结果保留 partial；不能把工具配置就绪或摘要进入上下文当作整套工具协作、全文覆盖或 F1 已验收。

另有显式指定原文行、编号和 `tool_choice` 的[四请求集成探针](../../docs/调研/ontology-tool-engine-20260916/tool-chain-probe01/summary.json)。完整协议与严格总结通过，但 `propose_mentions` 超工具结果预算、原文定位参数错误、实例查询引用未授权，三项均被阻止，候选数为零；这类工程夹具不计入自主抽取或同预算质量对照。冻结设备词表还缺少 25 项概念定义，包含 Reactor；未临时修改 overlay 或放宽门禁。

相同参数下的[零 Qwen 预算诊断](../../docs/调研/ontology-tool-engine-20260916/tool-chain-probe01-diagnostics/diagnostic.json)确认 GLiNER 实际执行 4 个批次，返回 7 个 span 和 25 项缺定义观察；完整结果为 4660 tokens，超过 4096 上限。有效实体标签仅 CMCReport，提及类型不正确，故执行覆盖、工具预算和实体识别质量仍须分别验收。

GLiNER2.5 的 Python 包固定为 `gliner2[local]==2.0.0`，不是 gliner2==2.5。新增 `gliner2` extra；CPU `uv.lock` 与 CUDA `requirements-cuda12.lock` 均已共同求解 Transformers 4.57.6、HF Hub 0.36.2、tokenizers 0.22.2、protobuf 6.33.5、peft 0.21.0，保留各自 PyTorch 版本和 sentence-transformers 5.6.0。独立克隆应用 CPU 环境按锁同步后，真实 GLiNER2.5、BGE embedding、BGE CrossEncoder 离线推理通过，受影响模型回归 192 项通过；CUDA 共同依赖解析通过不等于新锁已部署。原应用 venv、共享容器和模型权重未改动，不通过 sys.path 混用实验 venv。

应用进程在导入 Hugging Face/Transformers/GLiNER 相关库**之前**设置 `HF_HUB_OFFLINE=1` 和 `TRANSFORMERS_OFFLINE=1`，核对实际进程环境及缓存状态；不能等工厂创建模型后才设置。已统一为 `gliner2_extractor.verify_local_checkpoint(model_path,manifest)`，线上和离线 runner 复用，线上不导入 evaluation。构造器优先使用运行中冻结的 manifest；无该参数时读取本地 `DOWNLOAD-MANIFEST.json`。验收本地权重/tokenizer 完整加载、错误 hash 和缺失文件均明确失败、全程无下载请求；历史准备环境和线上环境各自验证。

新评测 manifest 至少包含以下字段，路径由操作者指定，不硬编码历史报告：

| 字段 | 内容 |
|---|---|
| run_id；命令参数 --output | 新运行与新目录；已存在目录即拒绝覆盖 |
| document、ir、ontology、metadata | 各为 `{path,sha256}`；相对 manifest 解析，校验同源原文/IR/摘要及 SHA256 |
| root_class_iri | 冻结本体中的用户指定根 |
| options.profile、options.vocabulary_overlay | 内嵌冻结的 ExtractionProfile / VocabularyOverlay；无 overlay 可省略 |
| model、model_revision、tool_protocol_version、api_protocol | 必须匹配项目配置身份；ontology-tool-extraction-v1、固定 responses |
| store、options.responses | store 固定 false；strict_tools/strict_answers 默认为 false，include 默认为空，reasoning 默认省略；能力以独立 probe 结果为准 |
| options.gliner2 | `{model_path,manifest,device}`；manifest 内嵌权重身份与逐文件 hash；完整工具方案须配置 |
| options.external_sources | 可省略；冻结 records/name_fields/alias_fields/incomplete_sources，不读评分参考 |
| budgets | max_model_calls、max_input_tokens、max_output_tokens、max_calls_per_lineage=4、工具和补证上限 |

`budgets` 另支持 max_tasks、max_hops、max_result_tokens、max_tool_calls_per_lineage；模型总额度在协调器实际请求预留前强制检查。F0—F4 分别冻结 manifest/输出目录，差异由外部对照说明记录，识别器不按 arm 名称分支。未列出的顶层字段一律拒绝。

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

## 3. 离线 CLI 契约

入口 `python -m app.evaluation.ontology_tool_engine`，放在 M13，已实现。工作目录为 backend/：

```bash
.venv/bin/python -m app.evaluation.ontology_tool_engine probe --output /tmp/new-responses-probe --max-model-requests 4
.venv/bin/python -m app.evaluation.ontology_tool_engine run --manifest /path/to/manifest.json --output /tmp/new-extraction-run
.venv/bin/python -m app.evaluation.ontology_tool_engine score --prediction /tmp/new-extraction-run/evaluation.json --reference /path/to/approved-reference.json --output /tmp/new-score
```

`score` 复用既有正式评分器，参考未经 approved 或文档/本体/根/范围不一致会明确拒绝；不能为得到分数改写参考身份。`run` 除最小制品外保存 evaluation.json 与 document-ir.json 供独立评分。工程入口 `tests/test_extraction/test_tool_engine_cli.py` 覆盖完整项续传、call_id、围栏/未完成拒绝、参考隔离、输入 hash、硬请求预算和拒绝覆盖。

| 子命令 | 参数 | 行为/退出码 |
|---|---|---|
| probe | `--output DIR --max-model-requests N` | 使用项目 Qwen Responses、合成非业务文本验收具体字段与无状态往返；预算硬上限，输出 capabilities.json；0=声明的基础验收通过，2=字段兼容/具体能力验收未完成，1=技术失败 |
| run | `--manifest PATH --output DIR` | 仅共享核心识别；不读金标；0=任务范围执行完成，2=partial/未执行，1=技术失败；0 不等于质量通过 |
| score | `--prediction PATH --reference PATH --output DIR` | 不调用模型、不改预测；输出 P/R/F1、未决、coverage、成本；参考未决单列，不静默丢弃 |

`probe` 首轮建议预算 4，验证至少一个真实 `response.output` 中的 function_call、相同 call_id 的 function_call_output 和利用结果的后续回答；无调用输出不能算工具往返成功。工具定义为平铺 `{type:"function",name,description,parameters,strict}`；基线显式 `strict:false`，仅在当前端点验收严格参数生成后冻结为 true，不能把省略 strict 当成关闭。严格参数生成与阶段 `text.format={type:"json_schema",name,schema,strict}` 分别记录；使用 `max_output_tokens`，不以请求接受/一次合法 JSON 推断完整能力。其他具体能力测试需要额外请求时，显式声明其预算，不超出本次 probe 上限。

每轮使用 `store=false` 和显式 instructions；下一轮 input 包含当前阶段完整先前输入、原样 output 项和配对 function_call_output。验收 output 同时含 reasoning/function_call/message 项的保存与续传，不能只发送 output_text 或抽取出的函数调用。`id` 是输出项身份，`call_id` 才是调用与结果的配对键；测试必须包含两者不同的情况。只在端点已验收时请求 `include=["reasoning.encrypted_content"]` 并保留不透明返回项；它和普通 reasoning 都不进入事实证据。不得发送 previous_response_id/conversation，不依赖远端会话继续。

capabilities.json 固定 api_protocol=responses，记录实际工具 strict、阶段结构化输出、无状态完整项续传和可选加密项结果。具体字段不兼容、response.status 非完整、incomplete_details 或 refusal 如实报告；不执行该批工具，不把拒绝或缺回答记为成功空图，不切换旧 Chat 协议。

新增 CLI 只装配命令和 Responses probe；`run` 复用 quality_guided_variant.py 的现有离线评测器，`score` 扩展 ontology_guided_scorer.score_evaluation 的组/scope/模态评分，不另建执行或评分引擎。`run` 输出最小集合：manifest.json、final-graph.json、coverage.json、metrics.json、calls.json、protocol-checks.json。在线仍使用现有当前存储；离线制品不成为新的线上真理来源。calls 记录实际请求/用量，不为每次调用复制全部运行快照。

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
| 可选工具与关系必检 | 属性可直接候选→核验；有效关系必须候选→validate_graph→语义核验。工具只读取已确认前轮结果；缺少检查时按单响应上限分批，并为语义回答预留预算 |
| 重复工具/同一证据与同一缺口无进展 | 不能靠重排或重复相同调用延长循环；有足够证据则进入阶段回答，否则保留未完成/未决，必检真理门不变 |
| 按需 ModelContextView | 摘要/索引帮助选取完整授权证据，范围/角色不丢；完整请求放不下时明确未完成，不裁切为误导片段或新增持久状态 |
| 独立核验包含冻结声明及授权依赖 | 从 discovery_ref 派生完整 verification_input，不继承发现 reasoning；对象、原单位、selection 或限定变化准确体现在输入，缺内容/错 hash 不发请求 |
| discovery/verification 阶段内 recovery mode | 由真实缺口和 recovery_kind 派生白名单，只用一次恢复机会；不新增运行阶段或重置请求/工具额度 |
| 保存完整 ResponseTurn/函数结果后暂停 | 从 stage_input_items、有序 turn_refs 和 completed_tool_results 派生完整 input/未处理 call_id，每轮发送 active_instructions；结果正文只存一份，不丢 reasoning、不扫描历史或重放已确认工具，无 owner 写权限时停止 |
| 检索确认新证据 → 暂停 → 恢复读取 | 从当前 protocol.context_authorization、外层预期证据版本/hash 和冻结 IR 重建上下文；文本、跨度、角色、归属、fact_eligible、scope 和 hash 与暂停前相同；工具结果不附加累计权限快照，未确认/过期描述不授予权限 |
| 旧 run 冷恢复和原投影视图 | 原键、hash、序列化和冻结语义不变；旧 verified 请求拒绝，不补 scope 后重算 |
| GLiNER 应用环境与离线加载 | 共同求解固定包和应用依赖，通过受影响模型回归；导入前 OFFLINE，共享清单校验，缺文件/错 hash 明确失败 |
| 无编号或无名称记录主体 | 有合法组成和类型证明可交付；不伪造 span 或全局键 |
| one_of、alternatives、all | 三种语义独立；多个对象一个规范组，不新增普通事实边 |
| 同实体在两个组、嵌套组、范围冲突 | scoped task/slot/coverage 不碰撞，后继保留范围，冲突不展开 |
| verified 图含孤立、否定、条件/模态声明 | 所有语义/证据保真；effective 只作为子图 |
| conditional 过滤父选择组而保留子声明 | scope_resolutions 仍含精确版本派生的选择/限定与证据；不计新增事实 |
| 区间/比较与端点 | 普通 scalar 不截值，显式 endpoint 保留完整来源；边界和比较符不丢 |
| mg→g 等真实尺度换算及已注册偏移 | 精确数值正确；未知/错量纲/单位 owner 错误未决或拒绝，不补单位 |
| SHACL 空图、错 focus、异构声明 | 非空/完整 focus 才完成；每声明适用 profile，不拿单一 slot 校验整图；表示图只在 finalize 内临时构建，暂停后可重做纯本地计算，无图引用登记或恢复状态 |
| 标准工具目录与模型可见集合 | 十项已注册；validate_metric 只供控制器，validate_graph 仅在 verification 有冻结关系时对模型开放。属性校准与 SHACL 仍由控制器完成，finalize 不增模型轮次 |
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

**历史 Responses 协议、审查修复前记录**：2026-09-16 使用系统 Python 与 jsonschema 检查：3 个 JSON 文件可解析；10 个工具参数 Schema、2 个阶段 Schema 合法，25 个对象定义均禁止额外字段并列全 required；12 份有效输入通过，36 个缺字段/额外字段/错类型反例被拒绝。18 个逐字引用、3 个核验目标集合、98 处本地链接及 26 项无环任务依赖通过检查。另核对 1 组 Responses 合成请求、完整 output 项、function_call_output 与本地完整 input 续传，阶段 text.format 的 Schema 与制品一致；1 组错误反馈的 call_id、参数、field_path 与阻断结果配对通过。11 段 Python 接口示意可解析，git diff --check 通过。当时只验证设计制品，未实施运行代码，未执行工程/浏览器测试或真实 Qwen、GLiNER 调用；项目 Qwen 支持 Responses 以用户确认为设计前提，具体字段兼容仍须按上述接通验收执行。

**历史 Harness 审查修复、极简审查前记录**：2026-09-16 实际执行 `python specs/027-ontology-extraction-engine-v2/check_design.py --self-test` 通过。4 个 JSON 文件、10 个工具参数 Schema、2 个阶段输出 Schema、3 个控制输入 Schema、53 个封闭对象定义及 1 个类型化动态引用映射通过；12 份有效输入通过，36 个参数/阶段 Schema 反例被拒绝。完整 discovery 往返、独立 verification 请求及 3 个目标、2 类错误保存恢复与 2 次纠正、1 个检索授权重建样例通过；模型可见目录为 8 项、控制器独占 2 项。15 项制品变异被预期检查拒绝，包含缺声明内容、过期 hash、重算 hash 后仍悬空的实体引用、call_id 失配、错误反馈空 issues、授权升级、悬空链接和任务环；另有 3 项表头单位 hash 检查通过，3 种非标准 JSON 常量被拒绝。27 个逐字引用、132 处本地文件链接、11 段 Python 接口示意、18 个需求到 13 个模块的归属及 26 项无环任务依赖通过。

实际执行 `backend/.venv/bin/ruff check --config backend/pyproject.toml specs/027-ontology-extraction-engine-v2/check_design.py` 和 `git diff --check` 均通过。独立检查器从其他工作目录运行也通过，缺少 jsonschema 的环境明确退出；本次未安装依赖。以上是设计制品和检查器验收，不是运行引擎、暂停恢复实现或真实 Qwen/GLiNER 的通过记录；26 项开发任务保持待实施。

**本次极简设计收紧后记录**：2026-09-16 实际执行上述仓库检查器 `--self-test`、定向 Ruff 和 `git diff --check` 均通过。4 份 JSON、10 个工具、2 个输出阶段、3 个控制输入 Schema、52 个封闭对象和 1 个类型化引用映射通过；12 份有效输入通过，36 个非法输入及 22 项制品变异被拒绝。另通过 2 项协议输入重建、3 项单位语义 hash 检查，拒绝 3 种非标准 JSON 常量；完整核验、错误反馈及授权恢复样例、135 处本地文件链接、11 段 Python 接口示意、18 个需求到 13 个模块的归属及 26 项无环任务依赖通过。

新增反例覆盖重复权威字段、重复实体副本、丢失完整 output/reasoning，以及同步删去目标和回答后仍能发现未核验声明。仅为设计制品验证，未执行应用测试、真实模型或部署。

## 图谱界面增量验收（FR-19 / T27）

在 `/analysis?tab=document` 检查历史为多列卡片；点击任意卡片，桌面详情抽屉宽度为视口 4/5，手机全宽。关闭恢复卡片焦点；刷新含 documentRun 的 URL 自动恢复详情。打开图谱 tab，核验可缩放/拖拽/适配、点击节点显示该节点属性与入/出关系，孤立节点仍可选；关系组菱形显示选择语义，虚线成员连接不计普通事实边。属性精确数量与继承限定仍可查，证据点击定位同一 Word 预览。全过程只发 GET，不产生或取消模型任务。

### 五项运行缺陷回归（2026-09-17）

详见[输入差异与修复对比](../../docs/调研/ontology-tool-engine-20260916/repair-20260917/README.md)。新增验收必须包含：

- 无记录标题、多记录共享表头能够读取、检索授权并冷继续；辅助片段 null record_id 不得提升事实权限。
- 实际32768输入/20480输出预算下，20000-token输入可发送；若另设35000总上下文则在请求预留前拒绝。完整续传和强模型独立核验不变。
- 合法空候选检查完成且继续检索，unknown/ambiguous/unbound仍未决；无候选不能解释为全文否定。
- 工具协议与通用稀疏规划实际组合执行，包括请求前/模型结果后暂停继续；离线结果记录真实 executor 版本。
- 多次相同缺定义/来源反馈不消耗重复返回空间；模型超限仍阻断，控制器完整binding/metric/SHACL必检不被模型返回额度误伤。

`budgets.max_context_tokens` 可选，只填经服务能力核实的总上下文容量；max_input_tokens是完整请求的输入额度，max_output_tokens包括推理输出。工程反例通过、真实局部调用成功、正式全文F1验收、部署是不同交付状态，按本轮报告分别核对。

## FR-20 / Drawer 实时观察验收

当前实现与实际结果见 [Harness Drawer 验证记录](../../docs/调研/ontology-tool-engine-20260916/harness-drawer-20260917/README.md)及[布局收纳与线程接线补充验收](../../docs/调研/ontology-tool-engine-20260916/harness-contained-20260917/README.md)。打开 `/analysis?tab=document` 的历史卡片，默认图谱，展开 Harness运行信息检查模型/工具与预算；Thinking、操作、上下文收纳在 Harness运行信息内并默认折叠。关闭外层信息后不继续读取上下文，实时正文仍更新。上下文提示词与 Schema 卡片使用同一实际调用，切换不发模型请求。运行期间上滚正文会停止跟随，可回到最新；暂停/结束后光标停止。

新增只读契约：`GET /api/document-analysis/runs/{run_id}/harness`、`GET /api/document-analysis/runs/{run_id}/harness/context?call_id=...`，严格响应类型见 `backend/app/schemas/document_analysis.py` 的 HarnessResponse/HarnessContextResponse。现有 SSE 增加无 id 的 harness 当前展示帧，保留原运行事件游标；上下文更换返回 CONTEXT_CHANGED / 409，不能将最新卡片配给旧提示词。

2026-09-17 13:08 UTC 加载线程接线修复后，现有运行已恢复并实际生成提示词、窄化 Schema 与可读 Thinking；见[部署核验](../../docs/调研/ontology-tool-engine-20260916/harness-context-deploy-20260917/README.md)。遇到空上下文先核验当前 call/cache 与进程加载时间，不用合成卡片掩盖未采集数据。

本轮无需迁移，未自动重启服务。后端重新加载代码后，仅之后的实际请求生成流式观察；无历史数据时界面明确为空。

## T32 / 关系校验分工验收

本次实现、可复现命令、真实 Qwen 工具探针与部署状态见[关系校验分工记录](../../docs/调研/ontology-tool-engine-20260916/relation-validation-20260917/README.md)。重点反例：模型漏调工具、工具失败仍回答 supported、结果与声明/上下文/菜单版本不匹配、重复创建同一精确提及，均不得接纳关系；同名不同原文位置不得因此合并。

冻结阶段已拒绝的无效候选不再要求模型调用工具；有效冻结关系必须先 validate_graph，再给出独立语义判断。工具结果只证明已列明约束，semantic_status 固定 not_checked。多关系分批时应预留全部工具轮及语义回答预算；暂停后直接使用已确认结果，续传完整 input/output，不重复执行。
