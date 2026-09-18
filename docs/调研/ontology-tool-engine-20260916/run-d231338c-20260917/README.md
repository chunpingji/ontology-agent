# 文档运行 d231338c 长时间少产出诊断

运行：`d231338c-f96f-436a-b759-85fc5610578c`。页面：`/analysis?tab=document&documentRun=d231338c-f96f-436a-b759-85fc5610578c`。

本次只读检查运行状态、调用账本、冻结输入、处理结果和图谱展示分区，并以冻结输入做纯内存复现。没有修改业务代码、启动额外模型调用、暂停运行或重启服务。本报告不是修复验收，也不是完整报告的 F1 评测。

## 1. 结论与观测口径

后端处理效率异常，图谱页面与后台数据一致。最初检查时确实没有关系；排查期间，运行于 **2026-09-17 05:31:19 UTC** 产生首条 `CMC 报告 —含合成路线→ SynthesisRoute` 关系（事件序号 82）。从 03:40:11 UTC 创建运行到首条关系约 **1 小时 51 分钟**。

截至 **2026-09-17 05:35:56 UTC** 的一致性快照：

| 指标 | 数值 |
|---|---:|
| 状态 / 阶段 | running / recognition |
| 图谱节点 / 关系 | 2 / 1 |
| 已尝试逻辑处理项 | 55 |
| 完成检查 / 未完成 | 1 / 54 |
| 计划处理项 / 未尝试 | 2,533 / 2,478 |
| 已记录强模型调用 / 未决调用 | 69 / 1 |

“处理项”是主体、谓词和来源记录的组合，不是互不重复的文档记录；不能把 2,533 解读成文档有 2,533 条记录。当前状态仍在变化，上述数字是定时观测值。

按 `task_id` 取最新结果，避免技术重试重复计数：

| 结果原因 | 处理项数 |
|---|---:|
| `record_undetermined` | 24 |
| `context_budget_exceeded` | 17 |
| `_ToolFailure` | 8 |
| `model_budget_exhausted` | 3 |
| `class_outside_menu` | 2 |
| `record_supported` | 1 |

安全摘要见 [snapshot.json](snapshot.json)；上下文复现摘要见 [context-reproduction.json](context-reproduction.json)。均未复制文档正文、模型长推理或完整 Mock 元数据。

## 2. 已确认原因

### 2.1 辅助上下文被要求唯一归属业务记录，8 项确定性失败

`assemble_context()` 会引入章节标题、共享表头等 `named_object_binding` 辅助片段。`ToolModelRecognitionAdapter._initial_items()`（`backend/app/services/extraction/ontology_guided/tool_model_adapter.py:505`）直接调用 `_inspect_evidence()`，后者通过 `_record_id()`（`tool_runtime.py:396`）推断片段归属。

当前判断只接受“属于当前记录”或“唯一匹配其他记录”。章节标题可能不在 `RecordIndex.records` 内，共享表头可能匹配多条其他记录，因此抛出 `evidence_record_ambiguous`。合法辅助上下文在模型请求前就阻断任务。

使用本运行冻结结构、本体、任务及实际关闭的 `evidence_repair` 配置，重新组装上下文并调用相同内部函数，**8 个失败任务全部复现**。26 个问题片段中，15 个匹配 0 条记录，其余匹配 2、5 或 6 条记录。影响包括 `describes`、`hasCleaningResidue`、`hasDegradationPathway` 等谓词。

此外，异常携带的是 `.code`，执行器却只读取 `.reason_code`（`executor.py:3697`），导致持久化结果只显示 `_ToolFailure`，没有显示真正原因。原样技术重试不能修复这种确定性错误。

### 2.2 输入与输出预算不匹配，17 项未完成

检查到的配置为 `evidence_max_input_tokens=32768`、`evidence_max_output_tokens=20480`。适配器在 `tool_model_adapter.py:747–751` 判断：

```python
measured = self.token_counter(canonical_json(request))
if measured + self.max_output_tokens > self.max_input_tokens:
    raise StructuredModelError("context_budget_exceeded")
```

因此，按当前本地计数口径，完整请求只剩 **12,288 tokens**，包含原文、Schema、核验目标及工具往返历史。这是本地预算拦截，不是已经验证出的 Qwen 服务上下文上限，也不是服务超时。

核验对象和多轮返回累积后容易超限。一个存放条件任务已经经过发现、核验和补证工具调用，下一轮因输入超限停止，最终没有接纳结果。模型提出过候选不等于候选正确；该样本还涉及对象类型/归属问题，不能用取消校验来换取入图。

### 2.3 工具返回预算阻断有效核验路径

