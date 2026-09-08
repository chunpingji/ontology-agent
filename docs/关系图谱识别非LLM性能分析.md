# 报告模板关系图谱识别：非 LLM 性能分析

分析日期：2026-09-07。模板：`f2ca301f-01f1-412f-a85f-59944445bb09`，风险评估文档 v2.2。默认源作业：`19e291e5-f344-42eb-b5e0-ff40ae3e760b`，源文件：原料药 HRS-5678 临床备样生产信息.docx。

**实施更新（2026-09-07）：六项核心优化已接入识别执行路径，并实现安全批次增量保存、非阻塞图谱展示、断点增量日志、覆盖编译复用及独立阶段计时。数据库已执行 `alembic upgrade head` 并确认处于 `0029_versioned_calculations`。本轮实际代码对比：上下文 263 → 38 ms，本体暖态读取 650 → 20 ms，列表函数 725 → 55 ms；完整上下文及候选/图结构/计算结果一致。实施清单、验收与部署情况见第 7 节。第 1—6 节保留为历史分析及实施前基准。**

**建议优先优化范围查找和上下文复用，其次减少 token 预算计算的重复请求、缓存本体语义结构。与此同时，应让已完成批次的图谱结果增量可见。** 文档解析本身约半秒，当前小断点的 JSON 编码不足 1 毫秒，均不是本次应首先处理的瓶颈。现有测量不能给出扣除 LLM 后的整轮耗时占比，也不能将单个函数的加速倍数当成整轮提速倍数。

## 1. 首轮方法与真实规模

检查当前工作区代码、数据库只读事务、运行断点，并在独立诊断进程中进行函数计时和 cProfile 分析。本体使用临时 SQLite 存储，与在线服务的本体存储隔离。实验没有调用模型生成接口，没有重跑或暂停真实识别，也没有更新或发布真实候选。

token 计数实验调用实际部署的 `/props` 和 `/tokenize`，使用相同词表；它们是预算检查开销，与 LLM 生成调用分别讨论。范围优化仅在诊断进程临时替换查找函数，结束后恢复；没有修改业务源码。

| 项目 | 观测结果 |
| --- | --- |
| 源文档 | 486 个证据单元，其中 440 个非空；证据文本合计 4,534 字符；10 张顶层表；51 个结构节点 |
| 本体语义结构 | 329 个类、138 种数据属性、67 种对象关系 |
| 当前文档类型可达的实体类别 | 136 个，全部可装入一个类别菜单 |
| 部署预算 | 输入 32,768 tokens；每任务最多 32 个区域；每轮最多 2,048 个任务 |
| tokenizer | `llama_server`，预算计数实际经过 HTTP |
| 状态快照 | 06:36:54 UTC 为 `paused`，作业候选数为 83；早先读取时仍为 `annotating`、69 条候选 |

诊断期间在线状态继续变化，因此上表中的候选数和断点计数是各次读取的快照，不是冻结的整轮运行数据。

## 2. 首轮量化证据与优化点

| 优先级 | 优化点 | 实测证据 | 主要收益 |
| --- | --- | --- | --- |
| P0 | 证据 ID 索引、缓存范围区间 | 31 片段的关系上下文触发 13,799 次线性证据查找；缓存 token 计数后，构建中位数约 265 ms；增加 ID 索引后约 71 ms，再复用区间后约 34 ms | 减少关系/属性任务反复执行的本地 CPU 工作 |
| P0 | 复用任务预算预检生成的上下文 | `fit_assertion_task()` 构建上下文后只返回任务，`execute_task()` 又重新构建 | 避免同一个合格任务重复支付上下文成本；尚未做完整任务链加速测量 |
| P1 | 类别菜单先整体计数 | 当前 136 个逐渐增长的菜单触发 136 次 token 请求，合计处理约 258 万字符，耗时 3.32 s；完整菜单一次计数为 10,582 tokens，低于预算 10,922，耗时 49 ms | 本例可省约 3.27 s 的准备时间；菜单分组结果相同 |
| P1 | 短文本先检查整段是否可装入窗口 | 前 32 个非空单元：当前二分切窗 135 次 HTTP、155 ms；先整段检查为 32 次、36 ms；窗口边界相同 | 降低切窗和后续各谓词重复扫描的预算检查开销 |
| P1 | 按本体版本缓存语义结构 | 暖态重建约 673–676 ms；剖析发现 329 次类详情读取、72,709 次 domain 匹配、12,439 次本体 SQLite execute | 减少每次识别准备、结果读取和覆盖率读取的重复成本 |
| P2 | 候选持久化批量查询 | 对已存在的 83 条真实候选进行幂等路径检查，执行 250 次 SELECT，耗时 282 ms，没有 INSERT/UPDATE/DELETE | 降低大结果集入库及重试成本；当前 83 条规模下不是主要耗时 |

