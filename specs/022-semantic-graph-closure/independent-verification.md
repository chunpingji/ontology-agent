# 真实模型集成与独立诊断核验

核验时间：2026-09-09 00:07 UTC。范围为 `integration-02/03` 与 `diagnostic-01` 至
`diagnostic-09` 已落盘制品（07 为零调用预检失败），另核验 prepared-04/05 的身份
稳定性。本次整理只读取文件和以 SQLite
`mode=ro` 核对私有调度表，
没有重复调用模型，没有改写任何实验制品。原文、请求/响应正文及凭据不复制到本文件。

**最终 v4.3 的有界真实集成运行已形成两条有效边，并实际执行了子主体属性的发现与独立验证；完整图谱质量仍未验收。** `integration-02`
完成了进程收尾，图谱仍为 `incomplete/partial`，没有有效肯定关系；`diagnostic-06`
进一步定位了文档根主体的原文引用契约缺陷。v4.2 的 `diagnostic-08` 已遵守根主体
`subject_support=[]`，但又暴露谓词等支持引用数组被省略的问题，候选仍未通过证明门；
支持引用数组显式必填后的 `diagnostic-09` 已取得真实谓词引用和有效边，随后
`integration-03` 验证了真实动态前沿展开。子主体属性仍未决，整体图谱保留
`partial/incomplete`。前轮失败原样保留，局部成功不能替代全图执行、专家裁决或质量对照。
**T017 保持未验收。**

## 输入、版本与实验范围

本页各实验使用同一份获准 DOCX 或其已冻结任务上下文。“独立”指新运行身份和私有调度
记账，不表示已有独立文档数据集、专家金标或统计质量证据。

| 项目 | 已核验值 |
|---|---|
| 原件 SHA256 | `e94822808601e73f4ba4c715c664812d4a01e42f0e4e84a6b08a3653b915daf7` |
| 本体语义 hash | `2d4c3dfcdec9340b6125781b3a2915c447e47f7286fbf5d99fa076886f905d26` |
| `prepared-02` runtime hash | `e366aca5261f73d355d4b12e3115daf02dd72087177de219a060617f0e767b0d` |
| `integration-02` run ID | `eval-022-independent-cpu-02` |
| `integration-02` fingerprint | `125427bab693a8e3c215007ac5d26fe835a8317c688883271b5add55bf1b494c` |
| 主模型 | `Qwen3.6-35B-A3B`，revision `0b21525e972670ed59e1812e170b27c26355381f0656ecc4e25617ece7dac58b` |
| 主模型适配器 | `ontology-guided-model-adapter-v4.1-independent`；tokenizer 为 `llama_server` |
| 实验 scope | 根类型 `CMCReport`，focus path 为 `usesEquipment`，不是全图任务 |
| 执行额度 | `max_tasks=8`，每记录主调用额度 6，最多 4 hops，单请求 600 秒，timeout retry 0，软截止 1,800 秒 |
| 排序配置 | semantic，pool size 8，batch size 4；本次没有摘要生成，独立摘要耗时 0 |

版本和配置依据：[prepared-02 manifest](../../evaluations/022-independent-upload-23c872fb-20260908/prepared-02/manifest.json)、
[integration-02 manifest](../../evaluations/022-independent-upload-23c872fb-20260908/integration-02/result.json)。
`diagnostic-01` 至 `03` 使用较早冻结 runtime
`105f7c204af948fea5f6402bf0d0cf68a8c0a1798c1d70b86ea031ee2433e74c`；
`04/05` 保持其冻结依赖和对应上下文，但换用 v4.1 适配器；`06` 使用 `prepared-02`。
因此这些诊断不是同配置无改动的多轮质量对照。

## integration-02：调用完成与语义完成分开记账

运行收尾用时 **799.736 秒**，其中解析 **1.769 秒**。调度共 **129 次请求**，
115 次排序操作为 `completed`，14 次主模型操作为 `complete`，没有缺失 run/task 归属。
这两个成功状态是对应客户端的传输记账状态，不能证明原文事实成立。

