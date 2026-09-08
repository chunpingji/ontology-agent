# Ontology Agent 仓库工作约定

本文件供 Codex（包括 `gpt-6-astra`）在本仓库执行调查、设计、实现和审查时使用。重点是独立判断、按需获取上下文、完成验证和可靠交付；实际模型、推理强度与工具权限由运行环境配置，本文件不修改这些设置。

## 1. 工作方式与任务范围

- 默认中文沟通；代码标识符、接口名称和既有术语遵循项目风格。先说明关键结论，报告可核验依据。
- 在用户授权范围内持续完成调查、修改和必要验证。普通可逆操作与合理实现选择直接推进，不反复询问已明确事项。
- 重要不确定性先定位证据；会改变目标、公共契约或不可逆操作范围且无法推断的信息需要澄清，其余独立工作继续进行。
- 复杂任务先建立简短计划，识别调用链、数据流、依赖及验收条件；简单修改直接完成。发现错误假设时说明原因并修正。
- 文档设计/审阅默认修改文档及必要引用，不自动实施文档中的开发、部署或清理计划。代码任务则完成相关实现和验证，不停在建议阶段。
- 避免无关重构、全库格式化、顺手升级依赖或扩大本体/业务范围。优先复用已有模块与契约。
- 独立调查、前后端检查或风险复核可用原生子代理并行；限定问题、输出和文件归属，避免并发修改同一文件。主代理负责核验与整合，不以多代理意见一致代替证据。
- 不为满足协作形式递归启动另一层 Codex。`CLAUDE.md` 中调用 Codex CLI 的协作段面向 Claude Code；本文件采用当前可用的原生协作工具。

## 2. 开始前定位依据

- 先检查相关工作区状态与差异，保护用户已有暂存、未暂存和未跟踪内容；有未提交改动并不要求停工。不得用 reset、clean 或覆盖文件消除无关改动。
- 修改前检查适用的 `AGENTS.md` / `AGENTS.override.md`，包括待修改子目录的指导。不要假定启动于根目录就已加载所有子目录规则。
- 使用 `rg` / `rg --files` 定位入口、调用者、测试与配置；独立搜索/读取可并行。按任务扩展上下文，不预读全部 `docs/`、`specs/` 或历史实验输出。
- 项目治理依据为 [.specify/memory/constitution.md](.specify/memory/constitution.md)。新特性遵循其 Spec Kit 流程；已有修复和文档任务按实际适用范围处理，不把每次小改动都扩成新特性流程。
- 按用户目标定位 `specs/<feature>/spec.md`、`plan.md`、`tasks.md` 与 `quickstart.md`，不单凭最大编号、全局 feature 标记或 `CLAUDE.md` 中的计划链接选择任务。
- 区分需求契约、目标设计、当前实现和已验证结果。文档中的拟新增文件、历史通过数量、目标命令不能当作当前事实；先核验路径和实现。用户明确指令优先于仓库惯例。

## 3. 仓库导航与职责

本项目是制药 GMP / SLPRA 本体管理、文档证据抽取、声明式推理和风险报告平台。

| 路径 | 职责与使用约定 |
|---|---|
| `backend/app/api/`、`schemas/`、`models/` | 后端路由、请求/响应契约、持久化模型；复用鉴权与依赖注入 |
| `backend/app/services/ontology_engine.py`、`ontology_meta_store.py`、`ttl_merge.py` | 本体引擎、编辑元数据与 TTL 保真回写 |
| `backend/app/services/extraction/` | 原文解析、记录/引用、抽取与对齐；先定位具体业务入口，避免混用不同执行域 |
| `backend/app/services/extraction/ontology_guided/` | 共享本体指引识别核心；保持领域与基础设施边界 |
| `backend/app/services/document_analysis/` | 文档分析运行、制品、仓储、执行所有权与生命周期 |
| `backend/app/services/reasoning/`、`reporting/` | 声明式规则、计算/条件、模板契约、报告 AST 和输出 |
| `backend/app/services/llm/` | 本地模型客户端与共享调度；调用成本、超时和取消按已有契约处理 |
| `backend/alembic/`、`backend/tests/` | 数据库迁移与后端测试；测试 fixture 是隔离环境的入口 |
| `frontend/src/app/`、`components/`、`lib/` | 页面、业务/UI 组件、共享客户端及类型；前端依赖清单在 `frontend/` |
| `ontology/slpra/` | 权威 TTL；包括基础对齐与领域公理，不以抽取结果倒推修改定义 |
| `cli/` | 独立 Python HTTP 薄客户端；保持 JSON 输出、错误码和认证契约，不复制后端业务逻辑 |
| `docs/`、`specs/`、`backend/app/evaluation/` | 设计/实测、特性规范、评测工具；目标、历史证据与活动代码分开解释 |

