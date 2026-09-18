# 图谱业务名称展示修复

2026-09-17，针对 FR-19 的可读性修复：图谱画布、关系/属性详情、覆盖表使用实体名、本体关系名和属性名；证据按钮以用途和序号命名。实体/候选/证明/依赖 ID、IRI、版本和原始限定数据移到默认折叠的技术详情。运行与元数据的技术标识同样折叠，原文定位提示不再展示证据哈希。

名称复用当前图制品的标签、predicate_menu 和覆盖条目。缺少标签时回退到可读本体局部名或明确提示；按精确 ID + revision 解析引用，不借用其他版本实体名称。属性值中的业务编号原样保留，否定、条件、模态及组选择限定继续显示。没有新增接口、持久化或模型调用，也没有修改抽取、暂停或恢复流程。

验证结果：

- `frontend/`：TypeScript `--noEmit --incremental false`、3 个改动 TypeScript 文件的 ESLint 通过。
- `node --test tests/document-graph.test.mjs tests/document-analysis-runs.test.mjs tests/template-document-performance.test.mjs`：3 个测试文件通过；直接执行 `node tests/document-graph.test.mjs` 确认 9 项映射与名称用例通过，包括缺名、同 ID 不同版本、菜单标签优先、业务编号和限定文本。
- `python specs/027-ontology-extraction-engine-v2/check_design.py --self-test` 通过：19 项需求、27 项任务、22 个拒绝变异反例。
- 更新后的 `document-analysis-history-browser.mjs` 在隔离前端通过，覆盖关系/属性名称、覆盖主体名、保留 `BATCH-2026-001` 和条件文本、折叠/展开技术标识、原文请求仍传原始 selection_ref，以及 390/768/1440/1920 像素布局。
- 真实 API 浏览器脚本的证据定位选择器同步为业务名称，并保留原始 selection_ref 断言；本次没有重新启动其专用后端或执行真实 API 集成验收。

浏览器使用已有 Chrome 131、Next.js webpack 隔离前端及合成 API。最初尝试现有 8081 开发入口时，测试浏览器停留在空页面且未发起业务 API 请求，随后改用隔离入口完成验收；不据此声称真实部署入口或真实模型验收通过。所有脚本中的上传/预算变更均由测试拦截，未操作实际运行。验证后停止本次隔离前端，没有重启业务后端。

- [图谱与关系详情](graph.png)
- [浏览器回归结果](browser-report.json)
