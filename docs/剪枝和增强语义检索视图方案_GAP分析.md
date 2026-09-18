# 剪枝和增强语义检索视图方案 GAP 分析

核对日期：2026-09-13（UTC）。对象：[剪枝和增强语义检索视图方案](剪枝和增强语义检索视图方案.md)，包括其 v4、enhanced 和 trial 后续补充；辅助契约为 [adaptive-retrieval.md](../specs/022-semantic-graph-closure/contracts/adaptive-retrieval.md)。

代码基线：本次核对的**当前工作树**，HEAD 为 `699924a0f881abdb174355e051941211a4270e40`。相关实现同时存在已跟踪未提交修改和未跟踪新文件，不能只看 HEAD，也不能把这些实现视为已经合并的版本。本文只新增分析文档，保留已有代码和方案改动。

证据口径：静态调用链核对、已有隔离测试、本次临时合成探针。未运行真实识别模型，未启动或重启服务，未连接生产数据库，未重新确认部署实况。方案和部署记录中的历史测试、分数冒烟、上线状态均按历史材料引用。

## 1. 核心结论

**v4 工程主路径已经落地，但尚未完整达到方案的增强检索、触发式恢复、校准审计和真实质量/性能目标。** 不能沿用早期评审的“尚不能进入实现”作为当前结论，也不能把第 10 节的“工程路径已实现”理解成第 4—9 节全部要求均已验收。

已形成闭环的主要能力是：模式冻结与旧版隔离、完整排序池与识别准入子集分离、前门全拒的有限步推进、缺分不剪枝、组上下文不授予事实权限、覆盖守恒、门/epoch/决策持久屏障，以及部分公开诊断。

优先处理的差距是：

1. **恢复与显式继续混用**：执行器恢复检查点后统一调用 `continue_search()`，会重新激活已饱和槽位的软剪枝项，并重置探索页计数；恢复过程没有区分自动接管和用户授权继续。
2. **重新激活未形成完整调度闭环**：补证、反证等原因虽在接口中定义，在线调用仅找到组命中；饱和后调用 `reactivate()`，记录变成可重激活，槽位仍不调度。
3. **新证明 generation 未保留首次覆盖**：主体证明恢复时重建 plan 和 RecallLedger，未看到旧已检查记录及准入状态向新 generation 的迁移。
4. **增强视图仍是有限静态实现**：组视图固定取前 8 个成员，兄弟按距离/顺序选择；尚无按当前任务贡献选择组成员、完整的命中锚点归属及跨 record 证据组任务复用。
5. **校准与验收尚未闭合**：正式模式检查配置中的 `validated` 声明和 hash 形状，但不核验所引用的专家/样本制品；observation 没有直接生成“拟剪枝及后续贡献”账本；独立样本、最终视图校准、A—E 对照、真实质量/性能和专用 PostgreSQL 验收仍待完成。

本次已有测试两组分别为 **57 passed** 和 **64 passed、10 skipped**。这些通过结果确认部分工程行为，未覆盖上述全部差距；四项合成探针的实际输出见第 6 节。

## 2. 当前实际执行链与模式边界

新运行经 `application.py` 读取自适应配置，冻结到运行的 performance 配置；`execution.py` 按冻结模式构建 v4 执行器。执行器先运行 H0/H1，再在 H2 调用 `RankingService.prepare_next_epoch()`：构建自身/上下文/组视图 → 廉价门记录 → dense/组通道 → 语义门 → 有限精排池 → 完整 epoch → 小决策 → 准入真实 record。H3 消费受控探索范围。识别上下文和原有事实核验继续独立执行。

主要入口：

