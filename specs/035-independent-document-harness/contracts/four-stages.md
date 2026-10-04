# 四阶段图谱分析契约

日期：2026-10-02。状态：工程契约已实施，真实模型与部署验收待完成。详细行为及编码落点见
[重构方案](../../../docs/图谱分析四阶段顺序重构方案-20261002.md)。
本契约替代 stages.md 中与阶段顺序、候选准入和提前数值处理相冲突的部分；
原文权限、精确定位、合法 IRI、预算和独立证明要求继续适用。

## 1. 服务域、模型协议和执行策略

- 服务域与公开 graph.protocol：`document-harness-v2`，用于区分独立 Harness 域。
- 模型消息 `protocols.PROTOCOL`：`document-harness-v10`，包含阅读吞吐、本体候选范围及文档根第一跳约束。
- 冻结执行策略 flow：`four_stage`，reading_concurrency 为 1 或 2，默认 2。
- 创建后的新运行默认走四阶段。旧 flow 不转换、不提供另一条执行主循环。
- 不新增路由、ORM 列或历史状态表；鉴权、所有权、请求键及暂停/继续沿用当前接口。
- 命中旧 request_key、读取旧 graph 或继续旧 flow 返回 HARNESS_NEW_RUN_REQUIRED（409）；
  新字段 schema 不对旧缓存做缺省转换，不删除原件或历史记录。

## 2. Phase、stage 和工作准入

```text
Phase = discovery | skeleton | semantic | deterministic | done
Stage = ingest | parse | discover | planning | type_alignment
      | property_alignment | relation_alignment | referent_alignment
      | referent_candidates | referent_selection | entity_review
      | coreference_review | group_interpretation | evidence_review
      | identifier_check | literal_normalization | shacl_check | complete
```

| phase | 可执行 stage | 调用约束 |
|---|---|---|
| discovery | discover | 最大二路；阶段内本地有界查询只作召回 |
| skeleton | planning、type_alignment、property_alignment、relation_alignment | 单路；全部结果为候选 |
| semantic | planning、referent_alignment/candidates/selection、entity_review、coreference_review、property_alignment、relation_alignment、group_interpretation、evidence_review | 单路；alignment 只对实际受影响项重做 |
| deterministic | planning、identifier_check、literal_normalization、shacl_check | 不调用模型 |
| done | complete | 不自动执行任务 |

ingest/parse 是准备 stage，初始化 cursor 后开始 discovery；不是第五个业务阶段。
stage 可以跨 phase 复用，phase 是成本和执行归属的明确分类。

## 3. 当前行与公开候选

所有候选保留 source refs，state 仍是 candidate/accepted/rejected/unresolved。
骨架阶段新候选只能 candidate，根节点的用户前提除外。

| 对象 | 必填/可空变化 |
|---|---|
| Entity/Mention | parent_mention_id: string\|null；refined_member_ids: string[]；verification；calibration: Calibration\|null |
| Property | predicate_iri: IRI\|null；source_unit_evidence: HarnessSourceRef[]；calibration: Calibration\|null |
| Relation | predicate_iri: IRI\|null；calibration: Calibration\|null |
| RelationGroup | predicate_iri: IRI\|null；ordered_object_ids: string[]\|null；order_evidence: HarnessSourceRef[]；calibration: Calibration\|null |

新增字段在新协议中必填：尚无值用 null 或 []，不通过省略字段表达未决。
verification 沿用 method/rule_id/rule_version/semantic_verdict；未核对时各可空值为 null。
内部依赖 hash 保存在当前行，不在公开接口回传请求正文。

null 谓词候选必须有真实字段/关系表达及端点，不能 accepted。
它在展示中标为“谓词待对齐”，不能生成 IRI 链接或业务事实。
未明确主体的字段和未发现目标的引用仍是 observations/reference_cues，不补文档根或虚构端点。
人工解释任务只由谓词已对齐且满足既有准入的 unresolved 关系组生成，null 谓词草案不进入该入口。

公开 subject_id/object_ids 继续是最新共指投影 ID，subject_mention_id/object_mention_ids
继续保留原始权威端点。ordered_object_ids 是公开投影 ID 顺序；对应原提及顺序从原权威行保留，
投影若合并后丢失成员或形成重复，保留原断言未决说明，不能静默折叠为更短执行序列。

## 4. 模型输入与输出改变

### 4.1 发现与骨架

