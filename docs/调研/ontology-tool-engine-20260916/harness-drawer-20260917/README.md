# Harness Drawer 实施与验收（2026-09-17）

范围：用户确认的 Pencil「应用分析-文档分析」设计，Spec 027 / FR-20 / T28—T31。默认关系图谱、桌面 4/5 Drawer、移动全宽；不展示覆盖与未完成范围。

## 实施

- 默认折叠的 Harness运行信息集中冻结模型与 GLiNER/Mock/实验词表启用状态、实际调用、预算/用量、标识版本，保留原排序预算 CAS 操作。
- 实时输出为 Qwen Responses 原生流的文字增量；Thinking 仅展示端点返回的可读内容。操作包括模型、工具、控制器校验和已提交图谱更新；模型与工具输出不直接写图。
- 上下文默认提示词 Tab，与本体 Schema 卡片 Tab 绑定同一实际 call_id。展示 instructions、任务、授权原文/摘要、回答 JSON Schema 和完整消息；卡片展示冻结窄化类型、谓词、基数、数据类型、数量单位规则、身份键与未解析约束。原始输入仅遮蔽 encrypted_content，原协议继续完整续传。
- 复用当前展示分区 display:harness / display:harness_context。增量最多每 0.5 秒写一次；上下文只在调用切换时覆盖，不随 token 重写。正文/Thinking 各保留末尾 128 Ki 字符，最近 30 项操作的参数/结果各最多 16 Ki 字符，截断明确标注。没有迁移、历史 token 记录或恢复入口。
- 当前展示写入采用既有执行 fence；只读 API 沿用 owner、删除/过期门禁。旧 call_id 返回 CONTEXT_CHANGED / 409。SSE harness 帧没有 durable event id，不改变运行/图谱水位。上下文按需读取，不随每个 SSE 帧推送。

## 本轮实际验证

- 后端定向：`tests/test_extraction/test_native_tool_protocol.py`、`test_tool_engine_adapter.py`、`test_tool_runtime.py`、`test_ontology_guided_boundaries.py`、`tests/test_api/test_document_analysis.py`，**96 passed**。覆盖真实 SDK 消费增量早于最终响应、取消关闭/用量、工具调用与必检观察、当前输入和窄化卡片一致、越权、旧调用冲突、过期执行写拒绝、只读 API/SSE 及运行水位不变。
- 最终状态展示补测：`test_tool_runtime.py` **25 passed**；成功执行但校验结果失败时，操作标为“未通过”，原始工具结果保持不变。此项与上述定向集合有交集。
- 生命周期与执行补充：`test_tool_engine_execution.py`、`test_document_analysis_execution_recovery.py`、边界与当前上下文用例，**63 passed**（与前项有交集，不相加）。未运行专用 PostgreSQL 验收，本轮无迁移。
- 前端：`node --test tests/document-analysis-runs.test.mjs tests/document-ranking.test.mjs tests/document-graph.test.mjs` 三个文件通过；TypeScript `--noEmit --incremental false`、定向 ESLint 与后端 Ruff 通过。
- 浏览器：Chrome 131；`frontend/tests/document-analysis-history-browser.mjs` 在隔离 Next.js 服务中通过 1440/1920/768/390 四屏宽。验证默认图谱、折叠/展开、提示词和卡片 Tab/滚动、数量单位文本、流式上滚停止跟随与回到最新、断线/结束光标、增量不重取图谱、来源定位及取消迟到响应、预算 CAS 与关闭只读。
- [浏览器报告](browser-report.json)使用合成 API 和注入的 SSE 显示事件，只验证前端；后端 SDK/API 测试和真实 Qwen 探针分别验证通道，未把它描述为完整报告端到端验收。
- Spec `check_design.py` 通过：20 需求、13 模块、31 无环任务，保留非法输入反例及原契约校验。

## 真实 Qwen 小范围探针

[探针脚本](qwen-stream-probe.py) / [运行结果](qwen-stream-probe.json)。本项目本地 `Qwen3.6-35B-A3B`，Responses `store=false` / `stream=true`；合成输入，两次调用，首次工具调用与 call_id 结果配对后返回结构化 JSON `{"label":"流式验证"}`。总耗时 **31.377 秒**，实际收到 **190 条 Thinking 增量、8 条正文增量、2 个完成事件**；491 input / 218 output tokens。仅使用临时 SQLite 调用账本，不接触上传原件或生产运行。此项证明流式、可读 Thinking、工具续传及 JSON 协议；不证明全文抽取 F1。

## 界面制品与交付状态

[图谱视图](history-desktop.png)、[提示词 Tab](harness-prompt-tabs.png)、[Schema 卡片 Tab](harness-schema-tabs.png)、[手机 Drawer](history-390.png)、[宽屏图谱](workspace-1920.png)。

代码与规范已完成；本轮未重启/部署运行中的后端。现有进程无 reload，须加载新版后端后，后续模型调用才会产生此观察数据；不会追补过去调用的 Thinking 或流式输出。工程和协议验证不替代全文质量验收，T25/T26 保持未完成。
