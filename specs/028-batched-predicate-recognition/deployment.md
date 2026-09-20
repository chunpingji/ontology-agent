# 028 部署记录

当前状态：**新在线运行默认启用 028，每批最多 4 个谓词**。最近一次后端修复重启的容器启动时间为 2026-09-19 02:56:28 UTC；最近一次完整应用重启为 2026-09-18 12:36:33 UTC。下方保留各次部署时的实际状态。

2026-09-18，按用户“请部署，并重启系统”的指令，在 `021-ontology-guided-doc-graph` 工作区部署当前实现。

## 执行

沿用现有 `docker-compose.yml` 与 `docker-compose.override.yml`。后端和前端均挂载工作区源码，依赖未变化，因此复用现有镜像，通过重启加载改动。

| 服务 | 重启完成时间（UTC） | 核验状态 |
|---|---|---|
| frontend | 11:45:25 | running |
| backend | 11:45:52 | running |
| web | 11:46:48 | running |

重启前，通过现有控制接口安全暂停正在运行的文档分析任务 `4b4a8267-3b79-48eb-9b22-f88b0e6c7ae9`，等待模型结果保存、任务进入 paused 且活动模型请求数归零后再重启后端。该任务仍保留暂停，未自动继续或改变冻结策略。数据库和宿主模型服务无需重启。

## 核验

- 后端 `http://localhost:8000/api/health` 与网关 `/api/health` 均返回 200，10 个本体模块正常加载。
- `http://localhost:8081/analysis?tab=document` 返回 200；实际返回的 JavaScript 包含多成员谓词显示和成员 Schema 卡片代码。
- 运行中后端 OpenAPI 的 `HarnessCall` 已含 `members` 与 `member_count`。
- 五个关键后端文件的容器 SHA256 与工作区一致，新增批处理模块可正常导入。
- 数据库 revision 和代码 head 均为 `0041_template_engine`。
- 安全暂停后与重启后的全部运行状态、冻结 fingerprint、work/request/control version 相同；运行、请求、结果及制品 head 数量相同。活动模型请求数为 0。

| 数据 | 重启前后数量 |
|---|---|
| document_analysis_runs | 15 |
| document_analysis_requests | 28827 |
| document_analysis_results | 22954 |
| document_analysis_artifact_heads | 112 |

## 配置状态

028 代码已部署，`recognition_batching` 当前未配置，仍按显式启用策略运行。未新建模型分析任务或执行真实模型对照；部署烟测不代替质量评测或 PostgreSQL 专项并发验收。工作区改动尚未提交 Git。

## 属性候选最小修复部署（2026-09-18）

按用户后续“请部署并重启”指令，部署已验证实体原文优先进入属性候选的修复。修复阶段的定向回归为 136 passed，Ruff 通过；本次执行部署烟测，未重复调用真实识别模型。

沿用上述源码挂载和现有镜像，执行 `docker compose restart --timeout 45 backend frontend`，随后重启 web。各服务启动时间（UTC）为：frontend 12:22:17、backend 12:22:21、web 12:23:07。重启前无 queued/running/pausing 文档分析运行、无活动模型请求，无需新增暂停操作；数据库及宿主模型服务未重启。

核验结果：

- 后端与网关 `/api/health` 均返回 200，10 个本体模块已加载；网关 `/analysis?tab=document` 返回 200。
- 后端容器能导入修复后的执行器；执行器文件 SHA256 与工作区一致：`3af69c3fb1e92663542118b8942773654dbe61ce42ff61b6748b0e9132c16bfd`。
- 数据库 revision 与代码 head 均为 `0041_template_engine`。
- 重启前后运行状态、冻结 fingerprint、work/request/control version 摘要一致；运行、请求、结果、制品 head 数量分别保持 14、28083、22215、107。活动模型请求数为 0。
- `batching` 仍未配置。属性候选修复也适用于单谓词执行；已保存的旧运行候选不自动改写，新建运行可完整应用修复。

## 默认开启 028（2026-09-18）

用户明确要求开启 028，并要求新特性完成后默认开启。该偏好已写入仓库 `AGENTS.md`；在线 `freeze_tool_engine_policy` 现在对缺失的 batching 配置采用 `RecognitionBatchPolicy` 默认值，不需修改或替换本机其余模型配置。显式容量覆盖仍经严格校验，旧运行继续按其冻结策略恢复。

