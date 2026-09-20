# 表前说明与数据行共同阅读优化

日期：2026-09-19。状态：本文保留首行共同阅读版本的设计及历史验证记录。

同日后续[文档识别调用效率修正](recognition-efficiency.md)已将默认策略更新为 `bounded-table-rows-v2`：同表、同表头的连续数据行每批最多 4 行，说明段仍仅在首批拥有独立事实资格。下文的“仅首条数据行”规则和 `lead-in-with-first-row-v1` 是历史实现，不再代表当前默认策略；历史测试结果不作为此次多行实现的验证结果。最新工程验证及部署状态见 [quickstart.md](quickstart.md)。

## 问题与目标

运行 `02bf5d6a-bf12-4324-a02b-c0dcea8ab9d0` 中，原文“残留物溶解度结果如下表……”作为独立记录进入实体发现；工具只处理该句并返回 `no_match`，整个记录任务消耗 2 次主模型调用后得到 `record_no_claims`。具体名称、溶剂、温度和溶解度实际在随后的数据行中。

用户要求优化此问题。本次修正 031 的阅读单位与检索上下文：避免表前说明独立消耗发现任务，优先阅读具有实际字段值的数据行，同时保留引导段自身可能包含的名称、条件、否定及其他陈述。

## 实施边界

1. 从冻结 Word 结构建立明确“下表”指向或相邻编号表题与表格之间的阅读联系。只匹配同节、直接相邻的顶层表格，可跨空段和一个编号表题；不跨其他正文、标题、导航、其他表格或嵌套表格。
2. 表格须有至少两个非空物理单元格的数据行。没有有效数据行时不吸收说明段。说明段最多 240 字；共同阅读仍受既有最多 8 条记录和 1200 字限制。
3. 引导段、表题与首条数据行组成一个发现阅读组，**主记录是数据行**。三者的物理记录、原文锚点和原有事实权限分别保留，不依据邻近关系合并实体或赋予属性。
4. 后续数据行的检索与识别上下文包含该说明，但说明不获得这些行的新事实权限。其他行的单元格也不进入当前行的事实来源。
5. 共同阅读超出容量时，各记录继续独立调度，不丢弃、不伪称已检查。保留独立字段消歧任务和既有调用预算；暂停继续复用已付费响应。
6. 新运行默认冻结 `record_discovery.table_reading=lead-in-with-first-row-v1`。没有新增表、外部依赖或模型调用阶段；未改写原运行及制品，也未改变本体与事实准入标准。

复用 `RecordIndex`、既有阅读组映射、语义检索及上下文授权机制。没有新增历史快照或第二套权威状态。该联系只改变阅读方式，不构成主体归属、共指、关系或事实成立的证明。

## 实现位置

- `backend/app/services/extraction/ontology_guided/table_reading.py`：结构联系、首行共同阅读及原文上下文引用。
- 同目录 `records.py`：缓存当前冻结文档的表前联系；`record_search.py`：数据行检索包含说明；`context.py`：保留逐来源事实权限。
- 同目录 `executor.py`：合并发现阅读组；`record_discovery.py`：冻结默认策略；`record_model_adapter.py`：提示优先定位数据行实体，不从引导句的空结果推断整组无实体。

## 实际验证

在 `backend/` 执行第一组：

```bash
.venv/bin/python -m pytest -p no:cacheprovider -q \
  tests/test_extraction/test_table_reading.py \
  tests/test_extraction/test_contextual_record_context.py \
  tests/test_extraction/test_tool_engine_configuration.py \
  tests/test_extraction/test_semantic_record_discovery.py \
  tests/test_extraction/test_reading_groups.py \
  tests/test_extraction/test_contextual_reading_executor.py
```

结果：87 passed。随后补充两行实体识别场景并复跑 `test_table_reading.py`，该文件最终 **21 passed**，另五个文件共 67 项通过；不重复累计该文件的旧结果。

第二组：

```bash
.venv/bin/python -m pytest -p no:cacheprovider -q \
  tests/test_extraction/test_record_discovery_contracts.py \
  tests/test_extraction/test_record_discovery_current_state.py \
  tests/test_extraction/test_record_executor.py \
  tests/test_extraction/test_record_model_adapter.py \
  tests/test_extraction/test_contextual_record_adapter.py \
  tests/test_extraction/test_contextual_record_executor.py \
  tests/test_extraction/test_record_focus_scope.py \
  tests/test_extraction/test_record_page_semantics.py \
  tests/test_extraction/test_record_reference_pipeline.py \
  tests/test_extraction/test_attribute_disambiguation.py \
  tests/test_extraction/test_attribute_calibration.py \
  tests/test_extraction/test_attribute_calibration_guards.py \
  tests/test_extraction/test_ontology_guided_boundaries.py \
  tests/test_extraction/test_docx_captions.py
```

结果：159 passed、4 skipped。跳过项因未配置专用可销毁 PostgreSQL 测试库；不代表 PostgreSQL 锁与并发验收通过。

合计 **247 个独立用例通过、4 项跳过**。每组另有 4 项既有 FastAPI/Starlette 与 Pydantic warning。涉及文件 Ruff（`--no-cache`）及 `git diff --check` 通过。

新增用例验证：无表、空表、单格表、嵌套表、跨节、间隔正文、超长说明与容量限制保留原记录；首次阅读保留附加名称、条件及否定；其他行不借用说明或别行的事实权限；两条数据行仍能产生不同实体；首次回答后暂停、冷继续及再次继续均不重复已付费调用。

受控调度样例中，引导段、表题和两条数据行由 4 个独立发现调用减为 2 个。另对上述问题运行的冻结文档进行**只读结构复核**：153 条物理记录保持不变，形成 4 个表格阅读组，减少 5 个单独阅读组；目标组以 `1234-3｜中性｜THF｜25℃±2℃｜易溶` 为主记录，完整携带引导句和“表3溶解度信息”。此复核未调用识别模型，不表示真实耗时或召回率已完成验收。
