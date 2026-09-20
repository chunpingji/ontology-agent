# 031 验证步骤与结果

日期：2026-09-19。使用已安装环境，不启动后端或模型服务。

本页记录工程验证；用户随后授权的后端重新部署已完成，实际状态与生效检查见 [deployment.md](deployment.md)。

本文前四节保留先前语义候选实现的工程验证记录；本次调用效率优化的最新验证单独列于末节，不将历史测试或前端检查计为本次结果。

## 核心调度与集成回归

在 `backend/` 执行：

```bash
.venv/bin/python -m pytest -p no:cacheprovider -q \
  tests/test_extraction/test_semantic_record_discovery.py \
  tests/test_extraction/test_record_executor.py \
  tests/test_extraction/test_record_reference_pipeline.py \
  tests/test_extraction/test_contextual_record_executor.py \
  tests/test_extraction/test_contextual_reading_executor.py \
  tests/test_extraction/test_record_page_semantics.py \
  tests/test_extraction/test_record_focus_scope.py
```

结果：40 passed。随后加强语义集成用例，使用实际精排持久化/恢复接口；该文件在下一组中重新通过。

## 契约、恢复、授权与 API 回归

在 `backend/` 执行：

```bash
.venv/bin/python -m pytest -p no:cacheprovider -q \
  tests/test_extraction/test_semantic_record_discovery.py \
  tests/test_extraction/test_record_discovery_contracts.py \
  tests/test_extraction/test_record_discovery_current_state.py \
  tests/test_extraction/test_contextual_current_state.py \
  tests/test_extraction/test_contextual_record_context.py \
  tests/test_extraction/test_contextual_record_adapter.py \
  tests/test_extraction/test_record_reference_dependencies.py \
  tests/test_extraction/test_record_model_adapter.py \
  tests/test_extraction/test_record_relation_adapter.py \
  tests/test_extraction/test_tool_engine_configuration.py \
  tests/test_extraction/test_semantic_ranking.py \
  tests/test_extraction/test_ranking_embedding_cache.py \
  tests/test_extraction/test_semantic_ranking_budget_control.py \
  tests/test_extraction/test_ontology_guided_boundaries.py \
  tests/test_api/test_document_analysis_schemas.py \
  tests/test_api/test_document_analysis.py
```

结果：236 passed，4 skipped。跳过项需要未配置的 `DOCUMENT_ANALYSIS_TEST_DATABASE_URL` 专用可销毁 PostgreSQL 库，不能视为 PostgreSQL 锁/并发验收通过。两组包含重复的语义测试，不相加为独立用例数。

## 静态与前端验证

本轮涉及的后端实现和测试文件执行 `.venv/bin/ruff check --no-cache` 通过；仓库根 `git diff --check` 通过。

在 `frontend/` 执行并通过：

```bash
./node_modules/.bin/tsc --noEmit --incremental false
npm run lint -- src/lib/api.ts src/lib/document-analysis.ts src/components/analysis/template-document-graph-panel.tsx
node --test tests/document-analysis-runs.test.mjs tests/document-graph.test.mjs
```

Node runner 报告两个文件测试通过。未执行前端生产构建或真实浏览器集成。

## 新运行验收场景

部署后使用新建运行，默认冻结 `record-discovery-v2`、每组最多 2 卡、阈值 0.25，正常配置使用本地语义模式：

1. 首条记录为日期封面、正文包含目标实体时，高相关正文应先进入识别；不得出现逐记录遍历全部卡片的计划。
2. 观察实体发现待处理数随准入减少；候选新增只来自明确原文缺口的有界补选。实体同类但不同位置不应被直接排除或合并。
3. 在已保存发现响应后暂停并继续，核对响应和向量缓存复用；未入选组合仍显示未经识别。
4. 固定文档、本体、模型与参考，比较最终实体/属性/关系质量、生成调用、排序调用、tokens 与耗时。召回下降不能用任务数减少掩盖，必要时校准候选上限和阈值。

以上真实运行场景本次未执行；工程用例采用受控向量和受控模型，不代表真实质量验收。

## 2026-09-19 调用效率优化验证

