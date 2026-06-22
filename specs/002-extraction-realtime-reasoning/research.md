# Phase 0 Research: 多源抽取与实时推理 —— 能力二/能力三 GAP 闭合

**Feature**: `002-extraction-realtime-reasoning` | **Date**: 2026-06-22 | **Plan**: [plan.md](./plan.md)

本文件汇总 12 项关键技术决策（R1–R12）。每项以 **Decision / Rationale / Alternatives considered** 三段式记录。所有 [NEEDS CLARIFICATION] 均已在 spec 的 Clarifications 段固化，无残留。研究主线：**最大化复用既有栈与既有内核，以"接线 + 补缺"闭合 gap**，仅在 PDF 渲染处引入唯一新依赖。

---

## R1 — 抽取流水线接线：BackgroundTasks + 进程内进度总线 + SSE

**Decision**: `create_job` 写入作业后，经 FastAPI `BackgroundTasks` 异步调用既有 `run_extraction_pipeline`；流水线各阶段（parsing→extracting→aligning→reviewing）经**进程内进度事件总线**（`extraction/progress.py`，`asyncio.Queue`/发布订阅）上报；前端经 `GET /jobs/{id}/progress` 的 **SSE（`StreamingResponse`, `text/event-stream`）** 订阅，浏览器用原生 `EventSource`。

**Rationale**:
- gap-analysis 标注 P1 "只差一根线"——`run_extraction_pipeline` 已完整实现且为零调用方死代码，最小改动即激活。
- `BackgroundTasks` 是 FastAPI 原生能力，无需 Celery/RQ 等任务队列（符合宪法原则 V 最小复杂度）。
- 低并发（个位数分析师）、抽取为离线批处理无硬时延，进程内队列足够；SSE 单向推送契合"进度只读流"，比 WebSocket 更轻。

**Alternatives considered**:
- *Celery + Redis broker*：引入消息中间件与额外运维，违反原则 V；并发量不需要。
- *轮询 `GET /jobs/{id}` 状态*：可行但进度粒度粗、体验差；SSE 复用同一进程总线，增量小。
- *WebSocket*：双向能力对"进度只读"过剩；EventSource 自带断线重连，更简单。

---

## R2 — 数据库源读取器：SQLAlchemy `inspect()` 反射

**Decision**: 新增 `extraction/db_reader.py`，用 SQLAlchemy `inspect(engine)` 反射目标库的表结构与外键：**表 → 实体类候选（candidate_kind=class）**，**列 → 数据属性候选**，**外键 → 关系候选（candidate_kind=link）**。读取器产出统一候选结构，进入与 Excel/Word 相同的对齐审核队列。连接信息经作业配置传入，**只读**访问，敏感连接串不落明文（见 R7）。

**Rationale**:
- SQLAlchemy 已是后端依赖，`inspect()` 反射零新依赖即可获得 schema/FK 元数据。
- 与 FR-012 完全对应（表结构→实体、外键→关系），且复用既有 `ExtractionCandidate` + 审核闭环，不新增并行路径。
- T-Box 候选（Class/LinkType）**仅入审核队列、不自动发布**，符合宪法原则 II（本体权威性）——发布回 T-Box 属能力一工作台职责。

**Alternatives considered**:
- *为每种 DB 写专用驱动 SQL*：重复造轮子，违反原则 V。
- *直接物化 DB 行为 A-Box*：越过人工审核，违反"人在环"与原则 II；DB 源服务于知识建模而非事实流（事实流由连接器负责，见 R4）。

---

## R3 — 抽取增强：Word 正文→Action 候选、受控词表注入、LLM 回退

**Decision**: 扩展 `pipeline.py` 与 `llm_extractor.py`：
- 表格行→实例候选；正文"若…则…必须…"→**带前置/后置条件的 Action 候选**（candidate_kind=action）。
- 抽取提示注入领域**受控词表**（OEB/PDE/材质/洁净级别）以约束自由文本。
- `anthropic` 不可用（无 key 或调用失败）时**回退到结构化原始数据抽取**（既有 `llm_extractor` 已有 fallback 雏形），记录 `degraded_reason` 而非失败。

**Rationale**: 直接满足 FR-004/005/006/007；`llm_extractor` 已具回退骨架，扩展成本低；受控词表注入是 prompt 工程，无架构改动。

**Alternatives considered**:
- *正则硬抽逻辑句*：脆弱、覆盖差；LLM + 受控词表更稳健，回退保证可用性下限。
- *失败即报错*：违反 FR-007 与边界用例"抽取无外部模型能力"。

---

## R4 — APS 真实连接器：轮询为主 + 可选 Webhook push

**Decision**: 新增 `integration/aps_connector.py` 实现既有 `ExternalSystemConnector(ABC)`，替换 `StubConnector`。接入采用**混合**：默认**短周期轮询**（`poll_interval` 默认 ≤ 2s，启动期 `asyncio` 后台任务驱动）；对支持推送的源提供**可选 Webhook 路由**（`POST /api/integration/connectors/{id}/webhook`），push 与 poll **汇入同一物化队列**。连接器复用统一抽象以便后续 ERP/MES/LIMS/CTMS 增量接入。

