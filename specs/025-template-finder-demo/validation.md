# 025 本地工程验证 — 2026-09-14

当前状态：已于 2026-09-14 重新部署至现有开发环境，真实页面验收通过；部署经过及恢复任务的异常见文末部署记录。下方本地测试中的“未部署”描述对应当时验证阶段。

验收范围澄清：前期工程检查及 AI 结构入口恢复仅覆盖各节列出的能力，不追认为报告端到端验收。[补充兼容方案](../../docs/Slot语义与本体指引1.0报告结果兼容方案-20260914.md)现已实施部署，指定 v2.5 模板的真实输入、AI 行文、DOCX 和浏览器结果见文末「Slot 报告兼容实施与真实验收」。

已完成隔离 Finder/Profile、模板分流、当前执行头与展示缓存、V1/V2 模板页、报告中心上下文及操作、节点/属性原文定位。默认绑定为空，未修改实际模板、原件或权威 TTL，未启动业务服务或真实模型。

## 实际执行结果

| 验证 | 结果 |
|---|---|
| 下列后端定向测试 | **77 passed，2 skipped**；skip 为专用 PostgreSQL 测试库未配置 |
| Finder API/原文配对、普通文档退役、报告中心 API、模板文档性能 Node 测试 | **4 个测试文件通过** |
| 前端 `tsc --noEmit --incremental false` | 通过 |
| Finder 与受影响后端入口 Ruff | 通过；未批量格式化既有代码 |
| 全部受影响前端文件 ESLint | 0 errors；保留 1 项既有 `meta.status` Hook 依赖 warning |
| Finder 浏览器脚本 | 通过：精确选区、两入口复用、用户隔离、模板切换及迟到响应 |
| 既有普通报告 Word 工作区浏览器脚本 | 通过：预览、目录、只读刷新、显式创建、恢复、用户隔离及错误重试 |

后端命令（`backend/`）：

```bash
.venv/bin/python -m pytest -p no:cacheprovider -q \
  tests/test_api/test_template_finder.py \
  tests/test_extraction/test_template_finder_sources.py \
  tests/test_extraction/test_template_finder_execution.py \
  tests/test_extraction/test_template_finder_postgresql.py \
  tests/test_api/test_template_document_runs.py \
  tests/test_api/test_report_document_runs.py \
  tests/test_api/test_retired_word_analysis.py \
  tests/test_api/test_annotation_rerun.py \
  tests/test_extraction/test_annotation_execution.py \
  tests/test_extraction/test_ontology_guided_boundaries.py \
  tests/test_reporting/test_retired_execution.py
```

前端命令（`frontend/`）：

```bash
node --test tests/template-finder.test.mjs tests/document-analysis-retirement.test.mjs \
  tests/report-center-api.test.mjs tests/template-document-performance.test.mjs
./node_modules/.bin/tsc --noEmit --incremental false
node tests/template-finder-browser.mjs
node tests/report-word-workspace-browser.mjs
```

浏览器脚本本次通过 `ESBUILD_MODULE`、`PLAYWRIGHT_MODULE` 指向机器上已安装的工具，使用 `/usr/bin/google-chrome`，未安装依赖。脚本需要临时 localhost 监听权限。Finder 脚本先用后端真实解析器和 Finder 生成合成 Word 的结果，再用隔离 HTTP 服务验证 React、Query、RelationPanel、WordViewer 及实际报告中心容器；Next.js 导航/链接采用测试适配。它不是已部署应用的真实后端集成验收。

## 核验内容

- 产品属性在重复名称/数值和非 BMP 字符场景下，定位到实际取数段落。
- 合成 Word + 当前权威本体的隔离加载：工艺条件来自工艺表，得量与收率分别定位到另一张表的对应单元格；拼接值和步骤序号标明计算来源。
- 合并单元格按实际 source cell 定位；嵌套表路径能解析；无效坐标和旧来源字符串不制造锚点。
- GET 不派发；上传只登记；通用识别与报告文档显式 Finder 上下文被拒绝；共享原件仍可显式使用普通模板。
- 已有 generic 执行在模板后来配置 Finder 后仍按原域继续，保留 `options.mode` 语义。
- 独立数据库连接下相同请求只派发一个 worker；活动执行冲突、过期 expected ID 和过期 worker 均受约束。SQLite 验证不能替代 PostgreSQL 验收。
- 内部 job 不出现在普通列表，普通详情、进度、候选、标注和报告入口不可读取。不同用户不能读取另一用户执行。
- 完成结果的原文来自同次缓存；原件移除后仍能按对应 execution 查看，状态标为过期。新执行不读取上一轮图谱。
- 浏览器发现只读 WordViewer 不会自动同步外部按钮触发的选区；已按验证后的编辑器坐标同步 DOM Range，未引入关键词定位。

## 未完成的实际验收

