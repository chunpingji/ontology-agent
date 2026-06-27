# Phase 1 Data Model: 离线本地实体抽取（air-gap 默认 + prose 召回）

**Feature**: `008-gliner-ner-extraction` | **Date**: 2026-06-26 | **Plan**: [plan.md](./plan.md)

本特性**零新建表**：仅给既有 `extraction_configs` 加一列、给 `Settings` 加若干字段、定义两个**进程内数据结构**（NER schema、富化合并）与本地模型**文件制品**约定。不写权威 TTL、不触 Owlready2 World 写路径。

---

## 1. 持久化变更：`extraction_configs.ner_columns`（经 Alembic 迁移）

### 1.1 模型层 — `backend/app/models/extraction.py`

`ExtractionConfig` 新增一列（与既有 `column_mapping` 同为可空 JSON，承载源表头字符串列表）：

| 列 | 类型 | 可空 | 默认 | 语义 |
|----|------|------|------|------|
| `ner_columns` | `JSON`（`Mapped[list[str] \| None]`） | 是 | `NULL` | 声明哪些**源列表头**为「自由文本列」、需走本地 NER 富化（US3）。`NULL`/`[]` = 不富化任何列 |

> 与 `column_mapping`（表头→IRI）**职责正交**：`column_mapping` 决定结构化映射，`ner_columns` 决定富化白名单（research [R4](./research.md)）。

### 1.2 迁移 — `backend/app/alembic/versions/<rev>_add_ner_columns.py`

- **upgrade**：`op.add_column("extraction_configs", sa.Column("ner_columns", sa.JSON(), nullable=True))`
- **downgrade**：`op.drop_column("extraction_configs", "ner_columns")`
- **不变量**：现有行 `ner_columns` 取 `NULL`，行为**等同改造前**（不富化），保证零回归；`main.py:lifespan._run_migrations()` 启动即应用（宪章 质量门禁「DB 变更 MUST 经 Alembic」）。

### 1.3 Schema 层 — `backend/app/schemas/extraction.py`

- `ExtractionConfigCreate`：增 `ner_columns: list[str] | None = None`
- `ExtractionConfigResponse`：增 `ner_columns: list[str] | None = None`
- 校验：列表元素为非空字符串；元素**应**为该配置 `source_type` 文件中存在的源表头（弱校验，未命中列在解析期被忽略，不报错——容忍模板演进）。

---

## 2. 配置/设置增项 — `backend/app/config.py:Settings`

| 字段 | 类型 | 默认 | 语义 |
|------|------|------|------|
| `gliner_extraction_enabled` | `bool` | `True` | 本地 NER 总开关；`False` → `get_gliner_extractor()` 返回 `None`，prose/富化静默跳过 |
| `gliner_model_path` | `str` | `"models/gliner_multi-v2.1"` | 本地权重目录（air-gap 预置，research [R8](./research.md)） |
| `gliner_threshold` | `float` | `0.5` | `predict_entities` 阈值；运营旋钮（research [R10](./research.md)） |
| `llm_cloud_enabled` | `bool` | `False` | 云端 LLM opt-in 总开关；默认关（research [R2](./research.md)，FR-002） |
| `semantic_embedding_model` | `str` | **改为** `"models/bge-small-zh-v1.5"` | 既有对齐嵌入模型路径，air-gap 下改本地目录（research [R8](./research.md)） |

> `anthropic_api_key`（既有，默认 `""`）保留，仍经 env 注入、不入库。云端触发条件 = `llm_cloud_enabled and anthropic_api_key`。

---

## 3. 进程内数据结构

### 3.1 NER Schema（从本体类派生，research [R3](./research.md)）

`pipeline.py:_schema_from_class(target_class_iri)` 的产物，**只读** `OntologyEngine.get_class_detail`，不落库：

```
NerSchema = {
  "labels":      list[str],        # 供 GLiNER：每个 data_property 的 label（缺省回退 name）
  "label_to_iri": dict[str, str],  # label → 属性 IRI，用于把抽取结果回填到 IRI 键
}
```

- **来源字段**（已核验 `get_class_detail(iri).data_properties` 形态）：每项 `{"iri", "name", "label", "range"}`。
- **去重**：同 label 多属性时保留首个映射并记 WARNING（标签集应唯一）。
- **空集**：类无 `data_properties` → `labels=[]` → NER 跳过（无可抽标签），不报错。

### 3.2 富化合并记录（Excel，research [R5](./research.md)）

解析→富化的行级流转（**不新增持久实体**，过程态）：

