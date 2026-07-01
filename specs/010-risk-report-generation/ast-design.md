# 设计文档：AST 报告语义模板与素材覆盖契约

> 状态：草案（待评审）
> 关联特性：010-risk-report-generation
> 目标：把现有「隐式硬编码」的风险评估报告生成管线，升级为一棵**显式声明、可遍历、可校验**的 AST（Assessment Semantic Template，评估语义模板），从而把「可控无遗漏」从「相信代码」变成「可验证的契约」。
> 关键决策（已与负责人确认）：
> - **范围**：先出设计文档，评审后再实施。
> - **缺失策略**：缺失的必填槽位在报告中**显式标注「⚠ 待评估（数据缺失）」**，不静默兜底；覆盖清单进审计链。

---

## 1. 问题陈述

### 1.1 现状

现有管线（`fact_bridge.py` → `risk_report_generator.py` → `docx_renderer.py`）已实现端到端 CMC 文档 → 风险评估报告的生成，约 90% 功能完备。但报告的**结构语义是硬编码**在 Python 中的：

- HazID 维度数量与顺序，隐含在 `_RISK_ASSESSMENT_RULES` 的规则列表里；
- 评估对象/设备表/评估表的版式，硬编码在 `RiskReportGenerator.generate()` 与 `docx_renderer.render_risk_report()` 中；
- 缺失事实的处理，是 `interpreter.evaluate()` 返回 `UNKNOWN` 后在 `_evaluate_rules()` 里**静默兜底为「低」**（[risk_report_generator.py:124](../../backend/app/services/reporting/risk_report_generator.py#L124)）。

### 1.2 三个「可控性」缺口

| # | 缺口 | 监管/工程后果 |
|---|------|---------------|
| G1 | **遗漏不可见** | `UNKNOWN→低` 把「评估后确认低风险」和「根本没数据」混为一谈。对 GMP 风险评估文档，这是语义错误：审阅者无法分辨某一行是真低风险还是漏抽。 |
| G2 | **结构变更需改代码** | QS-A-020F05 改版、增删 HazID 维度，都要改 Python，违背「内容可控」与本体权威性原则。 |
| G3 | **无覆盖率证明** | 生成报告时没有「本次 N 个槽位中，M 个来自抽取、K 个来自规则、J 个缺失留空」的素材覆盖清单，无法向 QA 证明「无遗漏」。 |

### 1.3 设计目标

引入一层**声明式 AST**，使报告结构与代码解耦，并附带**素材覆盖校验器**：

1. 报告「应该包含什么」由 AST 模板声明，而非代码隐含。
2. 生成时遍历 AST，对每个槽位产出填充来源与状态，形成**覆盖清单**。
3. 缺失必填槽位显式标注「⚠ 待评估（数据缺失）」，并写入审计链。

**非目标**（本次不做）：可视化 AST 编辑器、多模板版本管理、多语言切换。这些列入后续增强。

---

## 2. 概念模型：AST

AST 是一棵描述报告骨架的语义树。节点分两类：

- **结构节点（Section/Group）**：承载版式与层级，对应报告的章节、表格、分组。
- **槽位节点（Slot）**：叶子，声明一个可被自动填充的事实位置，绑定到一个**填充源**。

### 2.1 槽位（Slot）schema

```jsonc
{
  "slot_id": "subject.pde",                // 全局唯一，点分命名
  "label": "PDE 值",                        // 报告中展示的中文标签
  "source": {
    "kind": "extraction",                  // extraction | rule | manual | constant
    // kind=extraction 时：
    "object_class_iri": "...#DrugProduct", // 来源端点类
    "data_property": "pde_mg_per_day",     // 取 object_data_properties 中的某个 iri/label
    // kind=rule 时：
    "rule_key": "R-RA2",                   // 绑定的 OntologyDecisionRule.rule_key
    "field": "pre_control_level"           // pre_control_level|post_control_level|measures|traceability|status
    // kind=constant 时： "value": "QS-A-020F05"
  },
  "required": true,                         // 必填槽位缺失 → 显式标注待补充
  "on_missing": "annotate",                // annotate | leave_blank（required=false 时用）
  "missing_placeholder": "⚠ 待评估（数据缺失）"
}
```

### 2.2 AST 与现有代码的映射

AST **不替换** `Facts`/规则引擎/`RiskRow`，而是作为它们之上的**编排契约**。映射关系：

| AST 概念 | 复用的现有符号 | 文件 |
|----------|----------------|------|
| `kind=extraction` 槽位求值 | `edges_to_facts()` 产出的 `Facts.data_values`/`scalars`/`relations` | [fact_bridge.py:25](../../backend/app/services/reasoning/fact_bridge.py#L25) |
| `kind=rule` 槽位求值 | `evaluate(rule.antecedent, facts)` + `apply_postconditions()` | [interpreter.py:160](../../backend/app/services/reasoning/interpreter.py#L160) |
| HazID 行 | `RiskRow` dataclass | [risk_report_generator.py:40](../../backend/app/services/reporting/risk_report_generator.py#L40) |
| 设备分组 | `_build_equipment_tables()` / `_detect_workshop()` | [risk_report_generator.py:173](../../backend/app/services/reporting/risk_report_generator.py#L173) |
| 渲染 | `render_risk_report(report)` | docx_renderer.py |

**核心改动点**：`RiskReportGenerator.generate()` 从「硬编码顺序构建 RiskReport」改为「遍历 AST 模板 → 逐槽求值 → 收集覆盖清单 → 构建 RiskReport + CoverageManifest」。

### 2.3 默认 AST 模板（QS-A-020F05）

模板以 JSON 存储（位置见 §5），骨架对应现有报告结构：

```
report (QS-A-020F05)
├─ section: SECTION-I 风险评估
│  ├─ group: 1.评估对象
│  │  ├─ slot subject.name      ← extraction(DrugProduct.text)            required
│  │  ├─ slot subject.pde       ← extraction(DrugProduct.pde_mg_per_day)  required
│  │  ├─ slot subject.class     ← extraction(DrugProduct.分类)            required
│  │  └─ slot subject.dosage    ← extraction(DrugProduct.剂型)            optional
│  ├─ group: 2.评估小组          ← manual（预留空表）
│  ├─ group: 3.设备一览表
│  │  └─ repeat(workshop): slot equipment.rows ← extraction(Equipment[*]) required
│  └─ group: 4.风险评估表
│     └─ repeat(hazid in [人员,生产设备,物料管理,文件,三废处理]):
│        ├─ slot hazid.pre   ← rule(R-RA{n}.pre_control_level)   required
│        ├─ slot hazid.post  ← rule(R-RA{n}.post_control_level)  required
│        ├─ slot hazid.measures ← rule(R-RA{n}.control_measure)  required
│        └─ slot hazid.trace ← rule(R-RA{n}.traceability+source_ref) required
└─ section: SECTION-II 风险回顾   ← manual（定期回顾时补充）
```

「无遗漏」的形式化定义：**默认模板的所有 `required=true` 槽位集合，即风险评估报告的最小完备素材集**。任何一条 CMC 文档生成报告时，覆盖校验器保证这个集合中的每个槽位要么被填充，要么被显式标注待补充——不存在第三种「静默消失」的状态。

---

## 3. 素材覆盖校验器

### 3.1 职责

新增 `backend/app/services/reporting/coverage_validator.py`。在 `RiskReportGenerator.generate()` 内、渲染之前调用，遍历 AST，对每个槽位产出一条覆盖记录。

### 3.2 覆盖状态

| 状态 | 含义 | 报告中表现 |
|------|------|-----------|
| `filled` | 从抽取边或规则成功取到值 | 正常填充 |
| `inferred` | 规则求值为 `TRUE`，值来自规则结论 | 正常填充（风险等级等） |
| `missing_required` | 必填槽位求值为 `UNKNOWN`/取不到值 | **显式标注 `⚠ 待评估（数据缺失）`** |
| `blank_optional` | 选填槽位无值 | 留空（无标注） |
| `manual` | `kind=manual` 槽位 | 预留空白表格（评估小组/回顾） |

### 3.3 覆盖清单（CoverageManifest）

```jsonc
{
  "template_id": "QS-A-020F05@v1",
  "generated_at": "2026-06-30T12:05:35Z",
  "total_slots": 18,
  "filled": 13,
  "inferred": 3,
  "missing_required": 1,           // ← 非零即「有遗漏」，进审计 details
  "blank_optional": 1,
  "slots": [
    {"slot_id": "subject.pde", "status": "filled", "source_ref": "§产品信息"},
    {"slot_id": "hazid.人员.pre", "status": "inferred", "rule_key": "R-RA1"},
    {"slot_id": "subject.class", "status": "missing_required", "source_ref": null}
  ]
}
```

### 3.4 与审计链的集成

`generate` 端点在 `audit.append(db, "report.generate", ...)`（[extraction.py](../../backend/app/api/extraction.py)）的 `details` 中追加覆盖摘要：

```python
details={
    "report_id": str(report_id),
    "rules_fired_count": generator.rules_fired_count,
    "report_type": "risk_assessment",
    "coverage": {                       # 新增
        "total_slots": manifest.total_slots,
        "missing_required": manifest.missing_required,
        "missing_slot_ids": [s.slot_id for s in manifest.missing_required_slots],
    },
}
```

同时 `GeneratedReport.rules_summary` 扩展存全量 `manifest`，供 GET 端点回放。这样 QA 可对任意历史报告查询「当时哪些素材缺失」。

---

## 4. G1 修正：区分「低风险」与「无数据」

当前 [risk_report_generator.py:124](../../backend/app/services/reporting/risk_report_generator.py#L124) 的逻辑：

```python
pre_control_level=level_zh if result is TRUE else "低",
```

`result` 可能是 `TRUE` / `FALSE` / `UNKNOWN`（见 [interpreter.py:160](../../backend/app/services/reasoning/interpreter.py#L160)，多数 op 在事实缺失时返回 `UNKNOWN`）。现有写法把 `FALSE` 和 `UNKNOWN` 一律折叠为「低」。

**修正后的三态映射**（由覆盖校验器统一裁决，不在 `_evaluate_rules` 内静默处理）：

| `evaluate` 结果 | 语义 | pre_control_level |
|-----------------|------|-------------------|
| `TRUE` | 规则命中，风险存在 | 规则声明的等级（高/中/低） |
| `FALSE` | 有数据且明确不构成风险 | `低` |
| `UNKNOWN` | 数据缺失，无法判定 | `⚠ 待评估（数据缺失）`（必填）/ 留空（选填） |

> 注意：这是**语义订正**，会改变缺数据场景的输出。需在测试中新增「UNKNOWN → 待评估」用例，并更新 golden master。现有 `test_unknown_evaluation_maps_to_low_risk` 测试需重命名/改判。

---

## 5. 模板存储与加载

权衡两个选项：

| 选项 | 优点 | 缺点 |
|------|------|------|
| **A. JSON 文件**（`backend/app/services/reporting/templates/qs_a_020f05.json`） | 改版走 PR + code review，离线，启动即载入 | 非技术人员无法自助改 |
| **B. DB 表**（新增 `report_templates`，复用 `OntologyDecisionRule` 的 seed 模式） | 与决策规则一致，未来可接 UI 编辑 | 需迁移 + seed，复杂度上升 |

**建议：先做 A**。当前需求是「内容可控无遗漏」，JSON + PR 已满足可控性，且符合「最小复杂度与复用」原则。待 007 T-Box 专家自助维护落地后，再平滑迁移到 B（schema 一致，迁移成本低）。

---

## 6. 实施切片（评审通过后）

> 以下为预估任务，遵循 spec-kit 任务粒度；本次仅设计，不执行。

| 任务 | 内容 | 依赖 | 预估 | 状态 |
|------|------|------|------|------|
| AST-1 | 定义 AST schema + `qs_a_020f05.json` 默认模板 + 加载器 | — | 2h | ✅ 已完成 |
| AST-2 | `coverage_validator.py`：遍历 AST → CoverageManifest | AST-1 | 3h | ✅ 已完成 |
| AST-3 | 重构 `RiskReportGenerator.generate()` 遍历 AST，产出 report + manifest | AST-1,2 | 3h | ✅ 已完成 |
| AST-4 | G1 三态订正：`UNKNOWN→待评估`，更新映射 | AST-3 | 1h | ✅ 已完成 |
| AST-5 | 渲染层：`docx_renderer` 标注「⚠ 待评估」+ 覆盖摘要页眉/待补充清单 | AST-3 | 2h | ✅ 已完成 |
| AST-6 | 审计 details + `rules_summary` 扩展 coverage | AST-2 | 1h | ✅ 已完成 |
| AST-7 | 测试：覆盖清单、缺失标注、端到端审计/持久化 | 全部 | 3h | ✅ 已完成 |

合计约 15h。**全部切片已实施并通过测试（backend 529 passed）。**

实现落点：
- AST-1 [ast_template.py](../../backend/app/services/reporting/ast_template.py) + [templates/qs_a_020f05.json](../../backend/app/services/reporting/templates/qs_a_020f05.json)
- AST-2 [coverage_validator.py](../../backend/app/services/reporting/coverage_validator.py)
- AST-3/4 [risk_report_generator.py](../../backend/app/services/reporting/risk_report_generator.py)（`generate_with_coverage()` + `PENDING_LEVEL` 三态）
- AST-5 [docx_renderer.py](../../backend/app/services/reporting/docx_renderer.py)（覆盖横幅 + 页眉计数 + 待评估红标 + 待补充素材清单）
- AST-6 [extraction.py](../../backend/app/api/extraction.py) POST `/jobs/{id}/risk-report`（`rules_summary.coverage` 全量 + 审计 `details.coverage` 摘要）
- AST-7 [test_risk_report_coverage.py](../../backend/tests/test_api/test_risk_report_coverage.py)（端到端持久化/审计/哈希链）+ 各单元测试**T024（source_ref 并入 traceability）** 自然并入 AST-3 的 `kind=rule` 槽位 `traceability` 字段求值；**T025（三废 PDE 规则）** 与本设计正交，可独立进行。

---

## 7. 风险与权衡

| 风险 | 应对 |
|------|------|
| G1 订正改变既有输出，可能让原本「全低风险」的报告冒出多处「待评估」 | 这是**预期且正确**的——暴露的是先前被静默掩盖的抽取缺口。需向 QA 沟通语义变化，并在抽取侧补强召回。 |
| AST 模板与规则 `consequent.category` 双重定义 HazID，可能漂移 | AST 槽位通过 `rule_key` 单向绑定规则，category 仍以规则为准；模板只声明「该位置绑定哪条规则的哪个字段」，不复制内容。 |
| JSON 模板手写易错 | 加载时做 schema 校验（pydantic），启动失败而非静默跳过。 |

---

## 8. 结论

**该方案技术可行，且与现有架构高度契合**——AST 是对已有 `Facts`/规则引擎/`RiskRow` 的一层编排契约，而非重写。它以约 15h 的增量，把「可控无遗漏」从隐式代码行为提升为可声明、可校验、可审计的契约，直接回应了用户「使风险评估报告分析的内容可控无遗漏」的核心诉求。

建议评审聚焦三点：(1) 默认模板的必填槽位集合是否就是业务认可的「最小完备素材集」；(2) G1 语义订正的影响面是否可接受；(3) 模板存储选 A（JSON）还是直接上 B（DB）。