尚需确切模板 ID/修订、配套原件和必须呈现的关系/属性清单，才能完成实际绑定与逐项演示验收。当前只提供 `cmc_baseline_v1`，目标超出其根类或策略闭包时按具体需求补充。

未配置可销毁的专用 PostgreSQL 测试库，2 项 PostgreSQL 并发测试未执行。未运行生产构建、真实模型评测或部署；Finder 本身不调用模型。

## 模板页面引擎切换补充验证 — 2026-09-14

按用户补充需求增加模板详情页选择器、GET/PATCH 引擎设置接口、当前修订两个可空字段及 `0041_template_engine` 迁移。页面设置优先于部署文件；V1/V2 共用，未设置模板仍沿用原绑定。未执行实际数据库迁移或部署。

本轮实际执行：

- 后端 `tests/test_api/test_template_finder.py`、`tests/test_extraction/test_template_finder_execution.py`、`tests/test_api/test_template_document_runs.py`、`tests/test_api/test_report_document_runs.py`：**36 passed**。
- 覆盖 V1/V2 已发布模板双向切换、详情/报告中心上下文同步、零隐式执行、结构/hash/发布状态保留、角色权限、非法 profile/根类/静态演示拒绝、旧页面冲突、独立 SQLite 连接条件更新、已有 Finder 缓存保留、新修订不继承及迁移升降级的数据保留。
- `tsc --noEmit --incremental false`、本轮修改文件 Ruff 和 ESLint：通过；迁移脚本 head 唯一，为 `0041_template_engine`。
- 原有四个 Node 文件（见上方命令）：全部通过。
- 新增 `node tests/template-engine-browser.mjs`：通过。真实 React 选择组件、Query 和报告上下文 hook 在隔离 HTTP fixture 下验证显式保存、共享配置、草稿保留、冲突刷新、只读角色、根类限制及迟到响应隔离；所有写请求均为设置 PATCH，没有识别 POST。

浏览器使用上方相同的外部工具路径和本机临时监听；该检查不是已部署前后端验收。本轮迁移及配置条件更新在可销毁 SQLite 中验证，尚未完成 PostgreSQL 实库迁移/并发验收。

## 实际重新部署 — 2026-09-14

- 沿用当前 `docker-compose.yml + docker-compose.override.yml` 开发环境及既有 CUDA 后端镜像。代码和迁移已挂载，重启 backend/frontend 生效；未重建镜像、拉取依赖或重启数据库/网关。
- 将部署前运行中的 `3c2ff653-4879-4280-9f9b-eee020530069` 和排队中的 `4ce12d10-56fb-49da-9250-3a3dfa874635` 通过现有版本校验、幂等运行控制暂停。确认已暂停且无活动执行后停止后端。
- 自动审批拒绝整库备份，理由为重新部署授权未明确包含复制全部真实业务数据；未生成备份。改用 PostgreSQL 事务执行仅新增两个可空字段的迁移，未复制、删除或回填业务数据。
- `docker compose run --rm --no-deps -T -e 'PGOPTIONS=-c lock_timeout=15000 -c statement_timeout=60000' backend alembic upgrade head` 成功。实际数据库从 `0040_current_recognition_state` 升至 `0041_template_engine`；重启后 `alembic current` 确认为 `(head)`，两列均可空，24 个模板保留，显式引擎配置数仍为 0。
- 网关 8081 的 `/api/health`、登录页和报告模板页均返回 200。真实认证 API 和浏览器验证 CMC 模板详情可显示并选择“本体指引1.0”，内置配置标签为“CMC（本体指引1.0）”。使用已配置演示域名解析到本机进行浏览器验证，未测试外部隧道连通性。
- 页面检查未保存引擎设置、未启动识别；观察到的 `POST /api/ast-templates/coverage-doc-classes` 经核验仅查询本体关系覆盖，无模型调用或写入。
- 两个维护暂停任务均已按原运行身份恢复。原运行进度从 work_version 638 推进至 644 后再次失败，公开错误为 `RANKING_STATE_PERSISTENCE_FAILED`；另一原排队任务已运行。既有两个暂停任务仍保持暂停。该识别错误未在本次部署中修复；日志未提供底层异常，不能据此断言具体根因。

当前模板仍未绑定实际演示配置，真实原件的 Finder 效果及专用 PostgreSQL 并发验收仍待完成；部署成功不代表这些验收已完成。

## 引擎设置持续显示保存中的修复 — 2026-09-14

用户随后将模板 `74cd92d6-41ee-42d5-84a2-b4a736a087b9` 配置为“本体指引1.0”。数据库和网关日志确认设置已保存，PATCH 返回 200；界面仍显示“正在保存…”是因为成功回调等待模板详情及报告上下文刷新。该模板详情响应为 15,808,711 字节，保存引擎元数据后重新下载整份详情延长了等待。

