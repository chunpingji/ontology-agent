# Implementation Plan: 分析结论工作流状态机闭环 —— 结论流水线自举与显式生命周期

**Branch**: `003-workflow-statemachine-closure` | **Date**: 2026-06-22 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/003-workflow-statemachine-closure/spec.md`

## Summary

闭合 002 结论中心流水线的三处自举接线缺口并显式化其隐式状态机，采纳设计文档 §6 **路线 A**：

- **G1（评估即落库）**：让无状态的 `POST /api/reasoning/assess` 在返回结论的同时，把结论持久化为一条带唯一标识与**初始生命周期状态**的 `ReasoningExecution`，并经既有 `ActionEngine.orchestrate` 编排其隐含动作——使"评估 → 复核/签批 → 动作 → 报告"全链路有源头、可自举。
- **G2（强制 QA 闸门自动 arm）**：在落库时据风险特征（高总体风险等级 OR 需专用化 OR 命中青霉素/头孢/高致敏高危场景）自动判定 `requires_signature`，高风险结论入 `待签` 态、动作全抑制；低风险落库即 `生效`。
- **G3（事实变更自动重算）**：为既有 `fact_event_bus`（物化层已发布携子图事件、但零订阅者）注册一个进程内订阅者，桥接到既有 `incremental.recompute_subgraph`，使"事实变更 → 结论刷新"在 ≤5s 近实时窗口内**无人工触发**完成。
- **显式生命周期（路线 A 硬化）**：把分散在 `effective`/`superseded_by`/`ActionExecution.status` 三处的隐式状态固化为 `reasoning_executions.lifecycle_state` 显式四态枚举（`pending_signature`/`effective`/`superseded`/`rejected`），并以**单一迁移合法性来源**（新 `lifecycle.py`）集中守卫所有入口（落库、签批、QA 拒绝、增量取代），非法迁移一致拒绝且不改状态。新增 `POST /api/compliance/reject`（QA 拒绝 → `rejected` 终态 + 作废被抑制动作）。

**技术取向**：复用 002 已交付的推理内核、合规机制（哈希链审计、Part 11 签名、Action 编排、增量重算/取代链、报告导出）**不改写**；只补"接通源头（G1/G2/G3）"与"显式化状态机"两层。本特性**不**写回权威 TTL，**不**纳入路线 B（迁移定义数据化为 `OntologyAction`）。

## Technical Context

**Language/Version**: Python 3.12（后端，主战场）；TypeScript / Next.js 16 / React 19（前端，仅因 `/assess` 响应扩展做兼容性核对，无新页面）

**Primary Dependencies**: FastAPI（`APIRouter`+`Depends`）、SQLAlchemy 2.0（`Mapped`/`mapped_column`）、Alembic（迁移链）、Pydantic v2、Owlready2（推理 World，复用 `ontology_engine`）；无新增第三方依赖

**Storage**: PostgreSQL 16（库 `slpra`）；结构变更经**单一 Alembic 迁移** `0003_workflow_statemachine.py`（`down_revision="0002_extraction_realtime"`），接既有启动迁移链（`main._run_migrations → upgrade head`）。测试用共享内存 SQLite（`StaticPool`）

**Testing**: pytest（`TestClient` + `StaticPool` + `FakeOntologyEngine`，见 `backend/tests/conftest.py`）；契约/集成测试覆盖状态机、四类迁移、QA 拒绝、自动重算订阅者、动作作废

**Target Platform**: Linux 服务器，docker-compose（db/backend/frontend）内网部署

**Project Type**: web（backend + frontend）——本特性后端中心，前端仅消费扩展后的 `/assess` 响应

**Performance Goals**: "事实变更 → 结论刷新"端到端时延 **≤ 5 秒**（沿用 002 近实时口径；APS 轮询默认 2s + 重算）

**Constraints**:
- 审计为**全局单链**追加式哈希链（非每实例链）；写经单写路径 `audit.py`，禁止 UPDATE/DELETE
- QA 电子签名为 **21 CFR Part 11** 级（重认证 + 记录含义/时间/签名人 + 不可分割绑定）
- 对外回写为**建议性、非权威**；`not_accepted` ≠ 失败
- 角色门禁经可信网关头（`X-User`/`X-Role`）：`senior_analyst`/`operator`/`qa`
- 本特性**不写回权威 TTL**，无 T-Box 写入（宪章 II 不触发）
- 密钥/凭据（`qa_reauth_secret`）经 env/`settings` 注入，MUST NOT 入库或提交

**Scale/Scope**: 内网小并发、长生命周期。改动面：~5 个既有后端文件（`api/reasoning.py`、`api/compliance.py`、`api/actions.py`、`services/reasoning/incremental.py`、`main.py`）、~3 个新建服务模块（`services/reasoning/lifecycle.py`、`services/reasoning/recompute_subscriber.py`、`services/reasoning/risk.py` 高风险判据）、1 个模型扩展（`models/reasoning.py` 加 `lifecycle_state`）、1 个 Alembic 迁移

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

依据 `.specify/memory/constitution.md` v1.0.0 五原则逐条核验：

| 原则 | 适用性与符合性 | 状态 |
|---|---|---|
| **I. 规范驱动开发** | 经 `specify → clarify`（4 项澄清已记入 spec）→ 本 `plan`；规范为唯一真理来源，本计划与 spec 的 FR-001~FR-020 对齐；实现细节仅存于 plan/research/contracts，未渗入 spec。 | ✅ PASS |
| **II. 本体权威性与保真（NON-NEGOTIABLE）** | 本特性**不写回权威 TTL、无 T-Box 写入**：仅持久化 A-Box 推理结论（`reasoning_executions`）与状态流转，复用既有 `ontology_engine` 只读推理。外科式合并/BFO 对齐/双存储/三元组 diff 等约束在本特性**不触发**（无授权写路径）。 | ✅ PASS（N/A） |
| **III. 可追溯与审计** | 每次落库、状态迁移、签批、QA 拒绝、动作派发/作废、取代均经单写路径 `audit.py` 写入全局追加式哈希链；已写审计 MUST NOT 物理删除或就地篡改；链验真定位首个断裂点（复用 002 `verify`）。 | ✅ PASS |
| **IV. 测试纪律与契约优先** | 对外接口先有契约（`contracts/`）后实现：`/assess`（变更）、`/api/compliance/reject`（新）；关键路径 pytest 覆盖——状态机四类合法/非法迁移、自动重算订阅者、动作作废、角色门禁、审计链连续。补 002 遗留的 `recompute_subgraph`/`ElectronicSignature` 测试空白。 | ✅ PASS |
| **V. 最小复杂度与复用** | 复用既有栈与模式（FastAPI `Depends`、SQLAlchemy 2.0、`ontology_engine` 双写锁、React Query）；**无新增第三方依赖**；新模块（`lifecycle.py`/`recompute_subscriber.py`/`risk.py`）均为薄编排层，不引入并行框架；遵循 YAGNI——路线 B 显式排除。 | ✅ PASS |

**安全与合规**：写/迁移/签批端点据角色门禁（`senior_analyst` 评估与流转、`qa` 签批/拒绝、`operator` 只读）；`qa_reauth_secret` 经 env 注入不入库；身份经 `X-User`/`X-Role` 网关头、SSO 可插拔后续接入——均与宪章「安全与合规」章一致。

**质量门禁**：结构变更经 Alembic 迁移（`0003`），启动后由既有 TTL 幂等投影补种不受影响；本计划含 Phase 0 前 / Phase 1 后两次 Constitution Check。

**结论（Phase 0 前初次门禁）**：**PASS**，无违例，**Complexity Tracking 为空**。

**Phase 1 设计后复检**：设计制品（research.md / data-model.md / contracts/ / quickstart.md）未引入任何新违例——

- **II（NON-NEGOTIABLE）**：data-model 确认仅扩展 `reasoning_executions` 一列（`lifecycle_state`）+ `action_execution` 一个枚举值，**无 T-Box / 权威 TTL 写路径**，宪章 II 仍不触发。
- **III**：迁移表（T1–T5）每条均绑定一个审计动作（`reasoning.persist`/`reasoning.transition`/`compliance.sign`/`compliance.reject`/`reasoning.recompute`/`action.void`），经单写路径 `audit.py`、追加式哈希链，复检通过。
- **IV**：两份 contracts 先于实现定义对外契约（`/assess` 变更、`/reject` 新增）；quickstart 给出 US1–US4 可执行判据与 pytest 入口，契约优先满足。
- **V**：未新增第三方依赖；新模块（`lifecycle.py`/`risk.py`/`recompute_subscriber.py`）均薄编排层、复用既有内核；路线 B 仍排除。

**复检结论**：**PASS**，无新增违例，**Complexity Tracking 维持为空**。

## Project Structure

### Documentation (this feature)

```text
specs/003-workflow-statemachine-closure/
├── plan.md              # 本文件（/speckit-plan 输出）
├── research.md          # Phase 0 输出（设计决策）
├── data-model.md        # Phase 1 输出（lifecycle_state + 状态机 + 迁移表）
├── quickstart.md        # Phase 1 输出（US1–US4 端到端验证）
├── contracts/           # Phase 1 输出
│   ├── assess-bootstrap-api.md     # G1/G2：/assess 落库 + 自动 arm + 扩展响应
│   └── lifecycle-guard-api.md      # P4/拒绝/G3：迁移表 + reject 端点 + 动作作废 + 自动重算契约
├── checklists/
│   └── requirements.md  # 规范质量检查单（已全过）
└── tasks.md             # Phase 2 输出（/speckit-tasks，非本命令产出）
```

### Source Code (repository root)

```text
backend/
├── app/
│   ├── models/
│   │   └── reasoning.py            # ➕ ReasoningExecution.lifecycle_state；ActionExecution 增 voided 语义
│   ├── api/
│   │   ├── reasoning.py            # ✏️ /assess：落库 + orchestrate + 角色门禁 + 扩展响应（G1/G2 站点）
│   │   ├── compliance.py           # ✏️ sign 经 lifecycle 守卫；➕ POST /reject（QA 拒绝，FR-020）
│   │   └── actions.py              # ✏️ patch_action 增 from-status 守卫 + voided 终态
│   ├── services/
│   │   ├── reasoning/
│   │   │   ├── lifecycle.py        # 🆕 LifecycleState 枚举 + LEGAL_TRANSITIONS + transition()（单一守卫，FR-014/015/016）
│   │   │   ├── risk.py             # 🆕 requires_qa_signature() 高风险判据（G2，FR-005）
│   │   │   ├── recompute_subscriber.py  # 🆕 make_recompute_subscriber()（G3 桥接，FR-010/013）
│   │   │   ├── incremental.py      # ✏️ 取代经 lifecycle.transition；旧动作作废（FR-012）
│   │   │   └── action_engine.py    # ♻️ 复用 orchestrate（落库时编排）
│   │   └── integration/
│   │       ├── events.py           # ♻️ fact_event_bus（既有 publish/subscribe）
│   │       └── materializer.py     # ♻️ run_sync → fact_event_bus.publish（既有发布点）
│   ├── schemas/
│   │   └── reasoning.py            # ✏️ AssessmentResponse 增 conclusion_id/lifecycle_state/requires_signature/effective
│   ├── main.py                    # ✏️ lifespan 注册 recompute_subscriber 到 fact_event_bus（幂等）
│   └── config.py                  # ♻️ 既有 settings（qa_reauth_secret 等）
├── alembic/versions/
│   └── 0003_workflow_statemachine.py   # 🆕 lifecycle_state 列 + 索引 + 回填（down_revision=0002_extraction_realtime）
└── tests/
    ├── conftest.py                # ♻️ 既有 fixtures（client/db/*_headers/FakeOntologyEngine）
    └── test_api/
        ├── test_assess_bootstrap.py     # 🆕 US1/US2：落库自举 + 自动 arm
        ├── test_lifecycle_machine.py    # 🆕 US4：四类合法/非法迁移 + 多入口一致
        ├── test_qa_reject.py            # 🆕 FR-020：拒绝 → rejected + 动作作废
        └── test_auto_recompute.py       # 🆕 US3：事件订阅 → 自动重算 + 待签跳过 + 旧动作作废

frontend/
└── src/lib/                       # ♻️ 仅核对消费扩展后的 /assess 响应（无新页面）
```

**Structure Decision**: 沿用 002 既定的 **web（backend + frontend）** 单仓布局。本特性集中于 `backend/app`——`api/` 改三个既有路由 + 加一个端点，`services/reasoning/` 新增三个薄编排模块并改 `incremental.py`，`models/reasoning.py` 加一列，`main.py` 在 lifespan 注册订阅者，单一 Alembic 迁移落列与回填。前端不新增页面，仅核对 `/assess` 响应向后兼容。

## Complexity Tracking

> 无宪章违例，无需论证。本特性以最小增量闭合既有流水线缺口，未引入新框架、新依赖或并行架构；新增模块均为复用既有内核的薄编排层。

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| （无） | — | — |