以上为单机函数实验，不是生产 HTTP 延迟的 p95。范围优化实验对比了完整 `ContextEnvelope.model_dump()`，结果完全一致；采用真实 token 计数缓存，以隔离 CPU 变化。

### 2.1 范围查找存在乘法放大

调用链为：

`build_context → 对每个事实区域调用 scope_contains → 每次重算 scope_intervals → 对每条范围线性调用 DocumentIR.unit`。

本例文档根对象覆盖 440 个非空区域。一个 31 片段任务要计算 31 次全范围区间，再叠加每次证据 ID 查找在 486 个单元的列表中线性扫描。带 cProfile 的测量中，范围判断累计约 327 ms，占上下文构建约 410 ms 的近 80%；这个比例仅用于定位热点，前表使用未启用剖析的计时。

建议在不可变分析对象上建立 `evidence_id → unit`、章节、表格行列索引；按分析版本与 scope 版本复用合并、排除后的区间。表格逻辑记录也应按分析对象复用，避免 `build_context()` 为 metadata 和 notes 分别新建 `TableRecords`。

缓存必须包含原文结构身份、范围版本及相关候选修订，不能绕过原文定位、范围排除、合并单元格和竞争主体校验。

代码：[DocumentIR.unit](/opt/dev/chen/ontology-agent/backend/app/services/extraction/document_ir.py:74)、[scope_intervals](/opt/dev/chen/ontology-agent/backend/app/services/extraction/evidence_scope.py:37)、[scope_contains](/opt/dev/chen/ontology-agent/backend/app/services/extraction/evidence_scope.py:72)、[build_context](/opt/dev/chen/ontology-agent/backend/app/services/extraction/hierarchical_context.py:172)、[TableRecords](/opt/dev/chen/ontology-agent/backend/app/services/extraction/table_records.py:10)。

### 2.2 预检上下文被立即丢弃

关系/属性调度先通过 `fit_assertion_task()` 检查对象列表与证据区域能否装入预算。预算合格时，它只 yield task，已经生成的 envelope 没有保留。进入 `execute_task()` 后，再执行一次完整构建。

建议返回 prepared task + envelope，或使用任务、候选修订、scope、模型/tokenizer、协议版本共同确定的缓存。预算拆分后的子任务必须独立建键，绑定阶段增加了候选信息时仍需重新进行准确预算检查。检查点恢复时，也可优先识别已完成的任务，避免为仅恢复候选而重新支付预检成本。

代码：[预算预检](/opt/dev/chen/ontology-agent/backend/app/services/extraction/extraction_tasks.py:912)、[实际执行](/opt/dev/chen/ontology-agent/backend/app/services/extraction/extraction_tasks.py:480)。

### 2.3 token 预算检查有不必要的往返

`pack_classes()` 从 1 个类不断增长至 136 个类，每一步都序列化整个前缀并请求准确计数。该文档最终只有一个菜单，因此先测试完整菜单即可在本例保持同样结果；超限时再回退现有分组。

`split_windows()` 对远低于窗口上限的短文本也执行二分。先准确计数整段，能装入则直接返回原始边界，不能装入再执行切分。不同谓词对同一 scope 的切窗和区域成本也可复用。

`ServerTokenizer` 已有文本摘要缓存，但仅属于本次 runner，达到 4,096 项后整批清空。可改为有容量约束的淘汰缓存，并按模型制品和 tokenizer 身份复用。若考虑文件 tokenizer，须先验证与部署 GGUF 的 token 结果一致，不能用字符估算代替现有准确计数。

代码：[切窗](/opt/dev/chen/ontology-agent/backend/app/services/extraction/hierarchical_context.py:149)、[区域打包](/opt/dev/chen/ontology-agent/backend/app/services/extraction/extraction_tasks.py:845)、[类别打包](/opt/dev/chen/ontology-agent/backend/app/services/extraction/extraction_tasks.py:880)、[服务端 tokenizer](/opt/dev/chen/ontology-agent/backend/app/services/extraction/local_semantic_model.py:38)。

### 2.4 本体结构应从反复查询改为版本化快照