| 阶段 | 实际请求 | 已测输入 token | 已测输出 token | 调度 request 秒数 |
|---|---:|---:|---:|---:|
| 排序 tokenizer | 88 | 0（该接口记账值） | 未提供 | 40.844 |
| embedding | 23 | 24,642 | 未提供 | 113.738 |
| 联合编码精排 | 4 | 27,610 | 未提供 | 118.498 |
| 发现 discovery | 8 | 39,656 | 5,605 | 246.314 |
| 独立验证 verification | 6 | 45,518 | 4,571 | 252.205 |
| 合计 | **129** | **137,426** | **10,176** | **771.599** |

输入和输出从调度原始字段分别归并：排序读取 `input_tokens`，chat 在缺少该字段时读取
`prompt_tokens`；生成输出读取 `completion_tokens`。本轮输入未知数为 0；115 个排序
请求未提供生成型输出 token 字段，保留为未知输出请求，而不是据此补造为零个生成
token。tokenizer 的 token 记账为零不代表计算免费，其 40.844 秒调用耗时仍计入。
队列耗时合计 **1.212 秒**。request 耗时之和不是进程墙钟耗时，也不换算为货币费用。

排序预扣 **52,252 token / 27 次模型请求**，对应 88 个 embedding 输入和 16 个精排
输入对；tokenizer 的 88 次操作另有完整调度记录。一次完整 semantic epoch 已提交，
未降级，池包含 8 条记录及双意图 16 对输入。此池不是全文 86 条记录全部接受了精排。
主调用预扣 **14 次**，与 8 discovery + 6 verification 一致，结果待核实调用数为 0。

| 语义/图谱项目 | 已冻结结果及解释 |
|---|---|
| 适配器 inspect | 8 次返回，无适配器异常；其中有 `record_not_checked` 等未完成语义结果，不能称为 8 次事实识别成功 |
| 实际涉及原始记录 | 6 条；重复检查使 inspect 次数达到 8，不把重复检查增加为新覆盖 |
| 冻结记录全集 | 86 条；阶段计划为 phase 1：12，phase 2：74 |
| 原文执行状态 | 4 examined、2 attempted_incomplete、80 unattempted；实际不同记录阶段计数为 5 / 1 |
| 未决 | `unresolved_claims=2`；停止原因为 `attempted_incomplete` |
| 图节点 | 9 个，包含文档根及候选节点；节点数不等于已验证实体数 |
| 图关系 | 8 条候选边：5 unsupported、3 undetermined；8 条均 `structural_valid=false`、`model_supported=false`、`policy_eligible=false` |
| 图属性与有效肯定路径 | 属性 0；无有效肯定关系，不能宣称存在通过证明门的正向路径 |
| 整体状态 | `execution_status=finished`，但 `completion=incomplete`、`artifact_status=partial` |

`unsupported` 是当前候选未获支持，不能当作已经证明的否定事实；80 条未尝试记录也
不能当作全文否定。以上结果以
[run.json](../../evaluations/022-independent-upload-23c872fb-20260908/integration-02/run.json)、
[costs.json](../../evaluations/022-independent-upload-23c872fb-20260908/integration-02/costs.json)、
[scheduler_requests.json](../../evaluations/022-independent-upload-23c872fb-20260908/integration-02/scheduler_requests.json)
为依据。

## diagnostic-01 至 06：单任务排障，不累计成完整图谱运行

下表耗时来自 `scheduler_requests.metrics.request_seconds`；诊断 summary 未提供统一的
进程墙钟字段，因此不以请求耗时冒充总墙钟。

