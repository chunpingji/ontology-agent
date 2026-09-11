# 自适应剪枝与增强检索契约（设计版）

日期：2026-09-11。状态：P0 契约设计，尚未实现或通过启用验收。

本契约对应[专题方案](../../../docs/剪枝和增强语义检索视图方案.md)及本特性的 AR 需求，补齐记录级搜索状态、准入、空结果、恢复、费用和公开诊断。它不追溯修改旧运行的冻结行为。P0 门未全部关闭前，仅允许不改变候选顺序、模型请求、任务、覆盖和停止行为的 instrumentation 原型。

## 1. 版本与兼容

| 项目 | 旧运行 | 新设计 |
| --- | --- | --- |
| 启发式策略 | `heuristic-first-v1/v2/v3` 原逻辑 | `heuristic-first-v4`，显式加入策略白名单与新运行工厂 |
| 查询规则 | 原冻结版本 | 首版仍可使用 `ontology-labels-and-registered-aliases-v2`；视图变化独立版本化 |
| 搜索快照 | v1 不写 `resume_state` 且不支持恢复；v2/v3 保持原形状及严格相等校验 | 仅 v4 写 `resume_state.schema_version=1` 和新处置/结果引用 |
| 自适应政策 | 不存在时沿用原路径 | 冻结 `adaptive-retrieval-v1`，含模式、视图版本、校准版本、预算及继续规则 |
| 公开诊断 | 字段缺失代表未采集 | 可选 `retrieval_diagnostics.schema_version=1`；不为旧运行补写持久化默认字段 |

`observation` 模式只记录观测；`enforce` 模式允许新搜索行为和真实剪枝，两者进入运行指纹。视图增强、阈值、候选组映射、准入及搜索结果 schema 均参与各自依赖 hash。未知版本拒绝恢复；禁止给 v3 快照加默认键后通过归一化放宽原相等校验。

新运行冻结新契约，旧运行继续使用原策略及测试预期。不得通过配置热切换改变在途运行的冻结含义。新策略依赖现有证据修复/增量执行域；不支持该执行域的入口不得静默接受 v4。

## 2. 记录全集、处置与覆盖

### 2.1 六类搜索处置

每个槽位保留现有记录全集 U。槽位身份包含主体版本、谓词、源和本体依赖；查询依赖变化产生新 generation，保留旧历史。对当前 generation，每个 record 有一个有效搜索处置；意图和视图级评估通过引用挂在该处置下。

| 处置 | 定义 | 允许的下一步 |
| --- | --- | --- |
| `unevaluated` | 尚未完成当前所需门评估 | 评估，或按既有启发式规则准入；评估途中由持久化回执记录水位 |
| `evaluated_pending_disposition` | 已有门/精排结果，尚待确定或应用准入；不等同于已精排 | 从已有 observations/回执派生决策；需要下一道门时复用已有结果 |
| `soft_pruned` | 仍未核验，多路低相关且无保护理由，当前依赖下暂缓 | 仅在明确触发和预算内重新激活 |
| `reactivatable` | 已记录有效触发，等待重新处置或有限探索 | 优先复用已有得分与决策，只重新执行失效或未完成的部分 |
| `admitted` | 已进入精确识别批次 | 按原执行/重试/补证契约处理，不重新作为首次发现候选评分 |
| `dependency_exhausted` | 对该 generation 已无合法的新增检索动作，并有确定性原因或完整工作记录 | 新依赖/显式继续时重新判断；不能用技术失败或单纯低分替代原因 |

`dependency_exhausted` 不吞并软剪枝：低相关仍记 `soft_pruned`；范围或主体失效、等价已完成工作等记录明确原因。超长视图不可用不等于依赖已耗尽。新 generation 先从同一逻辑槽位的准入/覆盖重建 admitted，其余无历史处置的记录才默认 unevaluated；已有核验结果不因后续补证筛选改为未尝试。若主体版本变化创建新计划，其新覆盖机会与原计划分别记账，不能清空旧历史。

这些是搜索处置，不能加入覆盖计数相加。继续保留：

```text
planned = examined + incomplete + unattempted
current_soft_pruned ⊆ unattempted
0 <= records_soft_pruned <= records_unattempted
```

`admitted` 尚在队列时也可能属于 `unattempted`，因此搜索处置与覆盖不是同一个分区。组命中、读取上下文、向量化和精排均不授予 examined。

### 2.2 拆开 deferred 的四个调用职责

`deferred_record_ids = U - admitted` 在旧版保留；v4 可作为兼容统计，但禁止驱动以下工作：