`semantic_schema_from_engine()` 对所有类逐一读取详情、扫描数据属性和对象关系，并生成继承闭包；抽取 runner 初始化和 evidence/coverage 读取都会调用。多个页面请求还会争用同一 OntologyEngine 的锁。

建议在本体发布或编辑后构建一次不可变 schema 快照，同时预计算 domain、range、继承、显示标签索引。图谱所需的子集可从快照投影，不必每个请求重扫整个本体。需要明确本体变更时的失效规则。

代码：[语义结构构建](/opt/dev/chen/ontology-agent/backend/app/services/extraction/extraction_tasks.py:254)、[本体属性读取](/opt/dev/chen/ontology-agent/backend/app/services/ontology_engine.py:459)、[结果接口](/opt/dev/chen/ontology-agent/backend/app/api/evidence.py:224)。

## 3. “迟迟看不到图谱”的流程原因

这部分主要影响首条关系的等待时间和交互反馈，不应等同于减少全部模型任务的总用时。

1. **实体阶段阻挡所有关系/属性任务。** `_run()` 先处理全部源区域及类别菜单，再生成 subjects 和关系/属性队列。任何前置实体任务中断都可能让后续队列尚未开始。模板优先路径只影响后续排序，无法越过实体阶段。建议按证据批次及依赖就绪情况推进图谱分支；新增实体、竞争主体或修订出现后，相关范围和任务必须失效或补调度，防止提前形成错误结论。
2. **批次完成后只写断点，候选没有增量提供给页面。** `on_checkpoint()` 保存文件和计数；候选持久化发生在整轮返回完成或暂停后。建议每个安全批次原子保存候选增量及运行修订，再用现有 SSE 通知页面读取增量。
3. **页面只在终态刷新候选，而且运行中有整块遮罩。** 正在运行时即使已有旧结果，图谱也被覆盖。建议使用局部进度条或状态栏，使已完成结果可继续查看；将“尚未执行”“没有发现”“任务失败”分开呈现。
4. **图谱持久化还等待章节摘要。** 完整识别结束后，`_compute_annotation()` 先处理章节摘要，返回 payload 后才保存图谱。建议将图谱可见状态与章节摘要完成状态解耦。

代码：[实体阶段与队列边界](/opt/dev/chen/ontology-agent/backend/app/services/extraction/extraction_tasks.py:1176)、[模板路径调度](/opt/dev/chen/ontology-agent/backend/app/services/extraction/extraction_tasks.py:1194)、[断点通知](/opt/dev/chen/ontology-agent/backend/app/api/extraction.py:758)、[摘要步骤](/opt/dev/chen/ontology-agent/backend/app/api/extraction.py:515)、[候选保存](/opt/dev/chen/ontology-agent/backend/app/api/extraction.py:787)、[前端终态刷新](/opt/dev/chen/ontology-agent/frontend/src/components/extraction/template-slot-editor.tsx:833)、[运行遮罩](/opt/dev/chen/ontology-agent/frontend/src/components/extraction/template-slot-editor.tsx:2409)。

## 4. 后续规模扩大时再处理的项目

**数据库 N+1 与多次提交。** `persist_validated()` 先逐条查原 ID、再逐条查规范 ID，返回时又逐条 `get()`；关系依赖还可能增加查询。`_persist_evidence_payload()` 两次 save_analysis、一次候选持久化、一次作业保存，内部合计四次 commit。建议批量读取已有 ID 和依赖，分批写入，在一次有界事务内原子推进分析、候选与运行版本；保留唯一性、审核历史与修订 CAS。只需数量时使用 count 查询，避免重新加载全部候选。

**完整运行记录随列表返回。** 后续快照中，候选 payload 约 150 KB，run 约 351 KB，其中 tasks 约 290 KB。任务记录重复包含类别菜单和范围。建议列表返回摘要、计数和候选增量，详细任务和断点按需读取；静态 ontology/scope/context 用版本 ID 引用，完整审计仍在服务端保留。

**断点全量重写。** 每个任务都会序列化完整 checkpoint 并 fsync；非致命失败路径会连续调用两次 save_checkpoint；统计函数也每次重扫历史 tasks。当前约 30 KB 的断点 json.dumps 仅 0.27–0.33 ms，不应将它归为当前主要瓶颈。任务增多后可采用追加式任务日志、定期原子快照和增量统计，暂停/终止时强制持久化，并去掉重复保存。本次未测出在线 fsync 的延迟分布。

