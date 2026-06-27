# 验收结果: 008-gliner-ner-extraction（T028）

**执行日期**: 2026-06-26 | **Plan**: [plan.md](./plan.md) | **Quickstart**: [quickstart.md](./quickstart.md)

[quickstart.md](./quickstart.md) 场景 1–6 的**行为正确性**经自动化测试套件确定性验收（契约/集成
测试用确定性桩替换真实权重,不下载模型——[gliner-extractor.md](./contracts/gliner-extractor.md)
「测试桩约定」）。SC-004 / SC-005 的**目标数值**（prose 召回率 / 富化命中率）依赖真实中文样本
标定（research [R10](./research.md)）,本轮留占位,标定阶段单独验收。

## 套件结果

- 全后端套件: **334 passed**, 0 failed（`uv run pytest -q`）。
- 008 直接相关测试文件: **42 passed**。
- Alembic 链单 head: `0005_add_ner_columns`（`uv run alembic heads`）。

## 场景 → 测试 → SC 矩阵

| 场景 | US | SC | 验收测试（自动化） | 结果 |
|------|----|----|--------------------|------|
| 1 离线结构化抽取 | US1 | SC-001/003 | `test_offline_invariants.py`、`test_offline_pipeline.py`（黄金基线逐字一致 + 全程 `degraded=False` + anthropic 哨兵模块拦截零外发）、`test_parse_word_mapping.py`（表头→IRI 确定性映射,未调 LLM） | ✅ PASS |
| 2 Word 正文 prose | US2 | SC-004（行为） | `test_word_prose.py`（`#para` 溯源 + `pending` + 对齐结果 + Action 并存 + 多值聚合）、`test_schema_derivation.py`（`label_to_iri` 派生） | ✅ PASS |
| 3 Excel 自由文本富化 | US3 | SC-005（行为） | `test_excel_enrichment.py`（P5–P9/P11）、`test_excel_enrichment_pipeline.py`（空缺补齐 + 结构化权威不覆盖 + 候选数=行数 + 无 `__freetext__`） | ✅ PASS |
| 4 NER 不可用优雅降级 | — | SC-006 | `test_ner_degradation.py`（O6 作业不失败 / O7 结构化零回归·prose 空·Excel 不富化 / O8 仅 WARNING 不 degraded / P12 暂存仍清除） | ✅ PASS |
| 5 云端 LLM opt-in | US1 | SC-002 | `test_offline_invariants.py`（四象限门控:默认零云端调用;开启缺 Key → 回退且 `degraded_reason` 非空;开启有 Key → 触发;调用失败/空 → 降级） | ✅ PASS |
| 6 启动预热 | — | SC-007 | `main.py:lifespan` → `_warmup_local_models()`(关闭/缺包零开销、缺权重静默降级、异常不阻断启动);功能关闭路径随全套件启动覆盖 | ✅ PASS（行为）|

## 成功标准（SC）小结

| SC | 判据 | 状态 |
|----|------|------|
| SC-001 | air-gap 零联网完成结构化抽取 | ✅ 自动化验证 |
| SC-002 | 云端默认关、双条件门控（`llm_cloud_enabled AND anthropic_api_key`）才触发 | ✅ 自动化验证 |
| SC-003 | 离线非降级:`degraded` 仅在云端显式开启却不可兑现时为真 | ✅ 自动化验证 |
| SC-004 | Word 正文 prose 实体召回为 `pending` 候选,带 `#para` 溯源 | ✅ 行为验证;**召回率数值待真实中文样本标定（R10）** |
| SC-005 | Excel 自由文本回填本行:仅补空缺、结构化权威、不另生候选 | ✅ 行为验证;**命中率数值待真实中文样本标定（R10）** |
| SC-006 | NER 不可用作业不失败、结构化零回归、仅 WARNING | ✅ 自动化验证 |
| SC-007 | 启动期预热消除首作业冷启动 | ✅ 行为验证（air-gap 真机权重加载耗时在部署验收量取）|

## 真机待办（部署验收阶段）

- [ ] 预置真实权重后,跑 quickstart 场景 1–6 的「真实运行」分支,量取场景 6 首作业延迟 vs 后续作业（SC-007 数值）。
- [ ] 以真实中文 SOP / 台账样本标定 `gliner_threshold`,回填 SC-004 召回率 / SC-005 富化命中率目标（research [R10](./research.md)）。
- [ ] 断网环境复核零外发（HF/anthropic 出网全 mock 为异常,印证 `local_files_only=True` + `HF_HUB_OFFLINE`/`TRANSFORMERS_OFFLINE`）。
