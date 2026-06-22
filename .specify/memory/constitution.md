<!--
Sync Impact Report
==================
Version change: (template, unratified) → 1.0.0
Rationale: Initial ratification — first concrete constitution replacing the unfilled
template. MAJOR baseline established.

Principles defined (5):
  I.   规范驱动开发 (Spec-Driven Development)
  II.  本体权威性与保真 (Ontology Authority & Fidelity) — NON-NEGOTIABLE
  III. 可追溯与审计 (Traceability & Auditability)
  IV.  测试纪律与契约优先 (Test Discipline & Contract-First)
  V.   最小复杂度与复用 (Minimal Complexity & Reuse)

Added sections:
  - 安全与合规 (Security & Compliance)
  - 开发工作流与质量门禁 (Development Workflow & Quality Gates)
  - Governance

Templates requiring updates:
  ✅ .specify/templates/plan-template.md — Constitution Check gate is generic
     ("Gates determined based on constitution file"); resolves against these
     principles with no edit needed.
  ✅ .specify/templates/spec-template.md — principle-agnostic; no change required.
  ✅ .specify/templates/tasks-template.md — principle-agnostic; no change required.
  ✅ CLAUDE.md — SPECKIT managed block points at active plan; no change required.

Follow-up TODOs: none. Ratification date set to first-adoption date 2026-06-20.
-->

# SLPRA 本体智能平台 Constitution

临床药物智能辅助生产平台（基于本体建模的共线生产风险评估平台）的工程宪章。
本宪章统御所有特性的规范、计划、任务与实现，凡冲突以本宪章为准。

## Core Principles

### I. 规范驱动开发 (Spec-Driven Development)

每个特性 MUST 遵循 Spec Kit 流程：`specify → clarify → plan → tasks → implement`，
不得跳级。规范（`spec.md`）是需求与验收的唯一真理来源；实现细节（技术栈、API、
代码结构）MUST NOT 渗入规范层。计划（`plan.md`）及设计制品
（`research.md` / `data-model.md` / `contracts/` / `quickstart.md`）MUST 与规范一致；
当下游发现规范缺口时，MUST 回到 `clarify` 或修订规范，而非在代码中私自决断。

**Rationale**: 平台横跨能力一/二/三，范围易蔓延；流程化与单一真理来源把范围决策
显性化、可审查，降低返工。

### II. 本体权威性与保真 (Ontology Authority & Fidelity) — NON-NEGOTIABLE

权威 TTL（`ontology/slpra/*.ttl`）是知识模型的规范载体。对 T-Box 的任何写入：
- MUST 以**外科式合并**回写 TTL，逐字保留未建模三元组（注释、外部对齐、SWRL）。
- MUST 维持 BFO 上层本体对齐与既有外部对齐（DrOn/ChEBI/ISA-88/IDMP）不被破坏。
- MUST 保持双存储写后一致：可编辑元数据表（编辑期草稿真理来源）与 Owlready2 World
  （发布期物化）一致，失败则整体回滚。
- 写入前 MUST 提供三元组级 diff 预览。

**Rationale**: 本体是临床用药安全推理的根基；任何静默漂移或公理丢失都可能使风险
结论失真。保真与对齐不可让步。

### III. 可追溯与审计 (Traceability & Auditability)

所有模型变更 MUST 可追溯：
- 每个可编辑对象 MUST 带版本号，并发保存采用**乐观并发**冲突检测（冲突即拒绝）。
- 发布 MUST 以**批次**为单元：一次 TTL 导出 + 一次 Git 提交，提交 SHA 入库归档。
- 每次写/发布 MUST 在审计日志记录 actor、动作、实体、批次与时间。
- 已发布内容 MUST NOT 被物理删除或就地篡改；撤销以反向变更的新批次实现。

**Rationale**: 制药生产合规要求变更全程留痕、可回溯、可问责；版本化与 Git 线性历史
提供权威证据链。

### IV. 测试纪律与契约优先 (Test Discipline & Contract-First)

- 对外接口 MUST 先有契约（`contracts/`）再有实现。
- 后端关键路径 MUST 有契约/集成测试（pytest）：CRUD、乐观并发冲突、导出/发布、
  双存储一致性。
- 每个特性 MUST 提供 `quickstart.md` 端到端验证场景，且其判据可执行。
- 发布前 MUST 通过模型健康度/一致性校验；阻断性问题（孤立类、未映射、TTL 漂移、
  停用类被引用、基数矛盾）MUST 拦截发布。

**Rationale**: 双存储 + 本体推理的组合脆弱，契约与测试是防止隐性破坏的护栏。

### V. 最小复杂度与复用 (Minimal Complexity & Reuse)

- MUST 复用既有栈与模式（FastAPI `APIRouter`+`Depends`、SQLAlchemy 2.0
  `Mapped`/`mapped_column`、`OntologyEngine` 加锁双写、前端 `lib/api.ts`+React Query+d3）。
- 新增第三方依赖 MUST 最小化并在 `plan.md` 说明必要性；MUST NOT 引入与现栈冲突的
  并行框架。
- 遵循 YAGNI：未被当前已批准范围要求的能力 MUST NOT 提前构建。

**Rationale**: 平台为内网、小并发、长生命周期系统；克制复杂度优先于炫技，便于维护
与审计。

## 安全与合规 (Security & Compliance)

- 写/发布端点 MUST 基于角色门禁（最小权限）：`senior_analyst` 方可编辑与发布，
  `operator` / `qa` 按职责只读或受限。
- 身份经可信网关注入（`X-User`/`X-Role`），SSO MUST 设计为可插拔后续接入。
- 临床/制药领域数据 MUST 按内网部署与最小暴露原则处理；密钥与凭据 MUST NOT 入库
  或提交至版本库。
- 与推理绑定的合规判定属能力三范围，在其特性正式纳入前 MUST NOT 在能力一中隐式实现。

## 开发工作流与质量门禁 (Development Workflow & Quality Gates)

- `plan.md` MUST 含 Constitution Check，并在 Phase 0 前与 Phase 1 后各评估一次；
  违例 MUST 在 Complexity Tracking 中论证，否则阻断。
- 数据库结构变更 MUST 经 Alembic 迁移；启动应用迁移后由 TTL 幂等投影补种。
- 代码评审 MUST 核查：本体保真、双存储一致、版本/审计落点、角色门禁、依赖最小化。
- 范围变更 MUST 经澄清并回写规范后方可进入实现。

## Governance

本宪章 MUST 优先于其它实践；冲突时以本宪章为准。

- **修订程序**：修订 MUST 以 PR 记录动机与影响，更新版本号与 Sync Impact Report，
  并传播至受影响的模板与文档。
- **版本策略**（语义化）：MAJOR = 原则移除或不兼容治理变更；MINOR = 新增原则/章节
  或实质性扩展；PATCH = 措辞澄清与非语义修订。
- **合规审查**：所有 PR/评审 MUST 验证对本宪章的遵从；复杂度 MUST 被论证；运行期
  开发指引以 `CLAUDE.md` 的 SPECKIT 区块所指计划为准。

**Version**: 1.0.0 | **Ratified**: 2026-06-20 | **Last Amended**: 2026-06-20
