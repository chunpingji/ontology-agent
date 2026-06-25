# T-Box 领域专家自助维护助手（LLM Agent）设计文档

**状态**: Draft（设计评审用，未立项）
**日期**: 2026-06-24
**作者**: 本体团队
**关联**: `specs/006-declarative-rule-layer/`（规则层声明式化，本助手的直接上游）、能力一 T-Box 工作台（`frontend/.../ontology/page.tsx`）、能力二 LLM 抽取（`backend/app/services/extraction/`）

> 本文是**设计文档**，不是实现计划。目的是在评审通过后据此立为 spec-kit 新特性（拟 `007`），再走 specify→clarify→plan→tasks。文末「待决问题」即 `/speckit-clarify` 的候选输入。

---

## 1. 背景与问题

### 1.1 痛点

本体维护的工作量与难度过高，**最大瓶颈是：领域 / 法规事务专家无法自助维护 T-Box**，所有改动都得经本体工程师转译，工程团队成为单点。

根因是一堵**形式化的墙**。今天的 T-Box 工作台直接把这些概念暴露给维护者：

- IRI、BFO 上位类（`bfo_category`）
- OWL 约束（`some` / `only` / `exactly` / `min` / `max`）
- `owl:equivalentClass` 充要类表达式（006 引入）
- `domain` / `range`、`inverseOf`、函数性/对称性/传递性
- 006 的受限模式 AST（`datatype_facet` / `some_values_from` / `external_alignment` …）

而法规事务专家的心智模型是领域语言：

> 「致敏级别超过 3 级算高致敏药物，依据 CFDI 2023-03 §3.2」
> 「青霉素必须专用化」
> 「肿瘤药对应 ATC L01」

专家不会、也不该去操作 BNode 类表达式和 IRI。**这堵墙就是自助维护进不来的唯一原因。**

### 1.2 助手的角色

一句话：**领域语言 ↔ 形式化制品的双向翻译器 + 起草器 + 用领域语言把改动讲回去的解释器**。

它不是一个新的"真理来源"，而是一个**自然语言驱动的工作台前端**——调用的是工作台按钮今天已经在调的同一套 `OntologyMetaStore` CRUD。

### 1.3 与 006 的关系（重要）

006 的 US3（`specs/006-declarative-rule-layer/tasks.md` 的 T035–T042）计划做：

- E11/E12/E13（分类判据 / 决策规则 / 冲突策略）的 Pydantic schema 与 CRUD（T035–T039）
- 把它们纳入 ChangeLog 批次（T040）
- 前端"声明式规则**最小**编辑入口"（T042，明确写"不做通用类表达式编辑器"）

**本助手是 T042「最小编辑入口」的进阶形态**——把"受限模式受控表单"升级为"自然语言 + 提案卡"。因此：

- **依赖**：助手对判据/规则的读写依赖 006 US3 的 CRUD（T035–T039）与表结构（E11/E12/E13，已建表 T004/T005）。
- **协同**：建议 007 立项时把 T042 重定义为"由本助手实现"，避免重复造一个受控表单又造一个对话面板。

---

## 2. 设计目标与非目标

### 2.1 目标

- **G1**：法规/领域专家无需理解 IRI/OWL/AST，即可经自然语言完成 T-Box 的**读、问、改**。
- **G2**：低结构风险的变更（改阈值、改标签、启停判据）专家可**全程自助**（起草 + 发布）。
- **G3**：高结构风险的变更（新类、外部对齐、约束、BFO 挂位）专家可**起草**，由工程师**联签**发布。
- **G4**：每一次改动都经**既有门禁**（一致性校验、角色门禁、外科式合并、三元组 diff、批次审计、Git SHA），**零新增权威路径**。
- **G5**：专家审批前能看到**领域语言的改动讲回**与**行为影响预览**（不只是公理），即"知情同意"。

### 2.2 非目标

- **不**做通用 OWL 类表达式可视化编辑器。
- **不**让 LLM 直写权威 TTL 或绕过 `validate()` / 角色门禁。
- **不**改既有评估流程、MACO 计算、整体导航布局。
- **不**引入并行推理框架（宪章 V）；AST 仍由 006 的 Python 解释器执行。
- **不**把 LLM 输出当作真理：LLM 只产**候选**，对错由确定性代码与人裁决。

