# 临床药物智能辅助生产平台 —— 方案与代码 Gap 分析

> 版本：1.2 | 日期：2026-06-23
>
> 对照基准：[`临床药物智能辅助生产平台方案.md`](临床药物智能辅助生产平台方案.md)（v1.3）
>
> 核验对象：当前代码库 `main` 分支（已合并 `001-slpra-ontology-platform` + `002-extraction-realtime-reasoning` + `003-workflow-statemachine-closure`）
>
> 核验方式：逐条对照实际源码（`backend/app/`、`frontend/src/`、`backend/alembic/`、`backend/tests/`），以**实际接线与测试**为准，不以"有组件/接口"判定能力具备。

> **状态图例**：✅ 已实现 · 🟡 部分/未接线 · ❌ 缺口（未落地）

---

## ⮕ v1.2 更新 —— 结论工作流状态机闭环（feature `003-workflow-statemachine-closure`）

`002` 落地了结论中心的合规/动作流水线（哈希链/QA 签名/Action 引擎/增量重算/报告），但 `docs/workflow-statemachine-design.md` 核出该流水线**尚不能端到端自举**——结论无初始落库路径、QA 闸门未自动 arm、事实事件未自动订阅重算，且状态隐式分散。`003` 采纳设计文档「路线 A（务实闭环）」补齐三根线并把隐式状态固化为**显式四态生命周期 + 集中迁移守卫**。后端测试由 71 增至 **108 项全部通过**（`cd backend && pytest`）。逐项闭合证据：

| 003 缺口 | v1.1 状态 | v1.2 状态 | 闭合证据 |
|---|---|---|---|
| **G1 结论无初始落库路径** | 🟡 隐式 | ✅ | `POST /reasoning/assess` 评估即落库 `ReasoningExecution`，同事务内 `ActionEngine.orchestrate` 编排动作；流水线从首条结论自举（`test_assess_bootstrap.py`） |
| **G2 强制 QA 闸门未自动 arm** | 🟡 | ✅ | `reasoning/risk.py` `requires_qa_signature()`（高风险等级/需专用化/青霉素·头孢·致敏命中）→ 自动置 `pending_signature` 且动作 `suppressed`，签名后释放（`test_qa_gate.py`） |
| **G3 事实事件→增量重算未订阅** | 🟡 | ✅ | `recompute_subscriber.py` 在 `main.py` 启动注册（幂等），桥接 `FactEventBus` → `incremental.recompute_subgraph`，仅重算相交的 effective 结论（`test_auto_recompute.py`） |
| **显式生命周期状态机** | ❌（隐式三标志） | ✅ | `reasoning/lifecycle.py` 四态 `pending_signature/effective/superseded/rejected` + 单一 `LEGAL_TRANSITIONS` 守卫，非法跃迁抛 `IllegalTransition`（`test_lifecycle_machine.py`） |
| **QA 拒绝 → 终态 + 动作作废** | ❌ | ✅ | `POST /compliance/reject` 转 `rejected` 终态、作废非终态动作、入审计链（`test_qa_reject.py`） |
| **取代链 + 动作随取代作废** | 🟡 | ✅ | 增量重算 `effective → superseded`（T5），过时结论的待派发动作自动作废（`test_incremental_reasoning.py`） |
| **语义实体对齐** | 🟡（仅 ID/词法） | ✅ | `extraction/semantic.py` Embedder + `aligner.py` 余弦相似度（阈值 0.82），method ∈ {id, lexical, semantic}（`test_aligner_semantic.py`） |
| **历史结论状态回填** | — | ✅ | Alembic 迁移回填既有结论生命周期状态（`test_migration_backfill.py`） |

**003 仍 OUT OF SCOPE（显式延后）**：路线 B —— 把迁移定义数据化为本体库可治理的 `OntologyAction`（待路线 A 跑通、需求稳定后另立特性评估）。