后端采用 FastAPI、SQLAlchemy、Pydantic 和 PostgreSQL；前端采用 Next.js App Router、React、TypeScript、Tailwind 与既有 UI 组件。准确版本以对应依赖清单和锁文件为准。

## 4. 实现与领域边界

- 后端沿用 `APIRouter` / `Depends`、现有 schema、SQLAlchemy 模型和服务层模式；保留角色门禁、版本冲突检测及审计，不在路由中复制领域逻辑。
- 数据库结构变更使用 `backend/alembic/versions/`。修改跨层契约时同步核对后端 schema、前端 `src/lib/api.ts`、实际调用方及适用测试。
- T-Box 修改遵循宪章的三元组 diff、外科式 TTL 合并和双存储一致性要求；保留未建模三元组、外部对齐、SWRL、union/限制及属性链，不把多值 range 一律当作 union。
- 本体限定合法类型和谓词；文档结构、摘要、语义相似度用于定位和排序。事实成立必须有可回放原文证明，并核验主体归属、对象角色、关系方向、实体层级、极性与条件。
- 分开物理提及、局部共指、全局身份和关系证明。同名、相邻、同报告共现、图连通或高相似度不能单独证明实体合并、组成关系或全局唯一编号。
- 新文档分析运行、旧抽取作业和业务事实提交是不同域。在线代码不得导入 `app.evaluation`；共享识别核心的依赖边界以 `test_ontology_guided_boundaries.py` 核验。系统验证不自动等同人工确认或事实提交。
- 检索低分/拒绝不等于全文否定；覆盖与执行、语义结果分别记账。技术失败、未尝试、未决、否定事实不可互相替换；不能以空队列或空图宣称质量达标。
- 报告模板、声明式规则和本体各守职责；复用已有编译、输入绑定、条件/计算和渲染管线。缺失值、计算失败或覆盖不足不得补造为可信结论。
- 前端复用 `src/components/ui/`、主题变量与 `cn()`；遵循 TypeScript strict 和 `@/*` 路径别名。浏览器请求使用 `src/lib/api.ts`，默认同源 `/api/...`，保留认证、取消信号和错误处理。
- 页面 GET、刷新或切换展示不能隐式启动模型任务。长任务的运行身份、租约、取消、恢复和增量状态由服务端契约管理。

## 5. 离线运行与数据保护

- 应用默认面向内网/离线部署，运行期不得新增隐式外网依赖。云端能力仅按明确开启及配置就绪的既有政策运行；本地权重不入 Git，沿用离线加载和制品校验。
- 区分可选能力关闭与必需能力不可用。不能把旧可选 NER 的容错政策泛化为“主识别模型失败也视为成功”；按当前运行契约报告失败、暂停或未完成。
- 开发时通过 Context7 查公开技术文档与应用运行时出网是两回事；查询不携带密钥、用户原文或不必要的内部数据。
- 不读取或输出整份 `.env`、令牌、数据库凭据；检查配置时只获取任务所需的非敏感字段。保护上传原件、共享 OWL 存储、运行制品及审计记录。
- 启动/重启后端可能迁移数据库、播种 TTL、预热模型并中断后台任务。仅为验证代码时优先使用隔离测试；运维操作按用户已授权范围和真实运行状态执行。
- Compose 默认合并 `docker-compose.override.yml`；生产模式显式使用基础文件。当前后端未启用自动 reload。不要把 `docker compose down -v` 当作日常重启。
- 当前迁移异常可被启动流程捕获，健康接口 200 不能证明迁移完成；涉及迁移时核对实际数据库 revision 与代码 head。`scripts/README.md` 的历史 fail-fast 描述须以 `app/main.py` 和根 README 的当前行为核验，不能照抄。
- 历史冻结目录 `docs/evaluations/` 的原文、参考、输出与 runtime 不为新结果重写。新评测使用新目录/运行身份；模型调用可能写调度和用量记录，不应把整轮评测称为只读操作。

## 6. 开发环境与验证命令

优先复用已有依赖环境。后端使用 `backend/pyproject.toml` + `uv.lock` 和 `backend/.venv`；需要准备环境时按该配置同步，保留任务所需 extras，不为普通测试安装全部模型或下载权重。前端使用 `frontend/package.json` + `package-lock.json`，Docker 基线为 Node.js 22；确需安装时，在 `frontend/` 执行 `npm ci --legacy-peer-deps`。不要在只有锁文件的仓库根目录运行前端 npm 任务。

下表是现有入口；命令中的具体测试/源码文件为定向示例，应替换为当前改动涉及的真实文件。先检查存在性，不照抄设计文档里的待建测试名。

