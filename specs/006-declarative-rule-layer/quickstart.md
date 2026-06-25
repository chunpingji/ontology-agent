# Quickstart: 推理引擎规则层声明式化 — 端到端验证

**Plan**: [plan.md](./plan.md) | **Spec**: [spec.md](./spec.md)

本指南给出可执行的验证场景，逐一对应 spec 的验收场景（US1/US2/US3）与成功标准（SC-001~007）。实现细节见 `tasks.md`；此处只列前置条件、运行命令与预期结果。

## 前置条件

```bash
cd backend
# 1) 迁移：建 E11/E12/E13 表 + 种子 CFDI 默认判据/规则/策略
alembic upgrade head            # 0004_declarative_rule_layer
# 2) 零回归黄金基线即代码内 expected（test_parity_golden_master.py），无需快照插件；
#    全量绿即基线成立：
pytest -q                       # 期望：200 passed
```

外部对齐前置（research.md R3，NON-NEGOTIABLE）：写入肿瘤/激素/青霉素的 ChEBI/ATC 对齐前，MUST 先字节级核实本地名（ChEBI 用 OLS/`purl obo/CHEBI_xxxx`，ATC 用 WHOCC），核实通过方可发布。

## 场景 1 — 分类由属性自动推断且自带溯源（US1 / SC-001,002,005）

```bash
pytest tests/test_reasoning/test_parity_classification.py tests/test_reasoning/test_provenance.py
```

- 输入：仅含属性、未断言任何风险分类的药物 ABox（API 具基因毒性特征）。
- 预期：推断出 `CytotoxicDrug`，`rules_fired` 含 `R-DC1` 且 `regulation_ref="CFDI 2023-03 §3.2"`（US1-AS1）。
- 同理 OEB4→HighActivityDrug（AS2）、致敏 4→HighSensitizingDrug（AS3）、β-内酰胺环→BetaLactamDrug（AS4）。
- 对照：与现行引擎对同输入结论一致（AS5，见场景 4）。

## 场景 2 — 闭合缺口：激素/青霉素/肿瘤可推断（US2 / SC-001）

```bash
pytest tests/test_reasoning/test_inferable_gap.py tests/test_reasoning/test_penicillin_dedication.py
```

- 三个 ABox（仅含可判定类别的底层信号）分别推断为 `HormonalDrug` / `PenicillinDrug` / `AntineoplasticDrug`（AS1-3）。
- `AntineoplasticDrug` 结果可追溯到 `ATC L01 / ChEBI:35610` 对齐来源（AS3）。
- 青霉素被推断后，下游 `R-ED1`「青霉素必须专用化」照常 `requires_dedication=true`（AS4，下游衔接）。

## 场景 3 — 规则即数据，改阈值不改源码（US3 / SC-003,007）

```bash
# 仅经 API 编辑 R-DC3 阈值 3→2，重新发布，重算；不触碰任何 .py
curl -X PUT /api/ontology/classification-criteria/R-DC3 \
  -H "X-Role: senior_analyst" \
  -d '{"pattern":{"op":"datatype_facet","property":"sensitizationLevel","cmp":"gt","value":2},"version":1}'
pytest tests/test_api/test_criteria_edit_changes_inference.py \
       tests/test_reasoning/test_conflict_policy.py \
       tests/test_api/test_rule_audit_trail.py
```

- 预期：致敏级别为 3 的药物此前不点亮、现推断为 `HighSensitizingDrug`；全程未改应用源码（AS1）。
- 冲突聚合：多条规则对「是否专用化」矛盾时，安全优先（`requires_dedication=true`）胜出（AS2，conflict-policies INV-1）。
- 审计：变更可在版本/批次（TTL 导出 + Git SHA）追溯到 actor 与时间（AS3，SC-007）。

## 场景 4 — 零回归 + OWA（SC-004,006）

```bash
pytest tests/test_reasoning/test_parity_golden_master.py tests/test_reasoning/test_owa_unknown.py
pytest tests/test_api/test_assess_bootstrap.py tests/test_api/test_qa_gate.py \
       tests/test_api/test_qa_reject.py tests/test_api/test_audit_chain_workflow.py
```

- 预期：声明式重算与黄金基线逐用例相等，差异仅限预声明的 (a) 新增三类 + (b) OWA「否→未知」（assessment-invariants 零回归不变量）。
- 既有 4 个调用方测试保持绿（形状不变硬约束）。
- 属性缺失场景 100% 表现为「未知」，无负类误断（SC-006）。

## 场景 5 — 保真 round-trip + 发布门禁（FR-013,014）

```bash
pytest tests/test_reasoning/test_ttl_roundtrip.py
pytest tests/test_api/test_ontology_release.py
```

- 预期：含「命名 IRI 外部对齐 + BNode 判据公理 + 未建模注释」的类，连续两次 export→parse→export 三元组集合稳定；外部对齐/注释逐字存活（data-model §B1，research.md R2）。
- 发布前 `export_diff` 给出三元组级 diff；未核实对齐 / 不一致公理被一致性门禁拦截（FR-014）。

## 成功判据对照

| 场景 | 覆盖的 SC / 验收 |
|---|---|
| 1 | SC-001/002/005, US1-AS1~5 |
| 2 | SC-001, US2-AS1~4 |
| 3 | SC-003/007, US3-AS1~3 |
| 4 | SC-004/006, 零回归 + OWA |
| 5 | FR-013/014（宪章 II/IV 门禁） |

## 验证记录（T045）

2026-06-24 全量 `pytest -q` → **200 passed**。五场景按上列命令逐一执行，映射到实测用例，结果：

| 场景 | 实测用例 | 结果 |
|---|---|---|
| 1 | test_parity_classification + test_provenance | 9 passed |
| 2 | test_inferable_gap + test_penicillin_dedication | 5 passed |
| 3 | test_criteria_edit_changes_inference + test_conflict_policy + test_rule_audit_trail | 12 passed |
| 4 | test_parity_golden_master + test_owa_unknown + 4 调用方 | 33 passed |
| 5 | test_ttl_roundtrip + test_ontology_release | 9 passed |
| E2E | test_api/test_quickstart_e2e（US1 主流程 + 角色门禁） | 2 passed |