---

## 3. 核心原则

### P1 — Agent 出提案，既有门禁裁决，绝不新开写入路径

```
专家自然语言意图
  → agent 起草，映射到既有 Create/Update schema
     (ClassCreate / ClassificationCriterion / DataPropertyCreate / MappingCreate …)
  → 以 status=draft 暂存到 OntologyMetaStore（= 工作台按钮调的同一套 CRUD，挂在一个未发布 Release 下）
  → store.validate() 阻断性门禁（含 006 的 alignment_verified 核实 + validate_pattern AST 校验）
  → export_diff() 三元组级 diff + 领域语言讲回 + run_assessment 影响预览
  → senior_analyst 经既有 submit/publish → surgical_merge + Git SHA + 审计批次
```

这与能力二抽取流水线「LLM 候选 → 人审 → 提交」完全同构。

### P2 — 分层自助（安全的核心旋钮）

不是所有改动等风险。按"是否改变本体结构"二分：

| 层级 | 涵盖变更 | 谁能发布 | 守法依据 |
|---|---|---|---|
| **Tier 1（专家全自助）** | 改数值阈值、改 label/comment/定义、加 `regulation_ref`、启停已有判据/规则 | 专家（senior_analyst）起草 + 发布 | 只动参数不动结构；仍过 `validate()` + 审计 |
| **Tier 2（专家起草 + 工程师联签）** | 新类、新数据属性、**外部对齐**(ChEBI/ATC)、新约束、BFO 挂位、新形态决策规则 | 专家起草 → 工程师审三元组 diff → 发布 | 结构性变更可能动 BFO/外部对齐 → 守宪章 II NON-NEGOTIABLE |

层级由 agent 依"提案涉及哪些操作"**自动判定**（见 §6.3），不靠专家自觉。

### P3 — 知情同意：讲回 + 影响预览

专家既是发起人又是审批人，所以必须让他**真正理解所批的东西**。每个提案卡必含三层：

1. **领域语言讲回**："你在加一条规则：活性成分致敏级别 > 2 即判为高致敏药物，依据 CFDI 2023-03 §3.2。"
2. **行为影响预览**："这会让现有 4 个药物**新被判为**高致敏。"（复用 `run_assessment` + 006 golden-master，§7）
3. **三元组级 diff**：给工程师/审计用的 `export_diff()` 原样输出（Tier 2 必看）。

### P4 — 优雅降级（复用 `extract_with_fallback` 范式）

LLM 不可用时，助手面板降级为"不可用"提示，**工作台手工编辑路径完全不受影响**。助手是增量入口，不是必经路径。

---

## 4. 总体架构

```
┌─────────────────────────── 前端（Next.js）────────────────────────────┐
│  T-Box 工作台 page.tsx                                                   │
│  ├─ 既有：类层次树 / 编辑面板（基本·关系·属性·映射·操作）/ 图谱 / TtlToolbar │
│  └─ 新增：助手面板（AssistantPanel）                                      │
│       ├─ 对话区（chat）：问答 / 解释 / 自由意图                            │
│       ├─ 引导区（guided）：结构化编辑场景（改阈值 / 建判据）的受控表单       │
│       └─ 提案卡（ProposalCard）：讲回 + 影响预览 + diff + 「应用为草稿」     │
└───────────────────────────────┬──────────────────────────────────────┘
                                 │  POST /assistant/converse (SSE)
                                 │  POST /assistant/proposals/{id}/apply
┌────────────────────────────────▼─────────────────────────────────────┐
│  后端（FastAPI）  app/api/ontology.py 内新增 assistant 子路由            │
│                                                                        │
│  TBoxAssistantService（新增，薄翻译层）                                  │
│   ├─ anthropic SDK（已装）+ tool use + structured output                │
│   ├─ 工具：read_tbox / search_external_vocab / draft_proposal           │
│   └─ 模型：Opus（翻译/AST合成/BFO挂位推理重）                            │
│                                                                        │
│  复用既有（零改造或小改）：                                              │
│   ├─ OntologyMetaStore：CRUD + create/submit/publish_release + audit    │
│   ├─ store.validate()：一致性门禁（含 006 alignment_verified）           │
│   ├─ interpreter.validate_pattern()：AST 受限词汇闸门                    │
│   ├─ ttl_merge.export_diff()：三元组级 diff                             │
│   ├─ run_assessment()：影响预览模拟                                      │
│   └─ require_role(senior_analyst) / X-User·X-Role 身份                  │
└────────────────────────────────┬─────────────────────────────────────┘
                                  │  proposal 落 status=draft，挂未发布 Release
┌────────────────────────────────▼─────────────────────────────────────┐
│  PostgreSQL（E1–E6 / E11–E13 元数据） + 权威 TTL（surgical_merge 回写）  │
└────────────────────────────────────────────────────────────────────────┘
```