Discovery 沿用现有局部物理提及/字段/关系线索消息；提示不要求先拆斜杠编号或统一身份。
阅读容量、完整前缀续读、整行打包及按需来源反馈见 [stages.md](stages.md)。
当前协议为 `document-harness-v10`；已冻结旧协议的运行保留原记录，不在继续时改写模型契约。
发现的每个提及必须回答可空的 `candidate_class_iri`：非空值只能来自本轮阅读卡。
程序保存 `discovery_class_iris` 作为候选依据集合，不提前填写已对齐的 class_iri 或采信类型。
物理提及 ID 只依赖文档、来源及精确起止位置；角色措辞和候选类型不参与身份键。
同位置的重复发现合并字段、名称、角色及候选类型；集合合并与窗口完成顺序无关。
`role_candidates` 保存不同角色提议，公开 role 汇总这些提议；不同位置及仅重叠的片段不直接合并。
发现输入同时提供 `quote_fragments`：按原文标点分隔的可引用片段、稳定位置 ID、所属来源及
精确位置；完整来源继续提供上下文。定位菜单最多 8 KiB，不以菜单缺项表示原文遗漏或否定。
片段 ID 可用于 Quote/SourceAnchor，不能用于推进
阅读游标，不能扩大主阅读范围。引用仍须逐字匹配，重复位置不默认选第一项。
先校验 anchor，再解析可选名称；名称在已定位 anchor 内只有一个精确匹配时可确定性
消歧，显式错误 occurrence 不自动修正。名称、附加字段引用错误分别保留观察和未完成
来源，不丢弃独立有效的实体提及；实体边界和合法类型门禁继续执行。
阅读完成范围按来源保留：局部错误只阻止相应来源完成，不否定其他来源或整个已读前缀。
续读主范围是原范围扣除已确认部分，可有多个不连续区间；依赖的表头和上下文继续保留。
错误来源未解决时继续显示覆盖不足；未知来源错误无法可靠归属时保守保留原范围未完成。
null 表示尚无适用类型，原文和字段保留为观察，不进入实体候选；不将缺卡视为全文否定。
阅读卡共享提供普通属性的 IRI、别名、定义、定义域、值域和数据类型，保留适用类。
`document` 明确提供根 ID、类型 IRI、合法直接关系及完整值域。每个窗口额外保留根关系
最上层合法目标的类型摘要（名称、别名、定义、属性标签），不受局部卡排序淘汰；详细属性、
身份与关系菜单仍按原文排序提供，并在对齐阶段完整核对。摘要同样进入本轮类型枚举和
窗口已提供卡片记录；根类型本身不作为可重复发现的正文对象。
根不需要正文 anchor。规划器对符合根关系值域且定位于正文的对象生成有界待核验工作，
引用其原记录；标题/目录不能单独触发该路径。候选不代表关系成立，不自动保存肯定边。
明确关系线索中的否定与条件不能被结构候选覆盖。文档描述关系可由实质正文及内容归属
证明，无须逐字出现“描述”；他文、背景、同名或共现不能替代证明，使用/组成等语义仍须
对应陈述。根与同名药物分开；关系仍经过独立证据核验。
纯标签/值字段及布尔值不能借匿名主体变成实体；名称、编号和真实匿名事件仍允许定位。
字段型误提及降为观察，保留原值和相关未对齐线索，不因这种降级重复阅读；非法引文仍
阻止完整覆盖。实际读取完整性与实体/类型采信继续分开。
type_alignment 是类型提议，class_iri 可空；不触发 referent/entity review。
property_alignment/relation_alignment 不以端点 accepted、编号或规范值为输入前提。
响应继续按本批 ID 固定键返回；空 mappings/未决保持原值和 null 草案。
类型、属性、关系及核验回答的键数由本批输入集合决定，静态消息不得另设旧的 12/32/6 项
上限；请求仍受已有预算与分批约束，缺项、多项及越界引用继续拒绝。
下游只接受冻结目录中的非空谓词，null 由程序保存为尚未对齐的骨架状态。

lower/upper 是候选端点角色；阶段二只定位原字段里的片段，不提前换算。
PropertyMapping 增加必填可空 unit_quote: Quote\|null，仅引用可见原值/表头/单位字段；
程序保存 source_unit/source_unit_evidence，禁止引用规范单位声明充当来源单位。
阶段三核对数值含义、端点角色和单位归属后，保存 source_unit_evidence。
直接含于原值的单位可由完整原值精确证据覆盖；独立单位必须有其精确来源及语义归属支持。
模型不得输出无引用的规范数值供阶段四消费。

### 4.2 编号与局部修正

ReferentCandidates/Selection 仍以 exact spans、完整 partitions 和决定性原文核对。
分组完整性覆盖最小编号表达及每个输入提及的实际发生位置，不要求各独立编号覆盖中间分隔符；
整串方案和多个独立编号方案各自都须完整，不能删表达或提及绕过检查。精确引文权限不放宽。
expressions、members 和 member.expression_ids 的上限按当前已登记 identifier_span_ids 数量生成，
静态消息与生成 Schema 同步，不用旧固定 12/6 强迫漏项；完整方案数仍最多 4。
无编号片段时只允许空 expressions/partitions，可申请一次片段扩展，扩展后重建容量约束。
完整组不能容纳于预算时局部 failed/retryable=false，reason=referent_group_budget_exceeded；不能截断成员。
proposal_feedback 加入 issue、rejected_proposal、具体遗漏/冲突引用；
最多一次原文 span 扩展、一次局部方案修正，预算在当前 referent_work 中保存。
方案覆盖遗漏、成员重叠、表达重复归属三类错误可修正；耗尽仍非法，所属 work failed/retryable=false。
其它完整回答中的局部协议错误直接保存局部失败。网络/JSON 整体错误按运行技术失败处理。

### 4.3 顺序与并行

RelationProposal/GroupInterpretation 增加：

