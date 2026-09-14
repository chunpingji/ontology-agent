# 属性审核与局部修复 API v1
前缀 `/api/document-analysis/runs/{run_id}`，沿用文档分析身份、错误响应和拥有者隔离。高级分析师与 QA 可审核自有运行及请求其已驳回属性的局部修复；越权来源返回404。

`state_storage_version=4` 使用当前 `base_work_version`、精确候选/原文引用和 `work:candidate_tasks` 保存的原始任务建立修复边界，`base_checkpoint_artifact_id` 为空。当前工作状态保存已处理审核及修复状态，继续时不重放已执行任务。以下历史检查点和回放描述仅适用于旧冻结格式；公开审核与修复请求/响应不增加存储实现字段。

- `GET /reviews`：审核历史、head 与操作能力，只读。
- `POST /reviews`：request_key、expected_run_revision、graph_snapshot_id、candidate_id、candidate_revision、expected_review_revision（初始0）、decision（accepted/rejected）、reason（驳回必填）、reason_code（incorrect_value/incorrect_property/incorrect_subject/incorrect_scope/unsupported/other）。来源范围由服务端解析。返回审核条目和更新运行水位。活动运行409，先暂停。
- `POST /repairs`：request_key、expected_run_revision、review_id；冻结准确驳回及原检查点，复用原运行 dispatcher 排队局部修复；返回 operation 与 run。普通 resume 终态规则不放宽。
- `GET /repairs`：操作状态、审核引用、目标主体/属性/原文、替代候选及未决原因。

同键同内容回原回执，不同内容409；目标/图谱/head过期409，不自动改绑。确认不改变系统证明资格。驳回结论保留历史和原文；修复失败/未决不显示已纠正。
每次修复最多16个局部记录任务、32次模型请求；旧费用不清零，超限明确未完成，不循环自动重开。归属错误不自动分配给另一同名主体。
审核通过可回放操作边界持久化，不能直接改写旧候选、检查点或已提交批次。理由作为待核验意见传入，不增加证据权限。

修复状态为 `queued/running/completed/unresolved/failed/cancelled`。暂停保留待修复队列与费用，页面同时显示运行已暂停；取消/删除在同一控制事务结束活动修复。执行失败或依赖阻塞结束本轮修复，后续普通恢复通过 `repair_stop` 回放，不重开已终结预算。再次修复须针对当前候选重新审核创建新操作。

局部修复结束不自动完成之前暂停的普通识别；原运行已完成且修复/覆盖满足政策时才回到完成状态，其他情况保留暂停及明确未决原因。原检查点、历史候选版本和费用不重写；审核边界即使没有模型任务也持久化，但不增加原文检查计数。