| 阶段 | 行内键 | 说明 |
|------|--------|------|
| `parse_excel` 输出 | IRI 键（结构化列）+ `__freetext__: dict[源表头, 原文]`（仅 `ner_columns` 命中列） | 自由文本原文暂存，不直接作属性值 |
| `pipeline` 富化 | 对每个 `__freetext__` 值跑 `gliner.extract_text(text, labels, threshold)` | 产 `{label: value|[values]}`，经 `label_to_iri` 转 IRI 键 |
| `_merge_ner(row, ner_props)` | 写回 IRI 键 | **仅填充 row 中缺省/空的属性键**；已有结构化值保留（结构化权威，FR-008） |
| 收尾 | `row.pop("__freetext__")` | 清除暂存原文，候选不含临时键 |

**合并不变量**（见 [contracts/parser-and-enrichment.md](./contracts/parser-and-enrichment.md)）：
1. 结构化列已有非空值 → NER **不得**覆盖。
2. NER 仅写 row 中**不存在或为空**的属性键。
3. 富化**不产生新候选**——Excel 仍一行一候选。

### 3.3 Prose 候选映射（Word 正文，research [R7](./research.md)/[R9](./research.md)）

Word `paragraph` 段落经 NER 产出的 `ExtractionCandidate`（复用既有列，无新字段）：

| 候选字段 | 取值 | 说明 |
|----------|------|------|
| `candidate_kind` | `"instance"` | prose 实体作实例候选 |
| `source_ref` | `f"{doc_ref}#para"`（或带段索引） | 溯源回链（宪章 III，FR-005） |
| `properties` | `{IRI: value|[values]}` | `extract_text` 结果经 `label_to_iri` + `tag_controlled_vocab` |
| `group_key` | `_compute_group_key(...)`（复用） | 与既有去重/分组一致 |
| `review_status` | `"pending"` | **入复核队列，不自动断言**（宪章 II，FR-010） |
| 对齐 | `align_entity(...)`（复用） | 与结构化候选同一对齐/复核主流程 |

> 与既有 `parse_action_from_text`（Action 候选）**并存**：同一段落可同时产出 Action 候选与 prose instance 候选，互不替代。

---

## 4. 本地模型文件制品（不入库、不入 git，research [R8](./research.md)）

| 制品 | 路径 | 来源 | 校验 |
|------|------|------|------|
| GLiNER 多语权重 | `backend/models/gliner_multi-v2.1/` | 构建期 `huggingface-cli download urchade/gliner_multi-v2.1` | 交付清单登记 SHA256 |
| 嵌入模型权重 | `backend/models/bge-small-zh-v1.5/` | 构建期 `huggingface-cli download BAAI/bge-small-zh-v1.5` | 交付清单登记 SHA256 |

- 运行期 env：`HF_HUB_OFFLINE=1`、`TRANSFORMERS_OFFLINE=1`；加载 `local_files_only=True`（双保险，零外发）。
- `.gitignore` MUST 含 `backend/models/`（权重不进版本库，宪章 安全）。

---

## 5. 降级语义（`degraded_reason` 何时非空，research [R2](./research.md)）

> 完整不变量见 [contracts/offline-extraction-invariants.md](./contracts/offline-extraction-invariants.md)。

| 场景 | 云端开关 | NER 可用 | `degraded_reason` | 作业结果 |
|------|---------|---------|-------------------|----------|
| air-gap 正常（默认） | 关 | 是 | **None**（离线非降级） | 成功，结构化 + prose/富化 |
| NER 不可用（缺包/缺权重/加载失败） | 关 | 否 | **None** 或仅信息性说明（非 `degraded` 失败语义） | 成功，结构化兜底、prose/富化为空、记 WARNING |
| 云端开启且调用成功 | 开+有 Key | — | None | 成功，含云端补充 |
| 云端开启但调用失败/返回空 | 开+有 Key | — | **非空**（真实降级原因） | 成功（回退结构化），标 degraded |
| 云端开启但无 Key | 开+无 Key | — | 非空（配置缺失说明） | 成功（回退结构化） |

**核心**：离线（云端关）**永不**标 `degraded`（FR-003）；`degraded` 仅在**云端被显式开启却无法兑现**时落点。

---

## 6. 状态/边界一览

- **不触 T-Box**：本特性无 TTL 写入、无 `surgical_merge`；`get_class_detail` 只读。本体保真风险面为零（宪章 II）。
- **复核门禁零削弱**：所有 prose 候选 `review_status="pending"`，经既有复核/提交审计链入库（宪章 II/III）。
- **零回归**：`ner_columns=NULL`、`llm_cloud_enabled=False`、NER 不可用三态下，结构化主路径行为与改造前一致。
