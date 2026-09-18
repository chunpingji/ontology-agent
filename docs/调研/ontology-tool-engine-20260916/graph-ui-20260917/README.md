# 关系图谱界面验证（FR-19 / T27）

2026-09-17：分析历史改为响应式卡片；详情使用现有 Sheet，桌面宽度 2/3、手机全宽；关系图谱改为 D3 节点和有向连线。点击实体查看其属性和关联关系，关系组保留选择、模态与范围；组连接点不计实体或普通事实边。原文证据定位继续复用同一 Word 预览。没有新增依赖、API 或持久化状态。

- [历史卡片](history-cards.png)
- [关系组画布](graph-canvas.png)
- [节点属性](node-properties.png)
- [手机详情](mobile-graph.png)
- [真实 API 浏览器结果](report.json)：61 次页面 API 请求全部为 GET；前后专用数据库水位和受控模型计数一致，无页面异常或失败 API。覆盖抽屉宽度、关闭焦点、图组/孤立节点、节点属性、继承范围、原文定位、投影切换、快速切换、刷新恢复、缩放不被轮询重置，以及手机节点详情。
- [合成浏览器回归](history-regression.json)：历史分页/同名文件、预算变更/冲突、显式上传后打开抽屉、过期读取隔离、章节树、Word 预览实例不重建，覆盖 390/768/1440/1920 像素。业务 API 均由测试拦截，不能代替真实后端集成测试。

工程检查：TypeScript `--noEmit --incremental false`、5 个改动源码文件的 ESLint、`document-graph.test.mjs` / `document-analysis-runs.test.mjs` / `template-document-performance.test.mjs` 定向 Node 检查共 35 项通过（分别为 5、20、10 项）。Spec 检查 `python specs/027-ontology-extraction-engine-v2/check_design.py --self-test` 通过：19 项需求、13 个模块、27 项无环任务、22 个拒绝变异反例；检查器同步本次明确新增的 FR-19/T27，仍精确限定已批准集合。新图映射测试覆盖反向关系、one_of 组、孤立节点、精确版本、缺端点、限定标签、空图、自环和并行关系。

真实 API 验收复用 `backend/scripts/document_analysis_browser_fixture.py` 与 `frontend/tests/document-analysis-browser.mjs`，使用本次新建 `/tmp` PostgreSQL 14 集群、专用空 `graph_ui_browser_test` 库、独立前端副本，关闭后台工作器与真实模型。此处是受控图契约的 UI 验证，不是 Qwen 或 F1 评测；未改变业务数据。验收后已停止本次隔离前后端和 PostgreSQL，保留制品。当前部署前端为源码挂载的 `next dev`，入口 `/analysis?tab=document` GET 返回 200，无需重启后端。

复现参数和环境约束参照 [现有真实 API 验证说明](../browser/README.md)，使用新的专用空测试库与输出目录；合成回归入口为 `frontend/tests/document-analysis-history-browser.mjs`。
