# 工作流路线 B —— 当前能力 Gap 评估

> 版本：0.1 | 日期：2026-06-23
>
> 评估对象：[`workflow-statemachine-design.md`](./workflow-statemachine-design.md) §5/§6 + 附录 A 定义的**路线 B**
> （把迁移定义沉淀为 `OntologyAction`，由通用 `transition_engine` 解释执行——「思路工作流即本体库可治理资产」）
>
> 代码基线：commit `b667fed` + feature `003-workflow-statemachine-closure`（路线 A 已落地）
>
> 状态图例：✅ 已实现 · 🟡 部分/未接线 · ❌ 缺口

---

## 0. 一句话结论

路线 A（003）已把工作流做成**代码里的状态机**：四态生命周期 + 静态合法集 + 单一 `transition()` 守卫，端到端可自举。路线 B 的**数据容器（`OntologyAction` 表）与状态守卫骨架（`transition()`）都已存在**，缺的是把两者接通的**解释引擎**，以及让定义**可治理、可无损发布回 TTL**。

离 Route B 最远的是两块：**通用 `transition_engine`（不存在）** 与 **数据驱动副作用（硬编码）**；最易被忽视的是 **TTL 往返当前有损**——Action 定义的 `precondition/postcondition/params` 根本不序列化进 TTL，"可治理资产"目前只到数据库层、未到本体层。

---

## 1. 当前基线（路线 A，已落地）

先确认现状，避免高估缺口——003 已把路线 A 做完整：

| 能力 | 状态 | 证据 |
|---|---|---|
| 评估即落库（G1 闭合） | ✅ | `POST /assess` 落库 `ReasoningExecution` + 自动 arm QA 闸门 + 编排动作（单事务）—— `api/reasoning.py:101`、`risk.requires_qa_signature` 在 `:116` |
| 显式四态生命周期 + 集中守卫 | ✅ | `LifecycleState` 四态 + `LEGAL_TRANSITIONS` 静态集（5 条）+ 单一 `transition()` —— `services/reasoning/lifecycle.py:28/39/60` |
| 强制 QA 闸门自动 arm（G2 闭合） | ✅ | 据风险置 `requires_signature` —— `api/reasoning.py:116` |
| QA 签批 / 拒绝 / 审计哈希链 / Part 11 签名 | ✅ | `api/compliance.py`（`/reject`、`/signatures`、`/audit`） |
| `OntologyAction` 数据模型 | ✅ | `precondition/postcondition/params`（裸 JSON）—— `models/ontology_meta.py:155` |
| `OntologyAction` CRUD（增删改查 + CAS + 审计） | ✅ | `services/ontology_meta_store.py:561-611` |

**净结论**：路线 B 所需的**数据容器和守卫骨架都在**，缺的是解释引擎 + 治理往返。这点容易被 `OntologyAction` 表的存在所掩盖。

---

## 2. Gap 矩阵（当前 → 路线 B）

| # | 路线 B 构件 | 当前 | 证据 | 缺口与改造量 |
|---|---|:---:|---|---|
| 1 | **迁移定义数据化**（`OntologyAction` 行） | 🟡 | `models/ontology_meta.py:164` `precondition/postcondition/params` 为裸 `dict\|None`，无 schema | 容器在、**契约缺**。需定义并校验三段结构：`precondition.{from_status,require_fields,guard_expr}` / `postcondition.{to_status,route,edits,emit_events}` / `params.{required_role,requires_signature,regulation_ref}`。**量：S** |
| 2 | **发布回 TTL（可治理资产）** | ❌ | `services/ttl_merge.py:129-134` 导出仅写 `rdf:type slpra:Action` + label/comment/actor/target；`precondition/postcondition/params` **完全不序列化**；导入侧无 Action 解析 | **有损，核心语义丢失**。这是 Route B 的招牌动机（"思路即本体资产"），目前结构性未达成。需设计 TTL 词汇（JSON reify 成 RDF，或存为序列化 literal）+ 导入解析器，做到无损往返。**量：M** |
| 3 | **通用 `transition_engine`** | ❌ | grep `transition_engine/execute_transition/guard_expr/eval` 均无命中 | **不存在**。全新组件：`execute_transition()` = 载入 Action 定义 → 状态合法性 → 角色门禁 → 守卫求值 → 签名校验 → 路由 → 副作用（`invoke: run_assessment`）→ 审计上链 → 发事件。**量：L（核心）** |
| 4 | **守卫表达式安全求值** | ❌ | 当前是 `(from,to)` 静态元组集，无表达式 | 需受限 AST 白名单求值器（**禁 `eval`**），含绑定上下文（结论字段/risk_level）。安全敏感面。**量：M** |
| 5 | **状态集 / 对象语义** | 🟡 | `services/reasoning/lifecycle.py:28` 四态，conclusion-centric | 蓝本是 `Draft→Assessed→UnderReview→QASigned→Released(+Rejected)`——当前把评审**压扁了**：无 `Draft`、无独立 `UnderReview`（复核 ≠ QA 签批）、无 `Release`。要么扩 enum，要么按"数据驱动状态"放开。**量：M**（若引入 `AssessmentInstance` 则含迁移） |
| 6 | **预置 6 条迁移 Action** | ❌ | `list_actions()` 可列，但无种子数据 | 行为部分以硬编码存在（RunAssessment≈`/assess`、QASign≈`compliance.sign`、Reject≈`compliance.reject`），但**非 Action 定义**。需作成 6 行 `OntologyAction` 种子 + 引擎解释。**量：S**（依赖 #3/#5） |
| 7 | **条件路由（强制 QA 分支）** | 🟡 | `risk.requires_qa_signature()` 硬编码于代码 | **逻辑在、位置错**。决策需搬进数据：`Approve` 的 `guard_expr`/`postcondition.route`（HighRisk/专用化/青霉素 → QASigned，否则 Released）。**量：S** |
| 8 | **角色门禁** | ✅ | `require_role(ROLE_QA/...)` 在端点层 | 机制在、**未数据驱动**。引擎应读 `params.required_role` 强制。**量：S** |
| 9 | **强制签名** | ✅ | `compliance.sign` + `ElectronicSignature` + 重认证 | 机制在、**未数据驱动**。应由 `params.requires_signature` 驱动，而非布尔列。**量：S** |
| 10 | **审计链** | ✅ | `audit.append` 全局单链；`transition()` 已写 `reasoning.transition` | 引擎复用即可，几乎无改。**量：XS** |
| 11 | **数据驱动副作用**（`edits`/`emit_events`） | 🟡 | `action_engine._plan` 是写死的"结论标志→动作类型"映射 —— `services/reasoning/action_engine.py:21`；`fact_event_bus` 在但无数据驱动派发 | **硬编码**。`postcondition.edits/emit_events` 需由引擎解释执行。开放度高。**量：L** |

