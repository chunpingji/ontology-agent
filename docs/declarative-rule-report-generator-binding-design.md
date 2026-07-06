# 声明式规则与报告生成器的绑定设计

> 设计说明 · 2026-07-05
> 关联：`backend/app/services/reporting/risk_report_generator.py`、`coverage_validator.py`、`narrative_generator.py`、`reasoning/interpreter.py`、`reasoning/policy.py`
> 前置：`section-coverage-declaration-design.md`（016 覆盖声明）

## 1. 问题陈述

报告生成器 `RiskReportGenerator` 内部有两条并行路径——**声明式规则推理**（E12 决策规则 → RiskRow[]）和**覆盖验证**（016 Section.coverage → CoverageManifest）。两条路径共享 `Facts` 事实基但互不引用，且各自存在与声明式规则层（E11/E12/E13）的断裂：

```
源文档 → edges_to_facts(edges, engine) → Facts
                                           │
                    ┌──────────────────────┤
                    ▼                      ▼
           ┌─── 决策规则路径 ───┐   ┌─── 覆盖声明路径 ───┐
           │ E12 DecisionRule   │   │ AST Template       │
           │                    │   │ Section.coverage   │
           │ evaluate(ante,     │   │ CoverageBinding    │
           │   facts) → Ternary │   │                    │
           └────────┬───────────┘   └────────┬───────────┘
                    ▼                        ▼
              RiskRow[]                CoverageManifest
              (风险评估行)              (无遗漏证明)
                    │                        │
                    ▼                        ▼
              ┌─────────────────────────────────┐
              │ _try_narrative_generation        │
              │   只收到 edges（原始抽取边）        │
              │   看不到 RiskRow[] 和 Manifest    │ ← 断裂点
              └─────────────────────────────────┘
```

三层声明式规则（E11 分类判据 / E12 决策规则 / E13 冲突策略）在报告生成时的消费状态：

| 层 | 模型 | 当前消费者 | 报告生成器消费 | 缺口 |
|---|---|---|---|---|
| **E11** ClassificationCriterion | `pattern` (interpreter AST) + `target_class_id` | `ontology_meta_store.validate()` (TTL 发布校验) | ❌ 未消费 | 运行时从未 evaluate；分类结论不进 Facts |
| **E12** DecisionRule | `antecedent` + `consequent` + `rule_group` + `priority` | `_load_rules()` → `_evaluate_rules()` | ⚠️ 广播式 | 加载全部 `risk_assessment` 规则，不按文档类型过滤 |
| **E13** ConflictPolicy | `dimension` + `strategy` + `priority_lattice` + `override_direction` | `engine.py` (推理引擎) | ❌ 未消费 | `_evaluate_post_control` 硬编码，不查询发布的策略 |

## 2. 设计原则

1. **AST 模板不引用规则**：模板声明"报告说什么"（内容结构），规则决定"报告判什么"（推理逻辑）。两者由不同角色管理（模板作者 vs 本体专家），变化节奏不同。生成器是唯一汇合点。
2. **确定性优先**：E11/E12/E13 的评估结果是确定性的（三值逻辑，无随机性）。LLM 叙事生成是确定性结果的**下游消费者**，永远不反向影响评估（FR-009）。
3. **类型级连接**：模板与规则之间的桥梁是 `doc_class_iri`（文档实体类型），不是规则引用。分类结果 → 文档类型 → 关系图谱 → 覆盖声明候选集 + 适用规则集。
4. **优雅降级**：每层规则消费都有 fallback——E11 不可用时 drug_classes 保持现有提取逻辑；E12 无适用规则时返回空行；E13 不可用时退回默认策略。

## 3. 缺口分析

### 3.1 E11 分类判据：存而不用

E11 `OntologyClassificationCriterion` 存储了形如"∃ hasInactivationProfile . HighPotencyCompound → 高活性药物"的声明式分类条件。这些 `pattern` 与 E12 的 `antecedent` 使用同一套 interpreter AST 词汇（`some_values_from`、`datatype_facet`、`class_membership` 等）。

**当前**：E11 仅在 `ontology_meta_store.validate()` 中用于 TTL 发布前校验（检查判据引用是否可解析），从未在运行时被 evaluate。

**文档分类**（`document_classifier.py:classify`）使用的是一套完全独立的关键词打分机制：

```python
# document_classifier.py — 关键词打分，与 E11 无关
candidates = engine.get_subclasses(REGULATORY_DOCUMENT_IRI)
for cand in candidates:
    for kw in _CURATED_SIGNALS.get(local, ()):
        if kw in haystack:
            score += _CURATED_WEIGHT
```

E11 分类的不是文档，而是**药物/物质属性**（"这个 API 满足条件 X → 归入高活性药物类"）。这些分类结论影响下游哪些风险维度相关——例如高活性药物需要评估 OEB 暴露控制维度，而普通药物不需要。

