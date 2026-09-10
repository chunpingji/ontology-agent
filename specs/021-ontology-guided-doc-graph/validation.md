# 021 实现与验证记录

> 2026-09-08 后续实现：[022 语义精排与关系图谱闭环验证](../022-semantic-graph-closure/validation.md) 承接本特性，新增主体感知排序、公平派发、必要原文/迟到反证、恢复及质量工具。本文保留 021 的原有验证与待验门，不以 022 工程结果追溯勾选 021 的真实质量、部署或清理验收。

日期：2026-09-08。分支：`021-ontology-guided-doc-graph`。基线提交：
`89766a7721ea85725bc2ef1d61305516a1d3d38a`。当前是包含 021 实现的未提交工作区；
没有 commit、push、生产部署或正式切换。

## 结论

**工程主体已实现并通过当前环境可执行的本地回归，但发布状态为 `NOT READY`。**

持久化文档分析运行、显式根类型、冻结 IR/元数据/本体快照、本体直接菜单、两阶段检索、
证明门禁、增量图、持久 dispatcher、租约与 fencing、SSE、两个结果 Tab、运行控制、保留与
退役防护均已落地。AC-T31 的有限 CMC 权威本体扩展验收为 PASS。

以下发布必需证据仍不存在：隔离 PostgreSQL、真实浏览器流程、专家批准金标与预注册 SLO、
至少三次独立真实模型运行、消融、AC-T30 完整正反例、生产旧域 manifest、Constitution G-C03
批准、维护窗口、部署及正式切换。因此没有执行真实旧数据物理清理，也没有把模型桩或本地
SQLite 结果解释为生产就绪。

完整环境、源码与配置身份见 [baseline.json](./baseline.json)；历史离线评测输入的冻结边界见
[evaluation-baseline.json](./evaluation-baseline.json)。

Spec Kit 前置检查已解析到本特性目录及 `research/data-model/contracts/quickstart/tasks` 制品；
requirements checklist 为 **29/33**。未勾选的 GATE001—GATE004 正是治理、独立真实质量、旧域
manifest/维护窗口及完整发布集成门，不把它们改写为规范缺陷或本地通过。

## 已交付的工程范围

- `POST /api/document-analysis/runs` 只在用户显式提交 Word 与合法完整根 IRI 后创建 owner 隔离的
  持久运行；同键同输入重放、同键异输入冲突，同文件更换合法根类型和新键产生新运行。
- 一份 `DocumentIR` 同时驱动分层元数据与图谱；摘要只进入检索提示。元数据可早于图谱读取，
  GET、SSE、刷新和筛选不创建新识别工作。
- `OntologySnapshot → LocalMenu`、record/mention、verification/proof、两阶段 recall ledger、依赖、
  公平前沿、版本化候选与完整图快照由共享 `ontology_guided` 核心承载；在线代码不依赖
  `app.evaluation`，活动评测只是薄适配。
- PostgreSQL 语义的持久 dispatcher 使用有界并发和 `SKIP LOCKED` claim；SQLite 使用 CAS。
  独立 Session lease keeper 覆盖转换、解析、摘要与模型阶段；FastAPI lifespan 负责启停并等待
  in-flight，未运行 lifespan 的测试调用保留显式 fallback。
- 删除先撤销执行权，只删除本运行独占内容并保留最小 tombstone。公开 DELETE 在物理清理后仍
  可用原键和原内容逐字节重放首次 202；异内容 409、其他键 410、其他 owner 404，且不重复调度
  清理。
- 初轮页面提供“分层元数据”和“关系图谱”两个同级结果 Tab，并共享 run、metadata 与来源选择。2026-09-09 后续 UI 要求已调整为历史左侧导航、共享章节树/原文预览及右侧“节点元数据 / 关系图谱”标签，详见 [工作区布局验证](layout-validation.md)。
  旧同步 `/api/document-analysis/word`、旧前端客户端和旧 Word 新接单路径已退役；Excel、声明式、
  template default 与历史离线评测边界保留。
- 权威 CMC TTL 增加经批准的最终产品、中间体标识和两条中间体属性链；没有增加
  `CMCReport→API` 直连，也没有把属性链当作无需 reasoner 的已物化事实。

## 本轮审查发现并修复的问题