> 状态机权威定义（`reasoning/lifecycle.py`）：`(None→effective)` 无需签批落库即生效；`(None→pending_signature)` 需签批；`(pending_signature→effective)` QA 签批；`(pending_signature→rejected)` QA 拒绝；`(effective→superseded)` 增量重算取代。`superseded`/`rejected` 为终态。仅 effective 结论被自动重算覆盖，待签结论保留等待签批。

---

## ⮕ v1.1 更新 —— P2–P5 闭合（feature `002-extraction-realtime-reasoning`）

下列 §2–§4 列出的 P2–P5 gap 已由 feature 002 全部闭合。后端测试由 36 项增至 **71 项全部通过**（`cd backend && pytest`）。逐能力闭合证据：

| 原 Gap（§编号） | v1.0 状态 | v1.1 状态 | 闭合证据 |
|---|---|---|---|
| 抽取流水线接线（§2） | 🟡 | ✅ | `create_job` 经 BackgroundTasks 触发 `run_extraction_pipeline` + SSE 阶段推送；`tests/test_api/test_extraction_pipeline.py` |
| DB 源读取器（§2） | ❌ | ✅ | `database` 源类型抽取（表→Class、外键→LinkType 候选入审核队列，原则 II 不自动发布）；DSN 经 `dsn_ref` env 引用 |
| 对齐审核 UI（§2） | ❌ | ✅ | `frontend/.../components/extraction/alignment-review.tsx` 对接 candidates/review/merge/split；`test_alignment_review.py` |
| 真实连接器 + 增量物化（§3） | 🟡/❌ | ✅ | `APSConnector`（增量拉取+超时）替换 Stub、`fact_materialization_run` 表 + 进程内事件总线 + SSE；增量重算≤5s 仅算受影响子图；`test_aps_connector.py`/`test_fact_materialization.py`/`test_incremental_reasoning.py` |
| Action 引擎 + 报告（§3） | ❌ | ✅ | `services/reasoning/action_engine.py`（工单/告警/排期阻断/**建议性**回写，`not_accepted` 不算失败）+ `action_execution` 表 + reportlab PDF/JSON 报告；`test_action_engine.py`/`test_risk_report.py` |
| 实时推理看板（§3） | ❌ | ✅ | `realtime-inference-panel.tsx`（相容性热力图 + 排期风险 + 规则链溯源）轮询 `/integration/dashboard`；`test_dashboard.py` |
| 审计哈希链（§4） | 🟡 | ✅ | `audit.append/verify` SHA-256 链（`prev_hash‖canonical`、单调 `seq`、append-only）；全链路（抽取/对齐/物化/**推理**/动作/签名）均留痕；`test_compliance.py` |
| 强制 QA 电子签名（§4） | ❌ | ✅ | 21 CFR Part 11 重认证电子签名：高风险结论 `requires_signature` 未签前 `effective=false` 且动作 `suppressed`，签名后释放；`qa-signature-dialog.tsx` |
| RBAC 分级（§4） | 🟡 | ✅ | `operator` 只读、`qa` 复核签名、维护/发布/审核/增量触发限 `senior_analyst`；越权写 `403` |

**仍 OUT OF SCOPE（非本次目标）**：能力一（已于 001 闭合，无 gap）；企业级 SSO（合规硬化中 SSO 部分留待后续，当前 QA 重认证经可插拔共享密钥门禁实现，凭据不入库——见宪法安全约束）。

> 安全约束保持：连接器敏感凭据经 env/`settings` 注入，`connection_config` 仅存 `dsn_ref` 引用键名，不入库、不入版本库（R7）；DB 源派生的 Class/LinkType 候选仅入审核队列、不自动发布；回写仅建议性，不直改外部权威数据（原则 II）。

---

## 0. 总体结论

**当前代码已领先于方案文档（v1.2.0），方案的 P2–P5 路线图均已交付，且 003 又补入方案未描述的结论工作流状态机。** 经逐条核验：