**缺口**：E11 分类结论不注入 Facts，导致 E12 中依赖药物分类的规则（如 `class_present` 算子）只能看到抽取时硬编码的 `drug_classes` 标签，无法看到声明式分类推理的结论。

### 3.2 E12 决策规则：广播式加载

```python
# risk_report_generator.py:221-230
def _load_rules(self):
    return self._db.query(OntologyDecisionRule).filter(
        OntologyDecisionRule.rule_group == "risk_assessment",
        OntologyDecisionRule.is_disabled == False,
    ).order_by(OntologyDecisionRule.priority).all()
```

加载**全部**启用的 `risk_assessment` 规则，不考虑当前文档类型。三值逻辑隐式兜底：文档的 Facts 中缺少某规则引用的键 → `UNKNOWN` → "数据缺失，显式待评估"。

**后果**：每份报告包含与该文档类型结构性无关的风险维度行，全部标"待评估"。对 CMCReport 可能有 5 条规则相关、3 条完全无关；无关维度的"待评估"既是噪声，也让审阅者无法分辨"真的缺数据"和"这条规则本来就不适用于这类文档"。

### 3.3 E13 冲突策略：推理引擎消费了，报告生成器没消费

`policy.py` 已经将冲突解消外部化为数据：

```python
# policy.py — 接受 E13 策略参数
def resolve_risk_level(levels, policy=None):
    lattice = getattr(policy, "priority_lattice", None) or RISK_PRIORITY_LATTICE
    return max(levels, key=lambda level: lattice.get(level, 0))
```

推理引擎 (`engine.py`) 在 T034 中已经传入发布的 E13 策略。但报告生成器的 `_evaluate_post_control` 是硬编码的：

```python
# risk_report_generator.py:265-289 — 硬编码，不走 E13
postconditions = (rule.consequent or {}).get("postconditions", {})
if postconditions:
    row.post_control_level = "低"        # ← 硬编码
else:
    row.post_control_level = row.pre_control_level
```

当本体专家在 /ontology/rules 页面修改了冲突策略（如将 `risk_level` 的 `priority_lattice` 从三级改为四级），报告生成器无法感知。

### 3.4 LLM 叙事生成器的上下文断裂

`generate_section_narratives` 的调用签名和上下文注入：

```python
# narrative_generator.py:168-206
def generate_section_narratives(edges, template, client):
    facts_text = _format_facts(edges)          # ← 只有原始抽取边
    for sec in template.sections:
        user = (
            f"## 行文 Prompt\n{prompt}\n\n"
            f"## 本章节数据字段\n{labels_text}\n\n"
            f"## 抽取事实\n{facts_text}\n\n"       # ← 只有 edges
            "请按行文 Prompt 生成本章节正文。"
        )
```

LLM 收到的：原始抽取边、Section 插槽标签、Section.prompt 写作指令。

LLM 收不到的：

- E12 规则评估结果（哪条触发、TRUE/FALSE/UNKNOWN、风险等级）
- `RiskRow[]`（pre/post control level、控制措施、可接受状态）
- 016 覆盖声明展开结果（关系存在/缺失、属性填充状态）
- E13 冲突策略聚合后的结论

当 Section.prompt 写 `"根据风险评估结果，说明 {{风险等级}} 的评判依据及 {{控制措施}}"` 时，LLM 没有规则结果可引用——它只能从原始事实自己"猜"风险等级，可能与确定性评估结果矛盾。

## 4. 目标架构

### 4.1 生成器作为唯一编排点

```
generate_with_coverage() 修正后的执行流：
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

① facts = edges_to_facts(edges, engine)              ← 事实基（现有）
② facts = _enrich_with_classification(facts, db, engine)   ← E11 丰富（新增）
③ rules = _load_rules(doc_class_iri)                 ← E12 按文档类型过滤（修改）
④ pre_rows  = _evaluate_rules(rules, facts)          ← 风险行（现有）
⑤ post_rows = _evaluate_post_control(rules, facts,
                  pre_rows, risk_policy)              ← E13 策略驱动（修改）
⑥ report = RiskReport(..., assessment_rows=post_rows)
⑦ manifest = validate_coverage(template, edges,
                  rules, facts, engine=engine)        ← 覆盖证明（016 修改）
⑧ _try_llm_merge(report, manifest, ...)              ← LLM 填空（现有）
⑨ _try_narrative_generation(report, edges,
      assessment_rows=post_rows, manifest=manifest)   ← LLM 叙事（修改）
```

### 4.2 数据流全景

