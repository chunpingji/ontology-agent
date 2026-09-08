# 实现与验证记录

日期：2026-09-06。分支：`020-report-output-semantics`。本记录区分本地实现验证与业务发布验收；不代表指定模板已发布、真实规则已获批准或现有服务已经部署新代码。

## 交付范围

- Spec Kit 的 specify、clarify、plan、tasks、implement 制品已完成并保留需求追踪。V2 使用规范化的 Binding、InputDefinition、OutputUnit、TypeSpec 及服务端注册契约；编译器检查精确本体路径、类型、依赖闭包、重复范围和章节独立材料要求。
- 数据运行冻结 SourceBundle → ReportInputSnapshot → OutputAST。预览、覆盖、重试、DOCX 与下载共用固定产物；覆盖补抽任务直接消费冻结的逐主体选择记录，不重新选择事实。多来源必须显式绑定，旧单作业门面明确拒绝自动猜测多来源。
- 逐字段状态、集合闭合证明、主体/时间/来源和类型补充契约参与资格判断。FALSE/UNKNOWN 不生成低风险，计划不证明实施；声明的批准措辞、参数、适用范围和证据前提均受检查。
- 确定性表格、表单、段落、列表与重复输出，以及受限 assisted 节点均进入同一 AST。模型不能添加未授权事实或修改确定性引用格式。assisted 使用已审核措辞片段，正式内容仍须绑定正文哈希审核。
- 正文版本、审核、签署会话、真实账户重认证、追加签名事件、CAS、幂等封装及中断恢复分别持久化。签署不能补齐材料缺口，历史正文与已保存封装保持不可变。
- 模板创建、列表、详情、报告阅读和旧预览/覆盖/下载接口已接入版本分流。历史模板、原样例、来源文件与旧工件保留；已发布/被编译引用的模板不能删除，旧 published 规则不能原位更新或删除。
- 指定 v18 的 32 项 Slot 台账已重新生成；共享团队输入、成对批量范围、会签区域及单一七列风险表已登记。R-RA1～5 的逐声明迁移台账仍为待审核草稿。批量单位/边界、名称属性及业务结论不会从字段后缀或旧文案猜测。

## 已执行检查

| 检查 | 最终结果 | 范围与限制 |
| --- | --- | --- |
| `PYTHONPATH=backend backend/.venv/bin/pytest backend/tests -q` | **1034 passed, 5 skipped**，87.30 秒 | 5 项跳过仅因默认运行未提供隔离 PostgreSQL URL；不是业务模型质量样本 |
| 隔离 PostgreSQL 16 测试 | **5 passed**，3.43 秒 | 独立连接 CAS/幂等签署、SQL 不可变守卫、封装中断恢复、过期 worker 重试、published 旧规则 UPDATE/DELETE 拒绝 |
| PostgreSQL 0027 迁移 | 通过 | 从实际 0026 schema-only 副本升级；已验证 0027→0026→0027。只有临时数据库，无生产数据或生产迁移 |
| 真实事实提交至报告集成 | 通过，计入全量 | CandidateStore 验证/独立审核 → 真实 Owlready2 writer → 快照 → 覆盖/报告；冲突新提交影响新运行，旧正文/状态不变；高精度小数未舍入 |
| `node --test tests/*.test.mjs` | **15 passed** | 引用稳定性、共享依赖闭包、本体菜单循环/继承、认证头和取消请求 |
| 浏览器生产构建验证 | **8 项检查通过** | Chrome + Playwright 1.58.2；API 全部为合成拦截，不访问实际业务。详见 [browser-validation.json](browser-validation.json) |
| TypeScript / 改动区域 ESLint / production build | 通过 | `npx tsc --noEmit`；20 个本轮 TS/TSX 文件定向检查；Next.js 16.2.9 构建通过 |
| 改动 Python Ruff / `git diff --check` | 通过 | 84 个修改或新增 Python 文件；未隐藏规则或忽略新增错误 |
| 全仓前端 ESLint | **未通过：3 errors, 6 warnings** | 既有未修改文件的错误：`ast-tree-view.tsx`、`mock/edit-dialog.tsx`、`shell/auth-guard.tsx`。没有将定向检查写成全仓通过 |

后端存在 4 条既有 Starlette/Pydantic 警告。构建提示多个 lockfile。以上均在实际日志中保留。

