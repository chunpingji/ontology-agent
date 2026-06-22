# 药研分析思路工作流 —— Status 状态机 + 迁移 Action 设计

> 版本：0.1（设计草案） | 日期：2026-06-22
>
> 范式来源：Palantir Foundry 本体动能层（Action Type + 对象状态机 + Automate）
>
> 关联文档：[`gap-analysis.md`](./gap-analysis.md)（能力三 Action 引擎 ❌、QA 电子签名 ❌、审计哈希链 🟡、GenerateRiskReport 🟡）、[`临床药物智能辅助生产平台方案.md`](./临床药物智能辅助生产平台方案.md)
>
> 状态：🟢 拟实现 · 🟡 部分依赖既有 · ❌ 全新

---

## 0. 一句话结论

把"药研分析思路"从**硬编码的一次性 Python 函数**（`reasoning/engine.py:run_assessment`）升级为**本体驱动的状态机**：一次共线风险评估是一个**有状态的实例对象**，它沿 `Draft → Assessed → UnderReview → QASigned → Released` 推进；每一步推进由一个**迁移 Action（定义存于本体库）**完成，迁移自带**前置守卫、角色门禁、副作用、电子签名、追加式审计**。

**不引入** workflow/step 有序表，**不靠** `params.seq/next` 串顺序——顺序由"当前状态 + 迁移守卫"数据驱动地涌现。

---

## 1. 背景与需求

### 1.1 现状（为什么要做）

| 现状 | 问题 |
|---|---|
| `run_assessment(engine, drug_iri, equipment_iris)` 是**无状态**纯函数：进去药物+设备，出来 `AssessmentResult`（`rules_fired`/`risk_level`/`requires_dedication`/`maco`/`recommendations`） | 评估**不可暂存、不可追溯、不可复核**——跑完即弃，没有"一次评估"这个可持有的对象 |
| "分析思路顺序"硬编码在 `run_assessment` 内（分类→专用化→场景→污染→MACO→推荐） | 思路改动 = 改 Python + 发版；业务无法在本体库维护思路 |
| `OntologyAction`（`actor/target/precondition/postcondition/params`）只是**定义**，运行期无人读取（计划 R10、gap 分析能力三 ❌） | 本体里的"动词"是死的 |
| 强制 QA 复核 / 电子签名 **未实现**（gap 分析 §4 ❌） | 高风险/专用化结论无强制复核门禁，不满足 ALCOA+ / GMP |
| 审计表 `audit_log` 已建但**无哈希链**（gap 分析 §4 🟡） | 审计可被篡改，data integrity 不达标 |
| `GenerateRiskReport` 报告产物未建（gap 分析 §3 🟡） | 评估无正式交付物 |

### 1.2 需求清单

| 编号 | 需求 | 优先级 |
|---|---|---|
| **R-WF1** | 一次共线风险评估是一个**持久化、有状态**的实例，可暂存、查询、流转 | P1 |
| **R-WF2** | 评估按**状态机**推进；非法跃迁（跳步/回退）被拒绝 | P1 |
| **R-WF3** | 每个状态迁移由一个**迁移 Action**驱动，其定义（守卫/效果/法规依据）**存于本体库**、可维护、可发布、可追溯 | P1 |
| **R-WF4** | 迁移受**角色门禁**：分析师推进分析、QA 签署、operator 只读/录入 | P1 |
| **R-WF5** | 高风险 / 需专用化 / 含青霉素等结论**强制进入 QA 电子签名**方可发布（21 CFR Part 11 式签名：人 + 含义 + 时间 + 不可抵赖） | P1 |
| **R-WF6** | 所有迁移写入**追加式、哈希链**审计流水，可独立验真 | P1 |
| **R-WF7** | 推理内核（现有 `run_assessment` + 规则 + MACO/PDE）作为**某个迁移的副作用**被调用，**不重写规则** | P1 |
| **R-WF8** | 事实源变化（药物/设备属性变更）可**自动召回**已发布评估到待重算态（Automate 类比，依赖能力三事实流） | P2 |
| **R-WF9** | `Released` 时生成 `RiskAssessmentReport` 产物（补 GenerateRiskReport 缺口） | P2 |

---

## 2. 设计动机 —— 为什么是"状态机 + 迁移 Action"

### 2.1 Palantir 范式回顾

Palantir 本体分**语义层**（Object/Link/Property——名词）与**动能层**（Action/Function——动词）。工作流**不是**一个独立原语，而是由四件事组合涌现：