- 修复后直接使用 PATCH 响应更新引擎配置及模板缓存，取消保存前发出的配置/详情读取，避免旧响应覆盖新选择。报告上下文在后台刷新，不再决定保存状态，也不再因保存而重新下载模板详情。
- 扩展浏览器回归先复现旧行为：挂起后续刷新时，保存成功提示超时。修复后，慢刷新、刷新返回 502、旧详情响应迟到均不影响保存成功状态；原有显式保存、草稿保留、冲突、权限及模板切换检查继续通过。
- 本轮 `tsc --noEmit --incremental false`、三个受影响 TypeScript 文件的 ESLint、`node --test tests/template-finder.test.mjs`、`node tests/template-engine-browser.mjs` 均通过。
- 已部署页面真实浏览器验收通过：对上述模板重新提交其当前设置，PATCH 返回 200，成功响应后约 7 ms 显示保存成功；额外模板详情请求为 0，识别 POST 为 0。引擎仍为 `finder_legacy`，配置仍为 `cmc_baseline_v1`。
- 前端挂载代码通过开发服务热更新生效，本次未重启后端。此前 502 日志发生在 12:42:39–12:43:59 UTC 的部署重启窗口，当前接口已恢复。本轮真实页面验证经本机网关访问，未验证外部转发链路或识别质量。

## 本体指引1.0风险属性校验修复 — 2026-09-14

模板 `cb6ae363-75bb-4a6d-bcb6-d9400981f2c6` 原件及配置有效，失败执行为 `f7e71f96-3807-42ed-92ab-1701fbedb63d`。使用同一原件、临时本体存储和临时解析目录复现：`riskCategory` 被误判为不在本体菜单中。该属性在当前 TTL 中已声明为数据属性，但未声明 `rdfs:domain`，原先仅汇总各类属性的校验遗漏了它。

- `runner.py` 在同一个本体读锁下补充 Finder 明确使用、当前已声明且无 domain 的数据属性；仍拒绝缺失或误用对象属性的标识，未改权威 TTL 或普通识别核心。
- 风险表回归还发现旧策略的 `riskFactor`、`preControlRiskLevel`、`postControlRiskLevel`、`riskTraceability`、`riskStatus` 在当前本体中未声明。对应五列改为既有 `iri: null` 原文展示，保留属性值和可核验出处。
- 先用安全、质量、生产风险表的三个合成原件测试复现失败。修复后 `tests/test_extraction/test_template_finder_sources.py`、`tests/test_extraction/test_template_finder_execution.py`、`tests/test_api/test_template_finder.py` 合计 **27 passed**；包括风险表取值/原文定位、非法属性拒绝、执行并发及模板设置契约。四个修改的 Python 文件 Ruff `--no-cache` 通过。
- 通过现有控制 API 暂停运行中的 `4ce12d10-56fb-49da-9250-3a3dfa874635` 和排队中的 `15760224-efb8-4556-b688-c5dd14989ce7`；确认都已暂停、无活动执行后，以 `docker compose restart -t 30 backend` 加载修复。网关健康恢复后优先恢复原运行任务，再恢复排队任务，确认分别为 running / queued；原运行工作版本保留为 95，原有另外两个暂停任务未操作。
- 真实浏览器在上述模板页面点击重新识别，POST 返回 202，新执行 `a9c1aef1-43af-42f5-82dd-dc81c59fe45b` 已 **completed**，`has_result=true`、`stale=false`、`error=null`。返回 **178 个节点、687 个属性、680 个属性有原文锚点**；图谱展示和点击“查看出处”产生原文选区均通过，未出现失败请求。
- 本轮仅重试用户报告失败的 Finder 展示执行，无模型调用、报告生成或事实提交。节点/属性计数和页面通过不代表已逐项完成真实识别质量验收；浏览器通过本机网关验证，未验证外部转发链路。

## V2 模板 AI 自动分析结构入口恢复 — 2026-09-14

模板 `14d6e8a3-4aea-43d9-814a-db1d31a3dee5` 为已配置“本体指引1.0”的 V2 草稿，已有样例及结构信息、尚无章节。原 AI 分析按钮仅存在于旧 Slot 侧栏；V2 替换侧栏后未接入该能力，入口缺失与 Finder 运行结果无关。

