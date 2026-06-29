# Quickstart Validation: Word 表格实体识别优化

**Date**: 2026-06-27 | **Feature**: 009-word-table-ner-optimize

## Prerequisites

- Python 3.11+, `uv` package manager
- Backend dependencies installed: `cd backend && uv sync`
- GLiNER model weights available at `backend/models/` (offline, SHA256 verified)
- SLPRA ontology TTL loaded

## Scenario 1: 行级上下文拼接 — 表格数据行实体召回

**目标**：验证表格数据行的实体通过行级上下文拼接后召回率提升。

### 步骤

1. 准备一份含规格表的 Word 文档（至少 5 行 4 列，含表头行）
2. 运行标注：
   ```bash
   cd backend
   uv run pytest tests/test_extraction/test_word_formatting.py -k "table" -v
   ```
3. 检查测试输出：
   - 数据行产生的 entity span 数量 > 0
   - 短文本 cell（2-3 字）也能产生 span
   - 表头行（第一行）不产生 entity span

### 预期结果

- 数据行每个含实体文本的 cell 至少产生一个标注 span
- 表头行的 span 列表为空
- span 坐标相对于 cell 文本（而非行级拼接文本）

## Scenario 2: 多行表头检测

**目标**：验证双行表头的两行均被跳过，不产生 NER 结果。

### 步骤

1. 创建一份含双行表头的 Word 文档：
   - 第一行：跨列合并的分类标题（如"药品信息"合并 2 列）
   - 第二行：具体列名（"名称"、"剂型"、"规格"、"批号"）
   - 第三行起：数据行
2. 运行测试：
   ```bash
   uv run pytest tests/test_extraction/test_word_formatting.py -k "multi_row_header" -v
   ```

### 预期结果

- 前两行均不产生 entity span
- 第三行起的数据行使用第二行列名作为语义前缀

## Scenario 3: vMerge 去重

**目标**：验证纵向合并单元格不产生重复标注。

### 步骤

1. 创建一份含纵向合并单元格的 Word 文档
2. 运行测试：
   ```bash
   uv run pytest tests/test_extraction/test_word_formatting.py -k "vmerge" -v
   ```

### 预期结果

- 合并单元格文本仅在首行产生标注，续行不重复
- 续行其他非合并 cell 正常处理

## Scenario 4: 嵌套表格递归

**目标**：验证 cell 内嵌套表格的内容被递归处理。

### 步骤

1. 创建一份含嵌套表格的 Word 文档（cell 内嵌入子表格）
2. 运行测试：
   ```bash
   uv run pytest tests/test_extraction/test_word_formatting.py -k "nested_table" -v
   ```

### 预期结果

- 嵌套表格中的文本产生实体标注
- 嵌套表格在 tiptap JSON 中作为 cell 内子 table 节点输出

## Scenario 5: 段落回归验证

**目标**：验证段落文本的标注结果不因表格优化产生回归。

### 步骤

```bash
uv run pytest tests/test_extraction/test_word_formatting.py -v
uv run pytest tests/test_extraction/test_document_annotator.py -v
```

### 预期结果

- 所有既有段落相关测试通过
- `test_annotate_word_preserves_structure` 通过
- `test_tables_interleaved_with_paragraphs` 通过

## Scenario 6: 文档更新验证

**目标**：验证技术方案文档的所有 GAP 项已修复。

### 步骤

1. 打开 `docs/Word-PDF文档实体识别优化技术方案.md`
2. 逐项核查：
   - B1：§4.2 不含"制剂剂型"作为种子标签示例
   - B2：无硬编码行号引用，改为函数名锚点
   - B3：`data_property_labels` docstring 说明"阶段三属性标签查询"
   - C1+C2：§11 记录标注任务控制（暂停/恢复/重运行 + SSE 进度）
   - C3：§4 末尾记录 NER 三元组入复核队列机制
   - C5：§6.3 列出完整三档字号阈值（≥20pt→h1、≥16pt→h2、≥14pt→h3）

### 预期结果

- GAP 分析文档中 B1-B3、C1-C5 所有项的偏差均已消除
