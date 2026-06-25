# Implementation Plan: 推理引擎规则层声明式化（§8.0 升级路径）

**Branch**: `006-declarative-rule-layer` | **Date**: 2026-06-24 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/006-declarative-rule-layer/spec.md`

## Summary

把硬编码在 `backend/app/services/reasoning/rules/*.py`（`ALL_RULES` 列表 + `conflict_resolver.py`）里的风险分类「规则知识」外化为**声明式、可版本化、本体原生**的制品，并据《方案》§8.0 让现仅可断言（激素/青霉素）或不可表达（肿瘤药物）的风险类别变为**可推断**，同时保持每条规则的法规可追溯性。

**技术取向（关键决策，详见 research.md）**：在**不引入 JRE / HermiT / Pellet**（宪章 V：不引入并行框架、最小依赖）的前提下，用一个**通用声明式判据/规则解释器**（Python，复用既有引擎模式）替换四组硬编码规则函数；判据/规则/冲突策略以**可编辑 T-Box 元数据**（新增 E11/E12/E13 表）为编辑期真理来源，发布时经 `build_managed_graph` **外科式合并**投影进权威 TTL（分类判据 → `owl:equivalentClass` 充要公理 + OWL2 datatype facet；决策规则 → 原生 `slpra:DecisionRule` 声明式资源携 `dct:source`；冲突策略 → `slpra:ConflictPolicy` 资源）。TTL 公理为人审、可移植、未来可被真正 DL 推理机直接消费的真理载体；Python 解释器为执行器——与既有「E5 限制存于 TTL、Python 引擎执行规则」完全同构。激素/青霉素/肿瘤经 ChEBI/ATC **外部对齐物化**（澄清决策 2，复用 E6 映射机制）。

## Technical Context

**Language/Version**: Python 3.11（后端）/ TypeScript（前端最小编辑入口，Next.js）

**Primary Dependencies**: FastAPI（`APIRouter`+`Depends`）、SQLAlchemy 2.0（`Mapped`/`mapped_column`）、Alembic、Owlready2、rdflib（TTL 解析/序列化与外科式合并）、pytest。**不新增** JRE/HermiT/Pellet、不新增推理框架（宪章 V）。

**Storage**: PostgreSQL（可编辑 T-Box 元数据，新增 E11/E12/E13 表）+ 权威 TTL（`ontology/slpra/*.ttl`，单一真理来源）+ Owlready2 World（发布期物化派生缓存，启动重建）。

**Testing**: pytest（契约/集成）；新增 `test_reasoning_parity.py`（零回归黄金基线）、判据/规则/策略 CRUD 与乐观并发契约测试、外科式合并 round-trip 测试、模型一致性/健康度门禁测试。

**Target Platform**: 内网 Linux 服务器（小并发、长生命周期）。

**Project Type**: Web（backend FastAPI + frontend Next.js）。本特性以后端为主，前端仅新增「声明式制品」最小编辑入口（澄清决策 1 的 UI 可编后果）。

**Performance Goals**: 单次评估 p95 不劣于现状（解释器为 O(规则数×判据数)，规模为数十；无 JVM 启动成本）。发布期 TTL 导出/合并/一致性校验在秒级。

**Constraints**: 写权威 TTL MUST 走外科式合并 + 逐字保留未建模三元组 + 写前三元组级 diff（宪章 II / FR-013）；双存储写后一致（宪章 II）；变更可追溯落批次（宪章 III / FR-015）；写/发布受角色门禁（宪章 安全 / FR-016）；OWA 下属性缺失 → 未知，不得断言负类（FR-010）。

**Scale/Scope**: 判据 ~7–10 条（R-DC1~4 + 激素/青霉素/肿瘤）、决策规则 ~18 条（R-ED/R-SC/R-CP）、冲突策略 ~2 维（dedication / risk_level）。单位数并发用户。

## Constitution Check

*GATE: Phase 0 前与 Phase 1 后各评估一次。*

| 原则 | 适用门禁 | 本计划落点 | 结论 |
|---|---|---|---|
| **I 规范驱动** | 规范为唯一真理；实现细节不渗入规范 | 已完成 specify→clarify（3 决策）→本 plan；规范未含技术细节，技术细节只在本 plan/research | ✅ PASS |
| **II 本体权威性与保真 (NON-NEGOTIABLE)** | 外科式合并回写、逐字保留未建模三元组、维持 BFO/外部对齐、双存储写后一致、写前三元组级 diff | 扩展 `build_managed_graph` 投影 equivalentClass/规则/策略；扩展 `surgical_merge` 为「对象形态感知」以**保留**命名 IRI 外部对齐、仅托管 BNode 类表达式；确定性 BNode 保证 round-trip 稳定；复用既有 `export_diff` 出 diff | ✅ PASS（round-trip 正确性由 Phase 0 R2 + 测试坐实） |
| **III 可追溯与审计** | 版本号 + 乐观并发；批次发布（TTL 导出 + Git 提交 + SHA）；审计日志；已发布不可篡改 | E11/E12/E13 复用 `VersionMixin`/`NamedEntityMixin` + `OntologyChangeLog`/`OntologyRelease` 既有批次与审计链 | ✅ PASS |
| **IV 测试纪律与契约优先** | 对外接口先契约后实现；关键路径契约/集成测试；quickstart 可执行；发布前一致性门禁 | `contracts/` 先行；`test_reasoning_parity.py` 零回归基线；一致性校验阻断发布（FR-014） | ✅ PASS |
| **V 最小复杂度与复用** | 复用既有栈/模式；新依赖最小化并论证；YAGNI；不引入并行框架 | **不引入 JRE/Pellet**；通用解释器复用既有 `ALL_RULES`→数据驱动模式；新表复用既有 Mixin 与 CRUD 模式；前端仅最小入口 | ✅ PASS |
| **安全与合规** | 写/发布角色门禁（`senior_analyst`）；最小暴露 | 判据/规则/策略 CRUD 与发布复用 `require_role(senior_analyst)`（FR-016） | ✅ PASS |

**初评结论**：无违例，无需 Complexity Tracking。**关键风险**集中于 II 的 equivalentClass round-trip 正确性——置于 Phase 0 优先解决（R2）。

**Phase 1 后复评**：见本文件末「Post-Design Constitution Re-Check」。

## Project Structure

### Documentation (this feature)

```text
specs/006-declarative-rule-layer/
├── plan.md              # 本文件
├── research.md          # Phase 0：5 项决策（推断机制 / round-trip / 对齐取值 / 零回归 / 前端入口）
├── data-model.md        # Phase 1：E11/E12/E13 + TTL 公理 + 解释器数据契约
├── quickstart.md        # Phase 1：端到端验证场景（映射验收场景）
├── contracts/           # Phase 1：CRUD 与发布 REST 契约 + 评估响应不变量
│   ├── classification-criteria.md
│   ├── decision-rules.md
│   ├── conflict-policies.md
│   └── assessment-invariants.md
└── tasks.md             # /speckit-tasks 产出（本命令不产）
```

### Source Code (repository root)

```text
backend/
├── app/
│   ├── models/
│   │   └── ontology_meta.py        # +E11 OntologyClassificationCriterion / +E12 OntologyDecisionRule / +E13 OntologyConflictPolicy
│   ├── services/
│   │   ├── ttl_merge.py            # 扩展 build_managed_graph（emit equivalentClass/规则/策略）+ surgical_merge（对象形态感知）
│   │   ├── ontology_engine.py      # project_entities 支持新 kind（best-effort 物化）
│   │   └── reasoning/
│   │       ├── engine.py           # run_assessment 改为驱动通用解释器（保持 AssessmentResult 形状不变）
│   │       ├── interpreter.py      # 新增：通用判据/规则求值器（替代 rules/*.ALL_RULES 的硬编码）
│   │       ├── policy.py           # 新增：从 ConflictPolicy 读策略，泛化 resolve_dedication_conflict/resolve_risk_level
│   │       └── rules/              # 退化为「内置默认判据/规则的种子数据」或保留为兼容垫片（见 tasks）
│   ├── api/
│   │   └── ontology.py             # +criteria/decision-rules/conflict-policies 的 CRUD 路由（复用既有模式）
│   └── alembic/versions/           # +迁移：E11/E12/E13 建表 + 种子（CFDI R-DC/R-ED/R-SC/R-CP 默认行）
└── tests/
    ├── test_api/                   # +CRUD/乐观并发/角色门禁契约测试
    └── test_reasoning/             # +test_reasoning_parity.py（零回归黄金基线）+ round-trip + 一致性门禁

ontology/slpra/
└── slpra-drug.ttl                  # +hasBetaLactamRing 数据属性 +AntineoplasticDrug 类（经合并写入，非手改）
                                    # 外部对齐（ChEBI/ATC）经 slpra-integration.ttl 既有对齐机制

frontend/src/
├── app/(dashboard)/ontology/[projectId]/   # +「声明式规则」最小编辑入口（判据/规则/策略只读+编辑）
└── lib/api.ts                      # +对应 CRUD 客户端
```

**Structure Decision**: 沿用既有 Web（backend/frontend）结构与既有 T-Box 编辑栈。后端为主体；前端仅加最小编辑入口（决策 1 的 UI 可编后果，范围受限）。推理引擎在 `reasoning/` 内新增 `interpreter.py`/`policy.py` 两个通用执行器，`engine.py` 改为装配它们而**不改变 `AssessmentResult` 对外形状**（保护 `api/reasoning.py` 及其下游 `_build_canonical_results`/动作编排/报告，零回归前提）。

## Complexity Tracking

> 无宪章违例，无需填写。

唯一显著的「新增表」（E11/E12/E13）已由 FR-003 / 澄清决策 1（TTL + 可编辑元数据表）明确要求，且复用既有 Mixin/CRUD/批次机制，非额外复杂度来源。

## Post-Design Constitution Re-Check

Phase 1 设计完成后复评（详见 data-model.md / contracts/）：

- **II 保真**：`surgical_merge` 的「对象形态感知」改造经 round-trip 测试坐实——命名 IRI 外部对齐逐字保留、BNode 类表达式确定性重发不累积、孤儿 BNode 子图随 equivalentClass 一并回收。✅
- **III 审计**：E11/E12/E13 全部纳入既有 `OntologyChangeLog` 批次与 `OntologyRelease` SHA 归档；推断溯源（Derivation Provenance）随 `rules_fired` 落库。✅
- **IV 测试**：`contracts/` 四份契约先行；零回归基线覆盖既有四个调用方测试 + 新建规则分支矩阵。✅
- **V 复用**：未引入 JRE/Pellet/新框架；解释器与策略器为薄执行层。✅

**复评结论**：设计未引入新违例，门禁全部 PASS，可进入 `/speckit-tasks`。

## 实现状态（2026-06-24，T046）

特性全 6 阶段（T001–T046）实现完成，全量 `pytest -q` **200 passed**。

- **声明式三件套落地**：E11 分类判据 / E12 决策规则 / E13 冲突策略均为可编辑数据（`ontology_*` 表 + `0004_declarative_rule_layer` 迁移 + `seed_declarative` 种子），单一真理源在 `app/services/reasoning/defaults.py`，引擎经受限词表解释器 `interpreter.py` + 策略器 `policy.py` 执行（US1/US2/US3）。
- **零回归（FR-012/SC-004）**：`engine.run_assessment(criteria/decision_rules/policies)` 缺省回退 `defaults`，与黄金基线逐用例相等（`test_parity_golden_master`），既有 4 调用方测试形状不变。
- **legacy 退化（T043）**：`rules/*.py` + `conflict_resolver.py` 降为弃用垫片（`ALL_RULES=[]`，docstring 指向声明式层）；`/api/reasoning/rules` 改从 `defaults` 派生。
- **保真 round-trip（FR-013/014）**：`surgical_merge` 对象形态感知改造 + 确定性 BNode，命名 IRI 外部对齐逐字存活；发布前 `export_diff` 三元组级 diff。
- **前端入口（US3）**：`/ontology/rules` 三标签工作台（分类判据/决策规则/冲突策略）+ `api.ts` CRUD 客户端，复用 409 乐观并发与 shadcn 组件。
- **验证（T045）**：quickstart 五场景逐一对应实测用例全绿（见 [quickstart.md](./quickstart.md)「验证记录」）。
- **agent context（T046）**：CLAUDE.md 托管段已刷新指向本 plan。
