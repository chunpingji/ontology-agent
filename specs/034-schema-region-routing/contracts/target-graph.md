# 本体目标图展示契约

`GET /api/document-analysis/runs/{recognition_run_id}/target-graph`

使用现有身份与运行归属校验；只读取运行冻结的本体快照和最新公共图投影，不启动模型、
不增加执行任务、不将虚拟节点写入事实图。错误、过期和删除语义沿用同组运行接口。

响应包含既有运行水位（`contract_version`、`recognition_run_id`、`run_revision`、
`event_head`、`artifact_revision`）、`availability`、`ontology_snapshot_id`、`root`、
`graph`、`targets` 和 `summary`。`graph` 复用 `GraphArtifactResponse`，保留可定位的证据。

`root` 包含 `entity_id`、`revision`、`class_iri`、`label`。各目标包含：

- `target_id`、`subject_ref`、`subject_label`、`subject_class_iri`、`scope_id`；
- `kind`（`relationship` / `property`）、`predicate_iri`、`predicate_label`；
- `range_types`（`iri`、`label`）、`datatype_iris`、`multiplicity`
  （`single` / `multiple` / `unspecified`）；
- `state`（`pending` / `partial` / `supported` / `not_found` / `undetermined` /
  `rejected` / `negated`）、`supported_count`、`negated_count`、`completed`、`reason`；
- `assertion_refs` 与通过证据资格核验的 `supported_assertion_refs`；
- `coverage`：`records_planned`、`records_examined`、`records_incomplete`、
  `records_unattempted`、`pending_frontiers`、`scope_checked`。

`summary.relationships` 和 `summary.properties` 分别包含 `total`、`supported`、
`completed`、`percent`；`percent = completed / total * 100`，空分母为 `null`。
`supported` 只计至少一条合格肯定断言的目标。另有 `pending_expansion_count` 和 `notes`。

根实体及已由合格根路径连接的实体展开关系目标；已核验孤立实体可以展示属性目标。
只用当前实体版本、未失效断言及其依赖证明建立实线，否定和条件保留独立标识。
空 scope 规范为 `null`，有范围的目标不能与无范围目标混算。

多值或未声明基数目标，在文档成员总数未知时保留部分识别；仅单值/精确基数可证明且
对应范围核对完成时计入完整识别。尚未展开的目标另行提示，比例随合法目标展开而变化，
不能宣称整份文档已经识别完全。

前端在 `/analysis?tab=graph-analysis` 提供入口。读取、刷新、切换 tab 均不触发模型；
新建、暂停与继续使用已有显式运行操作。实线可点击原文证据；虚线表示待查目标，
不等于文档中确有该关系，不得据此自动重复发现。
