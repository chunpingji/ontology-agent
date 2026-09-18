# Harness运行信息收纳与接线修复（2026-09-17）

本轮按用户最新要求，将 Thinking、操作、上下文收纳到 Drawer 的 Harness运行信息中；外层与三个内层区域默认折叠，外部保留 LLM 实时正文。沿用 Spec 027 / FR-20 / T28—T31。

## 界面与数据来源

- `DocumentHarnessInformation` 复用 `useDocumentHarness` 的同一份数据，与外部正文共同消费只读 `/harness` 快照和既有 SSE 的 `harness` 帧。没有新增存储或订阅通道。
- 提示词与窄化本体 Schema 卡片仍使用两个 Tab，绑定当前 `call_id`。只有外层 Harness运行信息和内层上下文都展开才请求 `/harness/context`；收起外层会取消读取，随后切换调用不再读取上下文，外部正文仍更新。
- 切换运行重置折叠状态；嵌套 details 的 toggle 不改变父级状态。保留模型/工具配置、预算控制、操作参数/结果和标识版本。
- [浏览器报告](browser-report.json)、[提示词](harness-prompt-tabs.png)、[Schema 卡片](harness-schema-tabs.png)。截图与事件为合成测试数据。

## 实际接线断点

在线只读检查发现，当前运行 `cec43de7-977d-4de6-a163-32805716b9fd` 冻结协议为 `ontology-tool-extraction-v1` / `responses`，模型为 `Qwen3.6-35B-A3B`；已记账 27 次模型调用时，Harness 缓存 `sequence=10`、`call=null`，正文和 Thinking 均为空，操作仅有图谱更新。后续检查仍在运行，`sequence=11`、`call=null`。后端从 11:19:36 UTC 启动，已提供两个 Harness API，因此不能归因于接口未加载或旧协议。

原因是 `RecognitionCall` 为隔离 owner Session，在新线程建立空 Context，仅传递白名单字段，遗漏 `on_harness_event`。直接在执行线程发布的 graph_update 正常；实际模型线程既不发布 Harness 观察，也没有触发基于回调启用的 `stream=true`。

修复复用 `RecognitionCall.requests`：工作线程复制事件载荷并入队，执行所有者消费事件并调用原 HarnessObserver，保持数据库会话的线程边界。token 不增加持久化屏障；close 等待取消完成后处理最后的结束/中断事件。未改变模型预算、声明核验或图谱写入规则。

上一轮直接 adapter/SDK 探针和独立 API 用例没有经过协调器的真实线程边界，因此未发现该问题。本轮新增经过 `OntologyGuidedExecutor → RecognitionCall → Responses SDK` 的回归；修复前两个用例均失败，修复后通过，验证流尚未结束时即可收到 Thinking、正文与实际请求，且最后操作在发布图谱前到达、载荷不受工作线程后续修改影响。

## 本轮实际验证

- 后端：`test_recognition_coordinator.py`、`test_native_tool_protocol.py`、`test_tool_engine_adapter.py`，**56 passed**。覆盖跨线程流式观察、正常/异常退出、会话隔离、预扣失败及取消。
- 补充受影响路径：`test_semantic_ranking_execution.py`、`test_model_wait_durability.py`、`tests/test_api/test_document_analysis.py -k 'not expert'`，**23 passed**。包含当前缓存 API、调用绑定、owner/fence、只读 SSE 和现有调度屏障。
- 前端 TypeScript `--noEmit --incremental false`、两个改动组件 ESLint、3 个 Node 测试文件（document-analysis-runs / document-ranking / document-graph）通过；后端两个改动文件 Ruff `--no-cache` 通过。
- 浏览器：隔离 Next.js + Chrome 131，1440/1920/768/390 四屏宽通过；默认折叠、折叠停止上下文读取、SSE 更新、提示词/卡片 Tab 与滚动、原图谱/来源定位/预算控制验收保留。浏览器使用合成 API，不是线上登录或真实后端端到端测试。
- `check_design.py` 通过，20 需求、13 模块、31 无环任务；定向 `git diff --check` 通过。

本轮没有发起真实 Qwen 调用或全文质量评测，也未运行专用 PostgreSQL 锁/并发验收；不沿用上一轮探针结果冒充本轮实测。

## 生效状态

前端现有开发容器挂载工作区源码。后端无自动 reload；本轮未中断仍在运行的分析，也未重启后端。因此线程接线修复需后端重启后才生效，届时后续调用可产生模型展示数据；过去调用的 Thinking 不会追补。当前运行的空模型缓存不代表修复后的在线通道已经验收。