- [配置加载与冻结](../backend/app/services/document_analysis/adaptive_configuration.py#L11)、[新运行写入策略](../backend/app/services/document_analysis/application.py#L365)、[运行工厂校验](../backend/app/services/document_analysis/execution.py#L681)。
- [执行器构建槽位](../backend/app/services/extraction/ontology_guided/executor.py#L568)、[排序输入](../backend/app/services/extraction/ontology_guided/executor.py#L813)、[决策提交与接受](../backend/app/services/extraction/ontology_guided/executor.py#L872)。
- [增强视图](../backend/app/services/extraction/ontology_guided/contextual_retrieval.py)、[三道门及排序](../backend/app/services/extraction/ontology_guided/semantic_reranker.py#L1022)、[v4 搜索状态](../backend/app/services/extraction/ontology_guided/adaptive_search.py)。

| 模式 | 当前代码行为 | 应如何解释 |
| --- | --- | --- |
| `disabled` | 不创建 AdaptivePolicy；是否使用原有证据修复路径由其他冻结开关决定 | 不是把已有 v4 运行迁移回旧版 |
| `observation` | 使用 v4 包装及审计，但沿用旧视图和旧 `next_admission()` 的成功停搜行为；不执行低分淘汰 | 已验证部分 v3 等价性；不等于已完成拟剪枝贡献分析 |
| `enhanced` | 激活 v4 继续搜索、多视图和结构组；禁止携带校准制品；不按低分淘汰 | `config.py` 和 Compose 的代码默认值；会改变候选与成本 |
| `trial` | 要求 development 制品和 raw single logit；dense/self 前门不淘汰；双意图精排低于各自阈值且无保护才剪枝，阈值不得高于 -8 | 真实执行软剪枝，但明确未校准；方案后续授权引入的例外 |
| `enforce` | 要求对应视图/参数的校准；线上仅接受声明为 validated 的配置；未列谓词无淘汰阈值 | 校准身份检查已实现，外部批准材料的完整性检查仍有 GAP |

隔离评测还允许 `enforce + evaluation_only=True + calibration=None`，用于测量增强路径而不剪枝；在线工厂拒绝 `evaluation_only`。具体实现见 [AdaptivePolicy](../backend/app/services/extraction/ontology_guided/adaptive_retrieval.py#L66)。

代码默认 enhanced 与[历史本机 trial 部署记录](../specs/022-semantic-graph-closure/pruning-trial-deployment.md)并不矛盾：后者记录的是本机配置覆盖。本次没有读取整份 `.env` 或查询容器，不能据代码默认值断言机器当前模式。

## 3. 逐项对照总表

“已实现”仅表示所列工程边界有代码/测试依据；“部分实现”表示目标仍有缺项；“待验证”表示不能由工程测试推出真实结果。G 编号的细节、影响和验收建议见第 4 节。

| 方案要求 | 当前代码与证据 | 判定 / GAP |
| --- | --- | --- |
| §1、§3 检索只定位，事实须原文证明 | `assemble_context()` 保留 target/binding 区别，组上下文 `fact_eligible=False`；已有组权限用例通过 | 已实现基础权限隔离；真实 owner/方向/条件质量待验 |
| §4.1 复用原文、祖先、字段、兄弟、可信图上下文 | 有 self/context variants、祖先摘要、字段兄弟、可信提及和证明依赖 | 部分实现，G04—G06 |
| §4.2 自身、上下文、结构组独立信号 | self/context sparse、dense_self、dense context、group 分开记录；父组不是子记录进入自身检索的门槛 | 已实现基本分路；融合参数和消融不足，G04、G16 |
| §4.2 有预算的短描述与可追溯遗漏 | 当前主要拼装完整片段，超限切换 self；无独立短描述预算/裁剪清单 | 部分实现，G07 |
| §4.3 按任务选择兄弟，词面命中归到具体来源 | 静态字段邻近/兄弟标题；`anchor_hits` 覆盖部分物理来源，H2 未传兄弟上下文 | 部分实现，G05、G06 |
| §4.4 group→record 映射，保留未展开范围 | group ID 独立，保留 selected/omitted，按真实 record 入池；组最多 8 条 | 基础映射已实现；选择顺序/尾部贡献不足，G04 |
| §4.3—4.4 同组去重及任务复用 | record 去重、`admitted` 和既有 task 身份控制有效；同组多个不同 record 仍可分别识别 | 部分实现，G08 |
| §5.1 确定性排除、绑定排除、软剪枝、补证分开 | 复用来源/主体检查及原核验协议；新门直接表达的是通过、软剪枝、不可用 | 未见把硬排除/绑定排除误写成事实否定；付费前的细分原因体系仍较窄，G09、G14 |
| §5.2 三道成本门，先上下文后淘汰 | lexical 门先记录；enforce 有 dense 双意图/多视图门；trial/enforce 有精排后门 | 工程路径实现；廉价门不淘汰，冷向量化无节省保证，G09 |
| §5.3 校准按模型、视图、谓词族和意图 | 模型/视图参数/hash 和双意图阈值已约束；一份 profile 对所有列明谓词使用同一组阈值 | 部分实现，G10、G11 |
| §5.3 配额前过滤，允许小池及空池 | dense 通过集先过滤 channels，再 `fill_pool=False`；三条通过不补满 | 已有定向测试通过 |
| §5.4 不可评分不填零，不构造否定 | context 超限回落 self；全不可用返回有类型结果并探索；`None` 分数不满足剪枝条件 | 基础行为已实现；短视图/遗漏信息不足，G07 |
| §6.1 互补证据与事实边界 | 组成员以 binding 加载；原补证/反证协议保留 | 部分实现；尚无完整贡献驱动的组任务复用与触发，G02、G08 |
| §6.2 六处置、空结果、门水位、有限轮次 | 有稀疏 disposition、stage refs、四轮上限及结果消费；全拒可饱和 | 常规路径已实现；激活/失效边界及完整结果代数仍有缺口，G02、G13 |
| §6.3 supported 后必要继续、多值/列表/反证 | v4 不再以支持数停搜，继续候选并保留预算；v3 断言未放宽 | 基础继续已实现；没有完整按缺口原因选择下一步，G12 |
| §6.3 重新激活有触发，恢复不刷新探索额度 | 有 group/required/counterevidence 接口和 explicit_continue | 存在明确实现偏差，G01、G02 |
| §7.1 新 generation 保留已核验覆盖 | 主体证明恢复时旧 plan 记入事件后重建新 plan/ledger | 未满足该目标，G03 |
| §7.2 完整 epoch 与小决策精确校验 | commit 验证实际 pending；接受时校验上下文、双意图、输入 hash、有序子集及幂等 | 已实现主要校验与定向反例；不应再列为“只有布尔 committed” |
| §7.2 预扣、回执、门/epoch、决策、派发屏障 | 复用 owner hook 和持久排序状态；四个崩溃边界测试通过 | 工程路径实现；专用 PG 与扩大规模成本待验，G15、G17 |
| §7.3 静态共享、精确失效、紧凑增量 | 视图/向量缓存含源、结构、metadata、scope、模型等依赖；不可变门/决策载荷共享 | 部分实现；正文展开及跨轮全范围观察仍可能放大状态，G15 |
| §7.4 覆盖守恒、公开诊断、饱和暂停 | schema、progress、图覆盖、UI 接线；soft_pruned 子集校验；饱和映射 paused | 基础已实现；诊断细分和槽位 UI 不全，G14 |
| §7.5 v1—v4 恢复隔离 | v4 独立 schema 和策略校验；旧字段按版本省略；冻结配置恢复 | 基础已实现；恢复动作语义仍有 G01 |
| §8—§9 P0 材料、最终配置影子、A—E 与真实质量/性能 | 有专家包工具、旧排序评分器和 benchmark 入口；材料及质量门仍待完成 | 部分工具可复用，目标验收未闭合，G10—G11、G16—G17 |

## 4. GAP 明细

### G01：自动恢复与用户显式继续混用，探索额度会被重置

**优先级：高；类型：已确认实现偏差。对应 §6.3、§7.5、§9.3。**

方案要求探索保持固定预算和稳定顺序，不因恢复刷新额度；显式继续应带有新预算或剩余预算的授权语义。

代码在[检查点恢复校验后](../backend/app/services/extraction/ontology_guided/executor.py#L1388)对所有 search 调用 `continue_search()`。v4 的[该方法](../backend/app/services/extraction/ontology_guided/adaptive_search.py#L459)把所有 `soft_pruned` 改为 `reactivatable`，记录原因 `explicit_continue`，然后调用父类；父类[重置 `_exploration_pages = 0`](../backend/app/services/extraction/ontology_guided/heuristic_search.py#L581)。在线[恢复入参](../backend/app/services/document_analysis/execution.py#L1833)只传 checkpoint，没有向内核区分自动接管与用户主动继续。

**触发情形**：槽位 A 已饱和，运行仍因槽位 B 执行中而保存检查点；此时 worker 中断/租约恢复。A 的软剪枝项可能因普通恢复重新进入 H3，并得到重置后的页额度。

本次探针确认：`max_exploration_pages=1`、已用 1 页后，`restore → continue_search → next_admission` 又产生 8 条的新页。探针验证的是 search 层；自动接管会走同一调用的结论来自在线调用链。

这**不表示全局模型费用或任务预算被清零**：累计费用、`max_tasks` 等约束仍在。差距是探索子预算和“为何激活”的审计不再严格对应用户动作。

**建议与验收**：恢复只重建状态；将显式继续作为持久事件传入，冻结触发身份、范围与授权额度。增加“一个槽位饱和、另一个仍执行时自动接管”的用例，断言前者无新激活、无探索额度增长；单独测试用户继续只消费获准范围，重放不重复授权。

### G02：补证等激活原因未接入完整在线链，激活不一定恢复可调度状态

**优先级：高；类型：部分接口实现、已复现活性缺口。对应 §6.2—§6.3。**

[reactivate()](../backend/app/services/extraction/ontology_guided/adaptive_search.py#L340)接受 `group_member`、`required_evidence`、`counterevidence`。本次全量搜索在线调用点，仅找到 `apply_gate()` 的[组命中调用](../backend/app/services/extraction/ontology_guided/adaptive_search.py#L338)；executor 未把新可信名称、具体补证缺口、反证来源或探索误剪反馈转成该接口调用。旧证据修复仍工作，但不能据此认为它已经与剪枝处置联动。

此外，`reactivate()` 只改记录处置，未更新槽位 `status`、H3 候选列表/游标或已评分候选的复用入口。`next_admission()` 在 `pass_exhausted` 时直接返回。探针在全拒饱和后传入合法 `required_evidence` 触发，得到 `records_reactivatable=1`，但 `next_page=False`，状态仍为 `pass_exhausted`。

一次触发超过 `group_member_limit` 时仅激活前 N 条，事件中也只保留这 N 条；尚无该触发剩余成员的续处理水位。相同触发重放被幂等短路，不能自动补上尾部。

**建议与验收**：把触发验证、待激活范围、原评分引用和状态迁移放在同一受控转换中；接入证据工作队列和可信提及更新。测试饱和后激活立即变为可行动、已评分项不重评、超过配额的激活范围有序续处理、重复事件不重复任务，以及无触发 H3 不复活软剪枝。

### G03：证明 generation 更新会重建首次覆盖，尚无覆盖继承路径

**优先级：高；类型：静态路径确认，缺完整场景回归。对应 §7.1、§7.3。**

方案要求“已有核验记录在新查询 generation 保留准入/覆盖，新来源走补证工作，不重建首次覆盖”。

当主体失效后的证明恢复，[add_subject(reopen=True)](../backend/app/services/extraction/ontology_guided/executor.py#L453)丢弃主体调度、递增 generation、移除旧 plan，并将旧 plan 写入 `retrieval_plan_superseded` 事件；随后[重新执行 plan_slot](../backend/app/services/extraction/ontology_guided/executor.py#L519)。[plan_slot 创建全新的 RecallEntry](../backend/app/services/extraction/ontology_guided/retrieval.py#L130)，新 search 的 admitted/observed 也重新初始化。此路径由[恢复有效主体证明](../backend/app/services/extraction/ontology_guided/executor.py#L1815)触发，没有 v4 的覆盖继承分支。

历史记录并未被物理删除，但“历史事件保留”不等于“当前 generation 保留首次已检覆盖”。潜在结果是同一主体版本/谓词/原文再次成为首次识别任务，公开覆盖重新从未尝试开始，成本也可能重复。

**建议与验收**：保留物理检查历史，另标当前证明是否仍有效；仅失效断言进入补证/重验，明确哪些新增查询工作确需新机会。增加 v4 主体证明失效→恢复测试，校验首次覆盖和费用单调、无重复首次任务、旧事实不会因为保留 coverage 而被自动认定仍有效。本次未驱动该完整业务场景，不能把潜在重复数量当作已测结果。

### G04：结构组在查询前固定取前 N 条，未实现按贡献展开

**优先级：高；类型：已复现的能力缺口。对应 §4.2—§4.4、§6.1。**

[structural_groups()](../backend/app/services/extraction/ontology_guided/contextual_retrieval.py#L36)仅构造 field_group 和 section 两类组，按原文位置排序后固定 `ids[:group_member_limit]`。默认 8 条；[组模型文本](../backend/app/services/extraction/ontology_guided/contextual_retrieval.py#L67)也只包含这批成员。该函数没有当前主体、谓词、意图输入，因此“先检索组，再选择有贡献的成员”实际上被简化成“先选固定前缀，再给前缀组评分”。

本次 12 条同章节夹具输出：selected=8、omitted=4，第 12 条原文不在组模型文本中。后四条仍有 self/context 入口，**不能说它们完全不再被检索**；但它们不能凭尾部互补信息触发这个组，也没有组展开游标用于按预算翻到下一批。

组分数确实没有复制成 record 自身分数，但命中的所有 selected 成员统一获得 `group_protected`；这仍比“根据主体、字段、归属、条件等具体贡献挑成员”粗。独立表格/行列角色/明确引用组也未在此索引中建模；原有表格上下文功能不能等价替代组召回索引。

**建议与验收**：共享全成员清单和可定位的角色索引，再以当前任务执行有界选择；冻结选中/未选中及理由。加入“唯一关键 owner/条件在第 9 条以后”“前八条属于另一设备”“公共组高分但仅两条互补”的反例，验证尾部可被选择且不会全组赋予业务归属。

### G05：兄弟、祖先上下文缺少任务相关选择，结构与派生线索标记不完整

**优先级：中；类型：部分实现。对应 §4.1—§4.3。**

[build_contextual_views()](../backend/app/services/extraction/ontology_guided/contextual_retrieval.py#L137)选择最近若干祖先；字段兄弟按物理距离排序，普通兄弟标题取 metadata 顺序的前 `sibling_limit` 条。默认 2 层祖先、4 个兄弟，没有查询相关度、字段角色优先级或明确引用/承接的排序输入。普通兄弟不复制全文，是已实现的约束；字段兄弟仍拼接所选记录的完整原文，并非“相关短值”描述。

祖先摘要带 `node_id` 与 `authority=retrieval_only`，原文片段有角色锚点；但 sibling headings 只存字符串，heading path 也未逐项记录来源/生成类型。metadata 本身可以回放，并不意味着每个组装片段都已区分“解析结构、模型派生、已核验图关系”三种信任来源。

已有 [retrieval_query.py](../backend/app/services/extraction/ontology_guided/retrieval_query.py#L46)对可信原文提及和版本化证明依赖做校验，是可复用基础；目前不是包含任意必要关系路径的动态查询图上下文。

**建议与验收**：将静态结构索引与动态选择分离，保留来源 node/anchor、角色、生成方式与依赖；对缺摘要、层级不全、相关兄弟位于顺序尾部、多 owner 表格分别验证。改动应保持自身入口和事实权限不变。

### G06：H0—H2 命中锚点归属只部分补齐

**优先级：中；类型：审计缺口。对应 §4.3、AR-P2-01。**

`anchor_hits()` 能定位自身、表头、注释、父表以及**显式传入**的 context record 原文。[H0/H1 页审计](../backend/app/services/extraction/ontology_guided/adaptive_search.py#L112)传了 `context_ids`，比旧 record/channel 日志更完整。

但 [lexical GateEvaluation](../backend/app/services/extraction/ontology_guided/semantic_reranker.py#L1043)和 [H2 observations](../backend/app/services/extraction/ontology_guided/semantic_reranker.py#L1409)都调用 `anchor_hits(index, rid, terms)`，未传兄弟成员；词面 context 得分却来自包含字段兄弟、祖先和兄弟标题的 JSON。`source_roles` 列出输入来源，不能直接回答“哪一个 term 在兄弟 B 的哪个锚点命中”；标题/摘要命中也没有完整的逐项来源记录。

因此，任务清单中“跨 H0—H2 锚点归属已完成”表述过满。准确结论是：物理输入角色追溯明显增强，但具体词面命中归属尚未全覆盖。没有证据表明模型已经把 B 的属性错误挂到 A；这是审计能力与防错验证的缺口，不能直接写成已发生错挂。

**建议与验收**：由实际检索输入生成词面 hit 记录，保存 term、channel、record/node、role、anchor 或派生来源引用；dense/精排只保留输入来源，不伪造分数贡献。增加“只有 B 命中、A 自身无命中”的 H0/H1/H2 一致性测试。

### G07：短检索描述、遗漏范围和多视图状态尚未形成完整契约

**优先级：中；类型：部分实现。对应 §4.2、§5.4、§7.3。**

当前 self/context 都直接序列化所选完整文本，再计 token；[select_available_view()](../backend/app/services/extraction/ontology_guided/contextual_retrieval.py#L238)优先 context，超限回落 self，全部超限则不可精排。缺分不填零、回落 self 时 `context_complete=False` 保护已实现，本次相关测试通过。

尚无独立检索描述 token 预算、按角色保留必要条件的裁剪策略、兄弟/祖先未选范围及其引用清单。group 有 omitted record IDs，context 则主要以数量上限裁选，未记录同等详细的遗漏范围；[epoch observation 的 `omitted_refs`](../backend/app/services/extraction/ontology_guided/semantic_reranker.py#L1380)始终为空。当前 `complete` 主要表示该选定文本可输入模型，不应解释为任务所需上下文已经全部覆盖。

全部视图不可用时走原文探索已有实现，不能将这一点列为缺失；缺的是方案设想的“短描述可定位、完整核验证据独立加载、遗漏可追溯”的更细粒度层次。

**建议与验收**：区分 view 输入完整、原文范围完整和证明上下文完整；短描述记录预算、遗漏引用与原文加载键。测试多段方法、脚注否定和条件超预算时不产生完整性假象，且仍可回到原文补证。

### G08：去重主要停留在 record，尚无贡献明确的证据组任务复用

**优先级：中；类型：能力缺口。对应 §4.3—§4.4、§6.1。**

重叠组会按 record 去重，同一 plan 的已 admitted record 不重复首次准入，组 ID 不进入 task；[组上下文](../backend/app/services/extraction/ontology_guided/adaptive_search.py#L320)加载时保留 binding 权限。这些行为已有测试。

但[准入页逐 record 创建任务](../backend/app/services/extraction/ontology_guided/executor.py#L420)，尚无按“主体/谓词/待证明断言/证据范围/版本”形成的组级识别复用键。多个 record 可携带高度重叠的组上下文分别付费。合并 `_group_context` 时按插入顺序截到 group_member_limit，也没有针对被截互补来源的未决清单。

这是减少重复上下文与模型调用的优化尚未完成，**不是要求把同组成员无条件合并成一个事实任务**。不同 owner、不同断言及不同证据版本必须继续分开。

**建议与验收**：先定义贡献/证据范围，再复用确实相同的识别工作；新来源进入原补证/重验协议。测试同组同断言复用、同组不同设备不复用、跨组互补来源不因简单截尾丢失，并报告净模型请求/token 变化。

### G09：廉价门目前是观测屏障，冷向量化及前置成本节省尚未实现

**优先级：中；类型：已明确收缩范围，不应误称完整提速实现。对应 §5.2、§10.1。**

[lexical gate](../backend/app/services/extraction/ontology_guided/semantic_reranker.py#L1022)对 available 全部通过，理由固定为 `literal_absence_is_inconclusive`。它记录锚点和来源保护，不以关键词缺失剪枝；这与方案第 10 节的首版限制一致。

首次 H2 仍可能对全部 eligible context 编码，随后再编码 self 和 group；[有限 pool_limit](../backend/app/services/extraction/ontology_guided/semantic_reranker.py#L1258)在这些操作之后才应用。trial 又明确不做 dense/self 前置淘汰，因此其收益主要可能来自减少识别/独立核验，不能说减少了当轮完整精排或冷向量化。

前置的确定性权限/主体有效性与原核验约束仍存在，但尚无完整的廉价“待补证/绑定排除/多路弱相关”分类及对应节省测量。不能为了补齐此 GAP 简单改成“无关键词即淘汰”。

**建议与验收**：先计量 view/tokenization/embedding 各自冷暖成本，再选择可证明安全的廉价筛选；把自身模型/审计开销计入净收益。至少比较 enhanced 与 trial/enforce 的精排对数、主模型请求数和首达时间，不以剪枝计数代替性能结论。

### G10：正式校准准入验证的是配置声明，未核验专家/样本制品内容

**优先级：高，正式 enforce 验收前关闭；类型：证据验证边界缺口。对应 §5.3、§8、人工专家审核补充。**

[CalibrationProfile](../backend/app/services/extraction/ontology_guided/adaptive_retrieval.py#L19)要求双意图有限阈值、明确谓词清单和若干 64 字符 hash，并验证自身 profile hash；AdaptivePolicy 对线上 enforce 要求 `quality_status=validated`。这些配置门禁真实存在，development 默认不能直接上线 enforce。

但是[线上配置加载器](../backend/app/services/document_analysis/adaptive_configuration.py#L11)只读取 profile JSON，不读取、解析或比对 sample manifest、expert review、参考批准结果，也不验证它们是否属于当前校准配置。[专家审核工具](../backend/app/evaluation/adaptive_expert_review.py#L98)有独立完整性校验，但输出尚未通过可信制品引用/准入过程与线上配置装载闭合。

本次仅在临时目录创建 profile，将质量声明改为 validated，保留样本 hash 为 64 个 `a`、专家 hash 为 64 个 `b`，未创建任何对应批准材料；`configured_adaptive_policy()` 接受为线上 `enforce`。这证明程序验证边界止于配置声明，**不证明现有部署伪造了专家审核，也不意味着非授权用户能修改配置**。

**建议与验收**：可在离线交付/发布阶段验证整套校准包，并让运行时只接受该验证结果的不可变引用；不必让在线识别导入 evaluation 或加载金标。测试缺审核、hash 错配、参考未批准、样本身份不符、最终视图参数变更时拒绝正式准入。

### G11：谓词族校准与 observation 拟剪枝贡献分析不完整

**优先级：中；类型：能力及校准流程缺口。对应 §5.3、§9.1。**

一份 profile 只有一组 self/dense/group/rerank 双意图阈值和 `predicate_iris` 白名单；[thresholds()](../backend/app/services/extraction/ontology_guided/adaptive_retrieval.py#L114)对所有列明谓词返回同一阈值组。它支持未知谓词不剪枝，但尚无一轮运行内按谓词族/根层宽关系选择不同 profile 的映射。不能把“绑定谓词清单”等同于“已按谓词族校准”。

observation 保留旧行为且保存完整精排 observations，是后续离线分析的基础；然而 [epoch_decision()](../backend/app/services/extraction/ontology_guided/adaptive_retrieval.py#L294)仅在 trial/enforce 生成 soft_pruned，observation 的决策准入全部成员，也不执行增强前门。当前没有直接输出 `would_prune`、拟剪理由、对应后续事实/互补证据贡献的关联结果。

方案要求“影子记录拟剪枝，继续原流程，核查后续贡献，并审查未产出项”。现有分数可以事后加工，但这段闭环不能按已完成计算。尤其 observation 使用旧视图，不能直接为最终增强视图的阈值背书。

**建议与验收**：定义独立于实际准入的影子决策，冻结校准身份并与实际后续结果/专家漏标检查关联；支持按谓词族选择阈值，未知族保守保留。最终视图变化后重采，不复用失效影子数据。

### G12：supported 后继续已实现，按具体缺口决定扩搜仍较粗

**优先级：中；类型：部分实现。对应 §6.1、§6.3。**

[v4 next_admission()](../backend/app/services/extraction/ontology_guided/adaptive_search.py#L125)移除了旧版 `_supported_count` 导致的停搜，能继续已有页及剩余候选；测试已验证支持后进入下一轮。双意图精排也保留反证入口。

实际 H2 继续条件主要是“轮次 < 4、还有 needs_evaluation_ids、语义未显式关闭”，统一使用 `remaining_candidates_or_required_counterevidence` 理由。尚无显式多值/基数状态、列表/编号缺口、必须检查的竞争值、有限探索发现误剪等工作项驱动下一轮范围。它实现了“有界继续”，没有完整实现“区分无产出原因后选择补证、协议修复、技术重试或扩搜”。原协议已有修复/重试，不应将其抹去；缺的是与本次搜索处置的联动。

**建议与验收**：把具体缺口引用纳入扩搜触发与决策审计，沿用已有预算与公平调度。增加支持一条后发现列表尾项、单值竞争值、必要反证，以及协议失败不机械扩大四轮的独立场景。

### G13：有类型结果和阶段水位采用混合接口，完整目标分支未全部落地

**优先级：中；类型：契约实现范围差异。对应 §6.2、§7.2。**

[SearchPreparationResult](../backend/app/services/extraction/ontology_guided/adaptive_retrieval.py#L221)目前仅有 filtered_empty、no_new_candidates、view_unavailable、gate_completed 四个 kind。正常 ranked 和技术 paused 仍通过 RankingEpoch 表达，显式 disabled 经 `skip_semantic()` 表达；这属于可行的等价分流，不能仅因类型名不同就认定活性缺失。

需要补足的是：生产 `prepare_next_epoch()` 未找到返回 `gate_completed` 的路径；前门非空通过主要在同次准备中继续下门，崩溃后依靠已保存 GateEvaluation 和缓存恢复。按阶段的 `needs_evaluation_ids("lexical"/"dense")` 已定义，但执行器输入固定使用默认 rerank 集合；旧 None 也没有统一原因适配对象。

现有全拒、无新池、跨槽位和门后中断测试已通过，**不能据此声称常规路径现在卡死**。G02 的激活后不可行动则是另一个已复现问题。

**建议与验收**：明确当前混合接口是否作为正式契约；若保留，逐项映射每种结果、owner 持久水位和停止原因。补非空前门单独持久后恢复、仅 pending disposition、合法 None 兼容分支和技术阻塞的活性测试，确保每次结果改变状态或给出明确阻塞。

### G14：公开诊断接线已完成，原因、失效口径和槽位展示仍不足

**优先级：中；类型：部分实现。对应 §7.4。**

当前诊断有 schema/policy 版本、四类计数、search_status；核心与公共 schema 都检查 `soft_pruned <= unattempted`。应用 progress 和[图覆盖投影](../backend/app/services/document_analysis/public_projection.py#L405)传播了全局及槽位字段。旧图无诊断显示“未采集”，trial 有未校准提示。

尚有三项差距：

- [reason_counts](../backend/app/services/extraction/ontology_guided/adaptive_search.py#L479)基本仅 `low_relevance`，未细分前门/后门、上下文缺口、依赖失效、激活触发或探索受限；未公开独立标识的累计激活统计。
- [exhaust_dependency()](../backend/app/services/extraction/ontology_guided/adaptive_search.py#L365)把未准入的非 soft_pruned 项转 dependency_exhausted，却保留软剪枝项原状态。失效槽位上的软剪枝仍表现为低相关暂缓，不能完整表达“当前依赖已失效”的处置原因。
- [图组件](../frontend/src/components/analysis/document-relationship-graph.tsx#L812)仅展示全局软剪枝/可重激活摘要；主体—谓词表格未显示对应检索细分、待处置、依赖耗尽和 search_status。[进度面板](../frontend/src/components/analysis/document-analysis-panel.tsx#L1013)只显示全局软剪枝；缺字段时未在此面板单独显示未采集。页面文案也未明确计数单位为槽位-record 机会，可能被读成去重原文条数。

**建议与验收**：建立同一当前处置投影，明确失效原因的优先级；原因和累计事件分开。前端提供槽位诊断和单位说明，不将细分计数加回覆盖总数。增加旧运行、失效软剪枝、多槽位共享 record、trial/增强模式的公共投影与显示测试。

### G15：不可变载荷共享已实现，整体状态体积和固定增量成本仍未证明

**优先级：中；类型：部分实现及性能证据缺口。对应 §7.1—§7.3、§9.2。**

`FrozenDict/FrozenList` 让同一门、同一决策的历史载荷在快照间共享，adaptive_inputs 按 hash 存清单；已有增量状态存储和相关测试通过。缓存键绑定文档/结构/metadata、权限 scope、模型及视图参数，[静态视图构建缓存](../backend/app/services/extraction/ontology_guided/semantic_reranker.py#L432)无需因新主体而重新生成整篇文本向量，是重要已实现能力。

但这不等于所有正文和分数都已经按引用归一化：

- context 的祖先/兄弟文本仍逐 record 展开，field/section 重叠组也会包含重复原文。
- 每个 epoch 的 `retrieval_views` 仍含实际 model_text；同一 record 用于不同槽位时会再次携带相应文本。
- [每轮 GateEvaluation](../backend/app/services/extraction/ontology_guided/semantic_reranker.py#L1163)仍为 available 范围构造 observations，即使多数通道分数来自上一轮缓存。前轮通过但尚未精排的项可能在后轮再次写观察载荷；共享同一个历史 gate 对象不自动消除不同 gate 间的重复。

以上是逻辑载荷层的重复；底层增量/不可变块存储还可能进一步共享，不能直接把逻辑文本大小当作新增 SQL 写入量。这说明需要测量增长项，不能直接从对象 identity 测试推导“新账本没有抵消模型节省”。当前 [AR-P0-06](../specs/022-semantic-graph-closure/tasks.md#L30)也保留固定成本/专用 PG 待验。

**建议与验收**：在真实规模和扩大历史规模分别记录 GateEvaluation、决策、视图清单、周期基线、SQL 写入量、恢复加载量与耗时；只对确认占主要成本的重复载荷做引用化。冷热向量缓存与状态缓存分开报告。

### G16：A—E 消融与细分指标没有形成直接可复用的一套闭环

**优先级：验收阻断；类型：工具/配置与实验流程缺口。对应 §9.1—§9.2。**

现有[benchmark](../backend/scripts/benchmark_heuristic_document_run.py#L876)可接受冻结 adaptive policy，记录模型请求、SQL、持久化、阶段与首次有效候选；已有[排序评测说明](../backend/app/evaluation/README.md#L106)及评分器支持证据闭包、反证召回和输入一致性校验。这些都可以复用，不能说仓库没有评测工具。

不过，本方案的 A—E 是“旧小批 / 旧视图校准剪枝 / 基础上下文 / 再加结构组 / 最终三门”的特定因素组合，不能直接套用旧排序实验的同名字母。当前 AdaptivePolicy 无独立的“启用基础上下文但关闭组通道”开关：active contextual 模式会构建组；`group_member_limit` 最小为 1，不能等价表示关闭组。因此 C 与 D 的严格单因素对照尚需配置或隔离评测接口补齐。

指标也尚未自动聚合完整：lexical 的 elapsed 默认 0；dense gate 的 elapsed 从整次 prepare 开始累计，不能直接看成独立 dense 门耗时；组内展开/重复候选/误剪/按原因激活的汇总，以及“专家判正确且最终仍有效”的首达时间，需要将制品与独立评分结果联接。benchmark 当前明确输出 `business_quality=pending_independent_source_review`，并未把自身支持状态等同专家真值。

**建议与验收**：预登记本方案专用因素表和共享输入/预算身份，支持 B、C、D 的明确隔离；把 gate 时间段、请求身份、首次有效事件和最终专家评分关联。累计请求耗时与墙钟时间分开，失败/未评分/截断样本保留在结果中。

### G17：真实质量、冷暖性能与 PostgreSQL 并发恢复验收尚未完成

**优先级：验收阻断；类型：验证材料缺口，部分已由方案明确承认。对应 P0/P4/P5、§9。**

当前材料仍缺已闭合的以下验收链：最终模型/视图/查询配置的影子数据和校准、未暴露文档与批准专家参考、至少三个真实新运行、等预算 A—E 结果、误剪关键路径/否定条件/错 owner 的复核，以及原规模/扩大历史的状态成本。

HRS-1597/HRS-5592 已在[专家包工具](../backend/app/evaluation/adaptive_expert_review.py#L15)登记为开发暴露样本，不能改名成为保留集；`validate_review()` 审核逐记录完整性和批准记录，但其返回值仍是 `formal_quality_gate=pending_model_scoring_and_three_new_runs`。工具存在不等于专家已经审完。

历史 trial 的 12 对合成短句分数冒烟证明接线和分数尺度，不是最终增强视图全文误剪率或性能结果。方案对这一限制的说明正确，应继续保留。

本次 `test_performance_postgresql.py` 实际 **10 skipped**，环境未配置专用 `DOCUMENT_ANALYSIS_TEST_DATABASE_URL`。SQLite 和合成恢复测试不证明 PostgreSQL 跨连接锁、失租、回滚及接管边界已经通过。本次未使用生产数据库补跑。

**建议与验收**：先冻结样本、专家参考与绝对门槛，再完成校准/保留集分离的真实运行；最后在专用可销毁 PG 库验证并发/恢复。分别发布工程、模型质量、性能和部署结论，不因 trial 已获授权就将正式质量门置为通过。

## 5. 方案与实施记录本身的表述 GAP

以下属于文档一致性问题，本次仅记录，不修改原方案或任务勾选状态。

| 位置 | 当前差异 | 建议表述 |
| --- | --- | --- |
| 方案开头与第 8 节中的“关闭态”“不启用线上剪枝” | 后文又记录默认 enhanced 和进一步授权 trial；开头部分句子没有清晰标为初次交付历史 | 用一张时间/范围表区分最初交付、默认增强和本机 trial；以冻结运行策略解释是否生效 |
| 第 7 节“目标数据不代表公共接口已经实现” | 当前已有诊断 schema、公共投影及 UI 接线 | 更新为各子项的实际状态，保留 G14 的未完部分 |
| 第 10 节小节编号 | 两个 10.1、两个 10.2，使开头“见第 10.2 节”有歧义 | 为当前限制、人工审核、默认增强、trial 使用唯一编号 |
| AR-P2-01 已勾选“跨 H0—H2 锚点归属” | H2 仍未完整归到兄弟/派生来源，见 G06 | 标明“物理输入角色已补齐，全部词面命中归属待补” |
| 第 10 节“v4 多值及反证搜索已实现” | 已实现支持后有界继续；具体列表缺口与激活工作流仍不完整 | 避免把通用继续测试等同全部缺口驱动规则完成 |
| “正式 enforce 的已审核校准门” | 代码验证 validated 声明，未核验外部审核制品链，见 G10 | 区分“配置状态门禁”和“校准包批准证据核验” |
| [旧 review](剪枝和增强语义检索视图方案_review.md) | 指向 276 行旧版本，并保留实施前判断 | 作为历史评审保留；当前状态使用本文及最新契约，不照搬旧阻断结论 |

## 6. 本次验证、复现与证据边界

### 6.1 已执行的工程测试

工作目录均为 `backend/`，使用已有 `.venv`；测试 fixture 使用 SQLite、临时原件/本体及模型桩，没有调用真实识别模型。

```bash
.venv/bin/python -m pytest -p no:cacheprovider -q -o faulthandler_timeout=30 \
  tests/test_extraction/test_adaptive_retrieval.py \
  tests/test_extraction/test_adaptive_expert_review.py \
  tests/test_extraction/test_heuristic_search.py \
  tests/test_extraction/test_ontology_guided_boundaries.py
```

结果：**57 passed，4 warnings，17.56 秒**。

```bash
.venv/bin/python -m pytest -p no:cacheprovider -q -o faulthandler_timeout=30 \
  tests/test_extraction/test_incremental_state.py \
  tests/test_extraction/test_incremental_performance.py \
  tests/test_extraction/test_document_analysis_public_projection.py \
  tests/test_extraction/test_document_analysis_execution_recovery.py \
  tests/test_extraction/test_performance_postgresql.py
```

结果：**64 passed，10 skipped，4 warnings，38.19 秒**。skip 均为未配置专用 PostgreSQL 测试库；warnings 为现有依赖弃用和 schema 字段命名提示。本次未运行前端构建、浏览器检查或全量后端测试；前端结论来自实际类型/组件源码核对。

### 6.2 四项临时合成探针

运行方式为后端 `.venv/bin/python`，显式设置 `DATABASE_URL=sqlite://`、`SEMANTIC_ALIGNMENT_ENABLED=false`，使用 `TemporaryDirectory` 和现有测试夹具。未写仓库测试文件，临时原件/profile 随探针退出清理。

| 探针 | 可复现设置/步骤 | 本次实际结果 |
| --- | --- | --- |
| 饱和后的补证激活 | `adaptive_fixture(dense=2)` → prepare/accept 全拒结果 → 搜索饱和 → 对一条软剪枝记录 `reactivate(reason="required_evidence", trigger_ref=...)` → next_admission | `status=pass_exhausted`，`reactivatable=1`，无新页 |
| 恢复后的探索额度 | 同一 40 条夹具显式继续并消费 32 条 H3 页 → 用完默认 1 页 → snapshot/restore → continue_search → next_admission | 恢复前已用 1 页、最大 1 页，仍新增 8 条页；恢复后的计数再次从新一页开始 |
| 结构组前缀范围 | `test_semantic_ranking_execution.fixture()` 的同章 12 条原文 → `build_group_views()`，默认 group_member_limit=8 | 一个 section 组 selected=8、omitted=4，第 12 条正文未进入组模型文本 |
| 校准声明准入 | 仅写 profile，质量声明改为 validated，sample/expert hash 分别为 `a*64`、`b*64`，不创建对应制品 → configured_adaptive_policy(enforce) | 被接受，`evaluation_only=False` |

夹具与辅助函数位置：[adaptive_fixture / prepare](../backend/tests/test_extraction/test_adaptive_retrieval.py#L28)、[_observe_page](../backend/tests/test_extraction/test_heuristic_search.py#L48)、[同章 12 条夹具](../backend/tests/test_extraction/test_semantic_ranking_execution.py#L31)。前两项刻意执行与恢复入口相同的 search 方法序列；不将 search 层探针称为真实 worker/PG 故障演练。

### 6.3 已有测试覆盖与未覆盖项

| 测试类别 | 本次已核验的代表用例 | 仍需补充的关键反例 |
| --- | --- | --- |
| 准入与空结果 | `test_pre_gate_all_rejected_has_audit_and_reaches_saturation`、`test_three_passed_records_are_not_filled_and_group_ids_never_become_tasks`、跨槽位 executor 三组参数 | 饱和后合法激活立即可行动、激活尾部续处理、混合接口的完整结果映射 |
| epoch/决策与恢复 | `test_decision_must_match_actual_committed_epoch`、双意图/输入篡改、四个持久制品崩溃边界 | 自动恢复不能冒充显式继续；证明 generation 更替保留覆盖；真实 PG 失租/回滚 |
| 视图与证据组 | context 溢出保留 self、全部不可用探索、互补组成员无事实权限 | 第 9 条以后关键证据、不同 owner 兄弟、完整锚点归属、多段条件与遗漏追踪 |
| 兼容与模式 | v3/observation 次序/请求/停止等价、enhanced 不剪枝、trial 阈值限制与投影 | observation 拟剪贡献闭环、正式校准包缺件/错配、同轮谓词族差异 |
| 覆盖与公开层 | 子集约束、旧字段省略、公共投影、增量状态和执行恢复 | 失效 soft_pruned 口径、槽位诊断 UI、累计事件单位、真实前后端集成 |
| 质量与性能 | 合成工程行为；已有可复用评测工具经源码核对 | 专家批准参考、最终视图校准、未暴露样本、A—E、真实首达/完整路径/误剪和状态成本 |

## 7. 建议收敛顺序与完成判据

1. **先修恢复、激活及覆盖边界（G01—G03）**：让自动接管保持原额度与处置，合法触发可调度，新 generation 不重复首次覆盖。以公开行为和跨槽位用例验收。
2. **补齐增强视图的可解释选择与来源（G04—G08）**：先解决尾部关键成员和跨 owner 反例，再优化组任务复用；避免只增加上下文而放大模型成本。
3. **收紧校准交付链并建立最终配置影子分析（G10—G11）**：批准证据可验证，参考不进入识别；正式模式与 trial 的边界清晰。
4. **明确搜索契约与诊断（G12—G14）**：把实际混合结果接口、缺口触发、当前处置和 UI 口径统一，修订文档中已完成范围。
5. **再验净性能和真实质量（G09、G15—G17）**：预登记 A—E 与独立材料，统计门自身、状态和模型全成本；在专用 PG 环境验收持久性与接管。

完成判据应同时包含：关键边界回归通过、独立专家质量门通过、固定预算下首达和总成本达标、覆盖/未决完整公开，以及明确的部署范围。当前可确认的是工程基础与部分隔离验证已完成；整套方案仍有上述代码和验收 GAP。