| 工作目录 | 用途 | 命令 |
|---|---|---|
| `backend/` | 定向测试示例 | `.venv/bin/python -m pytest -p no:cacheprovider -q tests/test_reasoning/test_ttl_roundtrip.py` |
| `backend/` | 定向静态检查示例 | `.venv/bin/ruff check app/services/ttl_merge.py` |
| `backend/` | 需要时运行完整后端测试 | `.venv/bin/python -m pytest -p no:cacheprovider -q` |
| `frontend/` | 现有 Node 测试 | `node --test tests/*.test.mjs` |
| `frontend/` | 定向 ESLint 示例 | `npm run lint -- src/lib/api.ts` |
| `frontend/` | 类型检查，使用已安装版本 | `./node_modules/.bin/tsc --noEmit` |
| `frontend/` | 需要生产构建验证时 | `npm run build` |
| `cli/` | 已准备该包测试环境后 | `python -m pytest` |

- 前端当前没有 `npm test` 脚本；`lint` 是 ESLint，不是 `next lint`。需要定向测试时给 Node test runner 指定实际 `.test.mjs` 文件。
- 后端 Ruff 配置为 Python 3.11、行宽 100、E/F/I；按改动范围检查，避免顺带修整无关历史问题。
- 沿用 `backend/tests/conftest.py` 的 SQLite、依赖覆盖、临时本体与模型调度隔离；它没有自动隔离全部数据路径，新增文件制品使用 `tmp_path` 并显式配置相应存储。
- `DOCUMENT_ANALYSIS_TEST_DATABASE_URL`、`REPORTING_TEST_DATABASE_URL` 对应的 PostgreSQL fixture 会清表，只能指向按 fixture 要求准备的专用可销毁测试库。未配置导致的 skip 必须报告；SQLite 通过不能代替 PostgreSQL 锁/并发验收。
- 浏览器脚本另需外部 Playwright/Chrome 和服务配置；查看脚本及相关 quickstart 后执行。合成 API 拦截的浏览器检查不能代替真实后端集成测试。
- 先做受影响路径的必要检查；通过后仅在新增改动、失败或未解决风险需要时扩大测试。低影响文档修改验证链接、编号、术语和契约一致性即可。
- 回归测试覆盖真实失败模式、公开行为和关键边界，避免只复述实现。不要为了通过而放宽断言、删除反例或隐藏失败。
- 只报告实际运行的命令、结果和未完成项；不把历史验证记录当作本次测试，不声称未经执行的构建或 CI 通过。

## 7. 设计、审查与交付

- 方案写清输入输出、状态变化、数据/证明来源、失败处理、缓存失效与验收口径；重要选择说明收益、代价和适用范围。
- 图谱任务按需阅读 [本体指引基础方案](docs/基于本体指引的文档结构和摘要元数据的关系图谱识别方案.md)、[语义精排扩展方案](docs/支持语义相似度精排的关系图谱识别精度提升方案.md) 和 [活动评测说明](backend/app/evaluation/README.md)，核对其目标与实现差距，不将整份设计默认提升为所有任务的要求。
- 真实质量评测固定输入、本体、模型、参考和预算；金标不进入识别输入。排序指标、最终事实 precision/recall、完整路径与成本分别报告，未决和未评分项不能静默排除。
- 完成后复核本次修改范围与差异，重点检查证据权限、身份/版本、否定/条件、双存储、恢复幂等和跨用户隔离等受影响边界。
- 中文交付实际修改内容、文件链接、验证结果和必要限制；明确区分工程测试通过、真实模型评测完成与已部署。长期背景留在专题文档，避免不断膨胀本文件。

## 8. 当前技术文档查询（Context7）

<!-- context7 -->
Use Context7 MCP to fetch current documentation whenever the user asks about a library, framework, SDK, API, CLI tool, or cloud service — even well-known ones like React, Next.js, Prisma, Express, Tailwind, Django, or Spring Boot. This includes API syntax, configuration, version migration, library-specific debugging, setup instructions, and CLI tool usage. Use even when you think you know the answer — your training data may not reflect recent changes. Prefer this over web search for library docs.

Do not use for: refactoring, writing scripts from scratch, debugging business logic, code review, or general programming concepts.

### Steps

1. `resolve-library-id` with the library name and the user's question. Use the official library name with proper punctuation (e.g., "Next.js" not "nextjs", "Customer.io" not "customerio", "Three.js" not "threejs")
2. Pick the best match by: exact name match, description relevance, code snippet count, source reputation (High/Medium preferred), and benchmark score (higher is better). Use version-specific IDs when the user mentions a version
3. `query-docs` with the selected library ID and the user's full question (not single words), scoped to a single concept. If the question spans multiple distinct concepts (e.g. routing and auth and caching), make a separate `query-docs` call per concept with the same library ID, unless the question is about how the concepts interact — combined queries dilute ranking and return shallow results for each topic
4. Answer using the fetched docs
<!-- context7 -->

若 Context7 不可用或未覆盖问题，明确缺口，结合锁定版本和可获取的官方文档继续可完成部分；不得声称已查询或虚构接口行为。