**前端重复渲染。** 运行时整块模板编辑器每秒更新计时，图谱组件并未整体隔离这个更新；折叠的 details 仍会创建子节点。图索引已经使用 useMemo，不能将其描述为每秒重建。建议先把计时移至独立组件，候选规模增大时再测量节点渲染并按展开状态挂载。目前未进行浏览器帧耗时分析，不把这一点列为主要根因。

代码：[候选存储](/opt/dev/chen/ontology-agent/backend/app/services/extraction/candidate_store.py:126)、[持久化编排](/opt/dev/chen/ontology-agent/backend/app/api/extraction.py:622)、[断点](/opt/dev/chen/ontology-agent/backend/app/services/extraction/extraction_tasks.py:1046)、[每秒计时](/opt/dev/chen/ontology-agent/frontend/src/components/extraction/template-slot-editor.tsx:873)、[已有图索引缓存](/opt/dev/chen/ontology-agent/frontend/src/components/extraction/evidence-graph-tree.tsx:85)。

## 5. 实施顺序与验证边界

建议先实施 ID/范围索引与上下文复用，再实施菜单/切窗快速检查和本体 schema 缓存；增量图谱展示可作为独立交互改进。数据库批量化及断点日志化依据完整任务的阶段指标决定排期。

补充统一计时：文档解析、本体准备、tokenize HTTP、任务打包、上下文/范围校验、候选校验、断点编码/fsync、数据库读写、首条关系可见时间。模型生成等待单独记录。验收对比相同源文档和版本下的候选、原文锚点、范围与审核结果，不能通过放宽证据校验来获得速度。

测量的边界：

- 文档解析三次约 0.620 / 0.519 / 0.504 s；预览约 0.541 / 0.554 / 0.546 s。它们可以缓存，但收益低于重复执行的任务热路径。
- 没有执行完整真实模型重跑，因此没有整轮非 LLM 百分比、完整吞吐加速比或总耗时承诺。
- 尝试在独立进程中测量列表函数时，工作区新增计算模块访问了尚不存在的 calculation_decisions 表，调用中断；因此未报告 evidence 接口的完整 HTTP 延迟，也没有据此断言在线进程的接口不可用。没有为这项诊断变更数据库。
- 只新增本分析文档；实验脚本保存在 /tmp。业务源码和运行配置未由本次分析修改。

## 6. 实施前代码的逐项复核（2026-09-07 07:17 UTC）

本轮以磁盘上的当前工作区代码为准，核对 17 个核心后端/前端文件；记录文件摘要并在检查结束时复核，未发现这 17 个文件在本轮检查期间变化。没有用 Git 基线替代未提交的新代码。数据库只读检查时间为 07:14:21 UTC；没有重新识别、执行数据库迁移或修改业务源码。

### 6.1 原报告结论的当前状态

| 原结论 | 当前状态 | 当前代码证据 |
| --- | --- | --- |
| 证据查找与范围重算为 CPU 热点 | 仍成立，已重新测量 | `DocumentIR.unit()` 仍遍历列表；`scope_contains()` 每次仍完整调用 `scope_intervals()`；本轮同一上下文仍有 13,799 次 unit 调用 |
| 预算预检与执行重复构建上下文 | 仍成立 | `fit_assertion_task()` 第 912 行生成 envelope 后仅 yield task；`execute_task()` 第 480 行再次调用 build_context，没有 prepared envelope 或上下文缓存 |
| 类别菜单与短文本切窗存在多余计数 | 仍成立，本轮未重跑完整计数对比 | `pack_classes()` 第 880 行仍逐个扩展菜单；`split_windows()` 第 149 行仍直接二分，没有整段快速检查；服务端 tokenizer 缓存满 4,096 项仍整体清空 |
| 本体语义结构反复构建 | 仍成立 | `semantic_schema_from_engine()` 第 254 行仍逐类查询；runner 初始化、列表和 coverage 仍各自调用，未发现版本化 schema 快照 |
| 候选持久化存在 N+1 与四次 commit | 仍成立，原 SQL 次数是首轮样本 | `persist_validated()` 第 126 行保留逐条 get，返回时再次逐条 get；`_persist_evidence_payload()` 第 622 行仍调用两次 save_analysis，再分别保存候选和作业 |
| 实体阶段阻挡关系/属性阶段 | 仍成立 | 第 1176 行开始全文实体循环，第 1188 行才收集 subjects，第 1303 行仅在未停止时创建后续队列；优先路径与关系/属性轮转均位于该边界之后 |
| 断点不向页面提供候选增量 | 仍成立 | `on_checkpoint()` 第 758 行只写文件、更新计数并通知进度；候选保存仍在 `_compute_annotation()` 返回后 |
| 终态刷新、运行遮罩、等待章节摘要 | 均仍成立 | 模板编辑器第 833 行的 SSE 回调只在终态刷新候选，第 2409 行仍有整块遮罩；后端第 515 行摘要步骤仍在返回图谱 payload 之前 |
| 全量 run、全量断点及重复保存 | 仍成立 | 列表直接返回 run；save_checkpoint 每次扫描历史统计并写全量；非致命失败仍经过第 1124、1130 行两次保存 |
| 每秒计时带动图谱渲染 | 风险仍存在，未测浏览器帧耗时 | 计时仍在模板编辑器第 873 行；图索引已有 useMemo，但图谱组件仍未整体隔离父级更新；新增计算卡片每个实体还会筛选 calculations |

