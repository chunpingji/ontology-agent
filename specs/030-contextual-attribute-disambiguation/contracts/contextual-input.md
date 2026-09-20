# 030 内部与展示契约

- 新 IR 单元/段落的 `navigation_role` 为 `null | toc_heading | toc_entry`；null 不改变旧哈希语义。目录角色不能通过补证重新变为事实权限。
- 新记录任务可携带 `source_record_ids`（包含主 record_id 的完整原记录集合）、`reading_section_ids`、`purpose=property_disambiguation` 和字段标识。未携带时沿用 029 单记录发现与序列化。阅读节内其他记录只可作为绑定上下文，不能据此增加事实来源。
- 阅读组的原文单元逐条保留原 record/section/table 身份；标题/祖先仅为绑定，不作实体/值的新事实来源。
- 字段标识取自文档与标签/值物理锚点；同值不同位置为不同字段。待办保存当前状态、原文引用、候选对、相关输入签名、尝试额度和最新结果引用；调用正文沿用独立制品。
- 属性消歧允许返回正常 PropertyProposal 或 ambiguous/unbound/missing/unknown 观察；其 properties 必须使用授权主体与属性组合，entities/relations/external_links 不允许借消歧新增。
- 同一字段保持固定 lineage；候选变化不领取新额度。首轮实际消歧后，只有授权相关输入变化才允许一次自动再试，最多两轮；零调用前置缺口不扣实际尝试数。无合法候选、本体缺口和候选超出容量不发 LLM 请求，并保留可见原因。技术失败若已付费也计入本轮尝试。
- 重开反馈的哈希包含字段输入签名；候选主体修订及相关别名证据变化均可影响签名。属性任务重开的主体集合必须恰好等于已发布反馈集合，允许替换旧版本或移除已无效候选，不能另行追加。有效别名仅用于候选选择和原文上下文，不能替代属性独立核验。
- 新 Harness 调用可标识 `property_disambiguation`；最新字段结果显示原文字段、值、候选、原因和结果。普通识别/记录发现旧响应继续可读。
- `attribute_disambiguations[]` 投影现有 `work:record_discovery` 的当前行，包含 task_id/field_id/record_id、label/value、candidate_count、attribute_status、work_status、reason_code、disambiguation_attempts。未准备的候选数为 null；属性状态为 pending/resolved/unresolved，工作状态为 pending/active/examined/incomplete。GET 不创建模型任务；暂停时展示已暂停而不改写 active 工作行。
- 新冻结 contextual 策略同时控制阅读组和字段路由；未配置的旧运行保持原逻辑。模型输入、结果哈希和独立核验仍按原契约保存。