```
                        ┌─────────────────────┐
                        │  OntologyEngine     │
                        │  (只读 T-Box)        │
                        └──────┬──────────────┘
                               │
              ┌────────────────┼────────────────┐
              ▼                ▼                ▼
┌──────────────────┐ ┌──────────────────┐ ┌──────────────────┐
│ document_         │ │ get_relation_    │ │ get_subclasses   │
│ classifier        │ │ schema           │ │ get_data_props   │
│ (关键词打分)       │ │ (关系图谱)        │ │ get_alignments   │
└────────┬─────────┘ └────────┬─────────┘ └────────┬─────────┘
         │                    │                     │
         ▼                    │                     ▼
  doc_class_iri               │            edges_to_facts(edges, engine)
         │                    │                     │
         │                    │                     ▼
         │                    │                   Facts
         │                    │                     │
         │              ┌─────┤         ┌───────────┤
         │              │     │         │           │
         │              ▼     │         ▼           │
         │     ┌────────────┐ │  ┌────────────┐     │
         │     │ Section    │ │  │ E11 分类    │     │
         │     │ .coverage  │ │  │ 判据评估    │     │
         │     │ (016)      │ │  │ (新增)      │     │
         │     └─────┬──────┘ │  └─────┬──────┘     │
         │           │        │        │             │
         │           │        │        ▼             │
         │           │        │  Facts.drug_classes   │
         │           │        │  丰富后              │
         │           │        │        │             │
         ├───────────┼────────┼────────┤             │
         │           │        │        │             │
         ▼           ▼        │        ▼             │
  ┌────────────┐  ┌──────┐   │  ┌────────────┐      │
  │ E12 规则   │  │覆盖   │   │  │ E12 规则   │      │
  │ 过滤      │  │验证   │   │  │ 评估       │      │
  │(doc_class) │  │      │   │  │ (facts)    │      │
  └─────┬──────┘  └──┬───┘   │  └─────┬──────┘      │
        │            │       │        │              │
        │            ▼       │        ▼              │
        │     CoverageManifest│  RiskRow[]           │
        │            │       │        │              │
        │            │       │        ▼              │
        │            │       │  ┌────────────┐       │
        │            │       │  │ E13 冲突   │       │
        │            │       │  │ 策略聚合   │       │
        │            │       │  │ (policy)   │       │
        │            │       │  └─────┬──────┘       │
        │            │       │        │              │
        │            │       │        ▼              │
        │            │       │  post_rows (final)    │
        │            │       │        │              │
        └────────────┴───────┴────────┴──────────────┘
                                      │
                     ┌────────────────┤
                     ▼                ▼
              ┌────────────┐   ┌────────────────┐
              │ RiskReport │   │ LLM 叙事生成    │
              │ (确定性)    │   │ Section.prompt   │
              │            │   │ + facts          │
              │            │   │ + RiskRow[]      │ ← 注入确定性结果
              │            │   │ + Manifest       │
              └────────────┘   └────────────────┘
```

## 5. 修改设计

### 5.1 E11 → Facts 丰富（分类结论注入事实基）

**位置**：`risk_report_generator.py`，`generate_with_coverage` 方法内，在 `edges_to_facts` 之后、`_evaluate_rules` 之前。

**逻辑**：查询启用的 E11 `OntologyClassificationCriterion`，对每条判据执行 `evaluate(criterion.pattern, facts)`。`TRUE` → 将 `target_class` 名称追加到 `facts.drug_classes`；`FALSE`/`UNKNOWN` → 不追加。

```python
def _enrich_with_classification(
    self, facts: Facts, engine: Any | None = None,
) -> Facts:
    """E11: 评估药物分类判据，将推理结论注入 facts.drug_classes。"""
    if engine is None or not engine.is_loaded:
        return facts
    criteria = (
        self._db.query(OntologyClassificationCriterion)
        .filter_by(is_disabled=False)
        .all()
    )
    for crit in criteria:
        result = evaluate(crit.pattern, facts)
        if result is TRUE:
            target = engine.get_class_detail(crit.target_class_id)
            if target and target.name not in facts.drug_classes:
                facts.drug_classes.append(target.name)
    return facts
```

**降级**：engine 不可用 → 跳过，facts.drug_classes 保持抽取时提取的标签（现有行为不变）。

**作用**：E12 规则中的 `class_present` / `class_membership` 算子可以引用 E11 的推理结论，而不仅仅是抽取时硬编码的药物类别标签。这使"高活性药物 → 需要 OEB 评估维度"这类跨层推理成为可能。

### 5.2 E12 → 按 doc_class_iri 过滤规则

**位置**：`OntologyDecisionRule` 模型 + `_load_rules` 方法。

**方案**：在 E12 上增加 `applicable_doc_classes` 字段（JSON 数组，`NULL` 表示通用）。`_load_rules` 按文档类型过滤：

