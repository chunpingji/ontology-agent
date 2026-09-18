# 隔离验收
1. 合成文档包含根属性、同层关系和下一层属性。检查计划、排序、执行事件满足逐层属性优先。
2. 同一单值属性提供两处冲突值，验证冲突核验；多值、未指定、条件值不误关闭。
3. 暂停或完成后确认一条属性、按理由驳回另一条；有效图谱排除，驳回视图仍能定位原文。
4. 局部重识别只重开授权主体/属性/原文，保留其他结果/费用；新请求携带审核身份与理由，不直接复用旧失败结果。
5. 重复提交、旧版本、换用户、修复中暂停与冷恢复，验证幂等、409/404、调用/费用不重复。
使用临时 SQLite、TTL、IR、模型替身与隔离制品目录；生产操作和真实模型评测另行验收。

## 实际入口与部署前检查
报告上传文档及模板页的属性值节点进入属性审核抽屉；运行活动时先暂停，停止后重新选择当前属性以固定版本。确认、驳回、局部重识别独立保存；重复网络提交使用各自原请求键。总体专家意见仍是独立功能。

数据库结构新增 `0039_property_review_repair`，依赖本次已有的 `0038_property_cardinality`。交付未对生产执行迁移、重启或模型任务；发布时需按现有部署流程升级数据库。旧运行恢复沿用冻结策略，新的分层默认用于新运行。

本轮源码/测试入口：
- 后端：`test_layered_recognition.py`、`test_slot_completion.py`、`test_slot_replay.py`、`test_expert_review_core.py`、`test_expert_review_execution_audit.py`、`test_document_property_reviews.py`、`test_document_property_review_migration.py`、`test_document_property_repair_execution.py`。
- 前端：`node --test tests/document-property-review.test.mjs tests/template-document-performance.test.mjs tests/document-ranking.test.mjs tests/document-analysis-runs.test.mjs`，以及 TypeScript/ESLint。
- 浏览器：`tests/document-property-review-browser.mjs` 使用隔离合成 HTTP，覆盖 11 个交互场景；不能替代真实后端浏览器集成。

真实模型的事实召回、冲突发现率、总调用数与费用仍需固定输入/本体/模型/预算单独评测。单值关闭的冲突检查范围为已准入记录及全文精确词面候选，保留未检查数量，不宣称全文无遗漏。PostgreSQL 并发锁验收本轮未执行。

## 2026-09-13 本次验证结果
- 后端 27 个相关测试文件组合：264 passed。包括新旧策略、审核/修复 API、0039 SQLite 迁移、真实 worker/制品存储配合模型替身、失败/取消、冷恢复、逐层门禁与单值关闭、局部改属性及保留正确结果。
- 前端上述 4 个 Node 测试文件通过；审核文件包含 9 个检查场景。TypeScript `--noEmit`、定向 ESLint 与后端定向 Ruff `--no-cache` 通过。
- 隔离 Chrome 合成 HTTP：11 个交互场景通过。最后追加的暂停提示及原因中文映射由 Node/类型/静态检查验证。
- `git diff --check` 通过；未部署、未执行生产迁移，未运行真实模型质量/费用评测或 PostgreSQL 并发验收。
