# Implementation Plan: 多源抽取与实时推理 —— 能力二/能力三 GAP 闭合

**Branch**: `002-extraction-realtime-reasoning` | **Date**: 2026-06-22 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `specs/002-extraction-realtime-reasoning/spec.md`

## Summary

闭合能力二（多源抽取与实体对齐）与能力三（实时事实源对齐与 Action 推理）的全部缺口，并落地能力三耦合的合规硬化（审计哈希链、QA 21 CFR Part 11 电子签名、operator/QA 角色门禁）。

技术路径建立在已存在但未接线/仅桩的后端代码之上，以"**接线 + 补缺**"为主线，严格复用既有栈：

- **能力二**：把 `extraction.create_job` 接到已存在的死代码 `run_extraction_pipeline`（经 FastAPI `BackgroundTasks` 异步执行，进度经 SSE 推送）；扩展 pipeline 以支持 Word 正文→Action 候选、LLM 不可用回退、受控词表注入；新增**数据库源读取器**（表结构→Class 候选、外键→LinkType 候选）；前端把 60 行静态 `extraction/page.tsx` 改造为可创建作业 + 进度条 + **新建 `alignment-review` 审核组件**（确认/拒绝/合并/拆分），对接已有 `/jobs/{id}/candidates` 与 `/candidates/{id}/review`。
- **能力三**：实现 `APSConnector`（真实连接器，轮询为主 + 可选 push）替换 `StubConnector`；新增**事实物化服务**（增量归一→A-Box 个体，复用 `KGStore.sync_individual_to_shadow`）+ `fact_materialization_run` 表 + **事实变更事件**；事件触发**受影响子图增量重算**（复用 `reasoning.engine.run_assessment`）；新增 **Action 引擎**（事件驱动编排 + `action_execution` 表 + 内部工单/任务 + 对 APS 建议性回写）；**风险报告导出器**（PDF + JSON 双产物）；前端新建 `realtime-inference-panel`（相容性热力图 + 排期风险 + 规则链溯源，d3）。
- **合规硬化**：为 `audit_log` 增加 `prev_hash`/`entry_hash` 哈希链字段与 append-only 写入 + 完整性校验端点；新增 `electronic_signature` 表与 QA 签名流程（重认证 + 含义 + 绑定结论），未签名前结论 `effective=false` 且抑制对外动作；把 `require_role(operator/qa)` 接到推理/看板/签名端点。

新增第三方依赖：后端 `reportlab`（或等价库，PDF 报告渲染——FR-024 的最小必要依赖；现栈无 PDF 能力）。其余均复用既有 `openpyxl`/`python-docx`/`anthropic`/`sqlalchemy`/`fastapi`/`owlready2` 与前端 `d3`/React Query。

## Technical Context

**Language/Version**: Python 3.12（后端）；TypeScript 5 / Node（前端，Next.js 16 + React 19）

**Primary Dependencies**:
- 后端：FastAPI（`BackgroundTasks` + `StreamingResponse`/SSE）、SQLAlchemy 2.0、Alembic、Pydantic v2、Owlready2、`openpyxl`/`python-docx`（已用于解析）、`anthropic`（LLM 抽取，已接）、**`reportlab`（新增，PDF 报告）**；DB 源读取器复用 SQLAlchemy `inspect()` 反射（无新依赖）
- 前端：Next.js 16、React 19、`@tanstack/react-query`、`@tanstack/react-table`、`zustand`、`d3`（热力图/图谱）、`lucide-react`、Tailwind；SSE 经浏览器原生 `EventSource`

**Storage**:
- PostgreSQL 16（库 `slpra`）：新增 `fact_materialization_run`、`action_execution`、`electronic_signature` 表；扩展 `audit_log`（哈希链列）、`integration_connectors`（同步水位/调度列）、`reasoning_executions`（生效状态/签名外键）；既有 `extraction_*`、`entity_shadow`、`ontology_meta` 等不变
- Owlready2 OWL 存储（SQLite World，`OWL_STORE_PATH`）：事实物化写 A-Box 个体（经 `OntologyEngine` + `KGStore` 影子同步）
- 权威 TTL：本特性**不回写 T-Box**（抽取产出经审核后写 A-Box/影子，不改公理）；DB 源读取器产出的 Class/LinkType 候选仅进入抽取审核队列，发布回 T-Box 属能力一工作台职责，不在本特性自动执行

