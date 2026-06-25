---
description: "Task list for 推理引擎规则层声明式化（§8.0 升级路径）"
---

# Tasks: 推理引擎规则层声明式化（§8.0 升级路径）

**Input**: Design documents from `/specs/006-declarative-rule-layer/`
**Prerequisites**: [plan.md](./plan.md), [spec.md](./spec.md), [research.md](./research.md), [data-model.md](./data-model.md), [contracts/](./contracts/)

**Tests**: 包含（宪章 IV 测试纪律与契约优先 + FR-012 零回归 + FR-014 一致性门禁明确要求）。

**Organization**: 按用户故事分组，每个故事可独立实现与验证。

## Format: `[ID] [P?] [Story] Description`

- **[P]**: 可并行（不同文件、无未完成依赖）
- **[Story]**: US1/US2/US3
- 描述含精确文件路径

## Path Conventions

Web 结构（plan.md）：后端 `backend/app/...` + `backend/tests/...`；本体 `ontology/slpra/...`；前端 `frontend/src/...`。

---

## Phase 1: Setup（共享基础设施）

**Purpose**: 模块骨架与测试脚手架（项目栈已存在，无需初始化依赖）。

- [X] T001 [P] 创建推理执行器骨架文件 `backend/app/services/reasoning/interpreter.py`、`backend/app/services/reasoning/policy.py`（空模块 + docstring），并建 `backend/tests/test_reasoning/`、`backend/tests/test_api/` 下本特性测试包占位
- [X] T002 [P] 在 `backend/app/services/ttl_merge.py` 模块头部新增 `DCT = Namespace("http://purl.org/dc/terms/")` 与 `slpra:DecisionRule/ConflictPolicy` 词项常量及 `g.bind("dct", DCT)`（仅常量与绑定，无逻辑）

---

## Phase 2: Foundational（阻塞性前置 — 必须先完成）

**⚠️ CRITICAL**: 本阶段未完成前，任何用户故事不得开工。

- [X] T003 **先于一切重构**：用现行引擎对回归输入矩阵（R-DC1~4 / R-ED1~6 / R-SC1~8 / R-CP1~4 各触发/不触发分支 + 冲突场景）跑 `run_assessment`，快照 `{rules_fired[].rule_id, requires_dedication, risk_level, scenarios[].scenario_iri}` 为黄金基线 fixture，写入 `backend/tests/test_reasoning/fixtures/golden_master.json`（research.md R4，FR-012）
- [X] T004 [P] 在 `backend/app/models/ontology_meta.py` 新增 E11 `OntologyClassificationCriterion`（`VersionMixin`+`TimestampMixin`）、E12 `OntologyDecisionRule`（`NamedEntityMixin`）、E13 `OntologyConflictPolicy`（`NamedEntityMixin`），字段依 data-model.md §A
- [X] T005 新增 Alembic 迁移 `backend/alembic/versions/0004_declarative_rule_layer.py`：建 `ontology_classification_criterion`/`ontology_decision_rule`/`ontology_conflict_policy` 三表（依赖 T004）
- [X] T006 [P] 在 `backend/app/services/reasoning/interpreter.py` 实现「受限模式词汇」AST schema 与校验器（`op` 白名单见 data-model.md §C），非法 `op`/缺字段 → 校验错误（FR-004/FR-014）
- [X] T007 实现解释器三态求值核心 `{TRUE, FALSE, UNKNOWN}` 与 OWA 语义（属性缺失 → UNKNOWN，禁坍缩为 FALSE）于 `backend/app/services/reasoning/interpreter.py`（依赖 T006，FR-010/SC-006）
- [X] T008 改造 `backend/app/services/ttl_merge.py::surgical_merge` 为**对象形态感知**：受管主语仅剥离 `(s, owl:equivalentClass, BNode)` 并递归回收该 BNode 子图，逐字保留 `(s, owl:equivalentClass, namedIRI)`；将 `slpra:ruleGroup/antecedent/consequent/priority/dimension/strategy/overrideDirection/priorityLattice` 加入仅作用于 `DecisionRule_*/ConflictPolicy_*` 主语的受管谓词集（data-model.md §B，research.md R2）
- [X] T009 round-trip 测试 `backend/tests/test_reasoning/test_ttl_roundtrip.py`：含「命名 IRI 外部对齐 + BNode 判据公理 + 未建模注释」的类连续两次 export→parse→export，断言三元组集合稳定、对齐/注释逐字存活、BNode 确定性（依赖 T008，宪章 II NON-NEGOTIABLE）