- **能力一（T-Box 维护工作台）**：✅ 无 gap，全栈落地（001）。
- **能力二（多源抽取与对齐）**：✅（002 闭合 + 003 语义增强）抽取流水线已接线、DB 源读取器、对齐审核 UI、**语义实体对齐**均落地。
- **能力三（实时事实源与 Action 推理）**：✅ 功能闭合（002）+ **结论流水线端到端自举 + 显式生命周期状态机**（003）。
- **合规硬化**：✅（002 + 003）审计哈希链、Part 11 QA 电子签名（自动 arm 闸门 + 拒绝终态）、operator/QA/senior_analyst RBAC 分级落地。
- 后端测试 36（001）→ 71（002）→ **108（003）全部通过**。

**唯一实质性剩余技术 gap（代码也未闭合）**：

| 剩余 gap | 状态 | 代码证据 | 性质 |
|---|---|---|---|
| 外部事实源**真实 HTTP 接入** | 🟡 框架完备、外联是 stub | `integration/aps_connector.py` 实读 `config["inline_changes"]`（确定性测试），`_probe`/HTTP 真实拉取注"留待生产接入"；ERP/MES/LIMS/CTMS 的 `fetch_material_inventory`/`fetch_lab_results`/`fetch_clinical_trial_info` 均 `return []` | 连接器抽象 + 增量物化 + 事件总线已就绪，缺的是生产凭证与真实端点接线 |
| 企业级 **SSO** | ❌（显式权衡） | QA 重认证经可插拔共享密钥门禁，凭据不入库 | 合规约束下的设计取舍 |
| 路线 B（数据化迁移 `OntologyAction`） | ❌（显式延后） | 003 仅交付路线 A | 待需求稳定后另立特性 |

> 下列 §2–§4 为 v1.0 历史 gap 记录（已全部闭合），其闭合情况见上方"v1.1 更新""v1.2 更新"对照表，保留作沿革。

---

## 1. 核验通过项（✅ 无 gap）

下列文档 ✅ 声明经源码核验**全部属实**：

| 文档声明 | 代码核验证据 | 结论 |
|---|---|---|
| 后端独立服务（FastAPI + Owlready2 + PG + 规则引擎） | `app/services/ontology_engine.py`、`app/services/reasoning/`、`app/models/` 齐备 | ✅ 属实 |
| 全量 T-Box 写接口 | `app/api/ontology.py`（读 3×GET + 写 classes/link-types/data-properties/actions/restrictions/mappings + validate + import/export + releases + audit） | ✅ 属实 |
| 元数据草稿真源 + 外科式 TTL 导出 | `app/services/ontology_meta_store.py`、`app/services/ttl_merge.py` | ✅ 属实 |
| 可编辑元数据表 + Alembic 迁移 | `app/models/ontology_meta.py`（class/link_type/data_property/action/restriction/class_mapping/release/change_log/app_user/app_role）+ `alembic/versions/0001_ontology_meta.py` | ✅ 属实 |
| 前端自建 11 个组件 | `frontend/src/components/ontology/` 实测 **11 个文件**，与文档 §4.2 清单完全一致 | ✅ 属实 |
| 写操作 RBAC 门禁 | `app/api/ontology.py:57` `_writer = require_role(ROLE_SENIOR_ANALYST)` | ✅ 属实 |
| 4 组规则 + MACO/PDE 计算器 | `reasoning/rules/`（drug_classification / equipment_dedication / contamination_risk / scenario_identification）+ `calculators.py` + `conflict_resolver.py` | ✅ 属实（代码比文档多出独立 `conflict_resolver.py`） |
| 36 项后端测试 | `backend/tests/` 6 个测试文件，`grep -c "def test_"` = **36** | ✅ 精确属实 |

> **能力一确认无 gap**：文档 §4 的"已从只读浏览升级为可编辑/校验/发布/回写 TTL"在代码中完全成立。

---

## 2. 能力二 Gap —— 多源抽取与实体对齐（🟡）

