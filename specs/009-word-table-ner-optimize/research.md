# Research: Word 表格实体识别优化与文档-代码对齐

**Date**: 2026-06-27 | **Feature**: 009-word-table-ner-optimize

## R1: 行级上下文拼接策略 — 如何拼接同行单元格

### Decision

同行所有单元格以 `f"{hdr}：{cell_text}"` 格式拼接为一个行级文本段，列间以 ` | ` 分隔，
作为一个完整 segment 送入 `_extract_spans_batch`。表前紧邻标题段（如果存在）追加为行级
文本的前缀。

### Rationale

- 当前 `annotate_word` L462-478 逐 cell 送 GLiNER，短文本（2-3 字）导致 Transformer
  注意力不足、置信度低于阈值被丢弃（GAP E1 主因）。
- 行级拼接使 GLiNER 一次看到完整行语义（"原料药名称：阿莫西林 | 剂型：片剂 | 规格：0.25g"），
  注意力窗口覆盖所有列，短文本列也能从邻列获得上下文提升。
- ` | ` 分隔符保持列边界可见，便于 span 偏移校正回原始 cell 坐标。
- 表前标题段（"表 3：原料药规格"）作为 prompt 前缀追加到每行，与 GAP E1+E2 修复方向一致。

### Alternatives Considered

| 方案 | 优点 | 缺点 | 结论 |
|------|------|------|------|
| 逐 cell（现状） | 实现简单 | 短文本召回系统性偏低（GAP E1） | 拒绝 |
| 整表拼接（所有行一个 segment） | 上下文最充分 | 超过 GLiNER 输入长度限制；span 偏移校正复杂 | 拒绝 |
| 行级拼接 + 列分隔符 | 平衡上下文与长度；偏移可追溯 | 宽表（15+ 列）可能较长 | **采用** |

## R2: 多行表头检测 — 如何推断表头区域行数

### Decision

利用 Word XML 中的合并单元格标记推断多行表头：若第一行含有水平合并单元格
（`<w:gridSpan>` 值 > 1），则判定为多行表头，继续扫描后续行直至遇到无合并特征的行。
所有表头行仅作语义前缀材料，不参与 NER。

### Rationale

- 制药文档中双行表头常见模式：第一行为跨列分类标题（如"药品信息"/"生产信息"），
  第二行为具体列名（"名称"/"剂型"/"批号"）。
- python-docx 的 `cell._tc.tcPr` 可获取 `<w:gridSpan>` 和 `<w:vMerge>` 属性，
  无需额外依赖。
- 退出条件：当某行的所有单元格均无水平合并特征时，判定为首个数据行。

### Alternatives Considered

| 方案 | 优点 | 缺点 | 结论 |
|------|------|------|------|
| 固定跳过第一行（现有） | 最简单 | 双行表头第二行产生噪声 | 拒绝 |
| 基于合并单元格特征推断 | 准确度高，零额外依赖 | 需要访问 XML 属性 | **采用** |
| 启发式字体/加粗检测 | 不依赖合并 | 误判风险高（数据行也可能加粗） | 拒绝 |

## R3: vMerge 续行去重 — 如何检测纵向合并

### Decision

在遍历每行单元格时，检查 `<w:vMerge>` 属性。`w:val="restart"` 或无 `w:vMerge`
标记的单元格为主单元格（正常处理）；`w:val="continue"` 或 `<w:vMerge/>` 无 val
属性的单元格为续行（跳过，其文本已在主单元格中处理）。

### Rationale

- 当前 `annotate_word` 遍历 `table.rows` 不检测续行，导致合并单元格文本被重复 NER（GAP E5）。
- python-docx `cell._tc` 的 `<w:tcPr>` 子元素 `<w:vMerge>` 直接可读。
- `cell.text` 在续行中返回与主行相同的文本（python-docx 行为），必须跳过避免重复。

## R4: 嵌套表格递归 — 如何处理 cell 内嵌套 `<w:tbl>`

### Decision

在处理每个单元格时，递归检查 cell 内是否存在 `<w:tbl>` 子元素。若存在，对嵌套表格
执行与主表格相同的处理逻辑（收集表头、行级拼接、NER），嵌套表格的结果追加到主表格
的 all_texts 和 elements 中。

### Rationale

- 当前 `annotate_word` 仅通过 `doc.tables` 获取顶层表格，嵌套表格完全丢失（GAP E5）。
- python-docx 的 `cell._tc.findall(qn('w:tbl'))` 可发现嵌套表格 XML 元素，
  再通过 `docx.table.Table(tbl_elem, doc)` 构造 Table 对象。
- 递归深度在实际制药文档中不超过 2 层（spec assumption），实现时加递归深度上限（如 5）
  防止异常文档导致栈溢出。

## R5: 阶段二上下文窗口适配 — `_span_with_context` 改用行级文本

### Decision

`_type_and_filter_spans` 调用 `_span_with_context` 时的 `seg_text` 参数改为行级拼接
文本（而非原始 cell 文本），使 window=40 能从同行其他列获取上下文。

### Rationale

- 当前 `_span_with_context` 的 `seg_text` 为短小的单元格文本，window=40 几乎取不到
  有效上下文，嵌入归类失去语义锚点（GAP E3）。
- 行级拼接后 seg_text 为完整行文本（"原料药名称：阿莫西林 | 剂型：片剂 | ..."），
  前后窗口能覆盖相邻列信息。
- 无需修改 `_span_with_context` 函数本身，只需确保传入的 `segment_texts` 与
  `_extract_spans_batch` 使用的 texts 一致（均为行级拼接文本）。

## R6: span 偏移校正 — 行级拼接后如何映射回单元格坐标

### Decision

行级拼接文本中记录每个 cell 内容的起始偏移量（累积分隔符和前缀长度）。NER 后，
将 span 的 `[start, end)` 映射回原始 cell 索引和 cell 内坐标，用于前端 tiptap
表格渲染。

### Rationale

- 前端 `word-viewer.tsx` 使用 tiptap table 结构渲染，每个 tableCell 内的 span
  坐标必须相对于 cell 文本，不能是行级拼接文本的全局坐标。
- 映射方式：维护 `cell_offsets: list[tuple[int, int, int]]`（cell 起始、cell 结束、
  cell 在行内的序号），NER 后遍历 span 判断归属 cell 并减去 cell 起始偏移量。

## R7: 文档更新范围

### Decision

文档更新严格限制在 GAP 分析识别的具体偏差项，不涉及整体重写：

| GAP 项 | 动作 |
|--------|------|
| B1 — "制剂剂型"误导示例 | 更新 §4.2 示例 + 加注 |
| B2 — 行号漂移 | 硬编码行号替换为函数名锚点 |
| B3 — `data_property_labels` docstring | 更新 docstring "第三源" → "阶段三属性标签查询" |
| C1 — 暂停/恢复/重运行 | 新增 §11 章节 |
| C2 — 实时子阶段进度 SSE | 合并入 §11 |
| C3 — NER 三元组入复核队列 | 补到 §4 末尾小节 |
| C5 — 字号阈值完整列出 | 更新 §6.3 |

### Rationale

与 spec assumption 一致：文档更新范围仅限 GAP B1-B3、C1-C5，不涉及 D 类未实现能力。
