# 人工解释任务契约

`GET /api/document-analysis/runs/{run_id}/harness-graph` 增加 `interpretation_tasks`。每项包含稳定 `id`、原文 `evidence`、关系与对象标签、两道固定问题的选项、`answer`（未回答为 null）。只针对含斜杠原文、模型未决的多对象关系组。

`POST /api/document-analysis/runs/{run_id}/interpretation-tasks/{task_id}/answer` 请求为 `{meaning, scope, expected_revision}`；`meaning=alternatives|parallel|joint_unspecified|unresolved`，`scope=occurrence|document|platform`。`expected_revision` 来自任务的 `scope_revisions[scope]`。`unresolved` 仅可用于 `occurrence`。成功返回更新后的任务；无效输入为 400、任务已不适用或冲突为 409、无权访问按现有运行访问契约处理。平台范围要求 `senior_analyst`。

更窄范围优先：当前任务答案 > 同所有者同文档答案 > 平台答案。回答不改原关系组的 `state`、`participation` 或 `timing`。