---

## 3. 关键结论

1. **离 Route B 最远的是 #3（引擎）与 #11（数据驱动副作用）**——这是把"代码里的流水线"翻译成"定义解释器"的核心，都是 L 量级、全新组件。
2. **#2（TTL 往返）是 Route B 的"灵魂"且当前有损**——即使今天在 UI 里编一条 Action 定义，它的 `precondition/postcondition/params` 也发布不进 TTL，所以"可治理资产"目前只到数据库层，没到本体层。
3. **被低估的优势**：`OntologyAction` 继承 `NamedEntityMixin`（`models/ontology_meta.py:76`），天然带 `version/status/is_reviewed` + Release/ChangeLog 治理——Action 定义**复用既有 T-Box 发布生命周期**，这块几乎白送，且与 A-Box 工作流生命周期正交。

---

## 4. 建议的最小切入路径（强裁剪版 Route B）

不必一步到位。按价值/风险排序，strangler（绞杀者）模式逐步替换硬编码：

| 阶段 | 范围（对应 Gap #） | 价值 | 风险 |
|---|---|---|---|
| **P1** | 定义契约 + TTL 无损往返（#1+#2） | 让 Action 定义能治理、能发布——独立交付"工作流即本体资产"的一半价值；不动运行期 | 低 |
| **P2** | 只读式引擎（#3+#4 子集）：`transition_engine` 先**只接管合法性校验**（读 Action 的 `from/to` 替代静态 `LEGAL_TRANSITIONS`），保留现有副作用代码 | 引擎落地、风险最低 | 低 |
| **P3** | 数据驱动门禁/签名/路由（#7+#8+#9）：把三处硬编码判定搬进 `params`/`guard_expr` | 闸门规则可治理 | 中 |
| **P4** | 数据驱动副作用 + 6 条预置 Action + 状态集扩展（#11+#6+#5） | 完整 Route B | 高（最大、最该最后做） |

> 建议：**先 P1/P2**（务实首切，各约数日），P4 是开放式重头。

---

## 5. 约束 / 风险（对照 constitution v1.0.0）

- **原则 II（本体权威性，NON-NEGOTIABLE）**：Action 定义成为 T-Box 实体后，TTL 往返必须保真、需 BFO 对齐——#2 不能只存 literal 了事。
- **安全**：`guard_expr` 的 AST 白名单求值是真实注入面（#4），**禁 `eval`**，必须严格限定算子与绑定上下文。
- **向后兼容**：四态 conclusion 机 + 三处硬编码路径在迁移期必须并存可用（strangler），否则推理/合规闸门会回归。
- **测试纪律**：现有 `test_lifecycle_machine.py` / `test_qa_gate.py` / `test_qa_reject.py` 是回归基线，引擎接管后必须全绿。

---

## 6. 整体量级

这是一个独立新特性（建议立 **`005`**）。P1+P2 是务实首切；P4 开放式重头，工期取决于 `edits/emit_events` 副作用词汇的开放度与状态集是否引入 `AssessmentInstance`。

## 附录：相关代码索引

| 关注点 | 文件 |
|---|---|
| Action 数据模型（E4，定义态） | `backend/app/models/ontology_meta.py:155` |
| Action CRUD（CAS + 审计） | `backend/app/services/ontology_meta_store.py:561-611` |
| Action → TTL 序列化（有损） | `backend/app/services/ttl_merge.py:125-134` |
| 现状状态守卫（静态合法集） | `backend/app/services/reasoning/lifecycle.py` |
| 硬编码副作用编排 | `backend/app/services/reasoning/action_engine.py:21` |
| 评估即落库 + 自动 arm 闸门 | `backend/app/api/reasoning.py:101` |
| QA 签批 / 拒绝 / 审计 API | `backend/app/api/compliance.py` |
</content>
</invoke>