```python
def _load_rules(
    self, doc_class_iri: str | None = None,
) -> list[OntologyDecisionRule]:
    q = self._db.query(OntologyDecisionRule).filter(
        OntologyDecisionRule.rule_group == "risk_assessment",
        OntologyDecisionRule.is_disabled == False,
    )
    if doc_class_iri:
        q = q.filter(
            OntologyDecisionRule.applicable_doc_classes.is_(None)
            | OntologyDecisionRule.applicable_doc_classes.contains(
                [doc_class_iri]
            )
        )
    return q.order_by(OntologyDecisionRule.priority).all()
```

**模型变更**：

```python
# ontology_meta.py — E12 增加适用文档类型字段
class OntologyDecisionRule(NamedEntityMixin, Base):
    # ... 现有字段 ...
    applicable_doc_classes: Mapped[list | None] = mapped_column(
        JSON, nullable=True,
    )  # NULL = 通用；["...CMCReport", "...BatchProductionRecord"] = 仅适用于这些类型
```

**Alembic 迁移**：一列 `JSON NULLABLE`，默认 `NULL`（通用），向后兼容——现有规则保持全局适用。

**降级**：`doc_class_iri=None` → 现有广播行为不变（向后兼容）。

**前端联动**：/ontology/rules 的决策规则编辑器增加可选的"适用文档类型"多选（从 `get_subclasses(RegulatoryDocument)` 枚举），未选则通用。

### 5.3 E13 → 替换硬编码后控逻辑

**位置**：`_evaluate_post_control` 方法。

**逻辑**：查询发布的 E13 `OntologyConflictPolicy`（`dimension="risk_level"`），传入 `policy.resolve_risk_level`：

```python
def _evaluate_post_control(
    self,
    rules: list[OntologyDecisionRule],
    facts: Any,
    pre_rows: list[RiskRow],
    risk_policy: Any | None = None,       # ← 新增
) -> list[RiskRow]:
    for i, rule in enumerate(rules):
        row = pre_rows[i]
        if row.pre_control_level == PENDING_LEVEL:
            row.post_control_level = PENDING_LEVEL
            row.status = PENDING_STATUS
            continue

        postconditions = (rule.consequent or {}).get("postconditions", {})
        if postconditions:
            # E13 策略驱动，替代硬编码 "低"
            row.post_control_level = policy.resolve_risk_level(
                [row.pre_control_level], risk_policy
            )
        else:
            row.post_control_level = row.pre_control_level

        row.status = "可以接受" if row.post_control_level == "低" else "不可接受"
    return pre_rows
```

**调用处**（`generate_with_coverage`）：

```python
risk_policy = (
    self._db.query(OntologyConflictPolicy)
    .filter_by(dimension="risk_level", is_disabled=False)
    .first()
)
post_rows = self._evaluate_post_control(rules, facts, pre_rows, risk_policy)
```

**降级**：`risk_policy=None` → `policy.resolve_risk_level` 使用默认 `RISK_PRIORITY_LATTICE`（现有行为不变，golden-master parity）。

### 5.4 LLM 叙事生成器注入确定性结果

**位置**：`narrative_generator.py` 的 `generate_section_narratives` + `risk_report_generator.py` 的 `_try_narrative_generation`。

#### 5.4.1 签名扩展

```python
def generate_section_narratives(
    edges: list[dict],
    template,
    client,
    *,
    assessment_rows: list | None = None,          # ← 新增
    manifest: CoverageManifest | None = None,     # ← 新增
) -> list[dict]:
```

#### 5.4.2 格式化规则结果

```python
def _format_rule_results(rows: list) -> str:
    """将确定性风险评估结果格式化为 LLM 可读上下文（只读参考）。"""
    if not rows:
        return "（无风险评估结果）"
    parts = []
    for row in rows:
        line = f"- [{row.hazid}] {row.contributing_factors}："
        line += f"初始风险 = {row.pre_control_level}"
        if row.post_control_level:
            line += f" → 残余风险 = {row.post_control_level} ({row.status})"
        if row.control_measures:
            line += f"\n  控制措施：{row.control_measures}"
        parts.append(line)
    return "\n".join(parts)


def _format_coverage_status(manifest, section_id: str) -> str:
    """将覆盖验证结果格式化为 LLM 可读上下文。"""
    if manifest is None:
        return ""
    STATUS_ZH = {
        "filled": "已填充", "inferred": "推理得出",
        "missing_required": "缺失(必填)", "blank_optional": "空白(可选)",
        "manual": "待人工填写", "dismissed": "已忽略",
    }
    slots = [s for s in manifest.slots
             if section_id in s.slot_id or s.slot_id.startswith(section_id)]
    if not slots:
        return ""
    parts = [f"- {s.label}: {STATUS_ZH.get(s.status, s.status)}"
             + (f" = {s.value}" if s.value else "")
             for s in slots]
    return "\n".join(parts)
```

#### 5.4.3 注入 LLM prompt