**核心架构判断**：`TBoxAssistantService` 是一层**薄翻译器**。它唯一会"写"的动作是 `draft_proposal`，而 draft 落库走的是 `OntologyMetaStore` 现成的 `create_*` / `update_*`（带 `status=draft`）。提案**不**直写 TTL；TTL 写入仍只发生在既有 `publish_release` 的外科式合并里。

---

## 5. 安全模型

### 5.1 三道既有门禁（提案必过）

1. **AST 受限词汇闸门** — `interpreter.validate_pattern()`：LLM 合成的 `pattern`/`antecedent` 若含未知 `op`、缺字段、错 `cmp`，直接 `PatternError` 拒绝。LLM 的自由发挥被钉死在 10 个 op 的白名单内。
2. **一致性门禁** — `store.validate()`：判据 `target_class` 可解析、property/filler 在启用类中、外部对齐须在 `VERIFIED_EXTERNAL_ALIGNMENTS`（006 FR-014）、基数 min≤max、引用未禁用实体……阻断性问题挡住 submit/publish。
3. **角色门禁** — `require_role(ROLE_SENIOR_ANALYST)`：`apply` 与 `publish` 仍需 senior_analyst；身份经网关头 `X-User`/`X-Role` 注入（SSE 路径用 `get_current_user_sse` 回退 query 参数）。

### 5.2 Tier 2 联签机制

提案被判为 Tier 2 时，`apply` 落库的同时把 Release 标记 `needs_engineer_review=true`，`publish_release` 在该标记下**阻断**，直至工程师清除。"工程师"的落地有三选项（待决，§12 Q1）：

- **(a) 新增 `ontology_engineer` 角色**：清除 `needs_engineer_review` 仅限该角色。最清晰。
- **(b) 双人规则**：Tier 2 Release 的 `published_by` MUST ≠ `created_by`（起草人）。复用现有角色，零迁移。
- **(c) 显式"工程师已审"审计动作**：发布前须有一条 `release.engineer_cleared` 审计。

推荐 **(b) 双人规则**起步（零角色迁移、满足"两双眼睛"），后续按需升级到 (a)。

### 5.3 审计与可追溯（宪章 III）

- 对话本身不写权威数据，但每次 `apply` 写一条审计：`action="assistant.apply"`，`details={proposal_id, tier, intent_summary, model, confidence}`。
- 提案落库后即是普通 draft 实体，自动纳入既有 `OntologyChangeLog` 批次 + `OntologyRelease`（`ttl_commit_sha`）归档——与手工编辑同链，无特例。
- 这样"哪条规则是 AI 起草、谁批准、对应哪个 Git SHA"全程可查。

### 5.4 幻觉防护（最关键的对外保真风险）

| 风险 | 防护 |
|---|---|
| 幻觉一个不存在的 ChEBI/ATC IRI | `search_external_vocab` **确定性抓取** OLS/OBO PURL/WHOCC，字节核实本地名 + 回填权威 label；未核实 → `validate()` 阻断（006 FR-014）。LLM 只"猜候选"，对错由代码判。 |
| 编造 AST op / 字段 | `validate_pattern()` 白名单拒绝（§5.1.1） |
| 改错对象（张冠李戴） | 提案卡显示**领域讲回 + 影响预览**，专家在领域语言层即可发现"这不是我要的" |
| 版本竞态 | 提案携 `expected_version`；落库走既有 CAS，冲突 409 → 复用 `use-version-conflict`/`ConflictDialog` |
| 过度信任 | Tier 分层 + Tier 2 强制联签 + 影响预览，结构性错误不会单人静默发布 |

---

## 6. 交互设计（对话 + 引导式结合）

### 6.1 助手面板的两个模式