- 下一轮付费候选来自按阶段划分的 `needs_evaluation_ids(stage)`：未完成该阶段的记录及有有效触发的记录，只剔除本阶段已有可复用结果；通过前门但未精排者仍可进入精排阶段。
- `pending_disposition_ids`：先消费已有 gate/epoch 结果，这个应用动作不调用模型；通过前门者冻结 `next_stage_ready` 水位，后续阶段另行按预算派发。该水位不是第七类 record 处置。
- `exploration_eligible_ids`：未探索候选及有显式抽样/继续触发的暂缓项，单独保留页预算。
- 饱和与覆盖判定分别读取可行动集合、在途工作、补证和覆盖账本，不能仅检查 deferred 是否为空。

不持久化六份重复 record 列表。以已有准入/epoch/决策引用和稀疏处置变更重建投影；缓存只绑定精确已提交 head。

## 3. 有类型的搜索结果和活性

v4 的准备接口返回有类型的 `SearchPreparationResult`（设计名），同步和异步执行适配器均必须消费所有分支。旧 `prepare_next_epoch() -> RankingEpoch | None` 保留在旧路径；v4 适配层不能将裸 `None` 当作“继续等待”。

以下是 owner 应用侧的完成结果。私有准备线程只交准备产物及回执，owner 经过既有持久化屏障后封装 committed 引用，不将数据库 Session/提交职责下放工作线程。

| 结果 | 必备引用/原因 | 状态推进 |
| --- | --- | --- |
| `gate_completed` | 非空前置门结果和下一阶段通过集 | 持久应用 next_stage_ready 后推进下一道门；不授予 H2 识别准入 |
| `ranked` | 完整 committed epoch，以及当前依赖 | 先派生并提交 AdmissionDecision，再准入子集；子集为空也消费此结果 |
| `filtered_empty` | 完整 gate evaluation 引用；全部受评估成员的处置 | 不构造空 epoch；应用处置，选择其他新候选、有限探索或饱和 |
| `no_new_candidates` | 已用池/门处置/候选范围的 hash，明确耗尽原因 | 先处理待处置记录及可重激活项，再探索或饱和 |
| `view_unavailable` | 各视图可用状态、缺失范围与成本回执 | 使用其他可用视图或确定性/原文路径；有必要技术阻塞时暂停 |
| `disabled` | 冻结配置明确关闭可选能力 | 按确定性路径推进，不把主模型失败当关闭 |
| `blocked` | 预算、服务、存储、取消或版本错误及回执 | 沿既有技术暂停/失败/失效路径，保留未决和费用 |

异步任务尚未结束属于执行适配器的 pending，不伪造已完成搜索结果。正常 `used` 集合继续排除 committed epoch 的记录，跨 epoch 防重机制不得删除。

新增前置门评估记录必须留下处置，即使没有进入 epoch，也不会在同一依赖下反复烧完四轮。初始批上限仍可采用 8/16/32/64；以冻结的 `expansion_attempt_id` 绑定一轮范围及全部门，各门共享这一轮。完整 ranked 或该轮完整前置全拒消费一次轮次；gate_completed 中间水位、disabled/blocked/view_unavailable、无新增候选和恢复重放不递增。轮次与 epoch 数分开，没有新候选时提前走结果分支，不要求制造四个空轮。

每次消费完成结果，必须至少发生一项：提交新的处置/准入、推进有界游标、切换阶段、登记技术阻塞或达到搜索饱和。禁止重复相同 `(slot generation, candidate scope, result hash)` 而不改变状态。另一就绪槽位保持可调度，空结果不能占住唯一 semantic 等待位置。

必要用例：整池后置门全拒；前置门全拒且无 epoch；三条通过其余暂缓；所有剩余记录已在 committed epoch；恢复在结果已提交而状态未应用处。都应有限步退出 needs_semantic，或进入有明确定义的技术等待。

## 4. 支持结果、多值和反证的继续规则

v1/v2/v3 保留“新增 supported 后当前 pass 停在 local_results_only”的冻结行为，以及 `test_h2_success_defers_unchecked_tail` 原断言。v4 新增独立用例，不能修改旧版断言来放宽兼容性。

v4 的 supported 结果只更新事实和优先级，不能单独结束槽位：

1. 先完成已准入页、明确字段/清单剩余成员、必要反证及待处理补证；普通关系和明确属性按有限份额交错。
2. 多值关系或基数未知时，继续消费已有相关候选；有编号/续表/列表缺口时据缺口扩搜。
3. 即使本体有合法单值约束，发现一个值也不能跳过反证或竞争值审查；记录候选冲突，不直接按单值合并。
4. 有剩余预算和新候选时进入后续有界批；没有可行动候选且所有已派发工作已回收，才可达到饱和。预算用尽或必要核验失败使用其专用 stop reason。