| 诊断 | 真实发现 / 验证调用 | 输入 / 输出 token | request 秒数 | 程序结果与语义结果 |
|---|---:|---:|---:|---|
| [01 summary](../../evaluations/022-independent-upload-23c872fb-20260908/diagnostic-01/summary.json) | 1 / 0 | 3,384 / 12 | 14.115 | inspect 返回；`no_candidate_observed`，无候选。不能据此复现或排除原运行失败 |
| [02 summary](../../evaluations/022-independent-upload-23c872fb-20260908/diagnostic-02/summary.json) | 1 / 0 | 2,348 / 152 | 13.269 | HTTP 完成，`RecognitionModelFailure → ValidationError`；关系提议缺少有原文引用的类型化对象，未进入独立验证 |
| [03 summary](../../evaluations/022-independent-upload-23c872fb-20260908/diagnostic-03/summary.json) | 1 / 0 | 2,355 / 163 | 14.024 | 与 02 相同的提议结构失败类别；费用仍完整计入 |
| [04 summary](../../evaluations/022-independent-upload-23c872fb-20260908/diagnostic-04/summary.json) | 1 / 0 | 2,398 / 17 | 11.082 | 对应 02 的相同上下文改用 v4.1；返回 `no_candidate_observed`，无候选 |
| [05 summary](../../evaluations/022-independent-upload-23c872fb-20260908/diagnostic-05/summary.json) | 1 / 0 | 2,405 / 17 | 10.783 | 对应 03 的相同上下文改用 v4.1；返回 `no_candidate_observed`，无候选 |
| [06 summary](../../evaluations/022-independent-upload-23c872fb-20260908/diagnostic-06/summary.json) | 1 / 1 | 12,649 / 693 | 60.761 | 两次模型请求均完成；inspect 返回 `record_not_checked`、`complete=false`，未形成有效候选 |
| 合计 | **6 / 1** | **25,539 / 1,054** | **124.034** | 共 7 次真实请求，不能增加为六轮完整图谱质量评测 |

01、04、05 没有异常，但它们返回空候选，不能用来证明非空候选的结构/语义验证已经
通过。02/03 的安全错误字段是 `proposals[0]` 的 `value_error`，对应错误为
`relationship proposal requires a quoted typed object`；原响应只提供部分关系描述字段，
没有所需对象端点。错误诊断见
[02 errors](../../evaluations/022-independent-upload-23c872fb-20260908/diagnostic-02/errors.json) 和
[03 errors](../../evaluations/022-independent-upload-23c872fb-20260908/diagnostic-03/errors.json)。

六个诊断均使用独立调度 SQLite，仅含 `local_model_pools` 和 `local_model_requests`。
01–05 各保存 1 次主调用预扣，06 保存 2 次，与各自数据库和导出的调度请求数一致；
所有请求均有运行与任务归属。诊断请求的额外队列耗时合计 **0.564 秒**。

## diagnostic-06 定位的文档根主体引用缺陷

发现阶段的候选对象引用属于允许的原文 fragment，且逐字匹配唯一一次；该锚点通过。
独立验证阶段则把程序文档根的实体 ID 当成 `evidence_id`，并以本体类局部名充当原文
引用。这不是已输入的 27 个原文 fragment 中的证据 ID，引用也不在其原文中。
`_anchor` 正确拒绝该引用，机器原因码为
`source_quote_not_unique_in_allowed_context`，候选因此保持未完成。

诊断依据为
[06 findings](../../evaluations/022-independent-upload-23c872fb-20260908/diagnostic-06/findings.md)、
[06 anchor-checks](../../evaluations/022-independent-upload-23c872fb-20260908/diagnostic-06/anchor-checks.json)
及 [06 outcome](../../evaluations/022-independent-upload-23c872fb-20260908/diagnostic-06/outcome.json)。
两阶段原始请求/响应继续留在受控私有目录，不复制到规范或报告中。

修复方向是让文档根通过 `DocumentContext/root_ref` 的程序身份绑定，在根主体的验证
契约中明确不要求伪造 `subject_support` 原文；非根主体仍须提供可回放的原文归属证明。
不能把程序身份转换为原文证据，也不能放宽引用来源权限或逐字匹配来消除失败。
截至该次诊断，修复及后续真实补验仍在进行；06 本身没有证明修复已经生效。08 的
后续结果单列如下，不回写 06 的未完成状态。

## 后续 prepared-03 与 diagnostic-07 的比较界限

后续已新建 [prepared-03 manifest](../../evaluations/022-independent-upload-23c872fb-20260908/prepared-03/manifest.json)，
冻结 v4.2 适配器，runtime hash 为
`a190eaf0b4a1e9422131aa6a885b975c5d38fd33e046cf468d363f58c3b3e4b0`，
`evidence_max_tasks=4`。它的原件、IR、analysis ID 和 TTL 文件树 hash 与 prepared-02
一致，但运行代码、适配器身份及任务额度已有变化，不能称为相同模型适配器/相同预算
的质量对照；使用相同主 LLM revision 不会让适配器身份自动相等。