因此，本轮没有发现第 2 节六项优化已经完成的证据。新增计算能力与这些性能优化应分别判断。

### 6.2 当前代码重新测量的结果

沿用相同文档、31 个目标片段、覆盖 440 个区域的文档根范围，以及真实服务端 token 计数。没有调用模型生成接口。

| 测量 | 首轮 | 本轮 | 解释 |
| --- | ---: | ---: | --- |
| 原实现上下文构建，token 计数缓存后，中位数 | 265 ms | 268 ms | 热点仍存在，没有可确认的性能改善 |
| 仅增加证据 ID 索引的隔离实验，中位数 | 71 ms | 71 ms | 优化尚未写入业务代码 |
| ID 索引 + 范围区间缓存的隔离实验，中位数 | 34 ms | 33 ms | 完整 ContextEnvelope 输出与原实现一致 |
| unit 调用次数，cProfile | 13,799 | 13,799 | 重复查找机制一致 |
| 新进程首次构建 schema | 980 ms | 985 ms | 本轮仍为 329 类、138 种数据属性、67 种关系；不与暖态 673 ms 混比 |

本轮范围校验在启用 cProfile 时累计约 340 ms，上下文构建约 424 ms，仍占约 80%。本轮未重测首轮的文档解析、136 次菜单请求、候选持久化和断点编码计时；其精确数字只作为历史样本保留。

实现缓存时要补充一个约束：当前 [EvidenceModel](/opt/dev/chen/ontology-agent/backend/app/schemas/evidence.py:21) 没有冻结模型，DocumentIR、scope 等仍含可变列表。隔离实验中的对象 ID 缓存依赖“同一输入在实验中不变”，不能直接作为生产缓存方案。应限定为执行快照，或以实际内容版本与修订作为键，并明确变更时的失效规则。

### 6.3 更新后的列表读取：补充候选复读与计算成本

当前 [list_evidence](/opt/dev/chen/ontology-agent/backend/app/api/evidence.py:224) 已返回 `calculation_required` 和 `calculations`。它先调用一次 `CandidateStore.list(job_id)`，随后调用 [job_calculations](/opt/dev/chen/ontology-agent/backend/app/services/reasoning/calculation_review.py:29)，后者再次读取并验证同一作业的全部候选，再加载决策历史。

建议让计算函数接收本次已经读取的候选，避免同一请求中的重复 SQL、JSON 解码和模型校验。若缓存计算结果，键必须覆盖候选内容/修订、审核状态、关系绑定、方法版本及决策历史，不能只按 job_id 缓存。

本轮对 83 条真实候选执行纯内存计算，使用明确为空的诊断决策历史，五次为 0.84–0.98 ms，但返回 **0 条计算结果**。这只说明当前样本下不能把 PDE 算术列为主要耗时，不代表参数齐全或计算实体众多时的成本。当前 [evaluate_candidates](/opt/dev/chen/ontology-agent/backend/app/services/reasoning/pde_calculation.py:177) 对每个计算实体还会筛选全部属性、检查重复实例；有更多计算实体时再评估按主体/谓词/实例建立索引的收益。

### 6.4 覆盖检查包含报告预览编排，需要单独计时

前端 [refresh](/opt/dev/chen/ontology-agent/frontend/src/components/extraction/evidence-review-panel.tsx:46) 通过 Promise.all 发起图谱与 coverage 两个请求。两条请求均会重建本体语义结构。

[build_report_inputs](/opt/dev/chen/ontology-agent/backend/app/services/reporting/coverage_v2.py:54) 的当前执行顺序是：读取候选和完整 run、计算发现版本、编译模板、读取决策历史并构造幂等键，再调用 `service.start()`；新运行或缺少输入快照时继续 `service.execute()`。其中有 commit，首次运行还会持久化编译结果、预览运行与输入快照，因此它包含实际报告准备工作。