**Testing**: pytest（后端，`backend/tests/test_api/`）——新增抽取接线/对齐审核、APS 连接器、事实物化幂等、增量重算、Action 编排、哈希链完整性、QA 签名门禁、报告导出契约/集成测试；前端以 `quickstart.md` 手动端到端验证为主

**Target Platform**: Linux 服务器，docker-compose 三服务（db / backend / frontend），内网部署

**Project Type**: web（backend + frontend 分离，独立后端 API）

**Performance Goals**: 近实时联动——事实变更→结论/看板刷新 **≤ 5 秒**（含轮询 ≤2s + 物化 + 受影响子图增量重算 + 推送）；抽取为离线批处理（异步后台，无硬时延）；低并发（个位数分析师/操作员）

**Constraints**:
- 增量重算 MUST 限定受影响子图（按设备/产品/区域），不得全量重算；重算风暴下可合并/限流并降级为批量刷新（标注）
- 事实物化 MUST 幂等（以连接器同步水位 + 事实版本去重），事实源超时 MUST 保留上一良好状态、不污染知识库
- 审计 MUST append-only + 哈希链（`entry_hash = H(prev_hash ‖ 规范化记录)`），禁止就地改写
- 高风险/专用化/合规阻断结论 MUST 经 QA 21 CFR Part 11 电子签名（重认证 + 含义 + 绑定）方可 `effective`，未签名抑制对外动作
- Action 工单/任务为**平台内部记录**；对 APS 仅**建议性回写**，不改写外部权威数据
- 复用既有推理内核与抽取内核，不重复实现

**Scale/Scope**: 7 本体模块；事实实例 10²–10⁴ 量级；连接器个位数（首发 APS）；事实变更事件低频（分钟级批次 + 偶发突发）；前端看板设备×产品矩阵 10²–10³ 单元

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

`.specify/memory/constitution.md` v1.0.0。按五项原则与两节附加约束评估：

| 原则 / 章节 | 本计划如何满足 | 门禁 |
|------------|----------------|------|
| I. 规范驱动开发 | 严格走 specify→clarify→plan；范围经 4 项澄清固化（混合接入/Part 11/内部工单/PDF+JSON）；规范为真理来源 | PASS |
| II. 本体权威性与保真（NON-NEGOTIABLE） | 本特性**不回写权威 TTL**；事实物化只增删 A-Box 个体（不动 T-Box 公理），经 `OntologyEngine` 加锁写 + 影子同步；DB 源产出的 T-Box 候选仅入审核队列、不自动发布 | PASS |
| III. 可追溯与审计 | 审计升级为 append-only 哈希链 + 完整性校验；动作执行/物化运行/签名全程留痕；结论生效状态可追溯 | PASS |
| IV. 测试纪律与契约优先 | `contracts/` 先行（抽取/对齐/集成/物化/Action/签名/报告/审计端点）；pytest 覆盖接线、幂等、增量重算、哈希链、签名门禁、报告产物 | PASS |
| V. 最小复杂度与复用 | 复用 `run_extraction_pipeline`/`aligner`/`reasoning.engine`/`KGStore`/`ExternalSystemConnector` 抽象/`require_role`；仅新增 `reportlab`（已论证）；SSE/事件用现栈，不引入消息中间件（push 为可选适配器） | PASS |
| 安全与合规 | operator/QA 门禁经既有 `require_role` 扩展；QA Part 11 电子签名；密钥经 `settings`/env，不入库；连接器凭据经 `connection_config` 但敏感项不落明文（research R7） | PASS |
| 开发工作流与质量门禁 | 本节为 Phase 0 前门禁；新表/列全部走 Alembic 迁移（接到既有启动迁移链）；评审核查保真/审计/门禁/依赖最小化 | PASS |

