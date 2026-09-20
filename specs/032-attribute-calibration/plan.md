# 实现计划

流程：specify → clarify（spec 默认边界）→ plan → tasks → implement。

宪章检查：复用现有当前工作分区、协议、解析器和模型预算；无外部依赖、数据库迁移或本体修改。字段观察不改变事实准入。

1. 新增未可信解析观察模型；解析前置并与 trusted normalized value 分开，根因沿 SHACL blocked 结果传播。
2. 失败属性/字段转成精确原文候选，存于原 `work:record_discovery` 行；最新图展示由当前工作状态投影。
3. 收尾阶段基于相关证据签名唤醒原字段/记录任务，复用同 lineage 及剩余额度；不复制冻结原文或新增历史快照。
4. 公共契约和前端独立展示候选与原文，不进入正式 properties。
5. 用确定性及受控模型测试验证解析、证据边界、收尾校准、恢复、展示；不调用真实模型或部署。

## 当前契约

- 冻结 `record_discovery.attribute_calibration = source-observations-v1`，新运行默认启用。
- `ParsedAttributeValue` 仅含解析观察：值、数据类型、数量结构、精度和解析问题；不含事实通过标记，LLM 属性提案/工具参数不能提交此字段。
- `AttributeCalibrationCandidate` 包含稳定来源身份、原值、标签/值锚点、解析观察、合法主体/谓词选项、各检查状态和原因。未知主体允许空选项，不伪造 GraphProperty.subject_ref。
- 权威候选保存在原 `work:record_discovery.attribute_candidates`；最新展示使用现有 display 分区。候选源引用经原文 registry 投影为 opaque selection refs。
- 每原任务至多一次收尾校准；字段仍受最多两次消歧及原 lineage 调用上限约束。排队前比较相关实体/关系/原文签名，开始前刷新线索，活动恢复校验依赖版本；不会因继续重置额度。
- 校准成功仅移除对应原字段候选。技术失败或模型漏报仍保留原候选；非法冻结映射不能转成正常候选。

实施后宪章复核：无新增表、外部依赖、权威本体或真实运行修改；当前工作状态与最新展示缓存职责不变。相关关系的原文仅作为核对上下文，不扩大属性值的事实来源权限。