```text
ordered_object_ids: list[本批对象别名] | null
order_evidence: list[Quote]
```

timing=sequential 时，ordered_object_ids 为 object_ids 的完整无重复排列，order_evidence 非空。
timing=parallel/unspecified 时 ordered_object_ids=null；没有获证顺序时不借 ID 排序填充。
程序按当前对象别名及 source 范围转换为原提及 ID/精确 refs。
对象组事实、选择语义和时间/顺序分别核对；斜杠、表格行号或对象数量不能单独作为证明。

## 5. 确定性校准

```text
Calibration = {
  input_hash: string,
  checks: {
    identifier: CheckResult,
    datatype: CheckResult,
    unit: CheckResult,
    shacl: CheckResult
  },
  literal: LiteralValue | null
}
CheckResult = {
  status: not_run | passed | invalid | incomplete | not_applicable | error,
  reason_code: string | null,
  details: object
}
```

校准前/语义变化后 calibration=null；确定性阶段处理后全部检查键必填。
无声明/不适用为 not_applicable；输入不足为 incomplete；表示不符合为 invalid；工具失败为 error。
语义未获支持的适用检查 incomplete，不生成可消费规范值。

LiteralValue 复用现有 kind/raw_value/datatype_iri/operator/normalized_value/lower/upper/
lower_inclusive/upper_inclusive/raw_unit/canonical_unit/dimension/conversion_record。
规范数值以既有精确十进制字符串表示，不把原值覆盖成浮点数。
可消费规范值要求语义 accepted、datatype=passed、unit 和 shacl 各为 passed/not_applicable、literal 非空。
固定数字 SHACL 对 number/range/comparator 适用，非数值类型本期 shacl=not_applicable。
必需的 datatype/单位/SHACL 表示检查通过后，literal 才可作为规范值消费；
invalid/incomplete/error 时仍显示原值和问题，不借语义 accepted 标成规范化通过。

SHACL 使用固定内存 Graph 的字面量表示 profile，禁用 imports/inference/AF/JS/inplace；
没有 focus 的 vacuous conforms 不算通过。检查不能改变原文语义采信或引入业务必填约束。

## 6. Work、恢复与进度

WorkItem 增加 phase（skeleton/semantic/deterministic）和 retryable；
新 kind：entity_review、referent_alignment、entity_calibration、assertion_calibration。
闭合输入分别为 entity_id；group_id+mention_ids；entity_id；domain+assertion_id。
referent_work 只保留当前方案/反馈，WorkItem.status 是唯一任务执行状态。
work_id 含 phase；同阶段同依赖复用已处理结果，当前 batch 的付费回答精确应用。

cursor/main 增加/替换字段见方案第 9 节。阶段转出检查当前计划、所有直接步骤及 work，
不凭空队列或某个计数为零宣告完成。显式继续仅重入需要的 phase，不重建全文骨架。

HarnessProgress 增加：

```text
phase_work_counts: {
  discovery: WorkCounts, skeleton: WorkCounts,
  semantic: WorkCounts, deterministic: WorkCounts
}
calibration_counts: {
  identifier: CheckCounts, datatype: CheckCounts,
  unit: CheckCounts, shacl: CheckCounts
}
CheckCounts = { not_run, passed, invalid, incomplete, not_applicable, error }
```

WorkCounts 沿用 ready/waiting/pruned/done/failed。phase_work_counts 只计 work，
discovery 直接窗口调度以 reading_windows/reading 计进度；类型分片以窗口 skeleton_step 推进。
calibration_counts 汇总当前所有实体/断言的检查；calibration=null 按四个 not_run 计。
fact_count 仍为非根实体和断言的语义 accepted 数量，不能作为 SHACL/规范化通过数。

StageCost 增加 phase，metric.stages 按 phase+stage 聚合；
现有请求 payload.value.execution_phase 保存归属，无新增数据库列。
未测 token 保持 null；deterministic 不产生模型调用成本条目。
旧公共 WorkCounts 保留作为当前全部 work 的总和，不保留旧流程分支。
公共 work_counts 必须等于 phase_work_counts 逐项求和；阶段直接窗口工作不能凭该总和宣称收束。

## 7. 结束和失败

- 当前范围收束且只有正常未决/剪枝/表示问题：finished，但不宣称全量或全部通过。
- 局部 invalid 模型协议输出或确定性工具 error：保存相应 failed，继续无关项，最终 failed。
- 模型通信/完整响应失败、存储/所有权错误：按现有技术失败契约暂停推进。
- 已耗尽局部修正且依赖不变的失败 retryable=false；不能反复读取同一非法 proposal 自称继续。
- 原值和已获证图谱在所有情况下可查看；GET 零模型、零校准、零业务写入。

## 8. 契约验收

逐项对应方案 FS01—FS19，重点覆盖：骨架先形成；null 谓词可见而不采信；
局部分组失败隔离；成员重绑定不复制事实；顺序/并行原文证明；单位不补造；
SHACL 覆盖/执行/符合分开；阶段四零模型；四阶段暂停/继续与只读访问。
所有新增测试文件属于实施待办，不声称已存在或通过。