**Gate result**: PASS（无宪法违例；Complexity Tracking 无需填写）。

唯一需关注点：**push 接入**会引入"被动接收事件"路径。为遵守原则 V（不引入与现栈冲突的并行框架），push 设计为**可选的 Webhook 端点**（FastAPI 路由接收推送 → 落入与轮询相同的物化队列），不引入 Kafka/RabbitMQ 等中间件。默认运行仅靠轮询即满足 ≤5s。

## Project Structure

### Documentation (this feature)

```text
specs/002-extraction-realtime-reasoning/
├── plan.md              # 本文件
├── research.md          # Phase 0：关键技术决策（R1–R12）
├── data-model.md        # Phase 1：新增/扩展表与状态机
├── quickstart.md        # Phase 1：US1–US6 端到端验证指南
├── contracts/
│   ├── extraction-alignment-api.md   # 能力二：作业接线/进度SSE/对齐审核/DB源
│   ├── integration-realtime-api.md   # 能力三：连接器/物化/事件/增量重算/看板
│   ├── action-report-api.md          # 能力三：Action 引擎/工单·任务/报告导出
│   └── compliance-audit-api.md       # 跨切：哈希链校验/QA 电子签名/RBAC
├── checklists/
│   └── requirements.md  # /speckit-specify 产出的质量清单
└── tasks.md             # Phase 2（/speckit-tasks 生成，本命令不创建）
```

### Source Code (后端 backend/ + 前端 frontend/)

既有 web 结构。新增/修改集中在抽取、集成、推理子系统与对应前端页：