1. `DocumentRecognitionEventBatch.checkpoint_artifact_id` 会立即引用 checkpoint artifact；原清理
   顺序没有先删 batch，在 PostgreSQL 或启用外键的 SQLite 中会失败并残留敏感 checkpoint。
   现已先删除 batch，再删除 artifact，并新增 `PRAGMA foreign_keys=ON` 的独立真实回归。
2. Tombstone 重放曾对公开 Unicode `request_key` 调用 `hmac.compare_digest(str, str)`，中文键会
   触发 `TypeError`。现仅对 owner/request/result hash 使用恒时比较，公开键使用普通字符串相等；
   回归同时断言首次与重放的 HTTP JSON 字节完全一致且清理不再次调度。

修复后独立复核未发现新的 P0/P1；真实 PostgreSQL 运行时行为仍须由隔离数据库验证。

## 环境与冻结身份

| 项目 | 本轮记录 |
| --- | --- |
| Python / Node / npm | 3.12.7 / 20.18.0 / 11.3.0 |
| FastAPI / Pydantic / SQLAlchemy / Alembic | 0.138.0 / 2.13.4 / 2.0.51 / 1.18.4 |
| SQLite / psql client | 3.45.3 / 14.24；未连接 PostgreSQL server，服务端版本未知 |
| Next / React / TypeScript / ESLint | 16.2.9 / 19.2.7 / 5.9.3 / 9.39.4 |
| 本地模型配置 | `local_llm_enabled=false`；`qwen2.5:14b`；revision 见 baseline；本轮真实调用 0 |
| tokenizer | 配置标签 `llama_server`；本轮未连接或验真 |
| parser / IR / structure | 4 / `document-ir-v1` / `word-structure-v1` |
| 摘要 prompt | `word-tree-summary-v1` |
| CMC TTL SHA-256 | `ea90db5a324036c71a6e6b559b48aa14d202de75d2e491989791859abe2f8e25` |

只读取了任务所需的非敏感配置字段；没有输出数据库 DSN、令牌或模型服务凭据。

## 已执行验证

### 后端确定性与特性组合

实际执行以下显式文件集合：

```bash
cd backend
.venv/bin/python -m pytest -p no:cacheprovider -q \
  tests/test_cmc_intermediate_ontology.py \
  tests/test_reasoning/test_ttl_roundtrip.py \
  tests/test_api/test_document_analysis.py \
  tests/test_api/test_document_analysis_events.py \
  tests/test_api/test_document_analysis_schemas.py \
  tests/test_api/test_retired_word_analysis.py \
  tests/test_extraction/test_document_analysis_artifact_store.py \
  tests/test_extraction/test_document_analysis_dispatcher.py \
  tests/test_extraction/test_document_analysis_execution_recovery.py \
  tests/test_extraction/test_document_analysis_public_projection.py \
  tests/test_extraction/test_document_analysis_retention.py \
  tests/test_extraction/test_document_analysis_run_store.py \
  tests/test_extraction/test_document_run_execution_postgresql.py \
  tests/test_extraction/test_ontology_guided_boundaries.py \
  tests/test_extraction/test_ontology_guided_core.py \
  tests/test_extraction/test_ontology_guided_evaluation.py \
  tests/test_extraction/test_evaluation_citation_protocol.py \
  tests/test_extraction/test_evaluation_citation_domains_v3.py \
  tests/test_extraction/test_evaluation_citation_tables_v2.py \
  tests/test_integration/test_word_domain_cleanup.py
```

结果：**209 passed, 1 skipped, 4 warnings**。唯一 skip 是专用 PostgreSQL DSN 未配置。

AC-T31 的精确集合：

```bash
cd backend
.venv/bin/python -m pytest -p no:cacheprovider -q \
  tests/test_cmc_intermediate_ontology.py \
  tests/test_reasoning/test_ttl_roundtrip.py
```

结果：**12 passed, 4 warnings**。覆盖真实
`OntologyEngine → OntologySnapshot → compile_local_menu`、union domain/range、字符串
`intermediateIdentifier`、非 functional `hasProcessIntermediate`、两条属性链、inverse/RDF list
往返，以及未运行 reasoner 时两条链均不产生已物化 `hasProcessIntermediate`。

### 后端全仓回归与 PostgreSQL 缺口

