# Contract: Word Tree Summary Batch

## Service input

`summarize_word_tree(structure, client, progress_fn=None, should_stop_fn=None)` 消费完整确定性 `DocStructure`。服务不得修改 node ID、层级、路径、children、blocks、扁平 sections 或事实抽取结果。

批次顺序固定为：

1. 所有页节点，按文档顺序分批。
2. 章节按树深度从深到浅，同深度按文档顺序分批。
3. 文档根。

同一阶段的独立批次允许有限并发，上限为摘要并发配置与共享模型并发配置的较小值；下一个深度必须等待本层完成。调用身份和模型调度上下文传入工作线程；所有权检查、等待回调在协调线程执行。取消/失去所有权停止后续提交并收束在途请求，不作为普通摘要失败吞掉。

单节点材料包含节点 ID、路径、未被成功页摘要覆盖的直接正文、有限表格材料，以及叶节点页摘要或非叶节点直接子摘要。只有 `completed / llm` 且非空的页摘要允许去重；失败、未完成或输入/输出被截短的页仍保留原文材料。范围与唯一页完全一致的叶节点可复用该页摘要及生成来源，保留自身 scope、content_hash 和结构身份。

本次优化使用 `word-tree-summary-v2`，使新生成结果与旧摘要缓存区分。已冻结运行的摘要仍从其制品恢复。

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

提示明确每个摘要的字符上限，输出 token 预算包含摘要文本和 JSON 开销。输出截断时在同一总超时内最多重试一次，提高输出预算并保留结构化输出；不以相同预算机械重复。其他消费者保持原有重试策略。

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
