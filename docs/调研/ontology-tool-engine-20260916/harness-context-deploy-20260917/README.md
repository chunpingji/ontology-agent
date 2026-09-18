# 上下文与 Schema 空白：部署核验（2026-09-17）

用户反馈前端上下文没有内容、本体 Schema 卡片不显示。本轮核验并加载上一轮已通过工程测试的工作线程接线修复，没有新建运行或改写历史提示词。

## 原因与处理

排查时后端仍为 11:19:36 UTC 启动的进程，早于 12:57:56 的 RecognitionCall 修复。当前运行 `cec43de7-977d-4de6-a163-32805716b9fd` 已记账 33 次模型调用，但 `display:harness.call=null`、没有 `display:harness_context`，所以前端无法请求实际提示词，也无法绘制卡片。

通过现有 control 契约暂停当前运行，等到 `paused`、`work_version=15`、已记账模型调用 34，再重启 backend。新进程启动于 **13:08:57 UTC**；健康接口 200，数据库 revision 与 Alembic head 均为 `0041_template_engine`。随后通过既有 resume 契约继续同一运行，保留图谱、已记账结果和工作进度。原本 failed/cancelled 的其他运行没有恢复。

## 真实运行证据

继续后的模型请求已写入一对匹配当前 call_id 的展示缓存，且通过 HarnessResponse/HarnessContextResponse 校验：

- 指令：6,874 字符；输入消息：4 条。
- 窄化 Schema：1 个目标谓词，13 个允许类型。
- 首次采样：`running`、`work_version=15`、`sequence=1`，模型尚未返回文本。
- 后续采样：运行仍为 `running`，`work_version=16`、`sequence=3166`；当前调用为 `completed`，Thinking 2,320 字符，正文 0 字符，上下文仍匹配当前调用。无正文的工具调用不补造文本。

这里只保留计数与状态；实际输入在受限临时文件中用于本地页面核验，不复制报告正文到此目录。

## 页面核验

[浏览器报告](browser-report.json)：使用真实后端返回，在与当前源码逐字一致的隔离 Next.js 前端中打开 `/analysis?tab=document` Drawer。验证外层与上下文默认折叠、展开后只读取一次上下文、提示词非空、Schema 谓词标签可见、结构化/原始 JSON 切换、无横向溢出、无运行时错误。浏览器检查没有业务写入。

页面周边 API 和身份为合成数据，Harness 与上下文为真实运行采样；不宣称已经完成线上认证浏览器端到端测试。首次尝试现有开发代理 8081 时测试浏览器未挂载页面、HMR 握手失败，因此改用上述隔离环境，未据此更改应用认证或代理配置。后端加载和实际运行数据已在线核验。

现有前端订阅/轮询可读取新数据；用户可刷新页面，再展开 Harness运行信息 → 上下文，在“提示词”和“本体 Schema 卡片”两页查看。过去没有采集的调用不追补。
