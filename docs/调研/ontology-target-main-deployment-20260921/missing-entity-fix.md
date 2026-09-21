# 缺失标记实体候选的通用冻结修复

2026-09-21。下文记录部署前验证；随后主后端于 `05:42:11 UTC` 重建，已核对源码 hash、健康 200 与数据库版本，加载本次修改。完整部署记录见 [preflight.md](preflight.md)。

真实运行 `20bf2d25-98f3-4efe-99f0-1e5dbbae5d05` 第 5 组将 P80 独立原文 `N/A` 提为 Residue 实体。旧冻结门允许该候选进入模型核验，之后由模型拒绝。修复在实体指称原文的授权检查通过后，复用 `missing_field_value_kind` 拦截完整字段值或完整原文单元中的缺失/未知标记，不使用类 IRI 或领域词规则。

修改仅涉及 `claim_freeze.py` 与 `test_candidate_semantics.py`：mention 引文和 record 的 subject/value 组成命中时，记录 `entity_referent_missing` / `entity_referent_unknown`；新增缺失观察保留原文，`subject_id`、`predicate_iri` 均为空；原候选与组成不删除、不缩短，依赖属性继续通过既有 claim issue 传播隔离。辅助 field/context 组成不应用此指称检查。无事实权限或引文不存在时不生成该缺失观察；`NA`、`0`、`false`、`否`、`无` 和普通文本中的 `N/A` 子串不据此拒绝。

工程验证实际执行（工作目录 `backend/`）：

```text
.venv/bin/python -m pytest -p no:cacheprovider -q \
  tests/test_extraction/test_candidate_semantics.py \
  tests/test_extraction/test_tool_engine_freeze.py \
  tests/test_extraction/test_record_model_adapter.py
122 passed, 4 warnings in 8.60s

.venv/bin/ruff check \
  app/services/extraction/ontology_guided/claim_freeze.py \
  tests/test_extraction/test_candidate_semantics.py
All checks passed!
```

四项警告来自既有依赖弃用及 schema 命名。两文件的 `git diff --check` 通过。

真实制品离线复验见 [missing-entity-freeze-replay.json](missing-entity-freeze-replay.json)。使用该组已保存的原始模型回答与原文，另以数据库 `REPEATABLE READ, READ ONLY` 事务导出冻结协议、本体和 profile；根据持久投影配方重建 card/context。schema card ID、context hash、evidence hash、base target 全部与运行保存值严格相符。

- 原冻结输入进入核验的 target 为 `e1`；新冻结输入的 target 为空，`e1` 的 issue 为 `entity_referent_missing`。
- 新增一条无主体、无谓词的 missing observation，原文仍为同一 evidence ID 下的 `N/A`。
- 原始实体候选和模型原有的三条 observation 均未改写；后三者仍保留原始 `subject_id=e1` 作为未核验提示，不因保留而成为已证事实。
- 该真实回答没有属性或关系声明；依赖属性隔离由工程反例覆盖，不冒称实际回答存在这类声明。
- 新增模型调用和 embedding 调用均为 0，没有改写运行结果、启动或重启服务。此项证明真实制品经过新冻结门的行为，不替代部署后新运行的全流程质量实测。

最后生产源码 `backend/app/services/extraction/ontology_guided/claim_freeze.py`：mtime 为 `2026-09-21 05:30:29.409255810 UTC`；SHA-256 为 `ca2fd464e40a53f2be249670c76bf1566d79c5ca926e5b713dc7d9ec020ee25b`。
