# Contract: 解析与富化（parse_excel / parse_word / _merge_ner）

**Feature**: `008-gliner-ner-extraction` | **Modules**: `parser.py` / `pipeline.py`

界定结构化解析（Word 表头确定性映射）、Excel 自由文本暂存、富化合并三组契约（US1/US3 / FR-004/007/008/009，research [R5](../research.md)/[R6](../research.md)）。

---

## 1. `parse_word(file_path, column_mapping: dict | None = None)`（FR-004，research R6）

新增 `column_mapping` 参数，与 `parse_excel` 对齐：

```
表格行单元格键 =
    column_mapping[表头文本]   若表头命中映射
    表头文本（原样）           否则（容忍未映射列）
段落            = {"type": "paragraph", "content": text, "style": ...}   # 形态不变
```

| # | 不变量 | 验证方式 |
|---|--------|----------|
| P1 | **确定性映射**：表头命中 `column_mapping` → 行以 **IRI** 为键；未命中 → 原表头键保留 | 构造含已映射/未映射列的 docx，断言键集 |
| P2 | **零云端**：Word 表格表头→IRI **不再调用任何 LLM** | mock anthropic，断言 0 次调用 |
| P3 | **向后兼容**：`column_mapping=None` → 行为与改造前一致（原表头为键） | 既有 Word 测试零回归 |
| P4 | 段落解析形态不变（仍产 `{"type":"paragraph","content",...}`） | 既有段落断言不变 |

## 2. `parse_excel(file_path, column_mapping, ..., ner_columns: list[str] | None = None)`（FR-007，research R5）

新增 `ner_columns` 参数，命中列原文暂存：

```
对每行：
  结构化列 → 经 column_mapping 落 IRI 键（既有行为不变）
  若该列源表头 ∈ ner_columns → 原文写入 row["__freetext__"][源表头]（不直接作属性值）
ner_columns 为 None/[] → 不产生 __freetext__ 键（行为等同改造前）
```

| # | 不变量 | 验证方式 |
|---|--------|----------|
| P5 | **白名单暂存**：仅 `ner_columns` 命中列原文进入 `__freetext__`，非命中列不进 | 断言 `__freetext__` 键集 = `ner_columns ∩ 实际表头` |
| P6 | **不污染属性**：自由文本原文**不**直接作为属性值落 IRI 键 | 断言 IRI 键不含原文（除非结构化映射另有值） |
| P7 | **向后兼容**：`ner_columns=None` → 无 `__freetext__` 键、行为与改造前一致 | 既有 Excel 测试零回归 |

## 3. `_merge_ner(row, ner_props)`（FR-008/009，research R5）

pipeline 在抽取前对每行 `__freetext__` 跑 `gliner.extract_text` → `ner_props`（IRI 键），并回本行：

```
对 ner_props 中每个 (iri, value)：
    若 row 中 iri 缺省或为空 → row[iri] = value      # 仅补空缺
    否则                     → 保留 row[iri]（结构化权威）
合并后：row.pop("__freetext__", None)
```

| # | 不变量 | 验证方式 |
|---|--------|----------|
| P8 | **结构化权威**：结构化列已有非空值 → NER **不得**覆盖 | 行预置某 IRI 有值 + NER 产同 IRI 值，断言保留原值 |
| P9 | **仅补空缺**：NER 仅写 row 中不存在/为空的 IRI 键 | 断言新增键 ⊆ 原空缺键集 |
| P10 | **不另生候选**：Excel 富化后仍**一行一候选**，富化不新增候选 | 断言候选数 = 行数（与改造前一致） |
| P11 | **清除暂存**：合并后候选 `properties` 不含 `__freetext__` 临时键 | 断言候选无 `__freetext__` |
| P12 | NER 不可用时（[offline-extraction-invariants O6](./offline-extraction-invariants.md)）跳过富化，`__freetext__` 仍被清除、结构化候选不变 | 注入不可用桩，断言零回归 + 无残留临时键 |

---

**关联**：[ner-schema-derivation.md](./ner-schema-derivation.md)（`ner_props` 的 IRI 键来源）、[gliner-extractor.md](./gliner-extractor.md)（`extract_text` 形态）、[data-model.md §3.2](../data-model.md)（合并记录流转）。