| Gap 项 | 当前状态 | 代码证据 | 影响 |
|---|---|---|---|
| **抽取流水线未接线** | 🟡 核心缺口 | `app/api/extraction.py:53` `create_job` 仅写 `status="pending"`，**未调用** `run_extraction_pipeline`；codegraph 显示 `run_extraction_pipeline`（`pipeline.py`）**零调用方**——pipeline 为死代码 | 上传文档后无任何抽取发生 |
| **数据库源读取器缺失** | ❌ | `extraction/` 下仅 `parser.py`（Excel/Word）+ `llm_extractor.py`，无 DB 读取器 | 无法从 ERP/MES 表结构抽取 |
| **对齐审核 UI 缺失** | ❌ | `alignment-review` 组件不存在；`extraction/page.tsx` 仅 60 行**静态占位页**（"选择文件"按钮无 onClick/无 API 调用） | 抽取候选无法人工审核 confirm/reject/merge/split |
| 后端对齐逻辑 | ✅ | `extraction/aligner.py` `align_entity` 已实现并被 pipeline 引用 | 仅后端可用 |

**补齐方向**：
1. `create_job` → 触发 `run_extraction_pipeline`（FastAPI BackgroundTasks + SSE 进度推送）；
2. 实现 DB 源读取器（表结构 → Class，外键 → LinkType）；
3. 前端新建 `alignment-review.tsx`，对接 `/jobs/{id}/candidates` + `/candidates/{id}/review`。

---

## 3. 能力三 Gap —— 实时事实源对齐与 Action 推理（🟡/❌）

| Gap 项 | 当前状态 | 代码证据 | 影响 |
|---|---|---|---|
| **事实源连接器仅 Stub** | 🟡 | `integration/base.py` 仅 `ExternalSystemConnector(ABC)` + `StubConnector`（Mock）；无 APS/ERP/MES/LIMS/CTMS 真实连接器 | 无真实事实源接入 |
| **增量物化 / 事实变更事件** | ❌ | 无 `fact_materialization_run` 表、无事件机制 | 无近实时（≤5s）物化链路 |
| **Action 引擎** | ❌ | 全库 grep `RaiseDedicationRequirement`/`ActionExecution`/`action_engine` **零命中**；无 `action_execution` 表 | 推理结论无法触发工单/告警/回写 |
| **实时推理看板** | ❌ | `realtime-inference-panel`/`compatibility-matrix` 组件**零命中**；`integration/page.tsx` 仅 63 行只读 specs 展示 | 无相容性热力图/排期风险/规则链溯源 UI |
| **报告导出 GenerateRiskReport** | 🟡 | 评估结果（`run_assessment` 返回 `rules_fired`）已具备，报告导出器未建 | 无 RiskAssessmentReport 产物 |
| OWL 结构推理 + 规则引擎 + MACO/PDE | ✅ | `reasoning/engine.py` + `rules/` + `calculators.py`；`/api/reasoning/assess`、`/calculate/pde`、`/calculate/maco`、`/rules` 可用 | 推理内核完备 |

**补齐方向**：
1. 实现 ≥1 个真实连接器（建议先 APS 排期，作用面最大），替换 Stub；
2. 增量物化 + 事实变更事件 → 触发 Layer 1–3 重算；
3. Action 引擎（事件驱动编排 + `action_execution` 表 + Webhook 回写）；
4. 前端 `realtime-inference-panel.tsx`（热力图 + 规则链溯源）。

---

## 4. 合规硬化 Gap（🟡/❌）

| 维度 | 当前状态 | 代码证据 | 缺口 |
|---|---|---|---|
| 数据完整性（ALCOA+） | 🟡 | `app/models/reasoning.py:35` `AuditLog`(`audit_log` 表) 已建；grep `prev_hash`/`hash_chain`/`sha256` **零命中** | append-only 哈希链未实现 |
| 权限分级 RBAC | 🟡 | `require_role` 仅用于 `ontology.py` 写接口（senior_analyst）；`app_user`/`app_role` 三角色表已建 | operator/QA 业务流、SSO 未接 |
| 强制 QA 复核 | ❌ | 无电子签名流程 | 高风险/专用化结论无强制复核门禁 |
| 版本可追溯 | ✅ | 发布时 TTL 回写 + Git 提交（`ttl_commit_sha`）+ release 版本归档 | — |
| 可解释性 | ✅ | `run_assessment` 返回 `rules_fired`（rule_id/group/inputs/conclusion/regulation_ref） | — |