发现和反证分别评估，但 record 仅一个准入身份。任一意图命中或存在必要保护均阻止该 record 因另一意图低分被软剪枝。达到用户预先冻结的局部目标可以暂停，必须保留未核验范围，不能宣称全文完成。

## 5. 组级检索到记录及任务的映射

组 ID 只属于检索命名空间，不加入 `RetrievalPlan`、RecallLedger 或 `RankingEpoch.record_ids`。组由冻结原文/IR/组策略及稳定有序成员生成；每个成员必须属于同一 U，携带锚点、角色及结构连接理由。

组级分数保存在独立的视图/门结果引用中。展开为候选 record IDs 后才创建记录级精排池，组展开顺序、上限和遗漏成员可回放。重叠组按 record ID 合并，保留多组命中来源；record 的自身分数与组分数分开，不把一个组分数复制成所有成员的自身分数。

准入以 record 为单位，任务继续沿 `RecognitionTask` 身份及既有补证 lineage 生成。同一 record 由多个组命中时首次任务至多一个；上下文范围通过冻结 group expansion 引用组装。新增组来源若改变已核验断言的证据范围，走现有补证/重验版本，不伪造新的首次覆盖。

已知必要依赖作为 binding/context 引用加载；其中需要独立提出对象或值的成员必须单独准入或走明确授权的证据修复协议。组被检索、成员作为上下文被读取，不授予整组事实权限和 coverage。

命中审计覆盖 H0–H2：词面命中记录 term、lane、source anchor 与角色；dense/精排保存真实输入的分角色引用及视图 hash。整体语义分数只有输入来源可回放，不能伪造模型内部的锚点贡献分解。旧记录只有 record/channel 级信息时标记 legacy attribution unavailable，不补造锚点命中。

## 6. 视图不可精排与费用审计

### 6.1 not_rerankable

自身、上下文和结构组视图分别记录可用性。一个视图超预算不淘汰其他视图；`not_rerankable` 没有相关性分数，不填零分，也不因缺分进行软剪枝。

短视图可用于粗召定位，但不能让被截断部分获得“已否定/低相关”的结论。可用视图足够时继续定位，无法覆盖的必要依赖保留保护或缺口。全部视图不可用时进入 `view_unavailable`：使用有界完整原文窗口/现有确定性路径，或在无法保持事实协议时技术暂停。窗口化若需新增契约则先阻塞该能力，不默许截断。

### 6.2 前置门观察与付费屏障

epoch observations 当前只覆盖 base_pool。v4 对 epoch 前的计算补充最小 `GateEvaluation` 制品，引用共享输入清单和结果，不复制完整原文或向量。

其必备内容为：评估 ID、slot/generation、候选及视图清单 hash、实际输入 hash、模型/门版本、逐候选视图状态/输出引用、缓存命中及出处、执行耗时、请求 reservation/receipt 引用、完成状态与未完成范围。缓存命中不新计模型费用；首次付费归属不因多个查询引用而重复累计，按请求 ID 去重。

付费请求继续先持久预扣再派发；门输出和被评估范围持久化后才能提交由其派生的处置。即使所有候选被拒、没有 epoch，输入与费用仍可回放。未知响应保留未知支出和原请求身份，不能删除回执后免费重发。无模型的廉价门也保存紧凑输入/输出依据。

## 7. 小 AdmissionDecision 与校验

`AdmissionDecision` 是现有 observations 的派生小记录，设计必备字段为：`schema_version`、`decision_id`、`decision_hash`、`plan_id`、`generation`、`dependency_hash`、`policy_hash`、`evaluation_ref`、`decision_target`、`admission_source`、`admitted_record_ids`、紧凑 disposition/reason 分组、必要 `group_expansion_ref`。下一门决策另有 `next_stage_record_ids` 和 stage 水位，识别准入列表为空；识别决策的下一门列表为空。

`evaluation_ref` 是精确 committed epoch 或完整 GateEvaluation 的带类型/hash引用。`decision_target` 区分 next_stage、recognition、disposition_only；前置门通过只产生下一门候选，不能直接作为 H2 识别准入。H0/H1、明确配置的确定性/H3 普通探索沿其冻结 admission_source 授权，不能把付费语义路径失败伪装成这些来源。得分、views、protected reasons 及请求费用从原制品读取；决策不复制这些历史正文。ID/hash 覆盖精确决策内容和依赖，原引用变更时不能沿用旧 ID。