```python
# 修改后的 user prompt 构造
rules_text = _format_rule_results(assessment_rows) if assessment_rows else ""
coverage_text = _format_coverage_status(manifest, sec.section_id) if manifest else ""

user_parts = [
    f"## 行文 Prompt\n{prompt}",
    f"## 本章节数据字段\n{labels_text}",
    f"## 抽取事实\n{facts_text}",
]
if rules_text:
    user_parts.append(
        f"## 风险评估结果（确定性结论，必须原样引用，不可自行推断）\n{rules_text}"
    )
if coverage_text:
    user_parts.append(
        f"## 本章节覆盖状态\n{coverage_text}"
    )
user_parts.append(
    "请按行文 Prompt 生成本章节正文。"
    "引用风险评估结果时必须与上述确定性结果严格一致。"
)
user = "\n\n".join(user_parts)
```

#### 5.4.4 调用处传入结果

```python
# risk_report_generator.py — _try_narrative_generation 修改
def _try_narrative_generation(self, report, edges):
    # ... existing client/flag checks ...
    report.section_narratives = generate_section_narratives(
        edges, self._template, client,
        assessment_rows=report.assessment_rows,    # ← 传入规则结果
        manifest=self._last_manifest,               # ← 传入覆盖结果
    )
```

## 6. 不变量

- **FR-009**：LLM 叙事生成**永远不反向影响**确定性评估。规则结果以只读上下文注入 prompt；LLM 的角色是"用自然语言叙述已确定的结论"，不是"自己做风险判断"。
- **宪章 II**：本体只读。所有本体访问（`get_relation_schema`、`get_subclasses`、`get_class_alignments`、`get_data_properties_by_domain`）都是离线 `World` 的结构查询。E11 评估在 Facts 层操作，不写入本体。
- **宪章 VI**：E11/E13 不可用时静默降级到现有行为；不产生假的"降级"状态。
- **Golden-master parity**：所有修改的默认路径（`engine=None`、`policy=None`、`doc_class_iri=None`、`assessment_rows=None`）产出与修改前逐字节一致的结果。
- **覆盖声明不引用规则**：AST 模板的 `Section.coverage` 声明本体关系绑定；`Section.groups` 中的 `assessment_table` 组承载规则推理结果。两者在生成器中汇合但模板侧互不引用。

## 7. 时序依赖关系

```
          E11                         5.1 — 丰富 Facts
           ↓
         Facts
         ╱    ╲
       E12     016 Coverage
  (过滤后)      (validate)           5.2 — 按 doc_class 过滤
       ↓          ↓
   RiskRow[]   Manifest
       ↓
      E13                            5.3 — 策略驱动后控
       ↓
   post_rows (final)
       ↓          ↓
   RiskReport   LLM 叙事             5.4 — 注入确定性结果
                  ↑
            facts + rows + manifest
```

E11 → E12 → E13 是串行依赖：E11 丰富 Facts 后 E12 才能正确引用分类结论；E12 的 pre_control_level 是 E13 聚合的输入。LLM 叙事在最后，消费全部确定性结果。

## 8. 改动点清单

| 文件 | 改动 | 涉及 |
|---|---|---|
| `models/ontology_meta.py` | E12 增 `applicable_doc_classes: JSON NULLABLE` | 5.2 |
| `risk_report_generator.py` | `_enrich_with_classification`（新增）; `_load_rules(doc_class_iri)` 签名; `_evaluate_post_control(risk_policy)` 签名; `_try_narrative_generation` 传参 | 5.1–5.4 |
| `narrative_generator.py` | `generate_section_narratives` 增 `assessment_rows`/`manifest` 参数; `_format_rule_results`/`_format_coverage_status` 新增 | 5.4 |
| `reasoning/policy.py` | 无修改（已具备 E13 参数接口） | — |
| Alembic migration | 一列 `applicable_doc_classes JSON NULLABLE` on `ontology_decision_rule` | 5.2 |
| 前端 decision-rules-panel | 规则编辑器增可选"适用文档类型"多选 | 5.2 |
| tests | `test_e11_enriches_facts`; `test_rules_filtered_by_doc_class`; `test_post_control_uses_policy`; `test_narrative_receives_rule_results` | 全部 |

## 9. 与 016 覆盖声明的关系

016（[`section-coverage-declaration-design.md`](section-coverage-declaration-design.md)）解决的是"模板如何声明覆盖哪些本体关系"——**内容完整性**维度。本设计解决的是"生成器如何正确消费声明式推理规则"——**推理正确性**维度。两者共存于 `generate_with_coverage` 的同一编排流中：

- 016 的 `Section.coverage` → `validate_coverage(engine=engine)` → 覆盖位置 → 进入 LLM 叙事上下文
- 本设计的 E11→E12→E13 → `RiskRow[]` → 进入 LLM 叙事上下文

两条路径**在叙事生成步骤汇合**：LLM 同时看到"哪些关系覆盖了/缺失了"和"风险评估结论是什么"，据此生成完整的章节叙述。