1. **Action Type**：原子化、受治理、可审计的写回单元（参数 + 提交守卫 + 编辑逻辑 + 副作用）。
2. **对象状态机**：对象上一个 `status` 属性 + 一组"状态迁移 Action"。顺序由"守卫检查当前 status"数据驱动，而非硬编码指针。
3. **Functions**：业务逻辑/校验/编排。
4. **Automate**：事件/条件触发 → 执行 Action（编排层）。

### 2.2 为什么不用 `seq/next` 有序工作流表

| 维度 | `seq/next` 刚性流程图 | **status 状态机（本设计）** |
|---|---|---|
| 分支（高风险走 QA、低风险直发） | 需在流程图里画死分支节点 | 守卫即分支：`requires_signature` 条件天然路由 |
| 回退（打回重评） | 需反向边，易成环、难校验 | 一条 `Reject` 迁移，目标状态明确 |
| 并发/部分完成 | 难表达 | 状态即进度，天然可暂存 |
| 思路演进 | 改流程图拓扑 | 增删一条迁移 Action 定义即可 |
| 审计 | 节点流转日志 | 迁移 = 天然审计事件单元 |
| 与既有 RBAC/发布/审计对齐 | 新机制 | 复用 `OntologyAction`、`require_role`、`audit_log`、批次发布 |

**结论**：状态机用更少的原语表达更多的流程形态，且与本仓库既有治理骨架（角色门禁、审计、批次发布）天然契合。

### 2.3 关键区分：T-Box 生命周期 vs A-Box 运行期状态（务必不要混淆）

本仓库已有一个 `status` 字段（`STATUS_DRAFT/IN_REVIEW/PUBLISHED/ARCHIVED`，见 `models/ontology_meta.py:31`）——那是**本体定义的发布生命周期**（一个类/属性/Action 定义的草稿→发布）。

本设计新增的状态机作用在**完全不同的对象**上：**一次具体评估实例（A-Box / 业务数据）**。两者正交：

- **T-Box 层**：`OntologyAction` 定义"专用化判定迁移"这个动作*是什么*——它本身有 draft/published 生命周期，由高级分析师在工作台维护、批次发布回 TTL。
- **A-Box 层**：评估实例 #1234 的 `workflow_status` 当前是 `UnderReview`——这是运行期业务状态，由迁移引擎推进。

> 一句话：**T-Box 定义"思路长什么样"，A-Box 状态机记录"这次评估走到哪了"。**

---

## 3. 详细设计

### 3.1 状态集（A-Box 评估实例的 `workflow_status`）

```
                    ┌─────────────────────────────────────────────┐
                    │                                              ▼
  ●──Create──▶ Draft ──RunAssessment──▶ Assessed ──SubmitForReview──▶ UnderReview
                  ▲                         │                            │
                  │                         │ (低风险且无需专用化)        │ Approve
                  │   Reject                ▼                            ▼
                  └──────────────────── (直发分支) ──────────────▶ QASigned
                                                                         │
                                                                  Release│
                                                                         ▼
                  Recall (事实源变更, P2) ◀────────────────────────── Released
```

| 状态 | 含义 | 进入条件 | 可执行迁移 |
|---|---|---|---|
| `Draft` | 评估任务建立，待绑定事实源（药物 IRI、设备 IRI 集） | 创建 | `RunAssessment` |
| `Assessed` | 推理内核已跑完，`rules_fired/risk_level/requires_dedication/maco` 已落库快照 | 推理成功 | `SubmitForReview`、（重）`RunAssessment` |
| `UnderReview` | 高级分析师审阅推理结论 | 提交复核 | `Approve`、`Reject` |
| `QASigned` | QA 已电子签名（高风险/专用化结论的**强制**关口） | QA 签署通过 | `Release`、`Reject` |
| `Released` | 风险评估报告已生成并归档 | 发布 | `Recall`(P2) |
| `Rejected` | 被打回（携带退回原因），可重入 `Draft` | 任一复核环节否决 | `RunAssessment` / 重新绑定 |

**强制 QA 分支（R-WF5）**：`Approve` 迁移的*目标状态*由守卫动态决定——
- 若 `risk_level == HighRisk` 或 `requires_dedication == true` 或命中青霉素场景 → 必须经 `QASigned` 才能 `Release`；
- 否则允许 `UnderReview → Released` 直发（仍审计，但免 QA 签名）。