- V2「AST模板定义」右侧恢复「AI 自动分析结构」，两种引擎均可显式使用。复用现有分析接口，将结构结果转换为 V2 章节、分组和内容项，保留稳定标识和原文锚点，生成供作者继续配置的空内容项。
- 详情 DTO/页面补传 `sample_analysis`；样例结构单独保存时，也向原文预览提供该结构，保证生成内容项可定位。已有章节或分析期间新增的章节不被迟到结果覆盖；保存仍由作者点击「保存新修订」。
- `node --test tests/reporting-v2.test.mjs` 通过；新增的 `node tests/template-structure-browser.mjs` 使用真实解析的合成 Word、真实 V2 编辑器及隔离 HTTP fixture，通过 Finder/V2 入口、显式请求、失败重试、部分结果提示、原文选区、V2 保存及重复/迟到响应保留草稿等检查。
- 四个修改的 TypeScript 文件 ESLint、`tsc --noEmit --incremental false` 通过；既有后端接口回归 `tests/test_api/test_word_evidence.py` 为 **2 passed**。未修改后端或重启服务，前端热更新生效。
- 在指定模板真实页面点击一次分析，接口返回 200，生成 **1 个章节、6 个分组、27 个内容项**，原文定位通过。该样例唯一章节超出既有单次语义分析预算，返回 `completion=incomplete`、`section_budget_exceeded`，页面明确提示并保留已生成的原文结构。本次不代表完整语义模型分析通过。
- 真实浏览器验证未点击保存；数据库 schema 摘要及 `finder_legacy` 引擎设置前后一致。观察到的写请求只有结构分析及既有本体覆盖查询，无图谱识别、报告执行或模板保存请求。验证通过本机网关访问，未验证外部转发链路。


## Slot 报告兼容实施与真实验收 — 2026-09-14

本节对应新增 FR-09–FR-12，补充历史仅图谱展示的验收范围。

### 实际模板与报告

| 项目 | 验证结果 |
|---|---|
| 原修订 | v2.3 `14d6e8a3-4aea-43d9-814a-db1d31a3dee5` 无章节；v2.4 `4896d287-bdf0-448b-bf0a-b1d56953f772` 有 27 个内容项，全部未定义输入 |
| 已保存修订 | **v2.5 `121dae54-2b00-4b5a-ae49-e71e5a70acf9`**，1 个章节、6 个分组、21 个内容项；引擎为本体指引1.0 |
| 原件 | `a2c65c44-9f37-4cb6-8e0f-4401d037b525`，原料药 HRS-5678 临床备样生产信息 - 冲突测试.docx |
| 源内容 hash | `a7adbf233232b91dfbae8b9772dbba4eb2038eb6a07409e23e0814e402cca30b` |
| Finder | execution `47d8d2b2-2059-477a-8593-e57e55862612`，Profile 2，completed、非 stale |
| 模板 hash | `dcab6235d9bbc1de86fb81431e5726b2bb51845f32b954bba69e07e79cdf0841` |
| 数据预览 | run `4ab557a128bf4b09a88ef839ea8f0cc4`，completed；真实输入非空 |
| 报告预览 | run `08ddd47e71624050a0b21fd0c9618d55`，attempt 2，completed；材料 incomplete |
| AI | `Qwen3.6-35B-A3B` 实际生成产品/生产计划和工艺两个段落，必要引用检查通过，无模型替身或固定业务值代填 |
| DOCX | 38,658 字节，artifact `9448fc8267184fc65053a44c2ad65bf258a5e52bfd55b486be244bae0d5654d6`；已通过认证 API 与真实浏览器下载 |
| 输入要求 | **42/58（72%）**；必填 **11/27**，仍有 16 项缺口；并非材料齐备或全部内容项配置完成 |

核对的产品段落为：`产品：HRS-5678 剂型：注射用粉针剂 给药途径：静脉滴注 批量下限：1.50 kg 批量上限：12.00 kg 生产用途：临床I期试验`。工艺保留 `5678-4` 等真实原文引用；没有将样例产品 HRS-1234 写入报告正文。

两个设备表按候选编号连接 Mock 设备档案，分别 12、5 行；原文规格与 Mock 规格单列。相同编号的不同发生位置不自动合并；未能确定车间的候选保留并标明“归属待确认项”，不因为过滤条件未决而静默删除。备注行保留。另增加评估组 5 人、审批组 3 人的明确 Mock 名单，保留原附件、批准人和评估人/日期项，名单不代替签署。

### 实测中修复的问题

- `records` 的字段类型原先按整个集合推断，使 Mock 编号成为嵌套列表、阻止连接；改为按行推断，编译器版本为 `output-compiler-v2.4`。回归验证按编号连接、同名不串行、原文/Mock 规格并存、Mock 缺失和刷新变化。
- 本地模型拒绝原行文协议，HTTP 400：`Failed to initialize samplers: failed to parse grammar`。递归完整 AST 及过大的数组上限不适用于当前模型服务器；改为有限节点目录和必需引用索引。模型只选择措辞/引用顺序，服务器仍校验引用、缺口与预算。首个报告尝试失败，修复后使用原固定输入通过既有 attempts 接口只重试失败项，完成 DOCX。
- 历史列表 response model 没有 narratives，导致已写入的演示标识未显示。新增显式 `demonstration` DTO 字段，API 综合测试和真实页面均通过。
- 实际自动语义分析仍有错误和未决建议，例如“附件→评估组”。该建议未保存；最终修订采用核对后的字段和连接。自动建议是可审阅草稿，不能宣称任意复杂模板均可无人工核对地转换。

