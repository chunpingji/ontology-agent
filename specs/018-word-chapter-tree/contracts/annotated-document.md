# Contract: Annotated Document Word Tree Extension

> Compatibility scope: this job-bound endpoint remains available to existing extraction/graph consumers. `/analysis?tab=document` MUST NOT call it; the stateless tool uses `stateless-word-analysis.md`.

## Endpoint

```http
GET /api/extraction/jobs/{job_id}/annotated-document
```

现有认证、错误和基础响应不变。仅 Word 响应增加可选字段；非 Word 或旧缓存可省略。

## Added response fields

```json
{
  "section_tree": {
    "node_id": "document",
    "node_type": "document",
    "heading": "CMC 报告",
    "level": 0,
    "path": [],
    "heading_index": null,
    "is_leaf": false,
    "direct_block_ids": [],
    "paragraph_indices": [],
    "table_indices": [],
    "source_range": {
      "anchor_block_id": null,
      "start_block_id": "paragraph:0:0",
      "end_block_id": "table:2",
      "heading_index": null
    },
    "layer_metadata": {
      "content_summary": "报告覆盖产品性质和工艺信息。",
      "summary_scope": "subtree",
      "summary_status": "completed",
      "summary_source": "llm",
      "summary_model": "qwen2.5:14b",
      "prompt_version": "word-tree-summary-v2",
      "content_hash": "sha256...",
      "generated_at": "2026-08-25T12:00:00Z",
      "direct_paragraph_count": 0,
      "direct_table_count": 0,
      "descendant_section_count": 2,
      "leaf_count": 1,
      "page_count": 1
    },
    "pages": [],
    "children": []
  },
  "pagination": {
    "mode": "explicit_markers",
    "physical_page_numbers_available": true,
    "is_estimated": false,
    "warning": null
  }
}
```

`children` 递归使用相同 ChapterNode。叶章节的 `pages` 使用：

```json
{
  "node_id": "section:42:page:1",
  "node_type": "page",
  "ordinal_in_leaf": 1,
  "physical_page_number": 3,
  "break_source": "manual",
  "block_ids": ["paragraph:43:0", "table:1"],
  "paragraph_indices": [43],
  "table_indices": [1],
  "source_range": {
    "anchor_block_id": "paragraph:43:0",
    "start_block_id": "paragraph:43:0",
    "end_block_id": "table:1",
    "heading_index": 42
  },
  "page_metadata": {
    "content_summary": "本页列出产品理化属性。",
    "summary_scope": "page_segment",
    "summary_status": "completed",
    "summary_source": "llm",
    "summary_model": "qwen2.5:14b",
    "prompt_version": "word-tree-summary-v2",
    "content_hash": "sha256...",
    "generated_at": "2026-08-25T12:00:00Z",
    "paragraph_count": 1,
    "table_count": 1,
    "character_count": 20
  }
}
```

## Compatibility

- `content`, `triples`, `relationships`, `doc_class` 等现有字段不变。
- 新字段必须可序列化为普通 JSON，不能包含 Python 对象引用。
- 旧前端忽略新字段即可继续工作。
- 摘要失败不得把 HTTP 成功响应变成 5xx。

## Cache identity

缓存有效性至少包含 annotator version、parser version 和 summary prompt version。`refresh=1` 继续强制完整重算。
