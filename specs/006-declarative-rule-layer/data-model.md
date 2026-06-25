# Phase 1 Data Model: 推理引擎规则层声明式化

**Date**: 2026-06-24 | **Plan**: [plan.md](./plan.md) | **Research**: [research.md](./research.md)

本文件定义：(A) 三张新增可编辑 T-Box 元数据表（E11/E12/E13）；(B) 它们投影进权威 TTL 的公理映射；(C) 解释器消费的「受限模式词汇」数据契约；(D) 四组硬编码规则（R-DC/R-ED/R-SC/R-CP）→ 声明式制品的逐条迁移表；(E) 新增本体词项（`hasBetaLactamRing`、`AntineoplasticDrug`）。

所有新表复用既有 `NamedEntityMixin`(=`VersionMixin`+`TimestampMixin`) / `VersionMixin`，纳入既有 `OntologyChangeLog`/`OntologyRelease` 批次与审计链（宪章 III）；编辑/发布受 `require_role(senior_analyst)`（宪章 安全 / FR-016）。

---

## A. 新增可编辑 T-Box 元数据表

### E11 `ontology_classification_criterion`（分类判据）

把「底层属性条件 → 风险分类」显式化的声明式单元。承载 R-DC1~4 及激素/青霉素/肿瘤的**充要条件定义型**判据（投影为目标类的 `owl:equivalentClass`）。

继承 `VersionMixin` + `TimestampMixin`（非 `NamedEntityMixin`：判据本身不是 IRI-bearing 受管实体，而是挂在目标类上的类表达式）。

| 字段 | 类型 | 约束 | 说明 |
|---|---|---|---|
| `id` | GUID | PK | 主键；派生确定性 BNode id（见 B 节） |
| `criterion_key` | String(50) | unique, not null | 稳定业务键，如 `R-DC1`、`HormonalDrug-suff`（黄金基线/溯源引用） |
| `target_class_id` | GUID FK→`ontology_class.id` | not null | 充要条件归属的目标受管类（如 CytotoxicDrug） |
| `logic_role` | String(20) | not null, ∈{`defined`,`production`} | `defined`=充要(equivalentClass)；`production`=产生式(见 E12)。E11 仅存 `defined` |
| `pattern` | JSON | not null | 类表达式 AST（受限模式词汇，见 C 节） |
| `regulation_ref` | String(200) | nullable | 法规出处，如 `CFDI 2023-03 §3.2`（投影为 `dct:source`/`rdfs:comment`） |
| `version` | Integer | not null, default 1 | 乐观并发 |
| `status` | String(20) | not null, default `draft` | draft/in_review/published/archived |
| `is_disabled` | Boolean | default false | 软删除；停用则不投影进 TTL |

**校验规则**：
- `pattern` MUST 通过受限模式词汇 schema 校验（C 节）；未通过 → 阻断发布（FR-014）。
- `pattern` 引用的 property/filler/对齐 IRI MUST 在本体中可解析（一致性门禁）。
- 外部对齐取值（ChEBI/ATC 本地名）MUST 已字节级核实（research.md R3，`alignment_verified` 标志见 E13/集成层）；未核实 → 阻断发布（FR-014）。

---

### E12 `ontology_decision_rule`（决策规则）

超出充要定义表达力的**产生式规则**（设备专用化 R-ED、场景识别 R-SC、污染路径 R-CP）。投影为权威 TTL 的命名资源 `slpra:DecisionRule_<key>`。

继承 `NamedEntityMixin`（IRI-bearing：每条规则是受管命名主语，故有 `slpra_iri`/`label`/`comment`）。