### 本次验证命令与结果

- `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -p no:cacheprovider -q tests/test_reporting tests/test_extraction/test_slot_suggester.py tests/test_extraction/test_template_finder_sources.py tests/test_extraction/test_template_finder_execution.py`：**258 passed，9 skipped**。在记录类型修复后运行；随后本地行文协议的最终修改另执行下一项。
- `... pytest ... tests/test_reporting/test_finder_report_compatibility.py tests/test_reporting/test_output_semantic_guards.py`：**42 passed**，覆盖最终有限节点协议、必需索引、禁止伪造措辞、模型错误分类、旧 Slot 必填覆盖和 Mock/Finder 输入。
- `... pytest ... tests/test_reporting/test_finder_report_compatibility.py tests/test_reporting/test_model_context.py tests/test_reporting/test_output_contracts.py tests/test_api/test_template_finder.py`：**52 passed**。历史 DTO 修复后再执行 `tests/test_api/test_template_finder.py::test_finder_slot_configuration_mock_preview_and_formal_boundary`：**1 passed**，新增断言核对真实历史列表。
- 前端 `node --test tests/reporting-v2.test.mjs tests/template-finder.test.mjs`：两个文件全部通过；`tsc --noEmit`、相关文件 ESLint、后端相关文件 Ruff 和 `git diff --check` 通过。
- `node tests/template-structure-browser.mjs`：隔离浏览器通过表头合并/备注保留、显式分析、保存、错误重试、原文定位和迟到结果保留人工草稿。使用本机已装 Playwright/Chrome/Esbuild，没有新增依赖。
- 已部署应用真实浏览器：v2.5 的 Slot 已关联字段、报告正文、原文 DOM 选区、Mock 来源弹窗、历史演示徽标及 DOCX 下载均通过；无页面脚本错误，也没有页面读取隐式启动识别/模型请求。
- 通过现有配置 API 对 v2.5 双向切换引擎，schema hash 和 execution ID 均保持；最终恢复 `finder_legacy`，当前结果未过期。

9 项 skip 来自未配置专用可销毁 PostgreSQL 测试库；不能将 SQLite 或实库业务操作等同于 PostgreSQL 锁/并发测试通过。本轮无新数据库结构变更，未重建生产镜像或运行生产前端构建。

### 部署与使用

后端代码挂载后已重启生效，前端通过现有开发服务热更新；健康与真实认证页面访问通过。此前几轮维护暂停任务均曾恢复；本节最后几次部署前，活跃 run、lease 和 annotation 均为空，未中断其他工作或改写共享 Mock。既有失败任务未因本次部署自动重试。

打开 `/settings/ast-templates/121dae54-2b00-4b5a-ae49-e71e5a70acf9`，在「AST模板定义」查看已关联 Slot；在「报告预览 → 历史报告 → 查看/下载」使用已完成的演示草稿。原修订和失败尝试保留。外部转发链路未单独测试，浏览器通过配置域名映射到本机网关验证。

## 2026-09-15 Section Prompt 与自定义 AI 行文恢复

### 实现和部署

- V2 Section 保存可选 `narrative` 配置；未配置时保留旧 hash。编译器 `output-compiler-v2.5` 将章节正文展开为普通输出项，作者 schema 不保存展开副本。
- AST模板定义中恢复 Section 的写作要求、输入选择、AI 生成 Prompt、采用/编辑 Prompt 及本节行文预览。子内容项的非空 Prompt 保留，空 Prompt 继承章节要求。
- 恢复 `generate-section-prompt` 设计接口；正文预览、完整报告及 DOCX 共用 V2 管线，版式预览也显示章节正文。旧 V1 正文预览执行接口未恢复。
- 自定义草稿策略允许自然措辞，通过 `[[input:N]]` 代入真实输入引用；越界/未解析标记和遗漏必需引用会失败。自定义措辞本身仍需作者核对，不声称引用校验已证明全部文本事实正确；正式报告和签署继续受草稿门禁限制。
- 后端已重启、前端通过现有开发服务更新。重启前活跃 run/lease/annotation 均为空。启动期间网关曾短暂 502，启动完成后 `/api/health` 返回 `status=ok, modules_loaded=true, module_count=10`。未执行新迁移或生产前端构建。

### 本次验证