**016+ 语义化插槽**（见 [`section-coverage-declaration-design.md` §10](section-coverage-declaration-design.md)）把这一汇合下沉到 **slot 级**：`SemanticSource` 是 `Section.coverage` + `Section.prompt` 的投影，生成期 `generate_semantic_slots` 与本设计 §5.4 的 section 级叙事**共用** `_format_rule_results` 等确定性注入辅助——本设计的 `RiskRow[]` 以"确定性结论，必须原样引用"的只读上下文注入每个语义化插槽。**FR-009 不变量在 slot 级同样成立**：LLM 只引用、不重算风险等级，输出不回流评估。

## 10. 设计时预览：在模板编辑器中预览完整报告

### 10.1 当前状态：设计器与生成器的断裂

模板设计器（`template-slot-editor.tsx`）持有源文档的两个表示：

| 已有 | 来源 | 用途 |
|---|---|---|
| `sample_text` | 创建模板时上传的 DOCX 纯文本 | AI 分析（`suggest_slots`） |
| `sample_content_json` | 同一 DOCX 的 tiptap 结构化内容 | 忠实预览（`WordViewer`）+ 锚点推导 |

但从"设计器中的源文档"到"完整报告"之间**没有路径**：

```
当前设计器数据流：
sample DOCX → parse → sample_text + tiptap
                          │
                          ▼
                    suggest_slots (AI)
                          │
                          ▼
                    sections / slots / coverage / unresolved
                          │
                          ▼
                    模板编辑（author review）
                          │
                          ▼
                    保存到 DB（schema_json）
                          ╳ ← 到此为止，无法预览生成结果
```

报告生成需要的是 `edges`（关系抽取边），不是 `sample_text`。抽取管线（NER → 关系抽取 → 文档分类）绑定在 extraction job 上下文中，与模板设计器独立。

### 10.2 预览需要什么

要在设计器中对指定源文档预览 E11/E12/E13 + LLM 叙事的完整结果，需要跑通以下管线的一个**轻量级同步子集**：

```
预览管线（新增）：
sample DOCX
    │
    ├──① parse_docx_structure → DocStructure
    │
    ├──② classify(structure, engine) → doc_class_iri
    │
    ├──③ extract_relationships(structure, engine, doc_class_iri)
    │      → edges[]
    │
    └──④ preview_generate(template, edges, db, engine)
           │
           ├── edges_to_facts(edges, engine) → Facts
           ├── _enrich_with_classification(facts)       ← E11
           ├── _load_rules(doc_class_iri)               ← E12 过滤
           ├── _evaluate_rules(rules, facts)            ← E12 评估
           ├── _evaluate_post_control(..., risk_policy) ← E13
           ├── validate_coverage(template, edges, ...)  ← 016 覆盖
           └── generate_section_narratives(             ← LLM 叙事
                   edges, template, client,
                   assessment_rows=..., manifest=...)
           │
           ▼
    PreviewResult {
        doc_class: DocClassification,
        assessment_rows: RiskRow[],
        coverage_manifest: CoverageManifest,
        section_narratives: [{section_id, title, text}],
        warnings: string[],
    }
```

### 10.3 后端 API

新增一个**设计时预览端点**，不创建 extraction job，不持久化报告：

```python
# api/ast_templates.py — 新增
@router.post("/{template_id}/preview-report")
def preview_report(
    template_id: str,
    db: Session = Depends(get_db),
) -> PreviewReportResponse:
    """设计时预览：用模板的样例文档跑全管线（E11→E12→E13→覆盖→LLM），
    返回预览结果。不创建 job，不持久化。

    前提：模板必须有 sample_text 或 sample_content_json。
    """
```

从模板的已存样例出发——不需要用户再次上传文档：

```python
    template_row = db.get(AstTemplate, template_id)
    if not template_row:
        raise HTTPException(404)

    sample_text = template_row.sample_text
    sample_content = template_row.sample_content_json
    if not sample_text:
        raise HTTPException(422, "模板无样例文档，无法预览")

    template = ReportTemplate.model_validate(template_row.schema_json)
    engine = get_loaded_engine()

    # ① 解析文档结构（复用 slot_suggester 的文本解析路径）
    structure = _text_to_structure(sample_text)

    # ② 分类
    doc_class = classify(structure, engine) if engine and engine.is_loaded else None
    doc_class_iri = doc_class["doc_class_iri"] if doc_class else None

    # ③ 关系抽取（轻量级：复用已有的 relation_extractor）
    edges = extract_relationships(structure, engine, doc_class_iri)

    # ④ 全管线预览
    generator = RiskReportGenerator(db, template)
    report, manifest = generator.generate_with_coverage(
        edges, source_filename="preview",
    )

    return PreviewReportResponse(
        doc_class=doc_class,
        assessment_rows=[asdict(r) for r in report.assessment_rows],
        coverage_manifest=manifest.to_dict(),
        section_narratives=report.section_narratives,
        equipment_tables=report.equipment_tables,
        subject_description=report.subject_description,
    )
```

