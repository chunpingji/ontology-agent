# Data Model: Word 章节树与分层摘要

## Aggregate: DocStructure

`DocStructure` 是一次 Word 解析的 canonical aggregate，同时提供旧视图和新结构视图。

| Field | Type | Rule |
|---|---|---|
| title | string | 首个一级标题、首个视觉标题或文件名 |
| sections | DocSection[] | 旧扁平视图，文档顺序不变 |
| tables | DocTable[] | 旧表格视图，增加准确章节归属 |
| paragraphs/headings | string[] | 旧契约不变 |
| blocks | WordBlock[] | Word body 原始顺序 |
| section_tree | ChapterNode | 唯一文档根 |
| pagination | PaginationMetadata | 全文分页能力与可信度 |
| parser_version | integer | 缓存失效依据 |
| warnings | string[] | 可恢复的解析/摘要提示 |

## WordBlock

### ParagraphBlock

- `block_id`: `paragraph:{paragraph_index}:{fragment_index}`
- `block_type`: `paragraph`
- `paragraph_index`, `fragment_index`
- `text`, `style_name`, `heading_level`
- `section_node_id`
- `physical_page_number`

同一 Word 段落可因段内分页拆成多个 fragment。只有新块视图拆分，旧段落和章节正文保持整段。

### TableBlock

- `block_id`: `table:{table_index}`
- `block_type`: `table`
- `table_index`, `section_node_id`, `physical_page_number`

### PageBreakBlock

- `block_id`: `page-break:{body_ordinal}:{event_ordinal}`
- `block_type`: `page_break`
- `break_source`: `manual | pageBreakBefore | section | lastRendered`
- `physical_page_number_after`

分页事件不属于章节正文，不进入摘要字符数。

## ChapterNode

| Field | Rule |
|---|---|
| node_id | 根为 `document`；章节为 `section:{heading_index}` |
| node_type | `document | section` |
| heading/level/path | 根 level=0；章节保留已推断 1..6 和完整路径 |
| heading_index | 章节标题原始段落索引；根为空 |
| direct_block_ids | 仅节点直接内容，章节包含标题块作为范围起点 |
| paragraph_indices/table_indices | 直接内容来源索引，去重且有序 |
| source_range | `anchor_block_id/start_block_id/end_block_id/heading_index`，供前端定位；页片段的精确块集合保存在 PageNode `block_ids` |
| layer_metadata | 子树摘要与确定性统计 |
| pages | 仅叶章节或无章节文档根允许非空 |
| children | 按文档顺序排列的直接子章节 |

构树状态转换：读取新标题时持续弹出 `stack.top.level >= new.level`，然后挂到当前栈顶。跳级不创建占位节点。

## PageNode

- `node_id`: `{owner_node_id}:page:{ordinal}`
- `node_type`: `page`
- `ordinal_in_leaf`: 从 1 递增
- `physical_page_number`: 仅存在可靠标记时给出
- `break_source`: 页片段开始边界来源，可空
- `block_ids`, `paragraph_indices`, `table_indices`
- `source_range`
- `page_metadata`

同一物理页上的不同叶章节拥有不同 PageNode；它们可共享物理页码但不能共享不属于自己的块。

## Summary metadata

### LayerMetadata

- `content_summary: string | null`
- `summary_scope: subtree`
- `summary_status: pending | completed | partial | disabled | failed`
- `summary_source: llm | extractive_fallback | empty | none`
- `summary_model`, `prompt_version`, `content_hash`, `generated_at`
- `direct_paragraph_count`, `direct_table_count`
- `descendant_section_count`, `leaf_count`, `page_count`

### PageMetadata

- 与 LayerMetadata 相同的摘要溯源字段，scope 为 `page_segment`
- `paragraph_count`, `table_count`, `character_count`

状态规则：

```text
empty content -> completed + empty
feature disabled -> disabled + none
all requested summaries valid -> completed + llm
some child/node fallback -> partial/completed + extractive_fallback
enabled request failed -> failed + extractive_fallback
```

## PaginationMetadata

- `mode`: `rendered_markers | explicit_markers | single_page_fallback`
- `physical_page_numbers_available`
- `is_estimated`: fallback 时为 true，其余 false
- `warning`: 无可靠分页时给出用户可读提示

## Stateless API aggregate: WordDocumentAnalysis

`POST /api/document-analysis/word` 的一次性响应由以下字段组成：

- `filename`
- `content`: structure-only、rich-style Tiptap JSON
- `warnings`
- `section_tree`
- `pagination`
- `parser_version`
- `summary_prompt_version`

该 aggregate 不是数据库实体，不含 `job_id/status/triples/relationships/doc_class`，不写入 annotated cache。服务端生命周期止于请求级临时目录清理；前端生命周期止于页面刷新、关闭或用户清空。

## Validation invariants

1. 所有节点 ID 和块 ID 在文档内唯一。
2. 每个非分页块最多归属一个章节节点。
3. 章节树先序中的 section 与扁平 sections 一一对应且顺序相同。
4. 非叶章节 `pages` 为空；每个叶章节（或无章节文档根）的直接内容由其页节点覆盖，带子章节文档根的标题前序言仍保留为根直接内容。
5. source range 的首尾 ID 必须存在于同一有序 block 序列。
6. LLM 不得修改任何上述结构字段。
7. 即时分析响应不得包含图谱抽取结果或可用于恢复服务端作业的标识。
