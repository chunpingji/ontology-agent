# Contract: 内容层抽取编排（文档内部实体 → 候选 → 复核）

**Feature**: `007-rnd-document-fact-source` | **Covers**: FR-003/004/007、US2、SC-002/003 | **Refs**: research R3/R5, data-model §2.4/§4/§5.2/§5.3

> 本契约定义文档**内部业务实体**经能力二抽取的编排：事件 → 入待抽取队列（手动发起，Q1）→ `doc_repo` 抽取分支 → 复核门禁 → 确认入库注入 `extractedFrom`。核心红线：**复核门禁零削弱**（SC-003 = 0% 自动入库）与 **100% 可溯源**（SC-002）。

---

## C1. 抽取触发编排（FR-007 / Q1 手动发起）

| # | 断言 | 测试意图 |
|---|---|---|
| C1.1 | 文档 `approved`/新版本事件触发**编排入待抽取队列**：创建 `ExtractionJob(source_type='doc_repo', status='pending', source_config={doc_ref,content_ref})` | FR-007、US2 AS#4 |
| C1.2 | 入队**不自动发起**抽取管线；由授权角色经手动发起端点启动 `run_extraction_pipeline` | Q1、US2 Independent Test |
| C1.3 | 新版本文档入队时与旧版本溯源可区分（`doc_ref` 含版本指针） | US2 AS#4 |

---

## C2. `doc_repo` 抽取分支（`services/extraction/pipeline.py`）

`run_extraction_pipeline` 增 `config.source_type == 'doc_repo'` 分支（与现 `database`/`excel`/`word` 并列，`pipeline.py:57`）：

```python
source_ref = job.source_config["doc_ref"]   # = 文档个体 IRI（非 source_filename）
# 按 content_ref 按需取正文（Q2：不存全文）→ LLM 抽取 → 对齐 → 候选入库（review_status='pending'）
```

| # | 断言 | 测试意图 |
|---|---|---|
| C2.1 | doc_repo 分支产出的每个 `ExtractionCandidate.source_ref == <文档个体 IRI>` | R3 溯源来源 |
| C2.2 | 候选一律 `review_status='pending'`，**不自动断言**为权威事实（即使来源文档可信） | FR-003、Edge「抽取不确定」 |
| C2.3 | 抽取产出复用既有对齐/归组/降级（`align_entity`/`group_key`/`degraded_reason`）—— doc_repo 不另起对齐栈 | 宪章 V 复用 |
| C2.4 | 按需取正文经 `content_ref` 外部引用；平台不持久化全文 | Q2 |

---

## C3. 复核门禁（FR-003，SC-003 = 0%）

复用既有 `review_candidate`（`extraction.py:203`）+ `_commit_candidate`（`extraction.py:191`）：

| # | 断言 | 测试意图 |
|---|---|---|
| C3.1 | **唯一入库路径** = `status='confirmed'`（或 `edited`）→ `_commit_candidate`；`rejected` 不入库 | SC-003、US2 AS#2/AS#3 |
| C3.2 | 入库经 `require_role(senior_analyst)`；非授权角色不可确认 | 宪章 安全 |
| C3.3 | 拒绝决定可追溯（`extraction.candidate.*` 审计） | US2 AS#3 |
| C3.4 | 来源文档可信 **不**绕过 C3.1（无「doc_repo 直接入库」捷径） | FR-003 红线 |

---

## C4. `extractedFrom` 溯源回链注入（FR-004，SC-002）

`_commit_candidate` 确认入库时（data-model §4）：

| # | 断言 | 测试意图 |
|---|---|---|
| C4.1 | 提交个体 `extracted_properties["extractedFrom"] == candidate.source_ref`（文档个体 IRI） | FR-004 |
| C4.2 | 100% 经 doc_repo 抽取确认入库的实体均携 `extractedFrom`（可一键溯源回文档+版本） | SC-002 |
| C4.3 | 默认实体继承文档阶段：`hasDevelopmentPhase` 缺省取文档阶段（阶段冲突消解，Edge Case） | data-model §4 |
| C4.4 | 注入逻辑仅作用于 doc_repo 来源候选（`source_ref` 为 `facts#` 文档 IRI）；非文档候选 `_commit_candidate` 行为不变 | 既有抽取零回归 |

---

## C5. 与受影响子图重算的衔接（FR-007 下半句）

| # | 断言 | 测试意图 |
|---|---|---|
| C5.1 | 文档/派生实体事实变更经 `resolve_affected_subgraph` 解出 `document`（及关联 `sample`/`product`）维 | data-model §6 |
| C5.2 | 重算不改 `AssessmentResult` 对外形状 | FR-012/SC-007 |