```text
backend/
├── app/
│   ├── api/
│   │   ├── extraction.py          # 改造：create_job 触发 pipeline(BackgroundTasks) + GET /jobs/{id}/progress (SSE) + 候选 merge/split 端点 + DB 源作业
│   │   ├── integration.py         # 扩展：connectors 真实 test/sync、/connectors/{id}/sync、/facts、/events、/dashboard、webhook 接收
│   │   ├── reasoning.py           # 扩展：/assess 写结论生效状态；/conclusions/{id} 查询；/incremental 增量重算入口
│   │   ├── actions.py             # 新增：/actions（动作执行列表）、工单/任务状态流转
│   │   ├── reports.py             # 新增：/reports/{conclusion_id}（PDF + JSON 导出）
│   │   └── compliance.py          # 新增：/audit/verify（哈希链校验）、/signatures（QA 电子签名）
│   ├── services/
│   │   ├── extraction/
│   │   │   ├── pipeline.py        # 扩展：Word→Action 候选、LLM 回退、受控词表注入、进度回调
│   │   │   ├── db_reader.py       # 新增：DB 源读取器（SQLAlchemy inspect → Class/LinkType 候选）
│   │   │   └── progress.py        # 新增：作业进度事件总线（内存 pub/sub，供 SSE）
│   │   ├── integration/
│   │   │   ├── aps_connector.py   # 新增：APSConnector（轮询 + 可选 push）替换 Stub
│   │   │   ├── materializer.py    # 新增：增量归一 + A-Box 物化（幂等/水位） + 发布事实变更事件
│   │   │   └── events.py          # 新增：事实变更事件总线 + 受影响子图解析
│   │   ├── reasoning/
│   │   │   ├── incremental.py     # 新增：受影响子图增量重算编排（复用 engine.run_assessment）
│   │   │   └── action_engine.py   # 新增：结论→动作编排（工单/任务/告警/建议回写/报告触发）
│   │   ├── reporting/
│   │   │   └── risk_report.py     # 新增：RiskAssessmentReport 渲染（PDF via reportlab + JSON）
│   │   └── audit.py               # 新增：append-only 哈希链写入 + 完整性校验（封装 AuditLog）
│   ├── models/
│   │   ├── integration.py         # 扩展：IntegrationConnector(sync_cursor/poll_interval/mode) + FactMaterializationRun
│   │   ├── reasoning.py           # 扩展：AuditLog(prev_hash/entry_hash) + ReasoningExecution(effective/signature_id) + ActionExecution + ElectronicSignature
│   │   └── extraction.py          # 扩展：ExtractionCandidate(candidate_kind: instance/class/link/action; group_key; canonical) 
│   ├── schemas/
│   │   ├── extraction.py          # 扩展：进度、合并/拆分、DB 源请求
│   │   ├── integration.py         # 新增：连接器/物化/事件/看板 DTO
│   │   ├── reasoning.py           # 扩展：结论生效/动作/签名 DTO
│   │   └── reporting.py           # 新增：报告 DTO
│   ├── dependencies.py            # 扩展：get_action_engine / get_materializer / require_role 复用
│   ├── config.py                  # 扩展：poll 间隔、报告输出目录、APS 连接配置项
│   └── main.py                    # 扩展：注册 actions/reports/compliance 路由；启动调度（轮询循环 asyncio task）
├── alembic/versions/
│   └── 0002_extraction_realtime.py   # 新增：上述新表 + 列扩展迁移
└── tests/test_api/
    ├── test_extraction_pipeline.py    # 接线 + 进度 + 回退 + DB 源
    ├── test_alignment_review.py       # 跨源归组 + 确认/拒绝/合并/拆分
    ├── test_aps_connector.py          # 真实连接器契约 + 超时保留
    ├── test_fact_materialization.py   # 幂等 + 水位 + 事件发布
    ├── test_incremental_reasoning.py  # 受影响子图重算 + ≤5s 行为
    ├── test_action_engine.py          # 工单/任务/建议回写 + 留痕 + 签名抑制
    ├── test_compliance.py             # 哈希链完整性 + Part 11 签名门禁 + RBAC
    └── test_risk_report.py            # PDF + JSON 产物

frontend/src/
├── app/(dashboard)/
│   ├── extraction/page.tsx        # 改造：创建作业 + 进度条(SSE) + 嵌入审核组件
│   ├── integration/page.tsx       # 改造：连接器管理 + 同步触发 + 物化运行列表
│   └── reasoning/page.tsx         # 改造：结论列表 + QA 签名入口
├── components/extraction/         # 新增
│   ├── job-create-form.tsx        # 上传/选择源 + 配置 + 创建
│   ├── job-progress.tsx           # SSE 进度
│   └── alignment-review.tsx       # 候选审核：确认/拒绝/合并/拆分 + 跨源归组
├── components/integration/        # 新增
│   ├── connector-manager.tsx      # 连接器 CRUD + 测试 + 同步
│   └── realtime-inference-panel.tsx  # 相容性热力图(d3) + 排期风险 + 规则链溯源
├── components/reasoning/          # 新增
│   └── qa-signature-dialog.tsx    # Part 11 重认证 + 含义 + 签名
└── lib/api.ts                     # 扩展：抽取/集成/物化/动作/报告/签名/审计方法
```

**Structure Decision**: 沿用既有 **web（backend + frontend）** 结构。后端按子系统分层新增服务模块（`extraction/db_reader`、`extraction/progress`、`integration/{aps_connector,materializer,events}`、`reasoning/{incremental,action_engine}`、`reporting/risk_report`、`audit`），把每个 gap 收敛到独立、可测的服务，保持 API 层薄。事件与进度推送用**进程内事件总线 + SSE**（不引入消息中间件），轮询调度用启动期 `asyncio` 后台任务，满足 ≤5s 且符合原则 V 最小复杂度。前端在现有 Next.js/React Query/d3 栈中新建组件，改造现有占位页。

## Complexity Tracking

> 无宪法违例，无需填写。

新增第三方依赖仅后端 `reportlab`（PDF 报告渲染，FR-024 的最小必要依赖；现栈无 PDF 能力，前端渲染无法满足"可归档 PDF 产物 + QA 签批"的合规要求）。push 接入以可选 Webhook 路由实现，刻意**不引入**消息中间件，避免并行框架与运维复杂度。