- 新运行有效策略：`recognition_batching={"version":"predicate-batch-v1","max_members":4}`、`model_call_state_version=3`。
- 配置、批次状态、批次执行、运行 API 与连续预算五个测试文件合并运行：**90 passed**；四个受影响 Python 文件 Ruff 通过，`git diff --check` 通过。API 测试验证新运行实际保存默认策略，适配器测试验证旧单任务策略不被升级。
- 执行 `docker compose restart --timeout 45 backend`，后端启动时间为 **12:31:19 UTC**。本次未修改前端或网关配置。
- 容器内冻结策略检查确认默认值生效；后端与网关健康接口、分析页面均返回 200，10 个本体模块已加载。运行中版本对应的执行配置文件 SHA256 与工作区一致：`a000525b9b4394d2c592b05bdba9974be4b1e34fa28e320386d51a8b95838f6b`。
- 重启前后运行状态及冻结版本摘要一致；运行、请求、结果、制品 head 数量仍为 14、28083、22215、107。活动模型请求数为 0，数据库 revision 与代码 head 均为 `0041_template_engine`。
- 本次未创建识别运行或发起真实模型请求；默认启用不表示真实质量与成本对照已完成。

## 再次部署与重启（2026-09-18 12:36 UTC）

按用户再次要求，执行 `docker compose restart --timeout 45 backend frontend web`。frontend、web 启动于 12:36:29 UTC，backend 启动于 12:36:33 UTC。工作区源码挂载及依赖未变化，复用既有镜像。

后端和网关健康接口、分析页面均返回 200，10 个本体模块已加载。前后端关键文件容器哈希与工作区一致，028 有效策略仍为每批最多 4 个谓词、调用状态版本 3。重启前无活动文档分析及模型请求；重启后运行状态、冻结版本摘要和上述四项数据数量一致。数据库 revision 与代码 head 均为 `0041_template_engine`。

## 空目标核验与批处理异常修复（2026-09-18 13:29 UTC）

用户报告 `analysis_failed / ANALYSIS_FAILED`。定位运行 `a337a5f1-d9be-4ed5-8ec4-191aedd236b6`：两个成员的发现结果均无声明，核验目标为空，但请求 Schema 仍生成空的 target_id/content_hash 枚举。模型服务在 12:51:43 UTC 记录 `unparsed peg-native`，客户端记录 `model_request_failed`（约 181 秒）；证据不支持将其称为超时。随后适配器将仍有 pending 请求的传输失败转为成员错误，协调器尝试提交 outcome，触发 `pending batch cannot publish results`，掩盖原始故障。

本次修复：

- 无核验目标时，以 `verifications.maxItems=0` 约束空列表，不生成空目标枚举；不新增声明或放宽非空目标校验。
- 未确认请求发生异常时直接交回协调器；无论 worker 抛异常还是返回成员错误，只要请求仍 pending，就停止该批次的成员结果发布，保留唯一恢复入口及已用额度。
- 终止状态明确为 `model_calls_unresolved / MODEL_REQUEST_OUTCOME_UNKNOWN`；说明已保留结果和调用计数、继续不会自动重发。完整响应缺失的原运行不能靠重启补回结果，应检查模型服务后新建运行；本次未修改或重新执行该运行。

验证与部署：