- Section 专项最终 **16 passed**：旧 schema/hash、单次展开、禁用、缺配置/删除输入、子 Prompt 覆盖/继承、派生与 Mock 过滤依赖、真实输入引用/DOCX 段落、错误引用、草稿门禁、Prompt API 角色和模型关闭、无模型版式预览、新修订保存/读取及冲突。命令：`PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -p no:cacheprovider -q tests/test_reporting/test_section_narrative.py`。数据库为隔离 SQLite。
- 相关回归两组分别 **60 passed**、**50 passed**（包含当时的 Section 用例，数量有重叠，不相加）。第一组：`test_section_narrative.py test_output_rendering.py test_output_semantic_guards.py test_finder_report_compatibility.py`；第二组：`test_section_narrative.py test_compilation_cache.py test_output_contracts.py test_output_migration.py test_report_signing.py` 及 `tests/test_api/test_template_finder.py`。均使用隔离 fixture；本轮未运行 PostgreSQL 并发验收。
- 前端 `node --test tests/reporting-v2.test.mjs tests/template-finder.test.mjs`、`tsc --noEmit --incremental false`、相关文件 ESLint、后端相关文件 Ruff 和 `git diff --check` 通过。
- `frontend/tests/section-narrative-browser.mjs`：真实 React 组件、合成 API，通过显式生成/采用、手工编辑、共享预览请求、子 Prompt 保留、过期预览提示与切换修订丢弃迟到响应。使用现有 Playwright/Chrome/Esbuild。
- 部署页面只读检查：v2.5 显示 Section 编辑器，Prompt 与预览按钮可用，本体指引1.0保持，无页面 JS 错误。浏览器强制阻断非 GET 请求；两个原有 `coverage-doc-classes` 查询 POST 被阻断，没有发送模型、识别、保存或报告生成操作。该检查不等同真实 API 写入验收。
- 真实本地模型：对 v2.5 选取六项产品/计划输入，实际生成可复用 Prompt；读取既有报告 `08ddd47e71624050a0b21fd0c9618d55` 的固定来源包，在进程内完成 Section 编译、解析和自定义正文渲染，`execution_status=completed`，DOCX 内存渲染核对原值。没有创建新的线上模板修订或报告运行。

模型实际生成的自定义正文包含：“产品HRS-5678采用注射用粉针剂剂型，并通过静脉滴注途径给药……该产品用于临床I期试验，预计批量区间为1.50 kg至12.00 kg。”产品、剂型、途径、用途及批量均为输入引用代入。该次模型返回一个段落；后续提示补强段落数量和空行要求，分段转换经隔离测试验证，未再进行真实模型格式遵循测试。

### 待批准的线上验收操作

自动审批拒绝了浏览器保存新修订和创建报告运行的验收脚本，理由是当前授权未明确涵盖这些线上数据变更。当前保留 v2.5；T14 的线上写入部分未完成。准备的操作如下，需用户明确授权后执行：

1. 从 v2.5 创建新修订，保留 6 个分组、21 个内容项及现有 Prompt，章节标题改为“产品与生产计划”。
2. 启用 Section 正文，引用“原文对象描述、制剂剂型、给药途径、预计批量下限（kg）、预计批量上限（kg）、生产用途”六项现有图谱输入。
3. 保存以下已生成的 Prompt，并补充“正文严格分为两段，段落之间留一空行”；继续使用本体指引1.0，生成本节预览和完整 DOCX 草稿进行页面验收，不修改原修订或 Mock 数据。

> 本节旨在阐述产品的基本属性及生产规划，全文分为两段，不使用小标题或列表。第一段聚焦产品本体，需依次描述原文对象描述、制剂剂型及给药途径，语言需正式且连贯，严禁虚构任何未提供的技术参数。第二段聚焦生产计划，需明确生产用途，并给出预计批量区间，具体数值需严格引用预计批量下限（kg）与预计批量上限（kg），必须保留原始单位及数据来源说明；若任一变量缺失，需明确标注“待补”，不得估算或编造数据。

### 同日：去除 AST 编辑树的 upload 占位根节点

根据用户补充要求，AST模板定义不再显示标题为 `upload` 且没有原文标题来源的外层容器，下级分组直接展示；具有原文标题来源的真实同名章节保留。Section 行文控件仍可用，Prompt 设计使用“文档正文”而非临时文件名。此项仅调整作者界面，没有改变线上模板 schema、版本或绑定。前端热更新已生效；TypeScript、定向 ESLint 和实际页面只读检查通过，v2.5 的 6 个分组、21 个内容项、Slot 已关联数据及 AI 行文按钮均保留，无页面 JS 错误或写入请求。

## 2026-09-15：按 b6f9bd4 恢复 Prompt 生成与临时章节预览

对照提交 `b6f9bd4e55822e20dce0a2ef0fa8f6d9fdd472c1` 的 `template-slot-editor.tsx`、`ast_templates.py` 与原 `narrative_generator.py`：旧版从模板样例生成 `{{字段名}}` Prompt，预览直接返回本节正文，缺失字段待补，不执行完整报告。当前失败报告 `80d28d029a08490190efe21b3939441d` 的错误为 `OBJECT_UNIVERSE_OPEN`，属于模型调用前的输入消费阻断。

