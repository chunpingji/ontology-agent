# Contract: Conflict-Resolution Policies CRUD (E13)

**Plan**: [../plan.md](../plan.md) | **Data model**: [../data-model.md](../data-model.md) §A E13 / §D3

冲突消解策略（安全优先覆盖、风险等级取最严）的可编辑 T-Box 端点。外化 `resolve_dedication_conflict`/`resolve_risk_level`。投影为 `slpra:ConflictPolicy_*`。

Base path: `/ontology/conflict-policies`

| 方法 | 路径 | 角色 | 说明 |
|---|---|---|---|
| GET | `/ontology/conflict-policies` | any | 列表（两条：`dedication`、`risk_level`） |
| GET | `/ontology/conflict-policies/{dimension}` | any | 单条详情 |
| PUT | `/ontology/conflict-policies/{dimension}` | senior_analyst | 更新（须带 `version`）→ 200 / 409 |

> 仅 GET/PUT：维度集合固定（`dedication`/`risk_level`），由迁移种子建立，不支持任意新增/删除（YAGNI，宪章 V）。

## 请求体（Update）

```json
{
  "dimension": "risk_level",
  "strategy": "max_severity",
  "priority_lattice": { "HighRisk": 3, "MediumRisk": 2, "LowRisk": 1 },
  "override_direction": null,
  "regulation_ref": "CFDI 2023-03 §13.4",
  "version": 1
}
```

## 校验（违反 → 422）

- `dimension` ∈ {`dedication`, `risk_level`}。
- `strategy=max_severity` MUST 提供 `priority_lattice`（非空映射）。
- `strategy=safety_override` MUST 提供 `override_direction` ∈ {`restrictive_wins`, `permissive_wins`}。

## 不变量

- **INV-1（安全优先, FR-011 / US3-AS2）**：`dedication` 维度默认 `safety_override`+`restrictive_wins`——任一规则 `requires_dedication=true` 则最终为 true。
- **INV-2（取最严）**：`risk_level` 维度按 `priority_lattice` 取最高，空集回退 `LowRisk`（与现 `resolve_risk_level` 一致）。
- **INV-3（可审阅, FR-009）**：优先格/覆盖方向为声明式数据，变更经版本/批次留痕，不改源码。