**Rationale**:
- 对应 Clarification "混合接入" 与 FR-014；轮询 ≤2s 为 ≤5s 端到端目标留物化+重算余量（见 R6）。
- Webhook 作为**可选适配器**接入同一队列，**不引入 Kafka/RabbitMQ**，遵守原则 V（plan.md Gate 唯一关注点已记录）。
- 复用 `ExternalSystemConnector` 抽象 → 后续连接器零架构改动。

**Alternatives considered**:
- *纯 push/消息队列*：引入中间件与运维复杂度，违反原则 V；内网低频事实流不需要。
- *纯轮询*：满足时延但对突发变更不够及时；保留可选 push 兼顾。

---

## R5 — 事实物化：增量归一 + A-Box 个体 + 幂等水位

**Decision**: 新增 `integration/materializer.py`：连接器增量 → 归一到本体标识 → 经 `OntologyEngine`（加锁写）+ 既有 `KGStore.sync_individual_to_shadow` 物化为 **A-Box 个体**（不动 T-Box）。幂等以 **(连接器同步水位 sync_cursor + 事实版本/内容哈希)** 去重；每次物化写一条 `fact_materialization_run`（来源/水位/变更条目/事件引用/状态）。事实源超时/不可达时**保留上一良好状态、记录失败并告警**，不写入污染数据。

**Rationale**: 对应 FR-015/016/018/019 与边界"重复/乱序事实事件""事实源超时"。复用 `KGStore` 影子同步桥接 Owlready2↔PG，零新桥接代码；水位+版本去重保证乱序/重投幂等。

**Alternatives considered**:
- *每次全量重物化*：违反"增量"与 ≤5s 目标，且易抖动。
- *直接写 PG 影子跳过 OWL*：破坏本体保真（原则 II）；必须经 `OntologyEngine` 落 A-Box 再影子同步。

---

## R6 — 受影响子图增量重算：事件驱动 + 复用 `engine.run_assessment`

**Decision**: 新增 `reasoning/incremental.py`：订阅事实变更事件（`integration/events.py`），由事件携带的受影响实体解析**受影响子图**（相关设备/产品/区域），仅对该子图调用既有 `reasoning.engine.run_assessment` 重算，刷新结论与看板，端到端目标 **≤ 5s**。重算风暴时**按子图键合并/限流**，必要时降级为批量刷新并标注。

**Rationale**: 对应 FR-017/026/SC-005。`run_assessment` 已是完备内核，增量层只做"受影响范围解析 + 调度"，不重复实现推理（原则 V/FR-027）。进程内事件总线足够低频事实流。

**Alternatives considered**:
- *全量重算*：被宪法约束明确禁止；个位数设备也应限定子图以稳定时延。
- *定时批量重算*：达不到 ≤5s 近实时；事件驱动 + 合并限流兼顾时延与抗风暴。

---

## R7 — 连接器凭据安全：env/settings 注入，敏感项不落明文

**Decision**: 连接器非敏感配置存 `integration_connectors.connection_config`（JSON）；**敏感凭据（口令/密钥）经 `settings`/环境变量注入，不写库、不入版本库**。`connection_config` 仅存引用键（如 env 变量名）或经平台密钥封装的句柄，日志与审计**脱敏**。

**Rationale**: 宪法"安全与合规"明确"密钥与凭据 MUST NOT 入库或提交至版本库"。与既有 `config.Settings`（已托管 `anthropic_api_key`/`database_url`）一致。

**Alternatives considered**:
- *明文存 `connection_config`*：直接违反宪法安全约束。
- *引入 Vault*：内网单实例过重；env + settings 已满足，后续可平滑替换。

---

## R8 — 审计 append-only 哈希链

**Decision**: 扩展 `AuditLog` 增 `prev_hash`、`entry_hash` 列；新增 `audit.py` 封装**唯一写入路径**：`entry_hash = SHA-256(prev_hash ‖ 规范化记录)`，`prev_hash` 取链上最后一条 `entry_hash`（创世为全 0/空）。写入**只追加**，禁止 UPDATE/DELETE。新增 `GET /api/compliance/audit/verify` 顺序重算校验、检测篡改并**定位首个断裂记录**。规范化采用稳定字段序列化（排序键 + 固定编码）。

**Rationale**: 对应 FR-028/029/SC-008 与 ALCOA+（宪法原则 III）。SHA-256 标准库即可，无新依赖；单写入路径保证链不被旁路。

**Alternatives considered**:
- *Merkle 树/区块结构*：对单写者顺序日志过设计；线性哈希链已满足可检测+可定位。
- *数据库触发器防改*：补充手段，但完整性证明仍需哈希链；以应用层单写路径为主。

---

## R9 — QA 电子签名：21 CFR Part 11 级