已恢复专用预览接口与上述页面交互；保持当前模板引擎分流和来源权限。V2 输入复用现有编译/解析，Mock 保留来源与筛选；自定义报告行文允许读取部分集合及有缺失字段的记录，严格策略仍阻断不合格输入，材料状态不改为齐备。未重建旧报告执行器，没有新增数据库迁移。

本次实际验证：

- 后端 5 个定向文件（`test_section_preview.py`、`test_section_narrative.py`、`test_finder_report_compatibility.py`、`test_retired_execution.py`、`test_template_finder.py`）：**69 passed**。收尾新增空模型响应反例及措辞/单位修正后，两个章节文件 **24 passed**；反向路径复核后 `test_section_preview.py` **8 passed**。这些是分次运行结果，不相加为测试总数。
- Ruff、前端 TypeScript、定向 ESLint、`reporting-v2.test.mjs` 通过。`section-narrative-browser.mjs` 通过自动样例、Prompt 直接填入、手工修改保护、临时预览请求、关闭预览及切换修订后的迟到响应检查。
- 已部署到现有开发环境。重启前确认无活跃识别作业/租约；实际 nginx 网关 `/api/health` 返回健康。未修改两项已有失败任务。
- 真实浏览器访问当前 v2.5 模板 `121dae54-2b00-4b5a-ae49-e71e5a70acf9`，显式点击「从样本生成」和「AI 行文预览」，调用真实本地模型。最后一轮 Prompt/预览均 **HTTP 200**；自动样例 **3,836 字**、本节 **11 个变量**、Prompt **1,051 字**、正文 **1,618 字**。
- 本节包含两组不完整设备集合，正文仍成功返回；已验证 HRS-5678 产品、生产计划、两个车间设备表、分开的评估/审批 Mock 名册和待补标记。正文不残留 `{{...}}`，界面不再提示「本节生成未完成」。来源为当前用户的 Finder 执行 `47d8d2b2-2059-477a-8593-e57e55862612`，原件作业 `a2c65c44-9f37-4cb6-8e0f-4401d037b525`。
- 浏览器阻断模板保存、修订创建、报告创建等请求；两项 AI 请求前后 `AstTemplate` 和 `ReportRun` 数量一致，页面无运行错误。样例仅用于 Prompt 设计；真实预览的事实来自原件和显式 Mock。模型调度/用量仍按既有机制记录，因此模型验收不称为纯只读操作。

本轮真实模型验收针对本体指引1.0及该模板。普通引擎的已有结果读取、引擎隔离、章节路径/方向/条件做工程验证；未执行普通引擎真实模型质量评测。本文不将本节预览成功视为整份报告材料齐备或已审核；也不追认为已完成 T14 的线上新修订保存及完整报告创建验收。完整报告部分集合的自定义行文另有纯解析/渲染回归，原有正式报告/签署门禁保留。

## 2026-09-15 v2.6 输入要求满足率修正

目标页面：`/settings/ast-templates/31ce642d-e960-4413-a3c1-75f1a66f025f`。原快照为 33/58（57%）：12 项来源及派生输入未满足，13 项空输出配置也被混入输入统计。新修订不继承引擎是既定契约，本次仅通过引擎设置 API 为目标修订选择本体指引1.0，按该用户/模板/原件重新识别，未复制父修订结果。

- 输入满足率排除明确标识为 `SLOT_CONFIGURATION_MISSING` 的输出配置项；它们在页面单独显示数量并可跳转。未删除快照诊断、未将空白正文标为完成，其他未知问题和真实输入缺口仍计入。没有实际输入且材料未齐时仍为 0%。
- 旧 Mock 设备档案中的无 IRI 字段按提供方明确列名映射：规格型号、主体材质、安装位置。已有其他 IRI 的同名属性不误取；冲突值保留。映射只生成 Mock 记录，不写本体属性或修改共享档案。
- 真实页面刷新得到输入 **42/45（93%）**，另列 **13 项内容待配置**。最新验收运行 `14e7eb1e3d354ba5b17753135803faea`；隔离 Finder 执行 `0d89480f-7ac0-4bfd-bcb6-1b82e36e4216`。PF64203、PF64603 的已有材质“不锈钢”已成功读取。
- **100% 尚未验收通过**：EE64218 档案未提供安装位置；原件物料桶行没有设备编号，无法关联 Mock 档案或确定车间，两个车间筛选保留未决。因此剩余三个输入检查为设备档案必填字段及两组车间筛选。未用样例或猜测补值。
- 定向后端测试 `test_finder_report_compatibility.py` **17 passed**；前端 `reporting-v2.test.mjs`、TypeScript、定向 ESLint、后端 Ruff、`git diff --check` 通过。真实浏览器验证 93%、42/45、13 项提示及两类缺口跳转，无页面错误。后端重启生效，前端热更新；健康接口 `status=ok, module_count=10`。一次部署后浏览器脚本等待响应超过默认 30 秒，将脚本等待调整为 120 秒后通过。

