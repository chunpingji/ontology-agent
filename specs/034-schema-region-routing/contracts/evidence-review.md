# 原文采信展示契约

`RecordDiscoveryPolicy.graph_phase` 增加 `evidence_review`；主文档新运行默认启用。
`DocumentTargetGraphResponse.phase` 与之同步。

每个公开 GraphAssertion 增加：

- `decision_status`: `supported | unsupported | undetermined | not_checked`。分别展示采信、
  未采信、待定、待核验；不覆盖既有人审 `independent_review`。
- `validation_diagnostics`: 数组，每项含 `check`（`schema | metric | shacl | relation_graph`）、
  `status`（`passed | failed | incomplete | not_checked`）、`reason_codes: string[]`、`message: string`。
- 具体原因沿用 `reason_code` 和 `reason`，原文沿用角色 source_selection_refs，引用身份/权限不变。

GraphProperty 增加 `normalization_available: boolean` 与公开 `raw_unit: string | null`。
`raw_value/raw_unit` 表示原文，`normalized_value/unit` 表示规范化结果，失败时规范值为 null，
不得把原文字符串回填成规范化结果或把原文数值配上规范化单位。

SHACL/metric 诊断不改变源级采信结论。采信需要真实源/语义依据，不能由诊断通过替代。
未采信默认仅在 UI 隐藏，原始目标 assertion_refs 与分母保持不变；读操作不触发任务。

技术待核验的属性/关系使用同一候选身份的前置修订；冻结核验目标预先指向最终修订，
发现展示不带 proof，核验完成按最终修订发布。实体/共指身份版本保持原契约。
`evidence_review_pending` 表示已有发现、核验尚未完成；运行暂停并保留当前入口，
继续时不重发已确认发现。未知模型调用不因此被当作已完成。

联合候选调整：区域类卡的 `relationships` 与 `properties` 均来自冻结本体；沿用
DiscoveryEnvelope 的三个候选数组，关系可引用同轮实体。VerificationInput 的
`attribute_candidates` 保留同轮合法原文属性候选，拆分核验时仍可读取，属性采信或
SHACL 不是关系核验的前提。已登记端点的原发现属性通过 `endpoint_attributes` 提供，
只展示当前授权原文中可定位的项，不扩展事实权限或另存属性快照。

无额外限定/未见反证的 supported 结论允许 support_refs 为空，服务端记录
`scope_checked_no_qualifiers` / `scope_checked_no_counterevidence` 原因码、判断说明和
精确 searched_context_refs。此例外只适用于 evidence_review 的关系/属性，实际限定、
否定及指定反证不能使用；恢复后仍核对检查范围。引用对象本身仍禁止空文本。

多段谓词证据由同一 predicate 语义结论联合核对两端及谓词来源，不要求单段包含完整
主谓宾。未采信候选沿用当前状态与理由，充分支持才有 proof 并显示为实线。