应区分两个场景：

- **幂等命中**：可以复用已有预览输入，不会每次都完整执行报告；但进入幂等检查前的候选读取、模板编译和摘要计算仍会发生。
- **幂等未命中**：`start()` 内还会再次编译，后续执行计算检查与输入解析。决策历史已进入幂等键，新的 PDE 取值决定会导致重新准备，这是正确失效，不应取消。

建议为“覆盖检查首次准备 / 复用读取”分别计时，复用版本化编译结果，并将状态读取与重新准备的责任分清。当前前端在图谱请求成功后就会 setData，不能表述为“图谱一定等待 coverage 返回才显示”；但操作按钮的 busy 状态会等待整个 refresh 完成。

覆盖计算还会分别验证已发布快照和当前候选，以检查输入是否已发布，见 [execute_checks](/opt/dev/chen/ontology-agent/backend/app/services/reporting/calculation_execution.py:11)。两份输入可能不同，不能简单删掉其中一次验证。本轮未触发真实 coverage 预览写入，因此此项为代码确认的待测路径，暂列 P1 测量与优化候选。

### 6.5 原来的缺表限制仍成立，应先处理部署一致性

07:14:21 UTC 的只读查询确认：

- 数据库版本仍是 `0028_automatic_evidence_review`。
- `public.calculation_decisions` 不存在。
- 源码已包含 [0029_versioned_calculations](/opt/dev/chen/ontology-agent/backend/alembic/versions/0029_versioned_calculations.py:15)，负责创建该表。
- 当前列表函数无条件调用计算决策历史；coverage 的正常准备路径也需要读取该表。

这项限制没有随着代码更新自动消失。加载新代码前需要完成对应的部署/迁移流程，然后才能对正常列表和覆盖检查做完整验收。不能用“尚无计算实体”推断不会查表，因为 job_calculations 仍会执行 decision_history。

本轮只读查到了源码与数据库的差异，没有通过 HTTP 验证在线进程已加载哪一版代码，因而不将该差异直接当成当前在线请求必然报错的证明；也没有为诊断执行迁移。

### 6.6 修订后的建议顺序

1. **部署前提**：先对齐新代码与 0029 数据库迁移，恢复当前版本可完整验证的读路径。
2. **P0 性能实现**：证据/表格索引、范围区间复用、预检 envelope 复用；保持引用、范围和修订校验。
3. **P1 性能与交互**：菜单和切窗快速检查、本体 schema 缓存；按依赖安全地推进批次、增量提供图谱结果；补测覆盖准备与读取的分别耗时。
4. **P2 规模优化**：先复用列表已有候选，再按规模优化持久化批量查询、计算输入索引、断点日志和前端节点渲染。

本轮仅更新本报告。临时复核脚本和代码摘要位于 `/tmp/ontology-f2ca-perf-review-*`；没有修改识别实现、运行配置、真实候选或数据库结构。


## 7. 实施与验收（2026-09-07）

### 7.1 已接入的执行路径

