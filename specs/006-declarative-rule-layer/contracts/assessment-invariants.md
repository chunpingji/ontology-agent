# Contract: Assessment Invariants (零回归 + 推断溯源)

**Plan**: [../plan.md](../plan.md) | **Research**: [../research.md](../research.md) R4

本契约**不引入新评估端点**——它固定既有评估路径（`reasoning/engine.py::run_assessment` → `api/reasoning.py`）在规则层声明式化后 MUST 保持的对外行为。这是 FR-012/SC-004 零回归与 FR-002 溯源的可执行验收锚点。

## 形状不变量（硬约束）

`run_assessment(engine, drug_iri, equipment_iris)` 仍返回 `AssessmentResult`，字段与类型逐一不变：

| 字段 | 类型 | 说明 |
|---|---|---|
| `rules_fired` | `list[dict]` | 每项含 `rule_id`/`rule_group`/`description`/`inputs`/`conclusion`/`regulation_ref`（FR-002 溯源链） |
| `scenarios` | `list[dict]` | 每项含 `scenario_iri`/`scenario_name`/`requirements` |
| `requires_dedication` | `bool` | 经 E13 `dedication` 策略聚合 |
| `risk_level` | `str` | 经 E13 `risk_level` 策略聚合 |
| `maco` | `MACOResult \| None` | **不改**（非目标） |
| `recommendations` | `list[str]` | 不改触发逻辑 |

下游 `api/reasoning.py::_build_canonical_results` 及动作编排/报告 MUST 零改动通过。既有 4 个调用方测试（`test_assess_bootstrap`/`test_qa_gate`/`test_qa_reject`/`test_audit_chain_workflow`）MUST 保持绿。

## 零回归不变量（FR-012 / SC-004，澄清决策 3）

对回归输入矩阵（覆盖 R-DC1~4 / R-ED1~6 / R-SC1~8 / R-CP1~4 各触发/不触发分支 + 冲突场景），声明式重算 MUST 与重构前黄金基线**逐用例相等**，差异仅限两类**预先声明**：

- **(a) 新增可推断**：激素 / 青霉素 / 肿瘤（HormonalDrug / PenicillinDrug / AntineoplasticDrug）本特性应新点亮的分类。
- **(b) OWA 改进**：某判定属性缺省时，原引擎隐式判否（如 `sensitization_level` 默认 0）现表现为「未知/不触发负断言」。差异方向 MUST 为「否→未知」，**MUST NOT** 为「真→假」。

任何不在 (a)/(b) 清单内的差异 = 回归 = 失败。

## 溯源不变量（FR-002 / SC-005）

每个被推断分类的 `rules_fired` 项 MUST 携带可追溯到具体法规出处的判定依据（触发判据 key + 输入事实 + `regulation_ref`）。空 `regulation_ref` 的推断结论 = 失败。

## 一致性门禁不变量（FR-014）

发布前 MUST 通过模型一致性/健康度校验：新增 equivalentClass/facet/规则 MUST NOT 破坏 BFO 上层与外部对齐；未核实的 ChEBI/ATC 对齐、不可解析的 property/class 引用 = 阻断性问题 = 拦截发布。