- 相关 11 个测试文件合并运行 **214 passed**（98.11 秒），八个修改的 Python 文件 Ruff 通过；新增回归及完整命令见 [验收步骤](quickstart.md#7-批处理失败修复验证2026-09-18)。
- 对当前 Qwen3.6-35B-A3B 执行三笔仅含合成数据的流式 Responses 烟测。前两笔分别用满 256/2048 输出额度，未得到可解析的最终 JSON；第三笔将上限设为 8192，实际输入 90、输出 431 tokens，返回 completed 和两个空核验列表，通过批次解析器校验。烟测写入既有模型调度记录，不是只读操作，也不代表完整原文质量评测。
- 确认活动文档运行和活动模型请求均为 0 后，执行 `docker compose restart --timeout 45 backend`；后端启动时间为 **13:29:31 UTC**。沿用源码挂载和原镜像，无依赖或数据库结构变更；前端、网关、数据库及宿主模型服务未重启。
- 后端和网关 `/api/health` 均为 200，10 个本体模块已加载，分析页面返回 200。四个修复后端文件容器哈希与工作区一致；数据库 revision 与代码 head 均为 `0041_template_engine`。
- 重启前后运行状态、fingerprint、work/request/control version 及 event_head 摘要一致；文档运行、请求、结果和制品 head 数量分别保持 **15、28089、22223、112**。活动模型请求数为 0；028 有效策略仍为每批最多 4 个谓词、调用状态版本 3。

## 工具调用优化部署（2026-09-18 14:38 UTC）

按用户“请优化工具调用”及本会话已有部署授权，部署工具参数约束、空上下文处理与空目标核验优化：

- 工具 Schema 列出授权证据 ID、本体卡 ID 和单次读取上限（默认 16）；提示完整复制 `evidence_id`，区分共享文本的 `unit_id`。数量超限明确返回 `evidence_unit_limit_exceeded`，不再误报调用额度耗尽。
- 锚点工具将空串或精确字面值 `"null"` 视为未提供可选消歧上下文；原始工具调用保留，仍须通过授权、非空、逐字及唯一定位校验。其他非空错误上下文继续拒绝，已冻结声明和结果哈希不改写。
- 当时全组核验目标为空时不提供工具，但仍保留一次最终回答请求及真实计数。该处理未满足原单任务跳过空核验的约束，已由后续“空目标完整修复”替代；历史调用成本保留。

验证与运行状态：

- 最终 12 文件回归 **287 passed**（63.25 秒），无跳过；五个修改的 Python 文件 Ruff 通过。覆盖成员权限、歧义/错误引文、单次数量限制、冷继续的已付费响应与工具配对、关系检查、预算及文档分析 API。完整命令见 [验收步骤](quickstart.md#8-工具调用优化验证2026-09-18)。
- 对当前 Qwen3.6-35B-A3B 执行三笔合成数据模型调用：首笔锚点参数断言失败；第二笔保留参数，确认模型把 JSON `null` 输出成了字符串 `"null"`。补充该输入兼容及回归后，第三笔使用临时 DOCX、真实 IR 与工具执行链，一次响应返回 `inspect_evidence`、`resolve_source_anchor`，两项均为 `ok`，`Alpha` 锚点精确回读通过；耗时 33.39 秒，输入 1089、输出 1672 tokens。这些调用写入既有调度记录，没有创建文档分析运行，不是完整文档质量或速度对照。
- 重启前重新查询当前数据库，活动文档运行和活动模型请求均为 0，无需暂停或恢复任务。执行 `docker compose restart --timeout 45 backend`，后端启动时间为 **14:38:22 UTC**；前端、网关、数据库与宿主模型服务未重启。
- 后端和网关健康接口均返回 200，10 个本体模块已加载，分析页面返回 200。三个修改的后端文件容器 SHA256 与工作区一致；数据库 revision 与代码 head 均为 `0041_template_engine`。
- 本次重启前后运行状态、fingerprint、版本及 event_head 摘要一致，请求/结果的内容哈希摘要一致；运行、请求、结果、制品 head 数量分别保持 **14、28083、22215、107**。028 默认策略仍为 `predicate-batch-v1`、每批最多 4 个谓词，调用状态版本 3。

## 空目标完整修复（2026-09-18 15:17 UTC）

按用户要求，批次吞吐量优化必须保留原单任务约束。此次修复替代前两轮仅调整空核验 Schema/工具可见性的处理：

- 空目标不发起模型核验；混合批次仅为非空成员组包。复用确定性空结果与原 finalizer，保留无效声明、依赖级联及未决观察的诊断，不把空核验视为事实成立或全文否定。
- 单任务与批次的空目标恢复均使用已有有限重提；历史空核验先处理已保存工具再本地收尾。空成员不增加模型预留、usage 或参与次数，既有付费请求及未知请求约束保留。
- 修复冷继续时旧阶段引用遮蔽已保存重提/补证响应的缺口，确保新候选与工具结果被消费，保留成员版本、证据授权和实际预算。

验证与部署结果：

- 本轮工程验证共 **448 passed、9 skipped**，六个修改 Python 文件 Ruff 通过。九项跳过来自未配置专用 PostgreSQL 测试库；完整命令及场景见 [验收步骤第 9 节](quickstart.md#9-空目标与原单任务约束修复2026-09-18)。没有执行完整文档真实模型质量/速度对照。
- 重启前实时确认活动文档运行和模型请求均为 0，执行 `docker compose restart --timeout 45 backend`。后端启动时间为 **2026-09-18 15:17:30 UTC**；前端、网关、数据库及模型服务未重启。
- 启动完成后，后端和网关 `/api/health` 均为 200，10 个本体模块加载成功；`/analysis?tab=document` 返回 200。容器中两个修复文件及工具运行文件的 SHA256 与工作区一致，数据库 revision 与代码 head 均为 `0041_template_engine`，本次没有数据库结构变更。
- 以本次重启前读取的数据为基线，重启后运行状态、fingerprint、版本、event_head 摘要及请求/结果哈希摘要一致；运行、请求、结果、制品 head 数量分别保持 **7、0、0、59**。028 保持默认开启 `predicate-batch-v1`、`max_members=4`，调用状态版本为 3。

## discovery 空枚举修复部署（2026-09-18 23:48 UTC）

按用户“请重新部署”指令，部署 `claim_protocol.py` 的输出约束修复：无授权属性、关系、标识属性或关系证明类型时，对应数组使用 `maxItems: 0`，避免向模型发送空枚举。合法谓词与身份键的枚举约束保持生效。

- 修复阶段已完成 167 项定向测试、Ruff 和差异检查；本机 llama.cpp 语法转换器复现旧空枚举生成空值规则，修复后生成空数组规则。本次部署未重复模型推理或完整文档评测。
- 重启前活动文档分析运行及模型请求均为 0。执行 `docker compose restart --timeout 45 backend`，容器启动时间为 **2026-09-18 23:48:40 UTC**。沿用现有源码挂载和镜像，前端、网关、数据库及模型服务未重启。
- 容器中合成关系卡生成的 discovery Schema 验证通过：属性与标识数组仅允许空数组，且不存在空枚举；修复文件 SHA256 与工作区一致。有效批次策略仍为 `predicate-batch-v1`、`max_members=4`，调用状态版本为 3、工作状态存储版本为 4。
- 后端及网关 `/api/health`、目标分析页面、模型 `/health` 均返回 200；后端加载 10 个本体模块。数据库 `alembic current` 与代码 `alembic heads` 均为 `0041_template_engine`。
- 重启前后运行状态、fingerprint、版本、event_head、progress、error 摘要及请求/结果内容哈希摘要一致；运行、请求、结果、制品 head 数量分别保持 **8、20、38、64**。目标运行 `0b02d4f1-5068-4076-ad4f-84817cc0d372` 保留 12 次完成、1 次未确认的失败状态，本次没有修改、恢复或重新发起该运行。

## 回答结构、候选修正与原文覆盖修复部署（2026-09-19 02:56 UTC）

按用户“请重新部署”指令，部署批处理回答 Schema 说明、依据具体校验错误修正候选，以及桥接引用和记录组成原文覆盖修复。修复阶段两组定向回归合计 **277 passed**，无跳过，五个受影响 Python 文件 Ruff 通过；场景及命令见 [验收步骤第 10 节](quickstart.md#10-回答结构与原文覆盖修复2026-09-19)。

- 重启前活动文档分析运行及模型请求均为 0。执行 `docker compose restart --timeout 45 backend`，容器启动时间为 **2026-09-19 02:56:28 UTC**。沿用源码挂载及现有镜像，无依赖、配置或数据库结构变更；其他服务未重启。
- 后端和网关 `/api/health` 均返回 200，10 个本体模块已加载；目标 `/analysis?tab=document&documentRun=99033b53-e9ea-425f-b348-132915d4dc0f` 页面返回 200。数据库 revision 与代码 head 均为 `0041_template_engine`。
- `batch_model_adapter.py`、`recognition_batch.py`、`tool_model_adapter.py`、`claim_protocol.py` 的容器 SHA256 均与工作区一致。容器烟测确认实际批次 Schema 已写入提示正文，记录组成和桥接引用说明存在；只读解析原生产计划回答可获得 `endpoint_is_not_an_entity` 精确反馈。
- 有效批次策略保持 `predicate-batch-v1`、`max_members=4`，工作状态存储版本为 4、调用状态版本为 3。
- 重启前后运行状态、fingerprint、版本、event_head、progress、error 摘要及请求/结果内容哈希摘要一致；运行、请求、结果、制品 head 数量分别保持 **10、170、432、74**。活动文档运行及模型请求仍为 0。
- 目标运行保持 `paused / execution_model_call_budget_exhausted`，revision/work_version/request_version/event_head 分别为 **201/42/166/26**。本次没有继续、新建或重新执行分析运行，也没有执行真实模型推理；部署烟测不代表原文抽取质量已重新验证。