两份 preparation 的 TTL 文件树 hash 均为
`f2f55d8a715dddf8ea522f210f82a3a7a40a87cd0aa421fa71e70983f1785509`，
但 prepared-03 的 `ontology_semantic_hash` 变为
`4fdd4b099dad613f4e29c0b66523e53ffdcef8cc8a22a4d7841df97cdced0ae0`。
只读比较发现 24 个 class 的差异位于 `declared_properties`、`declared_relationships`
数组及派生 `source_hash`；仅排序这两种声明菜单数组并排除派生 hash 后，快照内容
一致。此比较解释了本次结构化快照的排序漂移，没有修改快照，也不能绕过消融协议
要求的精确身份一致性。快照 ID 和文件 hash 的变化仍须原样记入新运行。

[diagnostic-07 日志](../../evaluations/022-independent-upload-23c872fb-20260908/diagnostic-07.log)
记录的是旧预检 `adapter.model_identity == source_run.model_identity` 断言失败。
核对 [07 replay 脚本](../../evaluations/022-independent-upload-23c872fb-20260908/diagnostic-07/replay.py)
可见，该断言在私有 SQLite 建立和 `adapter.inspect` 之前执行；目录仅有脚本，未产生
调度库或模型响应。因此该次是 **零模型请求的预检失败**，不加到上文 01–06 的调用
费用，也不能记为模型语义失败或修复成功。后续诊断应同时记录 source/current 模型
适配器身份，而不是强迫新适配器等于旧身份。

## diagnostic-08：根主体契约修正生效，支持引用缺失仍被阻断

[08 summary](../../evaluations/022-independent-upload-23c872fb-20260908/diagnostic-08/summary.json)
记录使用 `prepared-03` 的 v4.2 runtime，但明确复用
`prepared-02/ontology_snapshot.json` 和 06 的相同源任务上下文，不是运行了
prepared-03 新快照下的完整图谱。源适配器模型身份为
`283e74dff031515254b2ff72f09b30d17e61d6b2daf3f5fbd009533b8c583331`，
当前身份为 `5a141dcba9c1cb56036c4b9e2c260d15a79fd02f5077b02ec7dada7792beb6d2`；
两个身份都已保留，不要求它们相等。

| 阶段 | 真实请求 | 输入 / 输出 token | request 秒数 | queue 秒数 |
|---|---:|---:|---:|---:|
| discovery | 1 | 5,729 / 220 | 26.676 | 0.104 |
| verification | 1 | 7,296 / 329 | 34.636 | 0.009 |
| 合计 | **2** | **13,025 / 549** | **61.312** | **0.113** |

两次请求均为 `complete`，主调用预扣 2 次，与私有 SQLite 及
[08 scheduler_requests](../../evaluations/022-independent-upload-23c872fb-20260908/diagnostic-08/scheduler_requests.json)
一致，输入输出均有统计。没有新增排序请求。以上仍为调度耗时，不冒充诊断总墙钟。

独立验证响应的 7 个 verdict 全部显式为 `supported`，根主体的 `subject_support=[]`
符合 v4.2 新契约。但 `predicate_support`、`condition_support`、`counterevidence_support`
均被省略：本次响应只提供了 schema 要求必填的字段，缺少关系桥接与相应适用/反证
核验所需的引用制品。模型给出肯定 verdict 不能替代原文支持。

[08 outcome](../../evaluations/022-independent-upload-23c872fb-20260908/diagnostic-08/outcome.json)
保留 1 个候选节点、1 条候选关系、1 个 proof payload 和 5 个 decision payload，
`semantic_outcome=undetermined`、`reason_code=record_undetermined`。
proof 的 `predicate_support_refs` 为空，`bridge_entailment` 决策为 `undetermined`；
候选边 `structural_valid=false`、`model_supported=false`、`policy_eligible=false`。
虽然本次 inspect 返回且 `complete=true`，这些制品没有形成有效事实或正向图谱路径。

这次真实运行验证了证明门不会因模型的 7 个肯定 verdict 就跳过缺失来源。下一步协议
修复为支持引用数组显式必填，以区分明确给出空数组与字段未生成；必填数组仍不等于
引用内容有效，原文回放和证明门继续适用。08 完成时 v4.3 尚待真实补验；后续 09
结果独立记录如下，不回写 08 的未决状态。