**Checkpoint**: 解释器核心 + 元数据表 + 保真合并就绪 — 用户故事可开工。

---

## Phase 3: User Story 1 - 风险分类由底层属性自动推断且自带溯源 (P1) 🎯 MVP

**Goal**: R-DC1~4 上抬为 `owl:equivalentClass`+facet 充要判据，由解释器推断分类并在 `rules_fired` 附判据/法规溯源；`AssessmentResult` 形状不变、零回归。

**Independent Test**: 构造仅含属性、未断言分类的药物 ABox（API 具基因毒性特征），运行评估 → 推断 `CytotoxicDrug` 且 `regulation_ref="CFDI 2023-03 §3.2"`；OEB4→HighActivityDrug、致敏4→HighSensitizingDrug、β-内酰胺环→BetaLactamDrug；与现行引擎结论一致。

### Tests for User Story 1 ⚠️（先写、先红）

- [X] T010 [P] [US1] 自动分类 parity 测试 `backend/tests/test_reasoning/test_parity_classification.py`：R-DC1~4 四条对触发/不触发分支断言推断分类与黄金基线等价（US1-AS1~5）
- [X] T011 [P] [US1] 溯源测试 `backend/tests/test_reasoning/test_provenance.py`：每个被推断分类的 `rules_fired` 项含非空 `rule_id`+`regulation_ref`+`inputs`（FR-002/SC-005）
- [X] T012 [P] [US1] OWA 测试 `backend/tests/test_reasoning/test_owa_unknown.py`：致敏级别缺省时 R-DC3 表现为 UNKNOWN（不点亮、不断言负类），方向为「否→未知」（FR-010/SC-006）

### Implementation for User Story 1

- [X] T013 [P] [US1] 经 E3 `OntologyDataProperty` 种子新增 `slpra-drug:hasBetaLactamRing`（domain=API，range=xsd:boolean），由合并写入 `ontology/slpra/slpra-drug.ttl`（非手改），落在 T005 迁移的 seed 或独立 seed 脚本（FR-005）
- [X] T014 [P] [US1] 种子 E11 判据 R-DC1~4（`logic_role=defined`，pattern 依 data-model.md §D1：some_values_from/class_membership/datatype_facet/boolean_has_value）于迁移 seed
- [X] T015 [US1] 在 `backend/app/services/reasoning/interpreter.py` 实现 op 处理器 `some_values_from`/`class_membership`/`datatype_facet`/`boolean_has_value`（依赖 T007）
- [X] T016 [US1] 扩展 `backend/app/services/ttl_merge.py::build_managed_graph`：为 E11 `defined` 判据发出 `target_class owl:equivalentClass _:c<id>` 确定性 BNode 类表达式 + OWL2 datatype facet（依赖 T008、T014，data-model.md §B1）
- [X] T017 [US1] 改造 `backend/app/services/reasoning/engine.py`：分类阶段改由解释器消费 E11 判据（替换 `drug_classification.ALL_RULES` 循环），仍 append `drug_classes`、仍写 `rules_fired` 形状不变（依赖 T015，plan「AssessmentResult 形状不变」硬约束）
- [X] T018 [US1] 在发布一致性/健康度校验（`validate`）中纳入新增分类 equivalentClass/facet：不可解析 property/filler → 阻断发布（FR-014）

**Checkpoint**: US1 可独立运行 — 分类声明式推断 + 溯源 + 零回归；R-ED/R-SC/R-CP 仍走原硬编码，输出不变。

---