- **对话区（chat）**：处理"问答 / 解释 / 模糊自由意图"。零风险高频场景（解释规则、查为什么、探索"如果我想…该怎么做"）走这里。
- **引导区（guided）**：处理"结构化编辑"。当意图收敛到一个明确操作（改阈值 / 建判据 / 加属性），agent 把它渲染成一张**受控提案卡**而非继续自由对话——减少幻觉空间、让改动可逐字核对。

二者用同一个 `converse` 后端；区别只在前端呈现：自由文本回答 vs 提案卡。

### 6.2 提案卡（ProposalCard）解剖

```
┌─ 提案 #ab12  ·  Tier 1  ·  置信度 0.92 ───────────────────────┐
│ 你想做：把"高致敏"判定的致敏级别阈值从 3 改为 2              │  ← 意图
│                                                              │
│ 我的理解（改动讲回）：                                        │  ← P3.1
│   规则 R-DC3「高致敏药物」充要条件                            │
│   原：致敏级别 > 3   →  新：致敏级别 > 2                      │
│   依据：CFDI 2023-03 §3.4                                    │
│                                                              │
│ 影响预览：                                                   │  ← P3.2
│   现有用例中，2 个药物将【新被判为】高致敏（致敏级别=3）        │
│   无既有判定被撤销；与黄金基线其余结论一致                     │
│                                                              │
│ 形式化改动（工程师视角，可折叠）：                            │  ← P3.3
│   ontology_classification_criterion R-DC3 .pattern           │
│     datatype_facet(sensitizationLevel, gt, 3 → 2)            │
│   三元组 diff：- :HighSensitizingDrug owl:equivalentClass …  │
│                + :HighSensitizingDrug owl:equivalentClass …  │
│                                                              │
│ 一致性校验：✅ 通过（0 阻断 / 0 警告）                        │  ← §5.1.2
│                                                              │
│        [应用为草稿]   [继续讨论]   [放弃]                      │
└──────────────────────────────────────────────────────────────┘
```

"应用为草稿"后，提案落库为 draft 实体，专家转到既有 `TtlToolbar` 走 submit→publish（Tier 1 可径直发布；Tier 2 卡片顶部会标"需工程师联签"，发布按钮在 `needs_engineer_review` 下置灰）。

### 6.3 Tier 自动判定

agent 在 `draft_proposal` 阶段按操作类型给提案打 Tier：

```
operations 仅含 {criterion.update(仅 pattern 数值/cmp), *.update(label/comment), *.enable/disable}
    → Tier 1
operations 含 {class.create, data_property.create, mapping.create(外部对齐),
              restriction.create, class.update(bfo_category/parent), criterion.create(新 target_class)}
    → Tier 2
```

判定结果写入提案，不可由对话覆盖（防 prompt 注入降级）。

---

## 7. 影响预览机制（信任的核心）

复用 006 已有资产，**无需新建推理能力**：

- **样本来源**：006 的 `backend/tests/test_reasoning/fixtures/golden_master.json` 回归矩阵 + 当前库内代表性 ABox。
- **机制**：`draft_proposal` 在内存中对"应用提案后的判据集"跑 `run_assessment`，与"应用前"逐用例 diff。
- **呈现**：
  - `newly_classified`：新点亮的分类（如"2 个药物新判为高致敏"）
  - `withdrawn`：被撤销的判定（阈值收紧/放宽都要显示）
  - `changed_decisions`：专用化 / 风险等级的变化
  - `golden_master_delta`：与基线差异，且**标注每条差异是否属 006 预声明的"否→未知"预期改进**（FR-012 口径）
- **价值**：专家看到的是**行为后果**而非公理。阈值从 3 改到 2"会影响谁"，比"`gt 3` 变 `gt 2`"直观一个量级。

> 注意：影响预览是**只读模拟**，在事务外或回滚事务中跑，绝不落库。

---

## 8. 数据流与接口

### 8.1 新增端点（挂在 `app/api/ontology.py` 的 assistant 子路由）

| 方法 | 路径 | 角色 | 职责 |
|---|---|---|---|
| POST | `/ontology/assistant/converse` | 任一有效角色（读）；SSE | 流式对话；返回自由文本回答或 `EditProposal` |
| POST | `/ontology/assistant/proposals/{id}/apply` | senior_analyst | 把提案落库为 draft 实体（挂 Release）；Tier2 置 `needs_engineer_review` |
| GET | `/ontology/assistant/proposals/{id}` | 任一有效角色 | 取提案详情（含影响预览/diff） |