| 字段 | 类型 | 约束 | 说明 |
|---|---|---|---|
| `id` / `slpra_iri` / `label` / `comment` / `version` / `status` / `is_disabled` … | — | 继承自 `NamedEntityMixin` | `slpra_iri` = `…/slpra/core/DecisionRule_<rule_key>` |
| `rule_key` | String(50) | unique, not null | 稳定业务键，如 `R-ED1`、`R-SC4`、`R-CP1` |
| `rule_group` | String(40) | not null, ∈{`equipment_dedication`,`scenario_identification`,`contamination_risk`} | 对应原 `rules/*.py` 分组，引擎据此装配 |
| `antecedent` | JSON | not null | 前件条件（受限谓词词汇 AST，C 节） |
| `consequent` | JSON | not null | 结论字典，结构与原 `RuleResult.conclusion` 同构（如 `{"requires_dedication": true, "unconditional": true}`） |
| `priority` | Integer | default 100 | 同组内求值次序（小先）；冲突由 E13 策略最终裁决 |
| `regulation_ref` | String(200) | nullable | 法规出处 → `dct:source` |

**校验规则**：
- `consequent` 的键集 MUST ⊆ 引擎识别的结论词汇（`add_class`/`requires_dedication`/`requires_independent_hvac`/`requires_inactivation_validation`/`scenario`/`risk_level`/`unconditional`/`requires_*`），保证 `AssessmentResult` 形状不变（plan「对外形状不变」硬约束）。
- `antecedent` 引用的类名 MUST 在本体可解析。

---

### E13 `ontology_conflict_policy`（冲突消解策略）

把多条矛盾结论聚合为单一结论的声明式策略，外化 `resolve_dedication_conflict`/`resolve_risk_level` 的优先格与覆盖方向。投影为 `slpra:ConflictPolicy_<dimension>`。

继承 `NamedEntityMixin`。

| 字段 | 类型 | 约束 | 说明 |
|---|---|---|---|
| `id` / `slpra_iri` / `label` / `version` / `status` / `is_disabled` … | — | 继承自 `NamedEntityMixin` | `slpra_iri` = `…/slpra/core/ConflictPolicy_<dimension>` |
| `dimension` | String(40) | unique, not null, ∈{`dedication`,`risk_level`} | 适用维度 |
| `strategy` | String(30) | not null, ∈{`safety_override`,`max_severity`} | 聚合算子；`dedication`→`safety_override`，`risk_level`→`max_severity` |
| `priority_lattice` | JSON | nullable | 取严序，如 `{"HighRisk":3,"MediumRisk":2,"LowRisk":1}`（外化 `resolve_risk_level` 常量） |
| `override_direction` | String(20) | nullable, ∈{`restrictive_wins`,`permissive_wins`} | 覆盖方向；安全优先=`restrictive_wins`（`requires_dedication=true` 胜） |
| `regulation_ref` | String(200) | nullable | 如 `CFDI 2023-03 §13.4` |

**校验规则**：`strategy=max_severity` MUST 提供 `priority_lattice`；`strategy=safety_override` MUST 提供 `override_direction`。

---

## B. TTL 投影映射（`build_managed_graph` 扩展 + `surgical_merge` 对象形态感知）

### B1 E11 → `owl:equivalentClass` 充要公理 + OWL2 datatype facet

每条 `logic_role=defined` 判据，在 `build_managed_graph` 中为其 `target_class` 发出：

```
<target_class> owl:equivalentClass _:c<criterion.id> .
_:c<criterion.id> <…类表达式三元组（由 pattern AST 展开）…> .
```

- BNode **确定性**派生：`BNode(f"c{criterion.id.hex}")`，仿 E5 的 `BNode(f"r{r.id.hex}")`，保证跨发布 round-trip 稳定、写前 diff 干净。
- 数值阈值用 **OWL2 datatype facet**：R-DC3 `sensitizationLevel > 3` →
  `owl:onProperty slpra-drug:sensitizationLevel ; owl:someValuesFrom [ rdf:type rdfs:Datatype ; owl:onDatatype xsd:integer ; owl:withRestrictions ( [ xsd:minExclusive 3 ] ) ]`。
- 类并集 R-DC2 `OEB4 ⊔ OEB5` → `owl:someValuesFrom [ owl:unionOf ( slpra-drug:OEB4 slpra-drug:OEB5 ) ]` on `hasOEBClassification`。