**Decision**: 新增 `electronic_signature` 表与 `POST /api/compliance/signatures` 流程：签名时**重新认证（用户名+密码）**、记录**签名含义（meaning）/时间/签名人**，并经外键**不可分割地绑定**到 `reasoning_executions` 结论记录。`ReasoningExecution` 增 `effective`（默认 false）与 `signature_id`；高风险/专用化/合规阻断结论**未签名前 `effective=false`、抑制对外动作**（工单/告警/回写置待签名）。签名为防抵赖，写入审计哈希链。

**Rationale**: 对应 Clarification "21 CFR Part 11 级" 与 FR-030/SC-009、边界"QA 签名缺位下的动作触发"。复用既有 `dependencies` 认证（X-User/X-Role）扩展重认证；身份层保持可插拔（SSO 后续）。

**Alternatives considered**:
- *勾选式确认*：不满足 Part 11 重认证/绑定/防抵赖。
- *外部签名服务*：内网过重；平台内电子签名 + 哈希链留痕已满足合规基线。

---

## R10 — RBAC 扩展到 operator/QA

**Decision**: 复用既有 `dependencies.require_role` 与 `Identity`（X-User/X-Role；`ROLE_SENIOR_ANALYST`/`ROLE_OPERATOR`/`ROLE_QA` 已定义），把门禁接到能力三端点：**operator 只读推理/看板**；**QA 可复核与签名**；**维护/发布仍限 senior_analyst**。身份层保持可插拔以便后续接入企业 SSO（不在本特性范围）。

**Rationale**: 对应 FR-031/SC-010。三角色与 `app_user`/`app_role` 表已存在，`require_role` 模式已在 ontology 写接口验证，扩展成本低、风格一致（原则 V）。

**Alternatives considered**:
- *本特性内做 SSO*：明确范围外；先固化角色边界，身份来源后置。
- *细粒度 ABAC/策略引擎*：对三角色过设计。

---

## R11 — Action 引擎：事件驱动编排 + 内部记录 + 建议性回写

**Decision**: 新增 `reasoning/action_engine.py` + `action_execution` 表。结论生效（且通过 R9 签名门禁）后编排：**需专用化 → 专用化工单 + 告警**；**需灭活/再清洁 → 灭活验证/清洁再验证任务**；**不相容同设备同时段 → 标记冲突 + 阻断排期 + 对 APS 建议性回写**；**触发报告生成**。工单/任务为**平台内部记录**（可人工流转状态），对外仅告警 + 建议回写，**不在外部系统创建工单、不改写外部权威数据**。每次执行留痕（动作类型/触发结论/规则链/执行结果/回写状态）。

**Rationale**: 对应 Clarification "平台内部记录""建议性回写" 与 FR-020/021/022/023、边界"动作回写被拒"。事件驱动复用 R6 事件流；内部记录 + advisory 回写符合原则 II（不改外部权威）。

**Alternatives considered**:
- *直接在 APS/MES 建工单*：越权改写外部权威数据，违反 Clarification 与原则 II。
- *规则硬编码触发*：用既有规则链结论驱动，避免与推理内核重复（FR-027）。

---

## R12 — 风险报告导出：`reportlab` PDF + JSON 双产物

**Decision**: 新增 `reporting/risk_report.py`，对一次评估结论产出 **PDF（含分类/专用化决策/污染途径评分/CFDI 情景/MACO·PDE/规则链与法规依据 + QA 签批信息，经 `reportlab` 渲染，可下载/归档）** 与 **JSON（结构化数据，供前端渲染与程序化复用）** 双产物，经 `GET /api/reports/{conclusion_id}` 提供。`reportlab` 为本特性**唯一新增第三方依赖**。

**Rationale**: 对应 Clarification "PDF + 结构化数据" 与 FR-024/SC-007。现栈无 PDF 能力；合规要求"可归档 PDF 产物 + QA 签批"，前端渲染无法满足归档/签批合规。`reportlab` 纯 Python、无系统级依赖，适配 docker 部署。

**Alternatives considered**:
- *WeasyPrint/wkhtmltopdf*：需系统级原生库（Cairo/Pango 或 Qt），docker 体积与运维更重。
- *仅前端 `window.print()`*：不可归档、无服务端签批绑定，不满足合规。
- *仅 JSON*：违反 Clarification 的 PDF 归档要求。

---

## 复用资产清单（不重复实现，FR-027 / 原则 V）

| 既有资产 | 复用点 |
|---|---|
| `extraction/pipeline.run_extraction_pipeline` | R1 接线激活 + R3 扩展 |
| `extraction/aligner.align_entity` | US2 对齐归组/规范实例 |
| `extraction/llm_extractor` + `parser` | R3 抽取 + 回退 |
| `reasoning/engine.run_assessment` + `rules/` + `calculators` + `conflict_resolver` | R6 增量重算内核 |
| `services/kg_store.KGStore.sync_individual_to_shadow` | R5 A-Box↔影子同步 |
| `services/integration/base.ExternalSystemConnector` | R4 APS 连接器抽象 |
| `dependencies.require_role` / `Identity` / 三角色常量 | R9/R10 门禁与签名 |
| `models.reasoning.AuditLog` | R8 哈希链扩展 |
| `config.Settings` | R7 凭据注入 |

**唯一新增第三方依赖**：后端 `reportlab`（R12）。