### 10.4 前端交互

在模板编辑器中增加"预览报告"按钮——仅在模板有样例文档时可用：

```
┌─────────────────────────────────────────────────┐
│ 模板编辑器                                       │
│                                                 │
│ ┌──────────┐ ┌──────────┐ ┌──────────────────┐  │
│ │ AI 分析  │ │ 从样本   │ │ 预览报告          │  │
│ │          │ │ 生成     │ │ (E11/E12/E13+LLM) │  │
│ └──────────┘ └──────────┘ └──────────────────┘  │
│                                                 │
│ ┌ 编辑面板 ────────────────────────────────────┐ │
│ │ sections / coverage / slots                  │ │
│ └──────────────────────────────────────────────┘ │
│                                                 │
│ ┌ 预览面板 ─────────────────── (slide-over) ──┐ │
│ │                                              │ │
│ │ 文档分类：CMCReport (score: 12)              │ │
│ │                                              │ │
│ │ ▸ 风险评估矩阵                               │ │
│ │   ┌──────────┬────────┬────────┬──────┐      │ │
│ │   │ 危害     │ 初始   │ 残余   │ 状态 │      │ │
│ │   ├──────────┼────────┼────────┼──────┤      │ │
│ │   │ R-RA1 …  │ 中     │ 低     │ ✓    │      │ │
│ │   │ R-RA2 …  │ 待评估 │ 待评估 │ ⚠    │      │ │
│ │   └──────────┴────────┴────────┴──────┘      │ │
│ │                                              │ │
│ │ ▸ 覆盖验证摘要                               │ │
│ │   filled: 12  missing_required: 3            │ │
│ │   blank_optional: 5  manual: 2               │ │
│ │                                              │ │
│ │ ▸ 章节叙事（LLM 生成）                        │ │
│ │   § 风险评估对象基本描述                       │ │
│ │   "本品为XXX原料药，由YYY合成路线制备…"        │ │
│ │                                              │ │
│ │ ▸ 设备清单                                   │ │
│ │   642车间: 3台  646车间: 2台                  │ │
│ │                                              │ │
│ └──────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────┘
```

**关键 UX 决策**：

1. **预览是只读的**——结果不持久化、不可编辑。它的目的是让模板作者看到"如果用这个模板对这份源文档生成报告，结果会是什么样"。
2. **预览是异步的**——全管线（抽取 + 分类 + 规则评估 + LLM 叙事）可能需要 10-30 秒。用按钮触发，显示 loading 状态。
3. **预览标注数据来源**——每个值标注来源（`extraction` / `rule` / `llm` / `manual`），使模板作者能看到哪些能自动填充、哪些需要人工。

### 10.5 预览面板中的分层展示

预览面板按声明式规则层分区展示结果，使模板作者能看到每一层的贡献：

```
┌─ 预览报告 ──────────────────────────────────────┐
│                                                  │
│ ① 文档分类                                       │
│   doc_class: CMCReport | score: 12 | signals: …  │
│                                                  │
│ ② E11 药物分类推理                                │
│   ∃ hasInactivationProfile . HighPotency → TRUE   │
│   → drug_classes: [高活性药物] (E11 推理注入)      │
│                                                  │
│ ③ E12 风险评估（按 doc_class 过滤后的规则）        │
│   R-RA1 高活性成分交叉污染 → 中 (TRUE)            │
│   R-RA2 清洁验证充分性 → 低 (FALSE)               │
│   R-RA3 共线设备兼容性 → 待评估 (UNKNOWN)          │
│   (已过滤 2 条不适用于 CMCReport 的规则)           │
│                                                  │
│ ④ E13 冲突策略聚合                                │
│   risk_level: max_severity                        │
│   R-RA1: 中 → 后控措施 → 低 (可以接受)            │
│                                                  │
│ ⑤ 016 覆盖验证                                   │
│   describes→DrugProduct: ✓ filled                 │
│   hasSynthesisRoute→SynthesisRoute: ✗ missing     │
│   hasEquipment→Equipment: ✓ filled (5 台)         │
│                                                  │
│ ⑥ LLM 叙事                                       │
│   § 风险评估对象基本描述                           │
│   "本品为XXX原料药，采用YYY合成路线，                │
│    涉及ZZZ关键工艺参数…"                           │
│                                                  │
│ ⑦ 诊断                                           │
│   ⚠ R-RA3 缺 cleanability 数据，建议检查源文档     │
│   ⚠ hasSynthesisRoute 关系缺失，该章节将报遗漏     │
│                                                  │
└──────────────────────────────────────────────────┘
```

### 10.6 实现层次选择

预览有三种可能的实现粒度，按成本递增：