**`surgical_merge` 改造（research.md R2，宪章 II 关键风险点）**：
1. `MANAGED_PREDICATES` **不**新增 `owl:equivalentClass`（避免误删命名 IRI 外部对齐）。
2. 改为**对象形态感知**剥离：对受管主语 `s`，仅当 `(s, owl:equivalentClass, o)` 且 `o` 为 **BNode** 时剥离，并**递归回收**该 BNode 子图（避免孤儿三元组累积）；`o` 为**命名 IRI**（外部对齐）时**逐字保留**。
3. 重新发出 managed 图中的 BNode 类表达式。

### B2 E12 → `slpra:DecisionRule_*` 命名资源

```
slpra:DecisionRule_R-ED1 a slpra:DecisionRule ;
    rdfs:label "…" ; slpra:ruleGroup "equipment_dedication" ;
    slpra:antecedent "<json>" ; slpra:consequent "<json>" ;
    slpra:priority 100 ; dct:source "CFDI 2023-03 §4.4…" .
```

- 全新命名主语（手工 TTL 中不存在），无外部内容可误删 → 安全。
- 新增受管谓词 `slpra:ruleGroup`/`slpra:antecedent`/`slpra:consequent`/`slpra:priority` 纳入 `MANAGED_PREDICATES`，使规则可重发不累积（这些谓词只作用于 `slpra:DecisionRule_*` 主语，与 E1-E5 主语不交叠）。

### B3 E13 → `slpra:ConflictPolicy_*` 命名资源

```
slpra:ConflictPolicy_dedication a slpra:ConflictPolicy ;
    slpra:dimension "dedication" ; slpra:strategy "safety_override" ;
    slpra:overrideDirection "restrictive_wins" ; dct:source "CFDI 2023-03 §13.4" .
```

同 B2，新增受管谓词 `slpra:dimension`/`slpra:strategy`/`slpra:overrideDirection`/`slpra:priorityLattice`。

> `dct:` 前缀（`http://purl.org/dc/terms/`）须在 `build_managed_graph` 绑定并随导出写入 `@prefix`。

---

## C. 解释器「受限模式词汇」数据契约

`reasoning/interpreter.py` 对 `pattern`/`antecedent` AST 求值。词汇受限但对当前规则集充分（research.md R1）。每个节点 `{"op": …, …}`：

| op | 字段 | 语义 | 来源规则 |
|---|---|---|---|
| `some_values_from` | `property`, `filler_class` | API 经 `property` 关联个体属 `filler_class`（或其子类） | R-DC1（`hasToxicityProfile`→`GenotoxicityProfile`） |
| `class_membership` | `property`, `classes[]` | 关联个体的类 ∈ `classes` 并集 | R-DC2（`hasOEBClassification`→{OEB4,OEB5}） |
| `datatype_facet` | `property`, `cmp`(`gt`/`ge`/`lt`/`le`/`eq`), `value` | 数值 facet 比较 | R-DC3（`sensitizationLevel gt 3`） |
| `boolean_has_value` | `property`, `value` | 布尔 data hasValue | R-DC4（`hasBetaLactamRing = true`） |
| `external_alignment` | `property`, `alignment` (ChEBI/ATC IRI) | API 类经对齐属某外部类别 | 激素/青霉素/肿瘤 |
| `class_present` | `class` | drug_classes 含某类（产生式前件） | R-ED/R-SC 大多数 |
| `literal_eq` / `literal_cmp` | `key`, `value`/`cmp` | 标量字段比较 | R-CP（pathway/pde/cleanability/form） |
| `and` / `or` | `operands[]` | 逻辑组合（intersectionOf/unionOf） | R-ED2/4/5、R-SC、R-CP |

**OWA 语义（FR-010 / SC-006）**：求值返回三态 `{TRUE, FALSE, UNKNOWN}`。判定所需属性缺失 → `UNKNOWN`，**MUST NOT** 坍缩为 `FALSE`；分类只在 `TRUE` 时点亮，`UNKNOWN` 不点亮也不断言负类。这正是「否→未知」预期改进的语义落点（research.md R4）。

---

## D. 规则 → 声明式制品逐条迁移表（零回归基线锚点）

