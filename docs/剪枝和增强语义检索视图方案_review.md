# 《剪枝和增强语义检索视图方案》评审意见

日期：2026-09-11。状态：评审结论。本次未修改代码、未修改被评审方案、未运行模型、未部署。

评审对象：[剪枝和增强语义检索视图方案](剪枝和增强语义检索视图方案.md)（2026-09-11 版，276 行）。

评审方式：先逐条核对当前工作树代码，再由 Codex（`gpt-5.6-sol`，effort xhigh，read-only 沙箱）
独立交叉审查。两方结论不一致处以代码为准，并在第 3 节明确记录被推翻的判断。
以下行号对应本次核对时的**未提交工作树**，后续改动可能偏移。

## 0. 总体判断

方案的**事实基础可靠**。它对现状的每一条描述都能在代码中验证为真，措辞克制
（"仍可能"、"取决于缓存和构建位置"、历史数据不作效果证据），没有把设计目标写成已实现。

但**不能进入 P1 实现**。阻断原因不是方案错，而是它描述的目标行为与现有**已冻结的契约、
测试和搜索状态机**存在六处直接冲突，而方案未把"修改它们"列为工作项。
P0（契约与基线）可以立即开始，其完成条件应是第 6 节清单全部闭合。

## 1. 已验证为真的部分

| 方案断言 | 代码证据 |
|---|---|
| 默认补满候选池（§2.1/§5.3） | [semantic_retrieval.py:80](../backend/app/services/extraction/ontology_guided/semantic_retrieval.py#L80)、[:108](../backend/app/services/extraction/ontology_guided/semantic_retrieval.py#L108)、[semantic_reranker.py:65](../backend/app/services/extraction/ontology_guided/semantic_reranker.py#L65) |
| dense 通道含大量低分记录（§2.1） | [semantic_retrieval.py:23-30](../backend/app/services/extraction/ontology_guided/semantic_retrieval.py#L23-L30) 只保留 score>0；dense 走 `dense_scores or {}` 全量 |
| 首次 dense 仍可能为全部合格视图编码（§2.1） | [semantic_reranker.py:847](../backend/app/services/extraction/ontology_guided/semantic_reranker.py#L847) 传入全部 `eligible`，`pool_limit` 在其后才应用 |
| H2 每轮新增池 8/16/32/64、最多 4 轮（§2.1） | [heuristic_search.py:333-338](../backend/app/services/extraction/ontology_guided/heuristic_search.py#L333-L338)、[:409-415](../backend/app/services/extraction/ontology_guided/heuristic_search.py#L409-L415) |
| 不能删低分记录表达剪枝（§7.2） | `RankingEpoch.exact_pool` 强制 `record_ids` 与 `ordered_record_ids` 同池同长 |
| 检索视图已含原文/标题路径/表头/父表上下文/注释/字段组（§2.1） | [retrieval_views.py:67-74](../backend/app/services/extraction/ontology_guided/retrieval_views.py#L67-L74)；章节摘要为独立 `metadata_text` |
| HRS-1597 引用数据（§2.2） | 521656 tokens、1924.424 秒、75.33%、五批 64 条、准入 8 条，与[源分析](报告中心HRS1597新内核运行耗时分析-20260911.md)完全一致 |

§5.3"RRF 融合相对名次，即使候选全部无关也有第一名"、§2.2"批次成本与产出不匹配，不能据此
认定全文没有相关事实"这类区分是正确的，也是本系统最容易出错的地方，应在实施中保留原文。

<!-- APPEND-MARK-1 -->