`tool_runtime.py:313–318` 对完整工具结果计量，超过 `max_result_tokens=4096` 即返回 `result_budget_exceeded`。实际存放条件样本中，一次 `check_claim_binding` 返回因此被阻断；另一次通过。控制层最终检查同样经过这个分派入口。

该问题使后续补证继续消耗模型调用与上下文预算。它是已确认的具体样本，不能据此把全部预算失败都归因于工具返回大小。

### 2.4 无候选与未完成混用，进度语义失真

`claim_protocol.py:1123–1130` 把没有声明状态或带 observation 的结果都设为 `undetermined`，并令 `complete=False`；默认原因却写成“已按声明及依赖完成独立核验”。

05:26 UTC 样本中，33 份保存的 discovery 有 24 份为空；29 份保存的 verification 有 27 份没有核验目标。无目标时适配器直接生成空 `VerifiedClaimSet`（`tool_model_adapter.py:1293`），没有调用 Qwen 核验。不能把保存的 verification 数当作真实模型核验次数，也不能把空候选等同于全文没有事实。

需要分别表达本记录已检查但无声明、有候选待核实、技术执行失败。当前口径使无产出的处理长期留在“未完成”中，并使提示文本难以解释进度。

### 2.5 候选空间大，Qwen 串行调用耗时长

本运行冻结策略未包含 `candidate_planning`、`adaptive_retrieval`、`evidence_repair`。新协议策略入口见 `backend/app/services/document_analysis/execution.py:776`，执行器接入见 `execution.py:2039` 和 `execution.py:2056`。实时配置中的 adaptive retrieval 为 `trial`，不代表本运行实际启用了该策略。

**摘要、embedding 和 reranker 确实在工作**，但当前主要是候选排序。149 条来源记录与根节点 12 个谓词构成 1,788 个初始处理项；首条关系产生新节点后，又增加 5 × 149 = 745 项，总计 2,533。稀疏候选规划未接入，导致不相关记录与谓词组合也需要强模型判断。

冻结配置 `recognition_inflight=1`。时延采样中的已完成调用：

| 阶段 | 调用数 | 平均请求耗时 | 累计请求耗时 |
|---|---:|---:|---:|
| Qwen discovery | 55 | 91.23 秒 | 5,017.81 秒 |
| Qwen verification | 10 | 107.56 秒 | 1,075.58 秒 |

这 65 次强模型调用累计排队约 2.36 秒，主要时间在模型请求执行。摘要 6 次累计约 137 秒，token count / embedding / reranker 请求累计约 253 秒。不同操作可能重叠，累计耗时不能直接相加当作运行墙钟时间。

05:26 UTC 已保存的 64 个模型轮次共计输入 401,796、输出 301,655 tokens，响应状态均为 completed。长输出放大串行调用耗时；没有证据把主要原因归为模型服务断线、全局队列拥塞或 GLiNER 耗时。

## 3. 工具启用情况

05:26 UTC 工具样本中，`propose_mentions` 调用 2 次，GLiNER 返回候选数分别为 0、2，并存在 `definition_missing` 省略项。`query_instances` 和 `retrieve_evidence` 均为 0 次。Mock 实例和实验词表已经冻结启用，但启用配置不等于模型实际使用过这些工具。

`retrieve_evidence=0` 也不代表摘要检索完全未运行：控制层排序调用和模型自主检索工具是两个入口。当前没有证据表明 SHACL 或单位校准是主要阻塞点。

## 4. 建议修复顺序与验收

1. **先修上下文归属和错误传递。** 为章节标题、共享表头保留真实来源、辅助角色及事实资格，避免把辅助上下文强行视为唯一记录事实；不得简单指定任意记录或跳过证据边界。以本次 8 个任务作为确定性回归输入。
2. **统一输入、输出及工具返回预算。** 区分请求输入额度与模型总上下文容量，按阶段预留输出；缩小核验批次和冗余回传。工具检查返回紧凑结论及必要引用。输入未变的预算失败不做无效重试。保留原文引用、工具协议和事实核验要求。
3. **修正完成度语义。** 分开执行是否完成与事实是否成立，明确无候选、未决和技术失败，不把无候选改判为全文否定。
4. **接续已有通用候选规划，再校准 Qwen 输出开销。** 复用文档结构、摘要及本体合法谓词范围来减少候选组合；保留检索未覆盖项的口径，避免恢复领域专用规则。用包含章节标题、共享表头和实际关系的小范围输入，验证完整发现—核验—入图路径、耗时和调用量后，再运行完整报告。

首要问题不能靠继续等待或单纯提高并发解决。建议修复前暂停这次低效运行以减少消耗；本次诊断没有代用户执行暂停。当前发现的首条关系也不足以证明完整报告的精确率、召回率或 F1 达标。
