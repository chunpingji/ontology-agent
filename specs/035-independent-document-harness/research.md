# 性能与候选剪枝研究（2026-09-30）

本轮按用户指定方案实施，活动规范为 035；分支 038-harness-pruning 仅用于代码隔离，不更换规范归属。

| Decision | Rationale | Alternatives considered |
|---|---|---|
| 调用轻量列 + 当前 metrics 差量 | runtime.progress/call_summaries/business_counts 每次全扫，JSON 提取为已测瓶颈 | 单次 JSON 提取仍全扫；不增费用服务 |
| 最新 GraphBase + 一次 bundle 读取 | projection 当前每次重建并读全账本，动态人工答案独立 | 不缓存人工答案，避免平台答案跨运行失效 |
| 原文线索有界候选，稳定 WorkItem | planning 当前按类型全配对；分批/新字段会重复展开 | 不恢复低排名全组合兜底 |
| 分开语义依赖与端点采信门控 | 类型含义不变时可以释放已有证明 | 不把确认布尔量放入语义指纹 |
| 唯一 active_batch 指向精确付费输入 | 暂停边界存在于回答持久化和业务应用之间 | 不保存历史恢复快照 |
| 规则证明在对齐前分流 | 只做对齐后规则核对仍浪费对齐调用 | 默认空规则，不假造生产表头映射 |
| schema/API/前端同次升级 | 页面原进度组件未实际接入主面板，旧文案把阅读叫核对 | 不保留旧运行消息适配 |

仓储研究确认：update_stage/append_event 各自推进 revision；终态会撤销 lease，必须先发布事件再结束运行。
调用正文专用 hash 校验；通用 get_row 不得误验新轻量调用。创建时初始化缓存，GET 缺缓存报错而不修复。
前端研究确认：复用现有 2.5 秒轮询并保留旧图；引入字符覆盖、任务计数和候选状态，不新增轮询器。

当前官方 Spec Kit 工作流（Context7 /github/spec-kit）确认现有项目同样先 clarify/plan/tasks，再 implement/验证；
本仓库 0.11.3 的脚本与模板为实际入口，不运行会覆写既有规范的模板复制。
无未解决需求澄清；真实质量需独立参考，工程验收不代替质量。