```bash
cd backend
.venv/bin/python -m pytest -p no:cacheprovider --collect-only -q
.venv/bin/python -m pytest -p no:cacheprovider -q -rs
.venv/bin/python -m pytest -p no:cacheprovider -q -rs \
  tests/test_extraction/test_document_run_execution_postgresql.py
```

- 收集：**1707 tests collected**。
- 全量：**1678 passed, 29 skipped, 4 warnings, 16 subtests passed**，156.66 秒。
- 021 PostgreSQL 文件：**1 skipped**，原因是
  `requires explicitly provisioned isolated document-analysis PostgreSQL`。
- 29 个全量 skip 全部要求显式提供隔离 PostgreSQL；其中 021 为 1 个，其他既有 PG 套件为 28 个。
  这些结果是 pending，不是 pass。`DOCUMENT_ANALYSIS_TEST_DATABASE_URL` 本轮为 unset。

四条 warning 分别为 Starlette TestClient/httpx 弃用提示、两个 `schema_json` 字段遮蔽提示，以及
一个 Pydantic class-based config 弃用提示；没有隐藏或转为 skip。

### Python 静态检查

对 document-analysis、ontology-guided、相关 API/测试、0033 migration 与两个退役脚本共 47 个
本特性文件执行 `ruff check` 和 `ruff format --check`：**通过，47 files already formatted**。

```bash
cd backend
.venv/bin/ruff check app tests --statistics
```

全仓仍失败，共 **98** 项：E501 65、F401 19、I001 11、F841 2、E731 1。同一 Ruff、同一命令在
基线提交的隔离归档上为 **100** 项：E501 67、F401 19、I001 11、F841 2、E731 1。当前特性定向
范围为零，且没有增加按同口径统计的全仓债务；不能因此把全仓 Ruff 写成通过。

### 前端

```bash
cd frontend
node --test tests/*.test.mjs
./node_modules/.bin/tsc --noEmit
npm run lint -- \
  'src/app/(dashboard)/analysis/page.tsx' \
  'src/app/(dashboard)/entities/extraction/page.tsx' \
  'src/app/(dashboard)/reports/[reportId]/page.tsx' \
  src/components/analysis/document-analysis-panel.tsx \
  src/components/analysis/document-relationship-graph.tsx \
  src/components/extraction/job-create-form.tsx \
  src/lib/api.ts \
  tests/document-analysis-retirement.test.mjs \
  tests/document-analysis-runs.test.mjs
npm run lint
npm run build
```

- Node：**42 passed, 0 failed, 0 skipped**。
- TypeScript：通过；9 个 021 文件定向 ESLint：通过。
- Next production build：通过，生成 25 个静态页面；提示根目录与 `frontend/` 同时有 lockfile，
  workspace root 为推断值。
- 全仓 ESLint：**3 errors, 7 warnings**。错误位于未被 021 修改的
  `ast-tree-view.tsx:43`、`mock/edit-dialog.tsx:38`、`shell/auth-guard.tsx:19`；因此是可见的既有
  全仓门禁失败，不是定向检查通过的替代物。
- `frontend/tests/document-analysis-browser.mjs` 不存在；没有运行 Playwright/Chrome，也没有把
  production build 当作浏览器验收。

### Migration、退役审计与清理边界

- 在临时 SQLite 上执行
  `stamp 0032_local_model_requests → upgrade 0033 → downgrade 0032 → upgrade 0033`，最终 revision
  两次均为 `0033_document_analysis_runs (head)`：通过。
- 从真正空 SQLite 执行全链 `alembic upgrade head` 仍在既有
  `0007_add_generated_reports` 失败：`generated_reports already exists`。0033 自身循环已通过，但
  T024 要求的完整空库链没有闭合，故 T024 保持未勾选。
- `backend/.venv/bin/python scripts/audit_word_recognition_retirement.py --static-only`：
  **pass**；扫描 345 个在线源码文件，逐项复核 20 条共享 allowlist edge，0 violations，
  `sources_sha256=ac30a7a247200a54d0d3e262437fac9fd5243b3eb17c0a9743862d62da20a32d`。
- `retirement/old-word-domain-manifest.json` 不存在；没有连接业务数据库生成目标/计数，没有运行
  生产 dry-run 或 cleanup execute、维护窗口或 G-C03 审批。合成清理测试通过不授权真实清理。

## AC-T01—AC-T31 验收状态