本次显式创建数据预览快照以验证用户要求的修正，未生成新的完整报告、模板修订或调用 AI 行文，未变更两项既有失败识别任务。达到全部必填输入齐备仍需补充上述位置和设备身份/归属依据。

## 2026-09-15 AST 业务分组标题恢复

目标仍为 v2.6 模板 `31ce642d-e960-4413-a3c1-75f1a66f025f`。根因是离线结构构建器统一使用“表格”作为表格分组标题，AI 语义补全仅处理候选字段，未补充分组标题。

- 结构构建器优先使用表内合并标题或紧邻表格的引导文字。嵌套表只读取本单元格内的前置段落，不跨单元格或复用上一张表的标题；无明确标题时使用字段标签，避免把样本字段值当标题。新样本的结构分析和已有模板的标题建议共用此逻辑，无额外模型调用。
- 模板详情 GET 增加只读 `structure_titles`（分组 ID → 建议标题）。编辑器初始化时仅补正“表格”等默认标题，保留手工标题；建议进入编辑草稿及其预览，随“保存新修订”持久化。GET 不改写已存 schema/hash，不影响当前 Finder 身份或输入快照，不新增自动保存操作。
- 真实页面六个标题为：**风险评估对象基本描述**、**642 车间设备表**、**646 车间设备表**、附件、风险回顾日期、QA 意见（同一标题）、附注说明、风险回顾情况、结论、评估人/日期（同一标题）。原 21 个内容项、输入绑定、Section Prompt 和本体指引1.0设置保留。
- `test_template_structure_builder.py` 与 `test_slot_suggester.py` 合计 **29 passed**；前端 `reporting-v2.test.mjs`、TypeScript、定向 ESLint、后端 Ruff、`git diff --check` 通过。隔离浏览器验证新样本业务标题生成及保存；真实页面只读浏览器验证六个标题、手工编辑保留、刷新重载、原 schema/hash 不变，无页面错误。
- 后端重启、前端热更新已生效，重启前无活动识别任务，网关健康。未创建线上模板修订或报告、未发起模型调用或重新识别。

## 2026-09-15 刷新覆盖率完成状态修正

目标仍为 v2.6 模板 `31ce642d-e960-4413-a3c1-75f1a66f025f`。用户反馈刷新无故中断，检查最近两次数据运行均为 `completed`、无执行错误，网关创建及结果读取请求均为 HTTP 200。真实页面复现发现：刷新已完成，界面却仍沿用完整报告的五步进度，停在 **60%**，等待不属于本次覆盖率检查的 AI 行文和 DOCX 渲染。

- 数据刷新仅显示数据抽取解析、模板匹配、覆盖率分析三个步骤，输入结果载入后显示「覆盖率检查已完成」和检查进度 **100%**。完整报告模式保留原五步进度；实际输入满足率继续独立统计。
- 补齐创建请求结束至输入结果载入之间的忙碌状态，期间持续显示刷新动画，并禁用刷新及生成按钮；不会在结果尚未载入时提示完成。
- 真实页面刷新运行 `31f0ca8c2b0349afae95e52b46cc67db` 成功；另一次运行 `5f252b210de549e69d1276e71e3c6e94` 暂缓真实 `/inputs` GET，通过等待期间的按钮、进度及完成提示断言，释放请求后成功完成。两次均为 `completed`、无执行错误，创建和结果读取 HTTP 200，页面无 JS 运行错误。
- 当前真实输入为 **43/45（96%）**，仍有 2 项输入未满足和单独列示的 13 项内容待配置；检查进度 100% 不代表材料齐备。浏览器截图分别为 `/tmp/coverage-refresh-completed.png`、`/tmp/coverage-refresh-delayed-completed.png`。
- 前端 `./node_modules/.bin/tsc --noEmit --incremental false`、两个修改组件的定向 ESLint、`git diff --check` 通过。`tests/reporting-browser.mjs` 已补充数据刷新完成、延迟输入及完整报告五步断言，但本次整页合成浏览器脚本在先前的源文档工作区「关系图谱识别结果」等待处失败，未执行到这些断言，不能计为通过；上述两次真实页面检查用于验证本次修复。

前端通过现有开发服务热更新生效，无需重启后端；本轮仅创建数据检查运行，未调用 AI 行文、生成完整报告、保存模板修订或修改共享 Mock。真实浏览器通过配置域名映射到本机网关访问，未单独验证用户侧外部转发链路。