### D1 分类判据 R-DC（→ E11 `defined`）

| key | 原逻辑 | pattern（受限词汇） | 目标类 | 法规 |
|---|---|---|---|---|
| R-DC1 | API toxicity 含 GenotoxicityProfile | `some_values_from(hasToxicityProfile, GenotoxicityProfile)` | CytotoxicDrug | §3.2 |
| R-DC2 | OEB ∈ {4,5} | `class_membership(hasOEBClassification, [OEB4,OEB5])` | HighActivityDrug | §3.3 |
| R-DC3 | sensitization_level > 3 | `datatype_facet(sensitizationLevel, gt, 3)` | HighSensitizingDrug | §3.4 |
| R-DC4 | API has_beta_lactam_ring | `boolean_has_value(hasBetaLactamRing, true)` | BetaLactamDrug | §4.4 |
| HormonalDrug-suff | 激素信号（升级可推断） | `external_alignment(hasActiveIngredient, ChEBI/ATC 激素)` | HormonalDrug | §4.3 |
| PenicillinDrug-suff | 青霉素信号（升级可推断） | `external_alignment(hasActiveIngredient, ChEBI/ATC 青霉素)` | PenicillinDrug | §4.4 |
| AntineoplasticDrug-suff | 肿瘤（新增可推断） | `external_alignment(hasActiveIngredient, ChEBI:35610/ATC L01)` | **AntineoplasticDrug（新增类）** | §… |

### D2 决策规则 R-ED/R-SC/R-CP（→ E12 `production`）

| key | group | antecedent | consequent | 法规 |
|---|---|---|---|---|
| R-ED1 | equipment_dedication | `class_present(PenicillinDrug)` | `{requires_dedication:true, unconditional:true}` | §4.4 |
| R-ED2 | equipment_dedication | `and(class_present(CytotoxicDrug), class_present(NonInactivatable))` | `{requires_dedication:true}` | §4.2 |
| R-ED3 | equipment_dedication | `and(class_present(BiologicalProduct), literal_eq(hasPrionRisk,true))` | `{requires_dedication:true}` | §4.5 |
| R-ED4 | equipment_dedication | `and(class_present(CytotoxicDrug), or(class_present(HeatInactivatable),class_present(ChemicalInactivatable)))` | `{requires_dedication:false, requires_inactivation_validation:true}` | §4.2(c) |
| R-ED5 | equipment_dedication | `and(class_present(HighActivityDrug), class_present(OEB5))` | `{requires_dedication:true}` | §4.6 |
| R-ED6 | equipment_dedication | `class_present(HormonalDrug)` | `{requires_independent_hvac:true}` | §4.3 |
| R-SC1 | scenario_identification | `and(class_present(ClinicalTrialDrug), class_present(CommercialDrug)@co)` | `{scenario:ClinicalWithCommercialScenario, requires_enhanced_documentation:true}` | §6.1 |
| R-SC2 | scenario_identification | `and(class_present(CytotoxicDrug), is_shared)` | `{scenario:CytotoxicSharedLineScenario}` | §6.2 |
| R-SC3 | scenario_identification | `and(class_present(HormonalDrug), is_shared)` | `{scenario:HormonalSharedLineScenario, requires_independent_hvac:true}` | §6.3 |
| R-SC4 | scenario_identification | `class_present(PenicillinDrug)` | `{scenario:PenicillinSharedLineScenario, requires_dedication:true}` | §6.4 |
| R-SC5 | scenario_identification | `and(class_present(BiologicalProduct), is_shared)` | `{scenario:BiologicSharedLineScenario, requires_tse_assessment:true}` | §6.5 |
| R-SC6 | scenario_identification | `and(class_present(HighActivityDrug), is_shared)` | `{scenario:HighPotencySharedLineScenario}` | §6.6 |
| R-SC7 | scenario_identification | `and(class_present(SterileDrugProduct), is_shared)` | `{scenario:SterileDrugSharedLineScenario, requires_aseptic_integrity:true}` | §6.7 |
| R-SC8 | scenario_identification | `and(is_shared, literal_cmp(source_form≠target_form))` | `{scenario:MultiDosageFormScenario}` | §6.8 |
| R-CP1 | contamination_risk | `and(literal_eq(pathway,residue), literal_cmp(pde<0.01), literal_cmp(cleanability<3))` | `{risk_level:HighRisk}` | §5.1 |
| R-CP2 | contamination_risk | `and(literal_eq(pathway,airborne), literal_eq(dosage_form,powder), literal_eq(area_type,general))` | `{risk_level:HighRisk}` | §5.3 |
| R-CP3 | contamination_risk | `and(literal_eq(pathway,residue), literal_eq(dosage_form,solution), literal_cmp(cleanability>4))` | `{risk_level:LowRisk}` | §5.1 |
| R-CP4 | contamination_risk | `and(literal_eq(pathway,confusion), literal_cmp(source_form=target_form))` | `{risk_level:MediumRisk}` | §5.4 |