| 范围 | 已实施行为 | 失效与正确性约束 |
| --- | --- | --- |
| 证据查找与表格索引 | `DocumentIR.unit()` 按 ID 查找；`TableRecords` 预建逻辑行、表头、证据顺序和段落索引；同一 runner 的表格索引复用 | runner 入参先重新验证结构身份并复制为本轮快照；索引不跨可变 IR 复用；直接调用的 ID 查找能处理列表替换、重排和单元替换 |
| 范围与上下文 | 一次构建范围区间供同一上下文的所有片段校验使用；调度竞争主体时也复用区间；预检与执行使用容量 32 的 envelope 缓存 | 缓存键覆盖任务完整内容、候选内容与修订、文档快照、模型/tokenizer、本体和协议；独立调用额外检查 IR 内容；保持逐字引用、范围排除、竞争主体和绑定校验 |
| token 预算 | 类别菜单和短文本先准确计数完整输入，超限再走原分组/二分逻辑；4096 项 LRU 按 tokenizer 身份和端点复用 | 继续使用部署词表；每次生成前仍核对 `/props`；不使用字符估算；不同模型版本、端点与测试传输隔离；每轮释放自有 HTTP 客户端 |
| 本体 schema | OntologyEngine 内缓存语义快照，并返回调用方独立副本 | 本体 load/close/失败清理和类、数据属性、关系、删除操作失效；锁覆盖读取与变更 |
| 持久化 | 候选和依赖按 500 个 ID 分批预取；构造返回值后再提交；一次有界事务推进候选、修订、审核、分析版本与作业计数 | 已有审核与人工修订继续保留；保留跨作业检查、依赖修订校验、唯一性和分析 CAS；事务失败不发布部分分析状态；数量使用 SQL COUNT |
| 增量结果 | 每个产生新结果的安全批次保存已校验实体/关系；仅覆盖完整证据单元的任务可提前发布，关系的主体、客体及依赖也必须在安全集合中；提交成功后 SSE 发送 `data_revision`、候选数和首条关系保存耗时 | 重叠/局部窗口候选等待最终协调后保存，避免后续否决被已有自动审核结果掩盖；属性继续走整轮返回前的共享绑定校验，不包含于增量回调；后台线程独立持有 SQLAlchemy Session |
| 调度 | 新运行 `document-batch-priority-v4`：有新实体的批次最多提前推进一个文档根关系任务；剩余关系进入公平轮转队列 | 仅显式文档根提前执行；其他主体属性/关系等待实体集合稳定；预算、路径防环与依赖校验保留；旧 v3 断点匹配原输入身份时保留原任务顺序与 ID，不丢弃已完成工作 |
| 章节摘要 | 后台先保存图谱与缓存、发出终态事件，再生成章节摘要 | 摘要失败保留已保存结果；运行令牌和缓存锁防止旧摘要覆盖新一轮；缓存采用原子写入 |
| 页面 | 移除运行中全图遮罩；计时独立到 `RecognitionTimer`；按数据修订最多每 1.5 秒合并刷新 | 运行中刷新仅读取图谱；终态再刷新 coverage；组件 memo、稳定操作回调和按主体索引计算卡片，减少编辑器更新传播 |
| 列表与计算 | 列表向 PDE 计算传入已加载候选；默认返回 run 摘要，`?debug=true` 按需返回原任务及断点 | 审计明细仍保存在服务端；PDE 使用按主体/实例索引；不删除已发布输入与当前候选的分别检查 |
| coverage | 编译缓存覆盖编译器版本、模板、本体和实时契约完整内容；同一请求把已验证编译和候选传给 `start()` | 每次读取仍校验实时契约与冻结编译；决策历史继续参与幂等键；候选复用检查状态修订；区分 `coverage_execute` 与 `coverage_read`，响应给出 `reused` |
| 断点与统计 | 每个任务追加带基底哈希和序号的 JSONL 差量；每 32 次追加或 30 秒生成完整原子快照；暂停/异常强制保存 | 每次追加仍 fsync，恢复只忽略撕裂的最后一行；新快照不重放旧代日志；清理同时删除快照与日志；统计增量累计，删除失败路径的重复保存 |

阶段计时已覆盖文档解析、本体 schema、tokenize HTTP、任务打包、预算预检、上下文、范围区间、候选验证、断点/文件写入、候选持久化、图谱事务与列表/coverage。实际模型请求单列 `model`，`/props` 不计入模型生成时间。`non_model_seconds = wall_seconds - model_seconds`，嵌套阶段不能相加当作总耗时。

### 7.2 本轮实际代码对比

实施前代码来自本轮修改开始时的工作区备份 `/tmp/ontology-perf-before/backend`，实施后来自实际 `backend/app`，不是 Git 提交基线。两者读取同一个暂停状态的源作业（83 条候选）和原 DOCX，分别使用临时本体存储。菜单/切窗访问实际 `/tokenize`；未触发模型生成。候选幂等入库实验运行于只读外层事务，确认无 INSERT/UPDATE/DELETE。

| 测量 | 实施前 | 实施后 | 说明 |
| --- | ---: | ---: | --- |
| 31 个目标区域的上下文，token 缓存后 | 263.22 ms | 37.79 ms | 各 5 次中位数；完整 `ContextEnvelope.model_dump()` 一致 |
| schema 暖态读取 | 649.84 ms | 20.47 ms | 各 3 次中位数；首次构建仍约 1 秒 |
| 136 类菜单整体分组 | 3447.92 ms / 136 次 HTTP | 81.80 ms / 1 次 HTTP | 均为一个菜单；本次各测一次，HTTP 延迟受服务负载影响 |
| 32 个短文本切窗 | 191.13 ms / 135 次 HTTP | 38.72 ms / 32 次 HTTP | 本次各测一次 |
| 83 条已存在候选的幂等保存 | 234.96 ms / 250 SELECT | 154.17 ms / 2 SELECT | 各一次；主要确认查询数量下降，不能据此承诺写入吞吐 |
| evidence 列表函数 | 725.19 ms | 54.86 ms | 各 3 次中位数，包含本体、候选、PDE 与响应字典准备；不含 HTTP 传输 |
| 列表 JSON 大小 | 551,828 B | 201,965 B | 对比时 run 部分 350,658 B → 565 B；新增后续计时字段会小幅变化 |
| 文档解析 | 517.03 ms | 500.34 ms | 各 3 次中位数；无针对解析器的优化，不将波动算作收益 |