发布仍走既有 `/releases/{rid}/submit|publish`，不新增。

### 8.2 提案对象 schema（新增 Pydantic）

```python
class ProposedOp(BaseModel):
    kind: str                    # "criterion.update" | "class.create" | "data_property.create" | ...
    target_iri: str | None       # update/删除时定位
    payload: dict                # 即对应既有 Create/Update body（ClassCreate.model_dump() 等）
    expected_version: int | None # update 时携带，落库走既有 CAS → 409

class ImpactPreview(BaseModel):
    newly_classified: list[dict] = []
    withdrawn: list[dict] = []
    changed_decisions: list[dict] = []
    golden_master_delta: dict = {}

class EditProposal(BaseModel):
    proposal_id: str
    intent_summary: str          # 专家原意（领域语言）
    readback: str                # agent 的改动讲回（领域语言）
    operations: list[ProposedOp]
    tier: int                    # 1 | 2
    regulation_refs: list[str] = []
    validation: ValidationReport # store.validate() 预跑（复用既有 schema）
    diff: DiffResult             # export_diff() 预跑（复用既有 schema）
    impact: ImpactPreview
    needs_engineer_review: bool
    confidence: float
```

`ProposedOp.payload` 直接是既有 `ClassCreate`/`DataPropertyCreate`/E11 判据 Create 的字段——`apply` 时反序列化成对应 schema 调 `store.create_*`/`update_*`，**零新增 CRUD 逻辑**。

### 8.3 LLM 工具集（tool use）

```
read_tbox(scope, iri?)        # 只读：类/判据/规则/属性/映射 当前态（经 store 查询）
search_external_vocab(term, source: chebi|atc|bfo)
                              # 确定性抓取 + 字节核实，返回候选 IRI + 权威 label + verified 标记
draft_proposal(operations)    # 不落库：跑 validate_pattern + store.validate(dry) + export_diff
                              #         + run_assessment 影响模拟 → 组装并返回 EditProposal
```

**关键**：commit 不是 LLM 的工具。LLM 只能产 `EditProposal`；落库由人点"应用为草稿"经 `apply` 端点触发。LLM 没有任何直接写权限。

### 8.4 一次完整数据流（建判据为例）

```
1. 专家（对话）："青霉素必须专用化，因为 GMP 要求 β-内酰胺药物专线生产"
2. converse → LLM 调 read_tbox 查现有专用化规则 / PenicillinDrug 是否可推断
3. LLM 调 draft_proposal，operations=[decision_rule.create(...)]：
     - validate_pattern 校验 antecedent AST ✅
     - store.validate(dry) ✅
     - export_diff → 三元组 diff
     - run_assessment 影响：1 个青霉素药物新判 requires_dedication=true
4. 返回 EditProposal（Tier 2，因新决策规则形态）→ 前端渲染提案卡，顶部标"需工程师联签"
5. 专家点"应用为草稿" → apply → store.create_decision_rule(status=draft) + audit
6. 专家 submit_release → 工程师审 diff、清 needs_engineer_review → publish_release
     → surgical_merge 写 slpra TTL + Git commit SHA + ChangeLog 批次
```

---

## 9. 自然语言 → 形式化的翻译契约

### 9.1 AST 目标词汇（LLM 的输出空间，受 `validate_pattern` 钉死）

| op | 字段 | 领域语义 | 例 |
|---|---|---|---|
| `some_values_from` | property, filler_class | 经某关系关联到某类个体 | API 有基因毒性特征 |
| `class_membership` | property, classes[] | 关联个体类 ∈ 集合 | OEB ∈ {4,5} |
| `datatype_facet` | property, cmp, value | 数值阈值比较 | 致敏级别 > 3 |
| `boolean_has_value` | property, value | 布尔标记 | 有 β-内酰胺环 |
| `external_alignment` | property, alignment | 经对齐属外部类别 | 活性成分对齐 ATC L01 |
| `class_present` | class | 已断言某类 | 已是肿瘤药 |
| `literal_eq` / `literal_cmp` | key,(cmp,)value | 标量事实比较（决策规则用） | 剂型 = 注射剂 |
| `and` / `or` | operands[] | 逻辑组合（Kleene 三值） | A 且 B |