本节对应[识别效率修正](recognition-efficiency.md)，与前述历史验证分开记录。默认同表连续数据行每批最多 4 行，仍受最多 8 条源记录与 1200 字限制；纵向合并单元格继续按单行处理。

最终整合回归在 `backend/` 执行：

```bash
.venv/bin/python -m pytest -p no:cacheprovider -q -o faulthandler_timeout=60 \
  tests/test_extraction/test_record*.py \
  tests/test_extraction/test_contextual*.py \
  tests/test_extraction/test_table*.py \
  tests/test_extraction/test_semantic_record_discovery.py \
  tests/test_extraction/test_batch*.py \
  tests/test_extraction/test_attribute*.py \
  tests/test_extraction/test_reading_groups.py \
  tests/test_extraction/test_model_context_projection.py \
  tests/test_extraction/test_ontology_guided_boundaries.py \
  tests/test_extraction/test_tool_engine_adapter.py \
  tests/test_extraction/test_tool_engine_configuration.py \
  tests/test_extraction/test_tool_engine_recovery.py \
  tests/test_api/test_document_analysis_schemas.py \
  tests/test_api/test_document_analysis.py
```

本次整合结果：572 passed、1 failed、4 skipped，耗时 189.05 秒。唯一失败是 `test_new_factory_uses_frozen_config_without_legacy_domain_policies` 仍断言旧表格策略；同步断言为 `bounded-table-rows-v2` 和 `max_table_rows_per_group=4` 后，执行：

```bash
.venv/bin/python -m pytest -p no:cacheprovider -q \
  tests/test_extraction/test_tool_engine_configuration.py \
  tests/test_extraction/test_record_answer_corrections.py
```

结果：40 passed，包含修正后的配置用例及后来补充的 4 项纠正容量边界用例。整合发现的失败已由定向复测关闭；这些数字包含重叠用例，不累加。

纠正容量边界补充后，还执行以下定向回归：

```bash
.venv/bin/python -m pytest -p no:cacheprovider -q \
  tests/test_extraction/test_record_answer_corrections.py \
  tests/test_extraction/test_record_model_adapter.py \
  tests/test_extraction/test_tool_engine_adapter.py \
  tests/test_extraction/test_tool_engine_recovery.py
```

结果：78 passed。包含与整合回归重复的用例，不相加为独立用例数。

运行环境说明：最初沙箱内整合回归在 `test_current_candidate_cache_rebuild_and_api_source_isolation` 的本地 TestClient 线程等待处停滞并中止；相同相关组在沙箱外使用同一隔离 fixture 得到 18 passed，随后完成上述沙箱外整合回归。4 个跳过项均需未配置的 `DOCUMENT_ANALYSIS_TEST_DATABASE_URL` 专用可销毁 PostgreSQL 测试库，不代表 PostgreSQL 锁/并发验收通过。涉及实现和测试文件 Ruff（`--no-cache`）通过，`git diff --check` 通过；测试仅有既有 FastAPI/Starlette、Pydantic warning。

关键受控结果：

- 两条普通表格数据行只需 1 次发现、1 次核验，共得到 2 个实体和 4 个属性；把甲行属性值错指乙行时，即使核验模型均返回 supported，仍由 `owner_row_mismatch` 拒绝。
- 没有合法对象端点时，关系调用为 0，工作保持未尝试/依赖阻塞；后到端点及冷恢复能继续证明关系，不重复已付发现。
- 桥接 ID 错误、supported 无引用及记录组成支持不足能在原 4 次预算内纠正；持续错误仅拒绝相关声明，保留其他通过核验的声明。
- 纠正清空候选时，原候选重新接受独立核验，不能凭恢复直接入图；完整纠正输入放不下时跳过纠正，2 次调用仍能核验独立正确声明。
- 章节和类型轮转不扩大候选池；所有组首选同一类型时，已入选的第二类型也及时获得机会，冷恢复保持同一顺序。

工程实现阶段只修改后端识别链路及相关规范/测试，未执行前端检查。当时没有重启或部署，没有对原运行回写或转换，也未进行真实模型质量/耗时评测。用户随后授权的[本机部署](deployment.md)已完成，新建运行默认启用新策略；上述调用数来自受控工程用例，不能推断真实文档的耗时缩短比例或召回率。
