# 030 验收与实际结果

状态：2026-09-19 实施及工程验收完成；随后按用户授权重新部署并核验四项生效。实现见 [implementation.md](implementation.md)，在线核验见 [deployment.md](deployment.md)。

## 本轮实际结果

- 后端综合回归 **342 passed、4 skipped**，耗时 100.92 秒；跳过的是未向该进程传入专用测试库配置的 PostgreSQL 用例。
- 通过项目专用 PostgreSQL 脚本另行执行同四项用例：**4 passed、37 deselected**。脚本确认数据库为 `ontology_document_analysis_test`、非管理员角色；没有使用业务库。
- Harness 前端 SSR **13 passed**；TypeScript、定向 ESLint、后端受影响文件 Ruff 和 `git diff --check` 通过。
- 初次综合回归及摘要单测在沙箱内卡于既有取消用例的 asyncio 线程关闭，已中止；相同模拟传输的摘要组在沙箱外 **13 passed**，随后完整综合回归也在沙箱外通过，未修改或跳过该断言。
- 使用受控模型和真实协调器验证：两个明确续文章节、三张普通类卡，从 6 个发现任务变为 3 个，保留两个原记录的证据权限；冷继续没有新请求。
- 字段跨类卡仅一项；合并单元格跨逻辑行仍仅一项且各行均延后同一字段；缺口零调用、相关候选唤醒、两轮上限、无关变化不唤醒和已付响应复用均通过。
- 普通 properties 与 identifier_claims 均不能使用待消歧原值绕过字段门禁；拒绝与未决核验结果保持原语义，独立实体和其他属性不受无关声明失败影响。
- 本体隔离验证：实际三元组 +12/-0，原有 3470 条保留；真实 World、元数据及 CMCReport 卡一致（6 个既有属性 + 2 个新增属性），重复播种不覆盖草稿，其他法规文档不继承两属性。

手工测试发现旧调度先连续执行字段消歧，前 12 项只有根节点。修复后的 `contextual-discovery-v2` 在字段去重后按阅读组先做普通实体发现，再做字段消歧；冻结的 v1 仍按原游标恢复。新增及相关回归本次实际为 **124 passed**，受影响文件 Ruff 通过。

本轮没有调用真实模型做质量评测，上述任务数变化不等于真实 tokens、耗时或准确率收益。T32 不因本轮工程测试通过而标为完成。

## 复现命令

后端目录，最终综合回归范围如下（文件通配与本次显式文件清单等价）：

```bash
.venv/bin/python -m pytest -p no:cacheprovider -q -o faulthandler_timeout=30 \
  tests/test_extraction/test_contextual*.py \
  tests/test_extraction/test_attribute_disambiguation.py \
  tests/test_extraction/test_reading_groups.py \
  tests/test_extraction/test_toc_analysis_filtering.py \
  tests/test_extraction/test_record_executor.py \
  tests/test_extraction/test_record_model_adapter.py \
  tests/test_extraction/test_record_discovery_contracts.py \
  tests/test_extraction/test_record_discovery_current_state.py \
  tests/test_extraction/test_tool_engine_configuration.py \
  tests/test_extraction/test_tool_engine_adapter.py \
  tests/test_extraction/test_docx_toc.py \
  tests/test_extraction/test_docx_structure.py \
  tests/test_extraction/test_document_ir.py \
  tests/test_extraction/test_word_tree_summarizer.py \
  tests/test_extraction/test_ontology_snapshot_ordering.py \
  tests/test_extraction/test_ontology_guided_boundaries.py \
  tests/test_extraction/test_model_context_projection.py \
  tests/test_cmc_report_metadata.py tests/test_ontology_engine_startup.py \
  tests/test_reasoning/test_ttl_roundtrip.py tests/test_api/test_document_analysis.py

.venv/bin/python scripts/test_document_analysis_postgresql.py \
  tests/test_extraction/test_record_discovery_current_state.py -k postgresql
```

前端目录：`node --test tests/document-harness.test.mjs`、`./node_modules/.bin/tsc --noEmit`、`npm run lint -- src/lib/api.ts src/components/analysis/document-harness.tsx`。

## 部署后的人工验收

新普通运行在调度修复部署后默认冻结 `contextual-discovery-v2`；旧运行保留原冻结策略、IR、本体快照和已付响应。后续部署已从权威 TTL 重建在线 World，并补入两个 published 属性；未发布工作台其他草稿，原有草稿和八个分析运行摘要保持一致。

人工验收场景：带目录的 CMC 报告；分开的字段标题和值；明确连续短节；相邻不同设备/批次的相同编号；仅标签缺值；两主体均合理的歧义字段；后到主体及暂停继续。查看目录是否零识别/摘要调用、分组是否保留来源、编号/密级是否出现在 CMCReport 卡中、同字段是否无新证据不重复调用、未决是否可见。