## Phase 4: User Story 2 - 闭合「仅可断言/不可表达」缺口（激素/青霉素/肿瘤） (P2)

**Goal**: 激素/青霉素由「仅可断言」升级为「可推断」，新增 `AntineoplasticDrug` 类并经 ChEBI/ATC 外部对齐物化可推断；与下游决策规则正确衔接。

**Independent Test**: 三个仅含底层信号的 ABox 分别推断为 HormonalDrug/PenicillinDrug/AntineoplasticDrug；肿瘤可溯源 ATC L01/ChEBI；青霉素被推断后 R-ED1「必须专用化」照常成立。

### Tests for User Story 2 ⚠️

- [X] T019 [P] [US2] 缺口闭合测试 `backend/tests/test_reasoning/test_inferable_gap.py`：激素/青霉素/肿瘤三 ABox 各被推断且肿瘤带 ATC L01/ChEBI 对齐溯源（US2-AS1~3，SC-001）
- [X] T020 [P] [US2] 下游衔接测试 `backend/tests/test_reasoning/test_penicillin_dedication.py`：青霉素被推断 → `requires_dedication=true`（US2-AS4，FR-011）

### Implementation for User Story 2

- [X] T021 [US2] **NON-NEGOTIABLE 字节级核实**：对 ChEBI/ATC 权威源核实肿瘤(`CHEBI:35610`/ATC `L01`)、激素(`CHEBI:24621`/ATC `G03·H02`)、青霉素(`CHEBI:17334`/ATC `J01C`)本地名（ChEBI 用 OLS/`purl obo/CHEBI_xxxx`，ATC 用 WHOCC），核实结果记入 `specs/006-declarative-rule-layer/research.md` R3 表并标 `alignment_verified`（research.md R3，记忆 idmp-o-rebased / external-alignment-must-use-nonmanaged-predicates）
- [X] T022 [P] [US2] 经 E1 `OntologyClass` 种子新增 `slpra-drug:AntineoplasticDrug`（subClassOf DrugProduct），由合并写入 `ontology/slpra/slpra-drug.ttl`（FR-007，data-model.md §E2）
- [X] T023 [P] [US2] 经既有外部对齐机制（E6 映射 / `ontology/slpra/slpra-integration.ttl`）写入三类的**命名 IRI** 对齐三元组（非受管，穿过合并保留）（依赖 T021）
- [X] T024 [US2] 在 `backend/app/services/reasoning/interpreter.py` 实现 op 处理器 `external_alignment`（API 类经对齐属外部类别）（依赖 T015）
- [X] T025 [US2] 种子 E11 判据 `HormonalDrug-suff`/`PenicillinDrug-suff`/`AntineoplasticDrug-suff`（`external_alignment` pattern）（依赖 T021、T014）
- [X] T026 [US2] 一致性门禁：未 `alignment_verified` 的 ChEBI/ATC 对齐 → 阻断发布（依赖 T018，FR-014）

**Checkpoint**: 可推断风险类别从 4 增至 7；US1+US2 各自独立可测。

---

## Phase 5: User Story 3 - 规则知识作为可版本化数据被维护 (P3)

**Goal**: R-ED/R-SC/R-CP 决策规则与冲突策略外化为 E12/E13 声明式数据并经 UI 可编；改阈值/补规则/改策略无需改源码、可审计落批次。

**Independent Test**: 经 API 把 R-DC3 阈值 3→2，不触碰 `.py`，重发布重算行为按预期变；冲突时安全优先胜出；变更可追溯到 actor/批次/时间。

### Tests for User Story 3 ⚠️