| 层次 | 内容 | 需要抽取管线 | 需要 LLM | 延迟 |
|---|---|---|---|---|
| **A. 结构预览** | 只展示 E12 规则列表 + 覆盖声明展开结果（不跑抽取） | ❌ | ❌ | <1s |
| **B. 确定性预览** | 跑抽取 → 评估 E11/E12/E13 → 覆盖验证（不跑 LLM） | ✅ | ❌ | 3-8s |
| **C. 全量预览** | B + LLM 叙事生成 | ✅ | ✅ | 10-30s |

**推荐**：先实现 **B（确定性预览）**，将 LLM 叙事作为可选追加（点击"生成叙事"按钮异步触发）。理由：

1. 模板作者最关心的是"覆盖了什么、漏了什么、规则怎么判"——这全在确定性层。
2. LLM 叙事是锦上添花（且受 feature flag 控制）；在设计时看不到也不影响判断模板质量。
3. 确定性预览可缓存（同一 template_id + 同一 sample → 同一结果），LLM 叙事不可缓存。

**层次 A 可作为零成本即时反馈**：用户修改 coverage 声明后，实时展示"这个 section 声明了哪些关系、展开后有哪些属性位置、匹配了哪些规则"，不需要后端调用。

### 10.7 与现有管线的复用

预览不新建抽取管线——它复用现有模块的**函数级**调用：

| 步骤 | 复用模块 | 注意 |
|---|---|---|
| 文档解析 | `docx_structure.parse_docx_structure` | 从 `sample_content_json` 重建 `DocStructure`，或从 `sample_text` 构造轻量结构 |
| 文档分类 | `document_classifier.classify` | 已有，纯函数 |
| 关系抽取 | `relation_extractor.extract_relationships` | 已有，需 engine |
| 事实构建 | `fact_bridge.edges_to_facts` | 已有，纯函数 |
| E11 评估 | `interpreter.evaluate` (本设计 §5.1 新增) | 新增 |
| E12 评估 | `_evaluate_rules` | 已有（加 doc_class 过滤） |
| E13 聚合 | `policy.resolve_risk_level` | 已有 |
| 覆盖验证 | `coverage_validator.validate_coverage` | 已有 |
| LLM 叙事 | `narrative_generator.generate_section_narratives` | 已有（可选，异步） |

不需要创建 extraction job、不需要持久化到 `generated_report` 表、不需要生成 DOCX 文件。

### 10.8 前端 schema

```typescript
// api.ts — 新增
export interface PreviewReportResult {
  doc_class: DocClassification | null;
  classification_enrichments: {     // E11 推理结论
    criterion_key: string;
    target_class: string;
    result: "TRUE" | "FALSE" | "UNKNOWN";
  }[];
  assessment_rows: {                // E12 + E13 结果
    hazid: string;
    contributing_factors: string;
    pre_control_level: string;
    post_control_level: string;
    control_measures: string;
    status: string;
  }[];
  filtered_rule_count: number;      // 被 doc_class 过滤掉的规则数
  coverage_manifest: CoverageManifestDTO;
  section_narratives: {             // LLM 叙事（可选，异步追加）
    section_id: string;
    title: string;
    text: string;
  }[];
  equipment_tables: Record<string, EquipmentEntry[]>;
  subject_description: string;
  warnings: string[];
}

export const previewReport = (templateId: string) =>
  fetchAPI<PreviewReportResult>(
    `/api/ast-templates/${templateId}/preview-report`,
    { method: "POST" },
  );
```

## 11. 待定

- **E12 `applicable_doc_classes` 的粒度**：按文档类型 IRI 过滤，还是按 rule_group 子分组（如 `risk_assessment.cmc`、`risk_assessment.batch`）？前者更灵活，后者更简单。建议先用文档类型 IRI，因为它与 `doc_class_iri` 自然对齐。
- **E11 判据的评估时机**：是在 `generate_with_coverage` 内每次评估，还是在文档分类时一次性评估并缓存到 extraction job？前者确保最新判据生效，后者更高效。建议前者（运行时评估），因为 E11 判据集很小（~10 条），evaluate 开销可忽略。
- **叙事上下文的 token 预算**：注入规则结果和覆盖状态会增加 LLM prompt 长度。需要在 `_format_rule_results` 中设置截断（如最多 30 条 RiskRow、每条覆盖位置 80 字符），防止超出本地 LLM 的上下文窗口。
- **预览缓存策略**：确定性预览结果（B 层）可按 `(template.schema_json hash, sample_text hash)` 缓存；模板修改后失效。LLM 叙事（C 层）不缓存。
- **预览与正式生成的一致性保证**：预览使用与 `generate_with_coverage` 完全相同的代码路径（调用同一方法），仅不持久化。需在测试中断言 `preview_report` 与 `generate_risk_report` 对相同输入产出相同的确定性结果。
