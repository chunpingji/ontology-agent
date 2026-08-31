# Contract: Word Tree Summary Batch

## Service input

`summarize_word_tree(structure, client, progress_fn=None)` 消费完整确定性 `DocStructure`。服务不得修改 node ID、层级、路径、children、blocks、扁平 sections 或事实抽取结果。

批次顺序固定为：

1. 所有页节点，按文档顺序分批。
2. 章节按树深度从深到浅，同深度按文档顺序分批。
3. 文档根。

单节点材料包含节点 ID、路径、直接正文、有限表格材料，以及叶节点页摘要或非叶节点直接子摘要。

## Model response schema

```json
{
  "type": "object",
  "properties": {
    "summaries": {
      "type": "array",
      "items": {
        "type": "object",
        "properties": {
          "node_id": {"type": "string"},
          "content_summary": {"type": "string"}
        },
        "required": ["node_id", "content_summary"],
        "additionalProperties": false
      }
    }
  },
  "required": ["summaries"],
  "additionalProperties": false
}
```

调用使用 `response_format.type=json_schema`、`strict=true`，并保留现有 Prompt JSON fallback 以兼容不支持结构化输出的本地端点。

## Server validation

- 只接收当前批次已知且尚未赋值的 node ID。
- 摘要清理空白并截断到配置最大字符数。
- 未知、重复和缺失 ID 按节点忽略/降级。
- 模型额外字段不进入元数据。
- 日志只记录批次、字符数、时延、成功/降级计数和错误类型，不记录全文。

## Degradation

| Condition | Status/source |
|---|---|
| feature or local LLM disabled | `disabled / none` |
| empty material | `completed / empty` |
| request/schema failure | `failed / extractive_fallback` |
| valid model result | `completed / llm` |
| parent consumes any failed child | parent may be `partial` while still carrying a summary |

确定性摘录只清理空白并截取首批完整句，不添加外部事实。