- [X] T027 [P] [US3] 规则即数据测试 `backend/tests/test_api/test_criteria_edit_changes_inference.py`：PUT 改 R-DC3 阈值 3→2 后重算，致敏3 的药物由不点亮变为推断 HighSensitizingDrug，全程未改源码（US3-AS1，SC-003）
- [X] T028 [P] [US3] 冲突策略测试 `backend/tests/test_reasoning/test_conflict_policy.py`：多条矛盾结论经 E13 `safety_override` 聚合 → `requires_dedication=true` 胜（US3-AS2，FR-011）
- [X] T029 [P] [US3] 审计/版本测试 `backend/tests/test_api/test_rule_audit_trail.py`：规则变更可追溯 actor+批次(TTL导出+Git SHA)+时间（US3-AS3，SC-007）
- [X] T030 [P] [US3] 契约测试 `backend/tests/test_api/test_rule_artifacts_contract.py`：覆盖 classification-criteria / decision-rules / conflict-policies 的 CRUD + 乐观并发 409 + 角色门禁 403（contracts/*.md）

### Implementation for User Story 3

- [X] T031 [US3] 种子 E12 决策规则 R-ED1~6/R-SC1~8/R-CP1~4 + E13 策略 `dedication`/`risk_level`（迁移 seed，data-model.md §D2/§D3）
- [X] T032 [US3] 扩展 `backend/app/services/ttl_merge.py::build_managed_graph`：发出 `slpra:DecisionRule_*`（ruleGroup/antecedent/consequent/priority/dct:source）与 `slpra:ConflictPolicy_*`（dimension/strategy/overrideDirection/priorityLattice）命名资源（依赖 T008，data-model.md §B2/§B3）
- [X] T033 [US3] 在 `backend/app/services/reasoning/policy.py` 从 E13 泛化 `resolve_dedication_conflict`/`resolve_risk_level`（safety_override / max_severity + priority_lattice）
- [X] T034 [US3] 改造 `backend/app/services/reasoning/engine.py`：R-ED/R-SC/R-CP 改由解释器消费 E12 + 经 `policy.py` 聚合（替换 `equipment_dedication`/`scenario_identification`/`contamination_risk` 的 `ALL_RULES` 与 `conflict_resolver`），形状/parity 不变（依赖 T017、T033、T024）
- [X] T035 [P] [US3] 在 `backend/app/schemas/ontology.py` 新增 E11/E12/E13 的 Pydantic Create/Update/Detail schema
- [X] T036 [US3] 在 `backend/app/services/ontology_meta_store.py` 新增判据/规则/策略 CRUD + 乐观并发(version 409) + 双存储投影（依赖 T035）
- [X] T037 [US3] 在 `backend/app/api/ontology.py` 新增 `classification-criteria` CRUD 路由（写经 `require_role(senior_analyst)`）（依赖 T036，contracts/classification-criteria.md）
- [X] T038 [US3] 在 `backend/app/api/ontology.py` 新增 `decision-rules` CRUD 路由（依赖 T036，contracts/decision-rules.md）
- [X] T039 [US3] 在 `backend/app/api/ontology.py` 新增 `conflict-policies` GET/PUT 路由（依赖 T036，contracts/conflict-policies.md）
- [X] T040 [US3] 将 E11/E12/E13 变更纳入 `OntologyChangeLog` 批次与 `OntologyRelease`(SHA) 归档（依赖 T036，FR-015）
- [X] T041 [P] [US3] 在 `frontend/src/lib/api.ts` 新增三类制品 CRUD 客户端（沿用既有 fetchAPI + jsonBody + 乐观并发约定，与邻近 T-Box 客户端一致；含 RulePattern 受限 AST 类型 + describePattern）
- [X] T042 [P] [US3] 在 `frontend/src/app/(dashboard)/ontology/rules/` 新增「声明式规则」最小编辑入口（列表/只读详情/受限模式受控表单；不做通用类表达式编辑器；从 T-Box 工作台表头链接进入）（research.md R5）

**Checkpoint**: 三类制品经 UI 可编、可审计；R-ED/R-SC/R-CP 全声明式化。

---

## Phase 6: Polish & Cross-Cutting

- [X] T043 [P] 将 `backend/app/services/reasoning/rules/*.py` + `conflict_resolver.py` 退化为弃用垫片（docstring 指向 `defaults.py`/`seed_declarative.py`/E11-E13；`ALL_RULES=[]`，`conflict_resolver` 委托 `policy.py` 保字节级一致）；`engine.py` 早已改吃 `defaults`（T034），`/api/reasoning/rules` 改从声明式 `defaults` 派生（含法规依据），移除 4 个 legacy 模块导入
- [X] T044 全量黄金基线 parity 运行（`pytest -q` 200 passed）+ 既有 4 调用方测试（`test_assess_bootstrap`/`test_qa_gate`/`test_qa_reject`/`test_audit_chain_workflow`）保持绿；逐用例差异仅限预声明 (a)新增三类 +(b)OWA 否→未知（FR-012/SC-004，assessment-invariants.md）
- [X] T045 执行 `specs/006-declarative-rule-layer/quickstart.md` 五场景端到端验证（命令块校正到实测用例路径；2026-06-24 全量 200 passed，S1=9/S2=5/S3=12/S4=33/S5=9/E2E=2 全绿，见 quickstart.md「验证记录」）
- [X] T046 [P] 刷新 agent context（`update-agent-context.sh` 将 CLAUDE.md 托管段由 005→006 plan）与本特性文档注记（plan.md「实现状态」+ quickstart.md「验证记录」）

---

## Dependencies & Execution Order

### Phase 依赖
- Setup(P1) → Foundational(P2) → 用户故事(P3~P5) → Polish(P6)。
- Foundational **阻塞**所有用户故事。

### 用户故事依赖
- **US1(P1)**：仅依赖 Foundational —— MVP，独立可测。
- **US2(P2)**：依赖 Foundational + US1 的解释器/判据机制（T015、T014）；其余独立。
- **US3(P3)**：依赖 Foundational + US1 引擎接线（T017，同改 `engine.py` 故 T034 串行于 T017）+ US2 的 `external_alignment`（T024，因 T034 统一接管全规则）。

### 关键串行点（同文件）
- `engine.py`：T017(US1) → T034(US3)。
- `ttl_merge.py`：T008 → T016(US1) → T032(US3)。
- `interpreter.py`：T006 → T007 → T015(US1) → T024(US2)。
- `api/ontology.py`：T037 → T038 → T039（同文件，顺序追加）。

### 并行机会
- Setup：T001、T002 并行。
- Foundational：T004、T006 并行（T003 应最先以免被重构污染）。
- 各故事测试（T010-T012 / T019-T020 / T027-T030）组内并行。
- 种子/本体类：T013、T014 并行；T022、T023 并行。
- 前端：T041、T042 与后端并行。

---

## Parallel Example: User Story 1

```bash
# 测试先行（组内并行）：
Task: "T010 自动分类 parity 测试 backend/tests/test_reasoning/test_parity_classification.py"
Task: "T011 溯源测试 backend/tests/test_reasoning/test_provenance.py"
Task: "T012 OWA 测试 backend/tests/test_reasoning/test_owa_unknown.py"
# 种子并行：
Task: "T013 hasBetaLactamRing 数据属性种子"
Task: "T014 E11 判据 R-DC1~4 种子"
```

---

## Implementation Strategy

### MVP First（仅 US1）
1. Setup → Foundational（含 T003 基线快照，**重构前必做**）。
2. US1 全量 → **停下验证**：分类声明式推断 + 溯源 + 零回归；R-ED/R-SC/R-CP 仍原样。
3. 可演示。

### Incremental Delivery
1. Foundation → US1（MVP，可推断分类 4 类）。
2. + US2（可推断 7 类，闭合三缺口）。
3. + US3（规则即数据 + UI 可编 + 全规则声明式化）。
4. 每增量不破坏既有（T044 parity 守门）。

---

## Notes

- 写权威 TTL 一律经外科式合并 + 写前三元组级 diff（FR-013，宪章 II 不可妥协）。
- ChEBI/ATC 本地名实现前**必须字节级核实**（T021 阻断 US2 后续）。
- `AssessmentResult` 对外形状不得变（保护 `api/reasoning.py` 下游），T017/T034 守此约束。
- 仅在用户明确要求时提交/推送。
