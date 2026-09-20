# 032 验证步骤

在 `backend/` 使用已有环境执行以下定向集合；不启动服务或真实模型：

```bash
.venv/bin/python -m pytest -p no:cacheprovider -q \
  tests/test_extraction/test_attribute_value_observation.py \
  tests/test_extraction/test_attribute_calibration.py \
  tests/test_extraction/test_attribute_calibration_guards.py \
  tests/test_extraction/test_attribute_calibration_projection.py \
  tests/test_extraction/test_tool_metric.py \
  tests/test_extraction/test_tool_shacl.py \
  tests/test_extraction/test_tool_engine_freeze.py \
  tests/test_extraction/test_tool_engine_tool_contracts.py \
  tests/test_extraction/test_tool_runtime.py \
  tests/test_extraction/test_tool_engine_quantity.py \
  tests/test_extraction/test_record_model_adapter.py \
  tests/test_extraction/test_tool_engine_contracts.py \
  tests/test_extraction/test_tool_runtime_recall.py \
  tests/test_extraction/test_tool_engine_adapter.py \
  tests/test_extraction/test_contextual_record_executor.py \
  tests/test_extraction/test_record_executor.py \
  tests/test_extraction/test_record_reference_pipeline.py \
  tests/test_extraction/test_semantic_record_discovery.py \
  tests/test_extraction/test_contextual_reading_executor.py \
  tests/test_extraction/test_record_focus_scope.py \
  tests/test_extraction/test_record_page_semantics.py \
  tests/test_extraction/test_tool_engine_configuration.py \
  tests/test_extraction/test_contextual_current_state.py \
  tests/test_extraction/test_record_discovery_current_state.py \
  tests/test_extraction/test_contextual_record_context.py \
  tests/test_extraction/test_contextual_record_adapter.py \
  tests/test_extraction/test_attribute_disambiguation.py \
  tests/test_extraction/test_record_reference_dependencies.py \
  tests/test_extraction/test_ontology_guided_boundaries.py \
  tests/test_api/test_document_analysis.py \
  tests/test_api/test_document_analysis_schemas.py \
  tests/test_api/test_record_property_reviews.py
```

## 前端检查

在 `frontend/` 执行：

```bash
./node_modules/.bin/tsc --noEmit --incremental false
npm run lint -- src/lib/api.ts src/components/analysis/attribute-calibration-list.tsx src/components/analysis/document-relationship-graph.tsx src/components/analysis/template-document-graph-panel.tsx
node --test tests/attribute-calibration.test.mjs
```

## 验收场景

1. “时间：2026年02月”可解析年月，业务属性尚未证明时显示待校准；引用边界可唯一修正，语义不自动通过。
2. 编号主体缺失时仍显示原值和原文；`00123` 在字符串卡下保持前导零。候选-only 图可经 API 读取，但其他用户不能读取运行或 source refs，GET 不调用模型。
3. 初次属性核验未决，关系补全后暂停，再冷继续：复用已付费结果，收尾校准通过后清理候选并生成正式属性；再次继续不重复调用。
4. 未证明或无关关系变化不触发校准；无变化不重试。错误字段引用、非法谓词、冻结授权失败均不能成为合法候选映射。
5. 技术失败或模型漏报保留旧候选；同值但不同来源、不同普通属性不被误清理；唯一物理字段主体纠正后可清理。
6. 前端展示区间开闭界限、比较符及“待校准，不用于推理/报告”，有字段/原值按钮。没有正式实体或属性时仍显示候选。

## 本次实际结果（2026-09-19）

- 上述后端定向集合：**551 passed, 4 skipped, 4 warnings**，耗时 113.87 秒。
- 4 项跳过因未配置 `DOCUMENT_ANALYSIS_TEST_DATABASE_URL` 专用可销毁 PostgreSQL 测试库；未完成 PostgreSQL 锁/并发验收。4 项 warning 来自既有 FastAPI/Starlette、Pydantic 使用。
- 前端 TypeScript、定向 ESLint、候选展示 Node 测试均通过；Node runner 计 1 个测试文件通过，文件内包含 4 个用例。
- 本次涉及后端文件 Ruff 检查通过，`git diff --check` 通过。

本节记录工程交付时的检查；当时未执行服务部署。后续用户授权的部署和普通文档入口补齐检查见 [deployment.md](deployment.md)。真实文档模型评测、完整后端测试和前端生产构建未执行；上述结果不代表真实模型的识别质量或耗时验收。