cmp ∈ `gt/ge/lt/le/eq/ne`。三值语义：属性缺失 → `UNKNOWN`（绝不坍缩为 `FALSE`），是 006「否→未知」改进之所在。

### 9.2 翻译示例（few-shot 锚点）

| 专家自然语言 | agent 合成的 pattern AST | target_class | regulation_ref |
|---|---|---|---|
| 致敏级别超过 3 级算高致敏 | `{"op":"datatype_facet","property":"sensitizationLevel","cmp":"gt","value":3}` | HighSensitizingDrug | CFDI 2023-03 §3.4 |
| OEB 4 或 5 的是高活性药 | `{"op":"class_membership","property":"hasOEBClassification","classes":["OEB4","OEB5"]}` | HighActivityDrug | §3.3 |
| 带 β-内酰胺环的是 β-内酰胺药 | `{"op":"boolean_has_value","property":"hasBetaLactamRing","value":true}` | BetaLactamDrug | §4.4 |
| 活性成分对齐 ATC L01 的是肿瘤药 | `{"op":"external_alignment","property":"hasActiveIngredient","alignment":"ATC:L01"}` | AntineoplasticDrug | （Tier 2，须 verified） |

这些示例直接作为 system prompt 的 few-shot，且**每个示例都附 `validate_pattern` 通过**——保证 LLM 学到的是合法输出形状。

### 9.3 反向：把形式化讲成人话

`read_tbox` 取到判据后，agent 把 AST + `rules_fired` 溯源反向翻成领域语言（提案卡的"讲回"、对话里的"解释规则"）。这是 §6.1 对话区"解释规则"能力的实现。

---

## 10. 能力清单（专家工作流）

| # | 能力 | Tier | 映射的既有 CRUD/schema | 确定性闸门 | 频次/价值 |
|---|---|---|---|---|---|
| C1 | **解释规则 / 为什么** | 只读 | `read_tbox` + `rules_fired` | — | 高频，零风险，先做 |
| C2 | **对话改阈值/参数** | 1 | `criterion.update`（仅 pattern 数值） | validate_pattern + validate | 高频 |
| C3 | **法规句→分类判据** | 1或2 | E11 判据 create | validate_pattern + validate | 中频，核心收益 |
| C4 | **法规句→决策规则** | 2 | E12 决策规则 create（依赖 006 US3） | validate_pattern + validate | 中频 |
| C5 | **引导建新类/属性** | 2 | `class.create` / `data_property.create` | validate（BFO 挂位检查） | 中频 |
| C6 | **提议外部对齐** | 2 | `mapping.create` | search_external_vocab 字节核实 + verified 门禁 | 低频，最痛 |
| C7 | **发布前评审员** | 只读 | `export_diff` 领域语言总结 + 风险标红 | — | 每次发布 |
| C8 | **抽取缺口回流** | 2 | 由抽取反复 unmapped 触发 → 提议建属性 | validate | 低频，闭环 |

---

## 11. 宪章对齐（仿 plan.md 体例自检）

| 原则 | 落点 | 结论 |
|---|---|---|
| **I 规范驱动** | 本设计先行，技术细节不渗规范；立 007 后走 specify→clarify→plan | ✅ |
| **II 本体权威性与保真（NON-NEGOTIABLE）** | agent 只产提案，写入仍唯一经 `publish_release` 外科式合并 + 三元组 diff；外部对齐经确定性字节核实 + verified 门禁；Tier 2 联签守结构性变更 | ✅（靠 §5 三门禁 + §7 预览坐实） |
| **III 可追溯与审计** | `assistant.apply` 审计 + 提案纳入既有 ChangeLog/Release(SHA) 批次；"AI 起草/谁批/哪个 SHA"可查 | ✅ |
| **IV 测试纪律与契约优先** | 翻译契约（§9）可测：法规句→AST 的 parity 用例 + validate_pattern 拒绝用例 + 影响预览与 golden-master 一致 | ✅ |
| **V 最小复杂度与复用** | 复用 anthropic SDK / OntologyMetaStore / validate / export_diff / run_assessment / 角色门禁；新增仅薄翻译层 + chat UI；不引入推理框架 | ✅ |
| **安全与合规** | 写/发布仍受 `require_role`；Tier 2 双人/工程师联签；LLM 无直接写权 | ✅ |

