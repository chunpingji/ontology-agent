# Data Model: Word 表格实体识别优化

**Date**: 2026-06-27 | **Feature**: 009-word-table-ner-optimize

本特性不引入新的持久化模型或数据库迁移。变更集中在 `annotate_word` 的内部数据结构
（运行时内存对象），以及 `_annotate_texts` / `_type_and_filter_spans` 的输入输出契约。

## 内部数据结构变更

### 1. 行级文本段（Row Segment）

**当前**：每个 cell 独立作为一个 segment 加入 `all_texts`。

**目标**：同行所有 cell 拼接为一个行级 segment。

```
# 当前 all_texts（表格部分）
["原料药名称", "剂型", "规格",          # ri=0 表头 cells
 "原料药名称：阿莫西林", "剂型：片剂", "规格：0.25g"]  # ri=1 data cells

# 目标 all_texts（表格部分）
# 表头行不加入 all_texts
["原料药名称：阿莫西林 | 剂型：片剂 | 规格：0.25g"]   # ri=1 行级 segment
```

### 2. Cell 偏移映射（Cell Offset Map）

行级 segment 中每个 cell 内容的位置映射，用于 NER 后将 span 坐标还原为 cell 内坐标。

```
cell_offsets: list[list[CellOffset]]
# 其中 CellOffset = (cell_start, cell_end, column_index)
# 示例："原料药名称：阿莫西林 | 剂型：片剂 | 规格：0.25g"
#        ^0                   ^22^25        ^34^37        ^43
# cell_offsets[row_seg_idx] = [(0, 22, 0), (25, 34, 1), (37, 43, 2)]
# 注：prefix_lens 记录每 cell 的 header 前缀长度（如"原料药名称："= 6）
```

### 3. 表头区域（Header Region）

**当前**：`ri == 0` 固定为表头。

**目标**：`header_row_count` 动态推断。

```
header_row_count: int  # 1 for single-row header, 2+ for multi-row
headers: list[list[str]]  # headers[row_idx][col_idx] = header text
# 多行表头时，数据行的前缀拼接使用最后一行表头的列名
```

### 4. 合并单元格标记

```
is_vmerge_continue: bool  # True = 续行 cell，跳过
# 检测方式：cell._tc.tcPr 的 <w:vMerge> 子元素
#   无 vMerge / val="restart" → 主 cell（正常处理）
#   val="continue" / 无 val → 续行 cell（跳过）
```

### 5. 嵌套表格

```
# 递归处理：cell 内发现 <w:tbl> 元素时，构造 Table 对象递归走相同逻辑
# 嵌套表格的 segments 追加到主 all_texts
# 嵌套表格在 tiptap 输出中作为 cell content 内的子 table 节点
max_nesting_depth: int = 5  # 防止异常文档栈溢出
```

## 不变量

- `_annotate_texts` 接口不变：`(texts, engine, ...) → (spans, triples, ckpt)`
- `_type_and_filter_spans` 接口不变：`(all_spans, segment_texts, engine) → typed_spans`
- `_span_with_context` 接口不变：`(seg_text, start, end, window) → str`
- 段落文本处理路径不受影响（零回归保障）
- 前端 `WordViewer` 接收的 tiptap JSON 结构不变（tableCell 内 span 坐标仍相对于 cell 文本）
- 前端 `ExcelViewer` 不受影响