候选、图结构和计算结果逐项一致；当前样本仍为 **0 条 PDE 计算结果**。完整函数测量数据保存在 `/tmp/ontology-performance-before.json`、`/tmp/ontology-performance-after.json`，复测脚本为 `/tmp/ontology-performance-implemented.py`。这些结果不是生产 p95，也不是完整识别总时长的加速比例。

### 7.3 验证、迁移与部署

- 后端完整回归：**1178 passed，7 skipped**；被跳过的 PostgreSQL 专用用例随后在隔离数据库运行，**7 passed**。
- 前端：类型检查通过；Node 单测 **26 passed**；生产镜像构建通过；真实浏览器合成数据用例通过，包括 SSE 合并刷新、运行中继续操作图谱、终态 coverage、异议理由、PDE 决策、文档切换、重复实体与深层引用。
- 新增回归约束：IR 可变内容与深复制、envelope 复用/失效、准确计数快速路径、批量查询上限、审核保留、事务回滚、差量断点删除/撕裂尾部恢复、旧调度断点恢复、增量保存与摘要顺序、旧摘要不覆盖新运行，以及编译缓存的版本失效。最终补充重叠窗口先通过、后否决同一候选的用例，确认不会提前发布；相关识别与后台执行回归 **44 passed**，另行运行语义与系统回归 **48 passed**；Ruff 检查通过。
- 数据库：本轮开始时已是 `0029_versioned_calculations`；先保存完整备份 `/tmp/ontology-performance-20260907.dump`，再执行 `alembic upgrade head`，确认 `calculation_decisions` 与 `immutable_calculation_decision` 触发器存在。隔离库另外验证了 **0028 → 0029** 的实际升级与约束行为。
- 部署状态：已构建并重新加载含最终重叠窗口保护的后端；新运行使用 `document-batch-priority-v4`。最终部署前让另一在运行文档安全保存断点（74 个任务、153 次模型调用），部署后通过原 v3 兼容路径恢复，输入身份和任务/模型计数保持一致；SSE 返回 `data_revision: 28`、257 条候选。部署验证记录为 `/tmp/ontology-performance-final-deploy.json`，断点备份为 `/tmp/ontology-performance-predeploy-final-checkpoint.json`。目标模板仍保留原暂停状态与 83 条候选。

在线验证（重启后的实际 HTTP）：健康检查 200；目标模板 evidence 接口首次约 1167 ms（含本体首次准备），两次暖态约 109 / 128 ms，均返回 83 条候选，`run` 默认不含任务明细；`debug=true` 能读取原 4 条任务记录。最终保护补丁部署后再次读取约 186 / 135 ms，候选数量和修订 14 保持一致。真实 HTTP JSON 约 191 KB（不同于上表 Python 默认 JSON 编码空白下的 202 KB）。

该模板的 coverage 返回 200 与明确的 `CONTRACT_NOT_FOUND: unresolved:ontology`，约 148 / 107 ms。这说明缺表问题已排除，但该模板仍缺少业务契约配置；不能把这两个数当成正常 coverage 编译/命中的性能。正常编译、幂等命中和 PDE 决策导致的失效已经由隔离集成用例覆盖。本轮没有擅自为模板选择或发布业务契约。

本轮隔离库从空库重放所有历史迁移时，发现既有 `0007` 与早期迁移的 `create_all` 对 `generated_reports` 重复建表；随后改用当前结构的空数据副本回到 0028 边界，验证本次相关的 0029 升级。该历史初始化问题不影响现有数据库的升级结果，未在这次性能改动中修改旧迁移。

尚未给出的指标：真实完整模型识别的首条关系延迟、非 LLM 总占比、整轮总时长和浏览器帧耗时。本轮用确定性模型夹具验证调度和增量执行语义，不以真实模型重跑消耗代替性能隔离实验。折叠节点的虚拟化仍属于后续大图规模工作，本轮先落实计时隔离、memo 与索引。