---

## 12. 关键风险与待决问题

### 待决（`/speckit-clarify` 候选）

- **Q1 — Tier 2 联签的落地形态**：新增 `ontology_engineer` 角色 / 双人规则（publisher≠drafter）/ 显式"工程师已审"审计动作？（推荐双人规则起步，§5.2）
- **Q2 — 对话历史是否落库**：审计要求 vs 隐私/成本。建议只落 `apply` 的提案快照与意图摘要，不落全部对话。
- **Q3 — 影响预览的样本范围**：仅 golden-master 固定矩阵，还是 + 当前库内全量 ABox？后者更真实但慢。建议 golden-master + 受影响子图采样。
- **Q4 — 模型与成本**：翻译/AST 合成用 Opus，解释/问答用 Sonnet 分流？（抽取已用 Sonnet）
- **Q5 — 多轮意图收敛**：何时从"对话区"切到"引导提案卡"——LLM 自判 vs 显式"帮我改/帮我建"触发词？

### 风险登记

| 风险 | 等级 | 缓解 |
|---|---|---|
| LLM 幻觉外部 IRI 污染权威对齐 | 高 | 确定性字节核实 + verified 门禁（§5.4），已是 006 既定纪律 |
| 专家过度信任、盲批结构性错误 | 中 | Tier 2 强制联签 + 影响预览 + 三元组 diff |
| AST 注入/降级（对话诱导 LLM 绕 Tier） | 中 | Tier 由 draft_proposal 按操作类型判定，对话不可覆盖（§6.3） |
| 006 US3 未完成则 C4/C2 判据写不通 | 中 | 立项排序：007 依赖 006 US3（T035–T039）先落 |
| Prompt 成本 / 延迟 | 低 | 工具化按需取数；SSE 流式；非必经路径可降级 |

---

## 13. 分阶段落地建议（立 007 后的用户故事雏形）

> 每阶段独立可用、可端到端验证，与 006 增量交付同纪律。

- **US1（MVP，最高频零风险）**：C1 解释规则 + C7 发布前评审员。纯只读，先验证"领域语言讲回"质量与专家信任，无任何写风险。
- **US2（Tier 1 自助）**：C2 对话改阈值 + C3 改既有判据。打通 converse→draft_proposal→apply→既有发布 全链，含影响预览。**依赖 006 US3 的 E11 CRUD。**
- **US3（Tier 2 起草 + 联签）**：C5 建类/属性 + C6 外部对齐（字节核实）+ C4 决策规则。落地 Tier 2 联签机制（§5.2）。
- **US4（闭环 + 打磨）**：C8 抽取缺口回流；提案卡/对话体验打磨；翻译 parity 测试集补全。

---

## 附：复用资产索引（实现时按图索骥）

| 资产 | 位置 | 用途 |
|---|---|---|
| `OntologyMetaStore` CRUD + release | `backend/app/services/ontology_meta_store.py` | 提案落 draft、发布批次 |
| `store.validate()` | 同上 | 一致性门禁 |
| `interpreter.validate_pattern()` / `VOCABULARY` | `backend/app/services/reasoning/interpreter.py` | AST 受限词汇闸门 |
| `run_assessment()` | `backend/app/services/reasoning/engine.py` | 影响预览模拟 |
| golden-master | `backend/tests/test_reasoning/fixtures/golden_master.json` | 影响预览基线 |
| `ttl_merge.export_diff()` | `backend/app/services/ttl_merge.py` | 三元组级 diff |
| `require_role` / `Identity` / `get_current_user_sse` | `backend/app/dependencies.py` | 角色门禁 / SSE 身份 |
| `extract_with_fallback` | `backend/app/services/extraction/llm_extractor.py` | 优雅降级范式 + anthropic SDK 用法 |
| 版本冲突 hook / 对话框 | `frontend/src/components/ontology/use-version-conflict.ts`、`conflict-dialog.tsx` | 409 复用 |
| E11/E12/E13 表 + AST 契约 | `specs/006-declarative-rule-layer/data-model.md` | 判据/规则的字段与投影 |