> 该表是零回归黄金基线（research.md R4）的逐用例锚点：重构后解释器对每行求值的 `fired`/`conclusion` MUST 与原函数等价（OWA「否→未知」例外除外）。

### D3 冲突策略（→ E13）

| dimension | strategy | 参数 | 原函数 | 法规 |
|---|---|---|---|---|
| dedication | safety_override | `override_direction=restrictive_wins` | `resolve_dedication_conflict`（任一 True 胜） | §13.4 |
| risk_level | max_severity | `priority_lattice={HighRisk:3,MediumRisk:2,LowRisk:1}` | `resolve_risk_level`（取最严） | §13.4 |

---

## E. 新增本体词项（经外科式合并写入 `slpra-drug.ttl`，非手改）

### E1 `hasBetaLactamRing` 数据属性（FR-005）— 经 E3 `ontology_data_property` 投影

```
slpra-drug:hasBetaLactamRing a owl:DatatypeProperty ;
    rdfs:domain slpra-drug:ActivePharmaceuticalIngredient ;
    rdfs:range xsd:boolean ;
    rdfs:label "has beta-lactam ring"@en ; rdfs:label "含β-内酰胺环"@zh .
```
（域取 API，与 R-DC4 读 `api_props` 一致。）

### E2 `AntineoplasticDrug` 类（FR-007）— 经 E1 `ontology_class` 投影

```
slpra-drug:AntineoplasticDrug a owl:Class ;
    rdfs:subClassOf slpra-drug:DrugProduct ;
    rdfs:label "Antineoplastic Drug"@en ; rdfs:label "肿瘤药物"@zh .
```
- 充要条件由 E11 `AntineoplasticDrug-suff` 提供（`owl:equivalentClass` BNode 类表达式）。
- 外部对齐 `ATC L01 / ChEBI:35610` 经既有外部对齐机制（E6 映射 / `slpra-integration.ttl`）写为**命名 IRI** `owl:equivalentClass`/`skos:*`（非受管，穿过合并保留）。
- **⚠ 本地名/编号实现前 MUST 字节级核实**（research.md R3，记忆 [[idmp-o-rebased-to-authoritative-namespaces]]、[[external-alignment-must-use-nonmanaged-predicates]]）。

---

## 实体关系图（文字）

```
ontology_class (E1) ──< target_class ── classification_criterion (E11) ──pattern──▶ [受限模式 AST]
       ▲                                                                                │
       └── owl:equivalentClass _:cN ◀──── build_managed_graph ◀───────────────────────┘

decision_rule (E12) ──rule_group──▶ engine 装配  ──▶ slpra:DecisionRule_* (named, managed)
conflict_policy (E13) ──dimension──▶ policy.py    ──▶ slpra:ConflictPolicy_* (named, managed)

全部 E11/E12/E13 变更 ──▶ OntologyChangeLog ──▶ OntologyRelease(SHA) （宪章 III 批次/审计）
```

## 状态流转

E11/E12/E13 复用既有生命周期：`draft → in_review → published`（`archived` 为停用）。仅 `published` 且 `is_disabled=false` 的行投影进权威 TTL。乐观并发经 `version`（写时校验，冲突 409）。