### 3.2 数据模型

> 复用既有 `OntologyAction`（T-Box）承载**迁移定义**；新增两张 A-Box 表承载**实例**与**追加式审计**。Alembic 迁移延续 `alembic/versions/0001_ontology_meta.py` 模式。

#### (a) 迁移定义 —— **复用** `OntologyAction`（不新增 T-Box 表）

每条迁移是一个 Action 定义实例，IRI 受管命名空间 `https://ontology.pharma-gmp.cn/slpra/`：

```jsonc
// OntologyAction 行：slpra:Action/Approve
{
  "slpra_iri": "https://ontology.pharma-gmp.cn/slpra/Action/Approve",
  "label": "复核通过",
  "actor_iri":  "https://ontology.pharma-gmp.cn/slpra/SeniorAnalyst",   // 谁能执行
  "target_iri": "https://ontology.pharma-gmp.cn/slpra/RiskAssessment",  // 作用对象类型
  "precondition": {                       // 守卫（Guard）
    "from_status": "UnderReview",
    "require_fields": ["risk_level", "rules_fired"],
    "guard_expr": "true"                  // 可引用 reasoning 结果的布尔表达式
  },
  "postcondition": {                      // 效果（Effect）
    "to_status": "QASigned | Released",   // 由 route 守卫决定，见 §3.3
    "route": {
      "if": "risk_level == 'HighRisk' or requires_dedication == true",
      "then": "QASigned", "else": "Released"
    },
    "edits": { "set": { "reviewed_by": "$actor", "reviewed_at": "$now" } },
    "emit_events": ["assessment.approved"]
  },
  "params": {
    "required_role": "senior_analyst",    // 角色门禁（R-WF4）
    "requires_signature": false,          // 电子签名（R-WF5）
    "regulation_ref": "CFDI 2023-03 §6",
    "idempotency_key": "approve"
  }
}
```

**约定**（引擎按此解析 `OntologyAction` 的 JSON 字段，无需改表结构）：

| 字段 | 子键 | 语义 |
|---|---|---|
| `precondition` | `from_status` | 仅当实例处于此状态才可触发（状态机合法性） |
| | `require_fields` | 这些结果字段必须已存在 |
| | `guard_expr` | 受限布尔表达式，可读 `risk_level/requires_dedication/maco/scenarios` |
| `postcondition` | `to_status` / `route` | 目标状态（可条件路由） |
| | `edits` | 对实例的字段写入（`$actor`/`$now` 占位） |
| | `emit_events` | 发出的领域事件（供 Automate 订阅） |
| `params` | `required_role` | `senior_analyst` / `qa` / `operator`（`ROLE_NAMES`） |
| | `requires_signature` | true → 必须携带合法电子签名方可执行 |
| | `regulation_ref` | 法规可解释性引用 |

#### (b) 评估实例 —— 新增 `assessment_instance`（A-Box）

```python
class AssessmentInstance(TimestampMixin, Base):
    __tablename__ = "assessment_instance"
    id: Mapped[uuid.UUID]            # PK
    workflow_status: Mapped[str]     # Draft/Assessed/UnderReview/QASigned/Released/Rejected
    version: Mapped[int]             # 乐观并发（CAS，复用 _cas_update 范式）
    drug_iri: Mapped[str]
    equipment_iris: Mapped[list]     # JSON
    result_snapshot: Mapped[dict | None]   # JSON：AssessmentResult 的不可变快照
    reviewed_by / reviewed_at
    reject_reason: Mapped[str | None]
    report_iri: Mapped[str | None]   # Released 时回填（R-WF9）
```

> `result_snapshot` 存"推理那一刻"的结论副本——保证审计可重现，即使 T-Box 之后改了规则。

#### (c) 追加式哈希链审计 —— 新增 `assessment_transition_log`（补 gap §4 哈希链）

```python
class AssessmentTransitionLog(Base):
    __tablename__ = "assessment_transition_log"
    id: Mapped[uuid.UUID]
    instance_id: Mapped[uuid.UUID]
    seq: Mapped[int]                 # 实例内自增序号
    action_iri: Mapped[str]          # 执行的迁移 Action
    from_status / to_status
    actor: Mapped[str]
    payload: Mapped[dict | None]     # 迁移入参/守卫快照
    # --- 电子签名 (R-WF5, 21 CFR Part 11 式) ---
    signer: Mapped[str | None]
    signature_meaning: Mapped[str | None]   # "QA 复核通过"
    signed_at: Mapped[datetime | None]
    # --- 哈希链 (R-WF6) ---
    prev_hash: Mapped[str | None]    # 上一条 hash
    row_hash: Mapped[str]            # sha256(prev_hash + 规范化本行)
    created_at: Mapped[datetime]
```