## prepared-04/05：最终 runtime 的跨 hash seed 身份核验

最终 v4.3 和本体声明菜单排序修复冻结于
[prepared-04](../../evaluations/022-independent-upload-23c872fb-20260908/prepared-04/manifest.json)；
另以不同 `PYTHONHASHSEED` 重新执行 preparation 形成
[prepared-05](../../evaluations/022-independent-upload-23c872fb-20260908/prepared-05/manifest.json)。
两次 seed 分别为 4、5，均 `evidence_max_tasks=4`，主模型 revision 保持前述值。

[preparation-stability.json](../../evaluations/022-independent-upload-23c872fb-20260908/preparation-stability.json)
登记的七项 identity 全部一致。本次独立重读两份 manifest，并核验本体快照文件字节
完全一致、其 SHA256 均匹配各自 manifest；对两份 `runtime/app` 按 preparation 的
相同 `.py` 文件树算法重新计算，runtime hash 也均匹配：

| identity | 两次共同值 |
|---|---|
| `document_hash` | `e94822808601e73f4ba4c715c664812d4a01e42f0e4e84a6b08a3653b915daf7` |
| `ir_hash` | `858dddbb762217ede057ed85e659bb6d0349fc90e0c936fbf42756859c882134` |
| `ontology_hash`（TTL 文件树） | `f2f55d8a715dddf8ea522f210f82a3a7a40a87cd0aa421fa71e70983f1785509` |
| `ontology_semantic_hash` | `25a0576f6f6d0efb002c44c5449dfba4329144da3868f9b6234a4038d3d68b6c` |
| `ontology_snapshot_id` | `24862a42ba8770c1e3a5786e2d16fd0d831e31dd159e63d739603a854d9a73db` |
| `ontology_snapshot_file_hash` | `38a1b6d5b3bfb573aac3b30c611d1d18d9df8465588fa7a49b467a50bdfd8311` |
| `runtime_hash` | `52a6ada08de16a28a224be09e372014b1534f51dabbd776cb900539abb50e461` |

额外核对 `analysis_id` 也相同。该结论严格限于上列身份：兼容 `schema.json` 路径的
`schema_hash` 不在这七项稳定门中，04/05 的该字段仍不同，不能声称整份 manifest
或所有 schema 身份完全相同。两次真实 preparation 是身份稳定性证据，不是两轮
模型识别或质量验收。

## diagnostic-09：支持引用完整后取得真实有效候选边

[09 summary](../../evaluations/022-independent-upload-23c872fb-20260908/diagnostic-09/summary.json)
记录 `ontology-guided-model-adapter-v4.3-independent`，runtime 为上列最终冻结版本，
模型适配器身份为 `4aa883b9193693ca3e939427bfc40d55b43e859e7213dc781ca3d70eee33fb06`。
它仍复用 `prepared-02/ontology_snapshot.json` 和 06/08 的同一源任务上下文；新适配器
身份与源身份分别保存，不把版本变化藏在“相同模型”表述中。

| 阶段 | 真实请求 | 输入 / 输出 token | request 秒数 | queue 秒数 |
|---|---:|---:|---:|---:|
| discovery | 1 | 5,729 / 213 | 26.383 | 0.109 |
| verification | 1 | 7,332 / 455 | 36.068 | 0.009 |
| 合计 | **2** | **13,061 / 668** | **62.451** | **0.118** |

两次 HTTP 请求均完成，私有 SQLite、
[09 scheduler_requests](../../evaluations/022-independent-upload-23c872fb-20260908/diagnostic-09/scheduler_requests.json)
和调用预扣均为 2。验证响应完整提供四个支持数组：`predicate_support` 有 1 项，
根主体 `subject_support=[]`，`condition_support=[]`、`counterevidence_support=[]`
也显式存在。发现对象引用及独立验证的谓词引用都属于允许的原文证据，且逐字唯一
匹配，见 [09 anchor-checks](../../evaluations/022-independent-upload-23c872fb-20260908/diagnostic-09/anchor-checks.json)。

