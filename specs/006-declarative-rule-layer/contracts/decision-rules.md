# Contract: Decision Rules CRUD (E12)

**Plan**: [../plan.md](../plan.md) | **Data model**: [../data-model.md](../data-model.md) §A E12 / §D2

产生式决策规则（设备专用化 R-ED / 场景识别 R-SC / 污染路径 R-CP）的可编辑 T-Box 端点。投影为权威 TTL 命名资源 `slpra:DecisionRule_*`（data-model §B2）。约定同 classification-criteria。

Base path: `/ontology/decision-rules`

| 方法 | 路径 | 角色 | 说明 |
|---|---|---|---|
| GET | `/ontology/decision-rules` | any | 列表（可 `?rule_group=`、`?status=` 过滤） |
| GET | `/ontology/decision-rules/{key}` | any | 单条详情 |
| POST | `/ontology/decision-rules` | senior_analyst | 新建草稿 → 201 |
| PUT | `/ontology/decision-rules/{key}` | senior_analyst | 更新（须带 `version`）→ 200 / 409 |
| POST | `/ontology/decision-rules/{key}/disable` | senior_analyst | 软删除 |

## 请求体（Create / Update）

```json
{
  "rule_key": "R-ED1",
  "rule_group": "equipment_dedication",
  "antecedent": { "op": "class_present", "class": "PenicillinDrug" },
  "consequent": { "requires_dedication": true, "unconditional": true },
  "priority": 100,
  "regulation_ref": "CFDI 2023-03 §4.4: 必须专用独立厂房、设施和设备",
  "version": 1
}
```

## 校验（违反 → 422）

- `rule_group` ∈ {`equipment_dedication`, `scenario_identification`, `contamination_risk`}。
- `consequent` 键集 MUST ⊆ 引擎识别的结论词汇（`add_class`/`requires_dedication`/`requires_independent_hvac`/`requires_inactivation_validation`/`requires_enhanced_documentation`/`requires_tse_assessment`/`requires_aseptic_integrity`/`scenario`/`risk_level`/`unconditional`）——保证 `AssessmentResult` 形状不变。
- `antecedent` 引用的类名 MUST 在本体可解析。
- `rule_key` 唯一。

## 不变量

- **INV-1（零回归, FR-012/SC-004）**：D2 表中每条迁移规则求值结果 MUST 与原 `rules/*.py` 函数等价（OWA「否→未知」例外）。
- **INV-2（保真）**：`slpra:DecisionRule_*` 为全新命名主语，重发不累积，不触碰任何手工 TTL 内容。
- **INV-3（冲突衔接, FR-011）**：多条规则结论的聚合交由 E13 冲突策略（见 conflict-policies.md）。