> 这张表同时满足 **R-WF6 哈希链** 与 **R-WF5 电子签名**，并可作为全平台 ALCOA+ 哈希链审计的样板，反哺 gap 分析 §4 的 `audit_log` 硬化。

### 3.3 迁移引擎执行语义

新增 `backend/app/services/workflow/transition_engine.py`，单一入口：

```
execute_transition(instance_id, action_iri, actor, signature=None, params=None)
```

执行步骤（全程单事务，失败回滚）：

1. **加载**：取实例 + 取迁移 Action 定义（T-Box，须为 `published` 且未停用）。
2. **状态合法性**：`instance.workflow_status == precondition.from_status`，否则 409。
3. **角色门禁**：`actor.role == params.required_role`，复用 `require_role` 同源校验，否则 403。
4. **守卫**：`require_fields` 齐备 + `guard_expr` 求值为真（受限求值器，仅白名单变量/运算符）。
5. **签名校验**：若 `params.requires_signature` → 必须有合法 `signature`（签名人 = actor、含义、时间戳），否则 422。
6. **路由**：按 `postcondition.route` 计算 `to_status`。
7. **副作用**：执行 `edits`（CAS 写实例，乐观并发）；若 Action 标注 `invoke: run_assessment`（见 §3.4），调用推理内核并写 `result_snapshot`。
8. **审计上链**：构造 `assessment_transition_log` 行，`prev_hash` = 该实例上一行 `row_hash`，计算本行 `row_hash` 后写入。
9. **发事件**：`emit_events` 推送（P1 同步日志；P2 接 Automate）。

> 守卫表达式求值器**不得**用 `eval`；用受限 AST 白名单（变量限 `risk_level/requires_dedication/maco/scenarios/dosage_form`，运算限 `== != and or not > <`）。

### 3.4 与既有推理内核的衔接（R-WF7，不重写规则）

`RunAssessment` 迁移的 `postcondition` 带 `"invoke": "run_assessment"`。引擎在步骤 7 调用既有 `reasoning.engine.run_assessment(engine, drug_iri, equipment_iris)`，把返回的 `AssessmentResult` 序列化进 `result_snapshot`。**规则、MACO/PDE、冲突解决一行不改**——它们成为"分析"这步迁移的副作用。

```
Draft ──RunAssessment──▶ Assessed
         │
         └─ effect: result = run_assessment(...)   # 复用 reasoning/，零改动
            snapshot ← {rules_fired, risk_level, requires_dedication, maco, scenarios, recommendations}
```

### 3.5 API 端点（延续 `/api/` 风格）

| 方法 | 路径 | 作用 | 门禁 |
|---|---|---|---|
| POST | `/api/assessments` | 建评估实例（→ Draft） | analyst/operator |
| GET | `/api/assessments/{id}` | 取实例 + 当前态 + 快照 | 全角色 |
| GET | `/api/assessments/{id}/transitions` | 当前态**可执行的迁移**列表（前端按此渲染按钮） | 全角色 |
| POST | `/api/assessments/{id}/transitions/{action}` | 执行一次迁移（body: signature?/params?/expected_version） | 按 Action `required_role` |
| GET | `/api/assessments/{id}/audit` | 哈希链审计流水 + 验真结果 | qa/analyst |
| GET | `/api/assessments/{id}/report` | 取 `RiskAssessmentReport`（Released 后，R-WF9） | 全角色 |

> `GET …/transitions` 让前端**完全由后端状态机驱动 UI**——按钮可见性、禁用、是否要签名都来自定义，不在前端硬编码流程。

### 3.6 自动化触发（R-WF8，P2，依赖能力三事实流）

Palantir Automate 类比：订阅 `emit_events` 与**事实变更事件**（能力三增量物化产物），定义"触发器 → 迁移"：

- 事实源中某药物/设备属性变更 → 自动对引用它的 `Released` 评估执行 `Recall` 迁移 → 回 `Assessed` 待重算，并发告警。
- `assessment.released` 且 `risk_level==HighRisk` → 触发 `RaiseDedicationRequirement`（gap 分析 §3 缺的 Action）→ 工单系统 Webhook。