[09 outcome](../../evaluations/022-independent-upload-23c872fb-20260908/diagnostic-09/outcome.json)
为 `record_supported`，包含 1 个候选节点、1 条边、1 个 proof payload、5 个 decision
payload；桥接等五项 decision 均为 `supported`，proof 有 1 项谓词支持引用。
边的 `decision_status=supported`，`structural_valid`、`model_supported` 和
`policy_eligible` 均为 true。因此，**这次单任务真实运行已形成系统可接受的有效边**。
这证明修正后的根主体绑定和支持引用契约能够走通两阶段证明门；它不是人工确认，
也没有给出全图 precision/recall、多跳完整率或跨文档质量结论。

09 没有活动集成 runner 的 `result.output_hashes` 清单，故不宣称存在该清单的校验。
本次只读核验另外记录以下审计 SHA256，供后续复核该诊断的汇总、结果和费用制品：

| diagnostic-09 文件 | 本次记录的 SHA256 |
|---|---|
| `summary.json` | `fd9cf62888aae682f9307309409d0d0e86f4df188f7bddcfa16bf56e64363cef` |
| `outcome.json` | `e7aa734a0e102320822531a14ee237eb490c00de126dab3d771fdf428436c3b8` |
| `scheduler_requests.json` | `fe3f2144af516c32e6b275783732f9e983a995be0044a5cbddcf3c37522d836c` |

## integration-03：两条有效关系与实际子主体任务

[03 manifest](../../evaluations/022-independent-upload-23c872fb-20260908/integration-03/result.json)
绑定 prepared-04 的最终 runtime、本体快照和原件，run ID 为
`eval-022-independent-cpu-03`，fingerprint 为
`ab1fa0b72716cda75aeab8e6a3eada6cbec87316d9f9d6133d64c070f8023e77`。
scope 仍是根 `CMCReport` 的单跳 `usesEquipment` focus path；额度为 `max_tasks=4`、
每记录主调用 6、软截止 900 秒、单请求 600 秒、timeout retry 0。
本轮墙钟 **618.611 秒**，解析 **1.693 秒**，最终收尾为 `finished`。

[03 run](../../evaluations/022-independent-upload-23c872fb-20260908/integration-03/run.json)
的四个 `task_outcome` 证明动态展开不只是建立了计划，已实际派发子主体任务：

| 顺序 | 主体及任务类别（不含原文对象标签） | phase / hop | 真实主调用 | 结果 |
|---|---|---:|---:|---|
| 1 | 文档根 `CMCReport.usesEquipment` 关系 | 1 / 0 | 2 | undetermined |
| 2 | 文档根 `CMCReport.usesEquipment` 关系 | 2 / 0 | 1 | 无候选，未作全文否定 |
| 3 | 文档根 `CMCReport.usesEquipment` 关系 | 1 / 0 | 2 | supported；该次含多条候选，其中两条有效 |
| 4 | 非根主体 `Reactor.equipmentID` 属性 | 1 / 1 | 2 | undetermined；属性未接受 |

有效边分别为 `CMCReport —usesEquipment→ Reactor` 和
`CMCReport —usesEquipment→ FilterPress`；两条均 `decision_status=supported`，
`structural_valid`、`model_supported`、`policy_eligible` 均为 true。各边均找到精确
`proof_ref` 对应的 proof 和五项 decision，五项均为 `supported`；各 proof 有一个
谓词支持引用。本次额外将这两个 `EvidenceAnchor` 对 `prepared-04/ir.json` 执行
`DocumentIR.resolve`，均能回放非空原文，未打印或复制原文。

图保留 9 个节点、8 条候选边、1 条候选属性。除两条有效边外，边中 1 条 undetermined、
5 条 unsupported；唯一属性为 undetermined，`policy_eligible=false`。
`progress.supported=1` 是支持任务的计数，不是有效边数；有效边数 2 来自最终边制品
的资格字段核验。proof 或 decision 制品的存在也不自动代表候选已被接受。

只有两个已获得有效关系的设备主体形成子槽位，共 **17 个计划**：根关系 1 个、
`Reactor` 属性 8 个、`FilterPress` 属性 8 个。每个主体/谓词槽位绑定同一原件的
86 条记录，因此 `records_planned=1,462` 是 **17 × 86 个主体—谓词—记录任务**，
不是文档有 1,462 条不同原始记录。根槽位已检查 3/86，`Reactor.equipmentID` 已检查
1/86，其余 15 个子属性槽位尚未检查；总计 4 examined、0 attempted_incomplete、
1,458 unattempted，仍有 2 个未决声明。