v4 语义准入接口采用显式 `accept_semantic(committed_epoch, decision, preparation_result_ref)` 或等价的已验证不可变引用，废除新路径中由调用者传 `committed=True` 的信任方式。前置门无 epoch 时走独立 `accept_search_result`，不得借假 epoch 通过。

必须验证：

- 同 run/plan/主体版本/谓词/源/本体/permission scope，政策和 generation 一致。
- epoch 分支：已持久提交，完整池及 observations hash 正确；决策引用精确命中该制品；准入 ID 唯一且属于完整池并保持相对排序，处置分区完整且互斥。
- gate 分支：评估已完整提交，阶段/候选范围/hash 正确，通过集和处置分区与原结果一致；H2 的 next_stage 决策不得包含识别准入 ID。普通启发式/确定性准入还需核验其独立冻结来源。
- record 属于 U；组展开成员、保护理由及预算边界可回放；不得准入已失效或无触发的历史拒绝项。
- 同 decision 重放幂等，不重复任务；同 ID 不同 hash 或跨 epoch 子集拒绝。

持久顺序：预扣 → 回执/完整门结果或完整 epoch → 小决策 → 原子提交的搜索状态与准入事件 → 任务派发。允许相邻步骤复用既有事务，禁止派发越过决策屏障。

恢复：epoch 已提交但决策缺失时用冻结代码政策与 observations 重建相同决策，不重评；决策已提交但任务未入检查点时回放幂等准入；前门已完成而后门未开始时恢复 next_stage_ready，不重复前门或跳过后门；门中断从回执水位恢复；owner/fence 或依赖失效时禁止新派发但保留已发生费用。

## 8. 饱和、公开投影和完成度

复用已存在的 `adaptive_search_saturated`：executor 已有该原因，document_analysis/execution 已在证据修复域映射 `paused`。v4 补齐其前提：没有在途或待应用结果、没有可调度新候选/有效激活、没有可立即处理的必要补证；全局还有其他就绪槽位时不能提前结束运行。

存在未核验软剪枝项时 `completion=incomplete`；有预算/技术阻塞优先报告对应原因。若用户继续或新线索使这些项重新激活且最终满足全部覆盖条件，仍可以达到 `in_scope_complete`，历史上发生过剪枝不构成永久障碍。

在内核 progress/coverage、应用 progress、图谱 coverage 及前端类型同步增加可选诊断对象：`schema_version`、`policy_version`、`records_soft_pruned`、`records_pending_disposition`、`records_reactivatable`、`records_dependency_exhausted`、`reason_counts`、`search_status`。按槽位提供相同口径；聚合单位是主体/谓词/record 机会，同一文档 record 跨槽位计多次。

诊断来自当前有效处置投影，历史累计事件/重新激活次数另标累计值。soft_pruned 是 unattempted 子集，其他搜索计数也不加入 coverage 恒等式。旧运行字段缺失显示“未采集”，不得伪装为零剪枝。公开字段不暴露未授权原文锚点和内部模型输入。

实施落点包含 `contracts.py`、`ledger.py`、`executor.py`、后端 `schemas/document_analysis.py`、`application.py`、`public_projection.py`、必要的执行状态映射、前端 `src/lib/api.ts`/`document-analysis.ts` 及实际图谱面板；前后端不能只显示“完成”来掩盖饱和暂停。

## 9. P0 退出与验证约束

P0 的退出检查必须逐项覆盖：六类处置与调用者；空池/None/异步结果活性；小决策及 accept 校验；组到 record/task 映射；v1–v4 快照；门与决策费用屏障；覆盖和公开诊断；not_rerankable；supported 后多值/反证继续；FR-003/SC-002 和增量契约版本化；样本定级及校准隔离。文档写入不等于已通过这些实现验收。

v1/v2/v3 历史序列化形状及适用恢复保持原测试，v1 原不支持恢复不被改变。v4 要求旧/新分支分别验证策略白名单、快照篡改、未知版本、空结果活性、跨 epoch 伪造决策、恢复幂等、费用和 coverage 守恒。

HRS-1597、HRS-5592 均为开发暴露样本，只承担诊断/回归或明确声明的开发校准；不能改名或重跑后充当独立保留集。继承父方案至少三个真实新运行及合格专家参考，另需未暴露文档承担独立质量门；三个运行不是三个独立文档的替代。

P1 旧视图的影子数据只校准同模型/视图/查询/归属版本。P2/P3 改变视图或组展开后必须重新采集相应配置的数据，原阈值不得直接用于 P4。比较性能保留固定输入/预算与冷暖条件；绝对成本、事实质量和覆盖范围分别验收，不以相对提速替代父方案质量门。