此层在能力三事实变更事件机制就位后再接；P1 阶段 `emit_events` 仅落日志。

---

## 4. 落地路线

| 阶段 | 内容 | 依赖 | 对应 gap |
|---|---|---|---|
| **P1-a** | `assessment_instance` + `assessment_transition_log` 表 + Alembic 迁移 | — | 能力三 Action 引擎雏形 |
| **P1-b** | `transition_engine` + 守卫求值器 + 哈希链 | P1-a | §4 哈希链 🟡→✅ |
| **P1-c** | 预置 6 状态 + 迁移 Action 定义（种子脚本，复用 `create_action`，发布回 TTL） | P1-b | 本体库预置思路 |
| **P1-d** | API 端点 + `RunAssessment` 接 `run_assessment` | P1-c | 推理状态化 |
| **P1-e** | QA 电子签名校验 + 强制分支 | P1-d | §4 强制 QA ❌→✅ |
| **P2-a** | `RiskAssessmentReport` 导出器 | P1 | §3 GenerateRiskReport 🟡→✅ |
| **P2-b** | Automate：事实变更 → `Recall` / `RaiseDedicationRequirement` | 能力三事实流 | §3 Action 引擎 ❌ |
| **P2-c** | 前端 `assessment-workflow-panel.tsx`（状态条 + 后端驱动的迁移按钮 + 签名弹窗 + 审计时间线） | P1-d | §3 实时看板 |

---

## 5. 与现有代码的映射 / 改动清单

| 改动 | 文件 | 类型 |
|---|---|---|
| 评估实例 + 审计链表 | `backend/app/models/assessment.py` | 🟢 新增 |
| 迁移引擎 + 守卫求值 + 哈希链 | `backend/app/services/workflow/transition_engine.py` | 🟢 新增 |
| 状态/迁移种子 | `backend/scripts/seed_assessment_workflow.py` | 🟢 新增（复用 `OntologyMetaStore.create_action`） |
| 工作流 API | `backend/app/api/assessments.py` | 🟢 新增 |
| Alembic 迁移 | `backend/alembic/versions/0002_assessment_workflow.py` | 🟢 新增 |
| 调用推理内核 | `reasoning/engine.py:run_assessment` | 🟡 复用，零改动 |
| 角色门禁 | `dependencies.py` `require_role` | 🟡 复用 |
| 迁移定义载体 | `models/ontology_meta.py:OntologyAction` | 🟡 复用，不改表 |
| 前端面板 | `frontend/src/components/.../assessment-workflow-panel.tsx` | 🟢 新增（P2-c） |

---

## 6. 开放问题（待确认）

1. **迁移定义存哪**：复用 `OntologyAction`（推荐，零新表、自带发布/审计）vs 新建 `workflow_transition` 表（语义更纯，但增表）。本设计选前者，待 review。
2. **守卫表达式语言**：受限 AST 白名单（本设计选用）vs 结构化 JSON 条件树 vs 引用 `reasoning/rules` 的 rule_id。可分阶段：P1 用结构化条件，P2 升级表达式。
3. **电子签名强度**：P1 用户名+口令二次确认（落 `signer/meaning/signed_at`）；是否需 PKI/CA 数字签名留待合规评审。
4. **哈希链范围**：先做评估流水自包含链；是否与全局 `audit_log` 合并为单一链待定。
5. **多设备/多药物**：一次评估含设备集（已支持）；跨多药物对比是否需聚合实例待定。

---

## 附：迁移定义速查（预置进本体库的 6 条 Action）

| Action IRI（`…/slpra/Action/`） | from → to | 角色 | 签名 | 副作用 |
|---|---|---|---|---|
| `Create` | ● → Draft | analyst/operator | 否 | 建实例 |
| `RunAssessment` | Draft/Assessed/Rejected → Assessed | analyst | 否 | `run_assessment` → 写快照 |
| `SubmitForReview` | Assessed → UnderReview | analyst | 否 | — |
| `Approve` | UnderReview → QASigned \| Released（路由） | senior_analyst | 否 | 写 reviewed_by/at |
| `QASign` | QASigned 关口 | qa | **是** | 写签名行 |
| `Release` | QASigned/UnderReview(低风险) → Released | senior_analyst | 视风险 | 生成 RiskAssessmentReport |
| `Reject` | UnderReview/QASigned → Rejected | senior_analyst/qa | 否 | 写 reject_reason |
</content>
</invoke>