`PARTIAL` 表示已有对应机制和回归，但规范要求的完整复合矩阵或必需运行层尚未闭合；它不是
验收通过。模型桩、legacy evaluation 回归、空图或单个正向用例不会提升为完整 AC。

| AC | 状态 | 当前证据与仍缺内容 |
| --- | --- | --- |
| AC-T01 | PARTIAL | 直接菜单不预展开二跳、首跳后再展开已有核心测试；缺 T034 所列完整最小本体组合矩阵。 |
| AC-T02 | PARTIAL | 冻结 snapshot、range 子类、未知绝对 IRI 拒绝已有覆盖；继承/多约束/未解析约束的专门复合 fixture 未闭合。 |
| AC-T03 | PARTIAL | metadata policy 与边界测试禁止摘要成为 proof；缺“摘要声称 API、正文无组成”的完整新核心反例。 |
| AC-T04 | PARTIAL | phase1 拒绝后实际 phase2、重试不饿死新记录已有覆盖；所有主体—谓词计划守恒矩阵未闭合。 |
| AC-T05 | PARTIAL | mention/referent/type/identity 契约分离；“可靠类型、无唯一号仍保留局部事实”的专门端到端断言不足。 |
| AC-T06 | PARTIAL | 既有 identity quarantine/角色边界回归可复用；新核心的 API 名称+试验来源号完整反例未独立闭合。 |
| AC-T07 | PARTIAL | 合并单元格共享物理 mention 测试通过；多观察、实体数量与不强并的完整组合仍缺专门测试。 |
| AC-T08 | PARTIAL | 原子 citation/table 定位回归通过；call 50/51 式表头/名称/来源格定位不升级为身份/组成的组合未闭合。 |
| AC-T09 | PARTIAL | 同名且模型声称 supported 仍被 proof gate 拒绝；Product 代码+API 名称+父 describes 的精确复合反例未完整迁入新核心。 |
| AC-T10 | PARTIAL | 受控 bridge bundle 正例可通过；明确句、配方表、跨段指代三种正例没有全部闭合。 |
| AC-T11 | PARTIAL | target/scope 绑定与错误引用失败关闭已有覆盖；错 target ID 与“ID 对但语义对象错”的完整矩阵不足。 |
| AC-T12 | PARTIAL | owner/scope 门禁存在；成品剂型/API 粉末的层级归属专门矩阵未闭合。 |
| AC-T13 | PARTIAL | placeholder、false、missing 不折叠已有单测；CAS=N/A/布尔否/未提及的全链持久化矩阵未闭合。 |
| AC-T14 | PARTIAL | nonaffirmed 不展开、scheduler 继续已有覆盖；binding unsupported 后精确 `not_checked` 与兄弟/下一记录组合不足。 |
| AC-T15 | PARTIAL | 有界重试与技术状态不冒充肯定已有覆盖；空召回/拒答/引用/token/超时/revision 全矩阵未闭合。 |
| AC-T16 | PARTIAL | 既有 identity quarantine 可复用；false/缺失/错误 true 在 router/shared/cache 三层的统一新核心矩阵未闭合。 |
| AC-T17 | PARTIAL | mention registry 与局部/全局身份契约分离；跨制剂/批次同项目的局部共指/全局拒并组合不足。 |
| AC-T18 | PARTIAL | 版本化依赖失效和公开投影回放已有覆盖；merge/split/第三模糊节点/晚到 owner 闭包未完整覆盖。 |
| AC-T19 | PARTIAL | 依赖替代、公平前沿、循环有界已有覆盖；审核拒绝、普通实体冒根和替代根路径完整矩阵未闭合。 |
| AC-T20 | PARTIAL | SQLite 恢复、dispatcher、lease keeper、fingerprint/fencing 测试通过；隔离 PostgreSQL 竞争与过期连接未运行。 |
| AC-T21 | PARTIAL | 首次非 1 revision、candidate/proof head 与 API 投影保留真实版本已有覆盖；所有对象类型的精确引用矩阵不足。 |
| AC-T22 | PARTIAL | event/head CAS、幂等 batch、checkpoint 崩溃恢复与 FK 清理已有覆盖；真实 PG 事务竞争和文件—DB 故障矩阵未运行。 |
| AC-T23 | PARTIAL | 后端缺失/非法/未知类型、202、幂等冲突、同文件换合法根新 run、GET 零副作用已覆盖；完整前端/浏览器显式提交流程未验收。 |
| AC-T24 | PARTIAL | durable SSE sequence、owner 隔离、GET 零副作用及 Node 状态测试存在；真实浏览器重连/快速切换/迟到响应未执行。 |
| AC-T25 | PARTIAL | metadata 与 graph 独立 availability、默认投影失败关闭、空图不冒充成功已有覆盖；所有失败/暂停 UI 组合未闭合。 |
| AC-T26 | PARTIAL | role-specific opaque source refs、段落/表格 anchor 与跨组件 SourceSelection 已实现；浏览器端全交互及所有证据角色未验收。 |
| AC-T27 | PARTIAL | run/SSE/source/control/delete 的 owner 与角色反例、上传/IRI 边界已有覆盖；真实认证网关及全端点/路径组合矩阵未闭合。 |
| AC-T28 | PARTIAL | 取消/删除 fence、tombstone、Unicode 幂等重放、共享 artifact、到期和 FK-on checkpoint 清理已有覆盖；真实 PG 晚到 worker 与完整前端状态矩阵未验收。 |
| AC-T29 | PARTIAL | 合成混合域 cleanup 测试与静态审计通过，旧入口不可达；真实 manifest、业务引用闭包、G-C03、维护窗口与清理计数均缺失。 |
| AC-T30 | PENDING | 只有受控模型/活动评测机制测试；缺 T087 工程 E2E、独立真阳性、关键反例、其他根、浏览器、专家金标和三次真实模型运行。 |
| AC-T31 | PASS | 12 项权威 TTL/Owlready2/schema/local-menu/round-trip/no-reasoner 断言通过；有限本体扩展本身不授权发布。 |