主要反例覆盖：错误主体/路径/时间/来源、非法 IRI 与类型、关系路径冲突、可选字段隔离、重复与嵌套范围、借用其他选择器闭合证明、部分集合、数值范围与单位、类型/声明契约修订、模型新增措辞、已计划但未实施、无确认记录、签名伪造/撤销/重放、文件中断及历史数据不变。

## 回归迁移说明

同一基线提交在独立 worktree 原样执行得到 **41 failed, 1356 passed, 4 skipped**，见 [regression-baseline.json](retirement/regression-baseline.json)。原基线已有多项 019 的“确认即提交”及旧 finder 预期失败。

纯 V1 执行套件按职责退役，原 Git 来源、文件哈希、用例名与替代测试登记于 [retired-tests.json](retirement/retired-tests.json)。混合套件保留历史工件、来源/文档版本解析、角色、拒绝审计、发布批次和结构化来源能力；过期假设改为新的显式契约，文件映射见 [adapted-tests.json](retirement/adapted-tests.json)。测试数量不能直接用于前后覆盖率比较；没有恢复自动提交或 FALSE→低来使测试通过。

## 退役审计

- [static-audit.json](retirement/static-audit.json)：314 个自有生产源码文件，Python/TypeScript AST、导入与动态执行检查通过；18 个退役模块不存在；本体执行注解加载守卫通过。第三方版本及审计边界单独记录。
- [dispatch-review.json](retirement/dispatch-review.json)：区分能力调度、显式来源记录/适配字段、仅用于合成数据的 mock 调度、历史草稿迁移工具及报告消费。没有以“搜索不到 re”代替业务分派复核。
- [database-config-audit.json](retirement/database-config-audit.json)：2026-09-06 13:54 UTC，以 `REPEATABLE READ READ ONLY` 检查实际 class mapping、property binding 和 extraction config 表；分别 0/0/1 条，无退役可执行配置。未写数据库。
- [ontology-triple-diff.json](retirement/ontology-triple-diff.json)：退役 65 条执行注解三元组，211 条业务三元组保持等构；原文与差异均保存。
- [package-audit.json](retirement/package-audit.json)：隔离镜像最终文件系统中，154 个 Python 源码哈希与审计清单一致，18 个退役模块无法导入，`app.main:app` 的 OpenAPI 包含 V2 入口。记录前端 BUILD_ID 与 2249 个产物哈希。镜像禁用网络，未运行启动生命周期、连接生产数据库或替换现有容器；该检查不替代最终生产镜像和部署入口验收。

## 尚未完成的发布门禁（T037）

1. [baseline.json](baseline.json) 的实际默认来源只有 67 个待审核候选，**没有发布事实快照**；指定模板与 R-RA1～5 未完成本次语义审核。须由业务责任人补齐精确主体、路径、单位/范围边界、条件、真实控制与确认记录，并审核新契约及迁移差异。不能把迁移草稿或合成测试当作批准。
2. 独立人工标注的 assisted/上游抽取质量样本、阈值及划分尚未冻结；无依据声明、条件/否定改变、关键引用遗漏、拒答和人工修改率尚无独立实测。不得宣称模型质量门禁通过。
3. 短/中/长文档、多来源、多记录和重复层级工作负载尚未形成部署环境的冷/热 p50/p95、峰值内存和调用量结果；也没有同配置基线 1.2 倍比较。单元测试耗时及构建耗时不属于 SLO 验收。
4. 未迁移生产数据库、发布实际模板/规则、生成真实正式报告或部署代码。实际服务的镜像/进程/schema/compiler/resolver/renderer 指纹和 HTTP 端到端验收须在后续上线步骤核对。
5. 全仓 3 个既有前端 lint 错误仍需处理后，才能要求全仓 lint 为零的发布门禁通过。

本轮无 commit、push 或业务审批。临时镜像、测试数据库容器、浏览器服务与基线 worktree 在验证完成后清理。

## 重跑入口

- 核心检查按 [quickstart.md](quickstart.md) 执行。PostgreSQL 测试只接受名称以 `reporting_` 开头的独立数据库，会清空该库业务表；不得使用实际业务库。
- 浏览器脚本为 `frontend/tests/reporting-browser.mjs`，合成数据位于 `frontend/tests/fixtures/reporting-browser.json`。先启动本地 production build，再指定 `PLAYWRIGHT_MODULE` 为外部 Playwright 模块的绝对路径，以及 `REPORTING_BROWSER_ORIGIN`（默认 `http://127.0.0.1:3107`）；可用 `REPORTING_BROWSER_RESULT` 保存结果。未修改仓库依赖。