---

## 5. 文档与代码的细微出入（需留意，非能力 gap）

1. **前端占位页措辞**：文档 §13 称能力二/三前端组件"未建/未集成"。实际上存在**静态骨架页**：`extraction/page.tsx`、`integration/page.tsx`、`reasoning/page.tsx`、`entities/page.tsx`、`knowledge-graph/page.tsx`。它们是**纯展示骨架**（无功能接线），故文档"能力未具备"的判断成立，但"组件未建"的字面表述不够精确——骨架页是存在的。

2. **冲突解决模块化**：文档把冲突解决描述在 `engine.run_assessment` 内，实际代码拆出独立的 `reasoning/conflict_resolver.py`。属代码优于文档，非 gap。

3. **集成端点**：文档 §9.1 称 `/api/integration` 为"connectors CRUD / test(mock) / specs(硬编码)"，前端 `integration/page.tsx` 实际消费 `getIntegrationSpecs`（specs 端点）。功能上一致（只读 specs 为主），无实质偏差。

---

## 6. 优先级建议（v1.2 更新 —— 原 P1–P5 已全部闭合）

下表 P1–P5 为 v1.0 提出的优先级，**至 v1.2 已全部落地**（002 + 003），保留作沿革：

| 优先级 | Gap | 状态 | 闭合 feature |
|---|---|---|---|
| **P1** | 能力二抽取接线（`create_job` → pipeline + SSE） | ✅ | 002 |
| **P2** | 对齐审核 UI（`alignment-review.tsx`）+ 语义对齐 | ✅ | 002 + 003 |
| **P3** | APS 连接器 + 增量物化 | ✅（外联仍 stub） | 002 |
| **P4** | Action 引擎 + 报告导出 | ✅ | 002 |
| **P5** | 实时看板 + 合规硬化（哈希链/QA 电子签名/RBAC） | ✅ | 002 + 003 |

**后续建议（真实剩余 gap，见 §0 表）**：

| 优先级 | 项 | 工作量 | 理由 |
|---|---|---|---|
| **N1** | 外部事实源真实 HTTP 接入（APS 排期优先，替换 `inline_changes`） | 中 | 框架/物化/事件已就绪，只差生产端点与凭证接线 |
| **N2** | ERP/MES/LIMS/CTMS 连接器实现（当前 `return []`） | 大 | 扩展 N1 至全事实源 |
| **N3** | 企业级 SSO（替换共享密钥重认证） | 中 | 合规硬化收尾 |
| **N4** | 路线 B：迁移数据化为 `OntologyAction`（本体库可治理） | 大 | 待路线 A 需求稳定后评估 |

> 对照方案 §11 路线图：**P0–P5 已完成**；剩余为生产接入（N1/N2）、SSO（N3）与可选的路线 B（N4）。

---

## 附：核验命令记录（可复现）

```bash
# 组件计数
find frontend/src/components/ontology -type f | wc -l        # 11
grep -c "def test_" backend/tests/**/*.py | <sum>            # 36

# 抽取接线核验（确认 create_job 不调用 pipeline）
grep -rn "run_extraction_pipeline" backend/app/api/          # 零命中

# 连接器核验
grep -rn "class.*Connector" backend/app/services/integration/
# → ExternalSystemConnector(ABC), StubConnector

# Action 引擎核验
grep -rln "RaiseDedicationRequirement\|ActionExecution\|action_engine" backend/app/
# → 零命中

# 审计哈希链核验
grep -rn "prev_hash\|hash_chain\|sha256" backend/app/models/  # 零命中

# RBAC 核验
grep -rn "require_role" backend/app/api/                      # 仅 ontology.py
```