实际 inspect/任务次数达到 `max_tasks=4` 后保留了未尝试前沿。冻结结果中的
`stop_reason=attempted_incomplete` 是当时过宽的回退原因标签；不能据此把 0 条
attempted_incomplete 写成有失败记录。当前应用随后仅修正这个停止原因的展示/分类，
不改变本次执行与证据结果。本轮 runtime 冻结在该标签修正之前，历史 JSON 仍保留
原值，不用修正后的代码或新标签回写旧结果，也没有因此另启真实运行。

本轮确实走通“根关系系统接受 → 局部主体展开 → 子主体属性排序 → 真实发现/独立验证”，
但只产生单跳有效关系，子属性未通过且绝大多数槽位任务未尝试。
`completion=incomplete`、`artifact_status=partial` 是必要限制，不能宣称完整多跳事实
质量或全图覆盖通过。

### integration-03 成本与制品校验

| 阶段 | 实际请求 | 已测输入 token | 已测输出 token | request 秒数 |
|---|---:|---:|---:|---:|
| 排序 tokenizer | 90 | 0（该接口记账值） | 未提供 | 40.864 |
| embedding | 24 | 25,430 | 未提供 | 121.327 |
| 联合编码精排 | 8 | 38,372 | 未提供 | 155.863 |
| discovery | 4 | 18,017 | 1,632 | 97.821 |
| verification | 3 | 27,003 | 3,837 | 167.008 |
| 合计 | **129** | **108,822** | **5,469** | **582.883** |

主调用预扣 7 次，与 4 discovery + 3 verification 一致，待核实调用为 0；
122 个排序请求和 7 个主请求均传输完成，run/task 归属缺失为 0。
排序预扣 **63,802 token / 32 次 embedding 或精排请求**，90 个 tokenizer 操作另
计调度成本；有 86 个 embedding 缓存命中和 0 次技术重试。根关系与子属性各提交一个
完整 semantic epoch，每池 8 条记录、双意图，合计精排 32 对，两个 epoch 均未降级。
新增子主体确实触发了新查询 embedding 和子属性精排，未把这些费用省略。

费用依据：[03 costs](../../evaluations/022-independent-upload-23c872fb-20260908/integration-03/costs.json)、
[03 scheduler_requests](../../evaluations/022-independent-upload-23c872fb-20260908/integration-03/scheduler_requests.json)。
队列耗时合计 **1.313 秒**；输入未知数为 0，122 个排序请求的生成型输出字段未提供，
仍分列未知输出。不得将本轮较小任务额度的费用与 integration-02 直接解释为同预算的
效率提升。本次重算 manifest 列出的 **12 个最终输出文件 SHA256，全部匹配**；
私有 SQLite 的 129 条请求也与 JSON 导出一致。

## 成本完整性与验收限制

本页已完成范围共 **269 次真实调度请求**，已测输入 **297,873 token**、已测生成输出
**17,916 token**。它只合并 `integration-02/03`、01–06 和 08–09 的诊断，07 零调用不增加
费用；不包含 integration-01、
CPU 排序专项或其他后续运行，不称为整个特性的总成本。

本次只读核验逐项重算 `integration-02/03/result.json` 各自所列的 **12 个输出文件 SHA256**，
合计 24 项全部匹配；并核对十个私有数据库的请求数与对应 JSON 导出一致。制品完整性说明记录
可复核，不自动证明模型回答正确或图谱质量通过。

仍缺独立专家完整裁决、独立文档集、预注册质量/成本阈值，以及同输入的三轮真实 A–D
固定池和动态前沿对照。integration-02 的候选边全部不具备接受资格，不能把空有效图
当作高精度结果；diagnostic-09 的单条有效边和 integration-03 的局部成功也不能替代完整图谱验收。根主体和支持引用
契约修复后继续用新制品保留独立验证、原文证明与正向图谱行为；本页不回写
历史实验来补齐这些结果，也不据此勾选 T017。