严格复合口径下：AC-T01—AC-T29 为 PARTIAL，AC-T30 为 PENDING，AC-T31 为 PASS。

## 任务清单与发布门禁

- T001、T007、T091、T094 现分别由 `baseline.json`、本文与 AC-T31 结果支撑；T021 的未知 IRI 和
  更换合法根类型新建运行证据已补齐。
- T024 保持未勾选：0033 独立循环通过，但全空库迁移链受既有 0007 缺陷阻断。
- T067、T088—T090、T092、T093 必须保持未勾选；没有真实 PostgreSQL、浏览器、独立真实模型、
  消融和联合签署。
- T069 仍保持未勾选：确定性 retention 机制显著增强，但指定测试中的取消不可恢复及全部
  `expires_at/allowed_actions` 状态组合尚未完整闭合；真实 PG 和前端状态分别仍由 T067/T070 跟踪。
- T084 保持未勾选：没有真实维护窗口、操作者、生产计数或 G-C03 结论。
- `[x]` 只表示对应工程任务有可复现证据，不表示其关联的复合 AC 或发布门整体通过。

## 明确 pending / blocked 的外部门

| 门禁 | 状态 | 原因 |
| --- | --- | --- |
| 隔离 PostgreSQL claim/lease/fencing/事务/清理 | PENDING | 专用 DSN 未配置；测试明确 skip |
| Playwright/Chrome 两 Tab 与 SSE 流程 | PENDING | 目标 browser 脚本不存在 |
| 专家标注、保留集、批准 SLO | PENDING | `ontology_guided_reference_v1.json` 不存在；assistant-silver 非专家金标 |
| 三次独立真实模型运行 | PENDING | 本轮模型关闭，真实调用 0 |
| structure/summary/可选 GLiNER 消融与成本 | PENDING | 未运行 |
| 部署代表性的性能、排队、token 与资源测量 | PENDING | 未建立工作负载或批准 SLO，本地测试耗时不代替性能验收 |
| AC-T30 | PENDING | 缺独立正例、关键反例与完整双 Tab 回看 |
| 旧域生产 manifest / 保护对象 / G-C03 | BLOCKED | manifest 缺失，且宪章禁止物理删除已发布内容 |
| 生产清理 dry-run | PENDING | 没有真实 manifest 或业务数据库只读盘点结果 |
| 维护窗口 cleanup execute | PENDING | 未授权、未执行，无生产计数 |
| 部署与正式切换 | PENDING | 未部署、未迁移生产库、未签署 T093 |

最终判定：可以继续非破坏性开发、补测试和评测准备；**不得执行生产物理清理，不得宣称已部署或
可正式切换**。
