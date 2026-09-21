# 类型证据与冻结指称绑定调查

调查日期：2026-09-21。范围：当前通用类型核验、冻结实体指称、上下文授权、字段绑定与桥接。只读源码、运行制品和实际保留的关系请求输入，并执行无模型的纯内存复现；本调查未修改生产代码、运行、提示、Schema 或本体。

**当前没有足够的既存结构，可以把“项目字段 + 附近剂型”的类型误判转换为一个可靠的确定性拒绝规则。** 类型证据与冻结指称没有单独的结构闭合校验，这是确定的实现事实；但“引用的是另一个对象或未来状态”仍需要语义判定，不能仅凭引文来自另一字段、段落或行确定。增加名称引文、限制同段同单元，或增加一份模型可自行填满的绑定 Schema，都不能据此证明同指和同一状态。

后续补查同时确认了一个应分开处理的确定性问题：首次 `describes` 的登记来源已混入实体类型辅助引文，模型再按全覆盖指令把它们用于对象角色。这是来源构造错误，能够在不判定类型语义、不放松关系门的情况下修正，见[输入来源角色补查](#补查首次-describes-的输入来源角色混合)。它不改变上面的语义类型质量结论。

协调方已确认：增强类型提示后，同一冻结目标的真实独立探针仍借附近剂型给出错误或缺证的类型支持。因此提示增强尚未解决真实类型质量。本文未读取该探针结果，探针结论以协调方实测为依据；本文提供其机制边界调查，不声称独立重跑或复核了探针。

## 当前代码实际校验什么

| 路径与位置 | 已检查的内容 | 没有得到的证明 |
|---|---|---|
| `claim_protocol.py:88-104`，`EntityProposal` | 固定类型 IRI、提及或记录组成、标识候选 | 实体契约没有独立的类型断言主体、类型桥接、模态或阶段字段 |
| `claim_freeze.py:285-299` | 提及及记录 subject/value 组成必须来自允许发现事实的原文 | 能定位名称或组成，不等于该对象具备类型定义的关键限定 |
| `claim_protocol.py:343-359`，`validate_verification` | 核验引文逐字可定位于授权上下文；supported 有非空 support | 每条 type 引文与冻结主体是同一对象、同一状态 |
| `claim_protocol.py:360-374` | record 的 referent support 覆盖全部冻结 record_components | 此覆盖要求没有独立应用于 type；即使同时覆盖两段文字，也不自动证明同指 |
| `verification.py:279-287`，`ProofGate.evaluate_frozen_claim` | 决策属于正确 target；引文在上下文内；facet 为 supported 且有引文 | 文本支持的实体归属、类型含义和时态本身 |
| `verification.py:388-395` | property/relation 必须通过相应 binding 等确定性检查 | entity 没有对应的物理所有者绑定检查 |
| `mentions.py:81-123`，`create_record_referent` | 组成与 subject_role 已核验，原文可重放，组成属于所选记录视图且被覆盖 | type 的语义成立；该函数根本不接收 type decision |
| `claim_protocol.py:1398-1435` | 用通过的三项实体 facet 和物理组成生成局部节点，保留来源与决策引用 | 把多项引用汇集到节点不产生它们之间的新归属证明 |

核验阶段允许 `fact_eligible=false` 的辅助原文检查已经冻结的声明，这是明确设计，见 `claim_protocol.py:343-348`，以及 `test_tool_engine_verification.py` 的 `test_supplemental_original_context_can_verify_but_not_rewrite_frozen_claim`。它不是授权泄漏：发现新事实与核验既有候选有不同权限，不能简单改为“type 禁止使用辅助上下文”。

此外，`required_facets` 对实体只要求 `type/referent/subject_role`（`claim_protocol.py:1091-1099`），没有属性和关系拥有的 qualifiers/counterevidence facet。当前无法从实体结构字段确定某句谈的是未来状态、关联产物还是当前对象；把这些区别判断错，仍然属于语义问题。

## 纯内存复现及其准确含义

复现复用了现有 `backend/tests/test_extraction/test_tool_engine_verification.py` 的 `case` 与 `gate_case`，没有新建测试文件或请求模型。只给可信传入的测试上下文添加一个辅助片段，然后把第一个实体的 type support 改为该片段，其他 facet 继续引用原冻结来源。

```python
from tests.test_extraction.test_tool_engine_verification import case, gate_case

frozen, context, response = case.__wrapped__()
claim = frozen.targets[0]
text = "另一个独立对象乙具有完整装置的类型特征。"
fragment = context.fragments[0].model_copy(deep=True)
fragment.anchor = fragment.anchor.model_copy(update={
    "evidence_id": "independent-other-object",
    "block_id": "independent-other-block",
    "span_start": 0,
    "span_end": len(text),
})
fragment.text = text
fragment.purpose = "required_context"
fragment.fact_eligible = False
context.fragments.append(fragment)
facet = next(item for item in response["verifications"][0]["facets"]
             if item["name"] == "type")
facet["support"] = [{
    "evidence_id": fragment.anchor.evidence_id,
    "text": text,
    "context_text": None,
}]
facet["reason"] = "模拟错误判断；仅测试确定性门，不调用模型。"
bundles = gate_case((frozen, context, response))
```

实测输出：

```text
entity_original_sources = ['ev-1']
entity_type_source = 'independent-other-object'
entity_policy_eligible = True
entity_validation_issues = []
model_calls = 0
```

该结果证明：在函数层，实体原始指称与类型引用分离时，当前确定性门不要求额外的主体归属闭合。它**不证明**现有 IR 已经知道新片段具有另一个排他的所有者：“另一个对象”来自这段模拟文字的含义，仍需语义理解。测试直接构造可信上下文，没有模拟在线授权更新、上下文哈希重建、模型输出质量或真实持久化，因此也不是绕过在线授权的端到端攻击复现。

## 字段绑定和已有桥接能否补足

### 字段组提供定位候选，不能提供唯一所有权

`FieldBinding` 的成员名本身是 `owner_candidate_refs`，`mapping_status` 默认是 `structural_candidate`（`field_bindings.py:16-30`）。表格将同行其他单元格列作候选 owner（`:41-68`）；普通字段组把组内其他字段值列作候选 owner（`:72-87`）。

字段组由“同节、连续段落、看起来包含冒号或等号的字段”组成（`records.py:203-257`）。这有助于找同一行或同一组文字，但没有独立证明各字段描述同一实例，更没有证明相同对象层级或阶段。不能把它升级为“同 field_group 就是同一实体”。

`context_records.py:8-22` 依据已有 subject_label 匹配组内值以收集候选 owner 原文；记录发现的上下文没有预设主体（`context.py:467-512`）。因此该路径也不能替本轮新实体创造一个已经证明的 owner 边。

`validate_field_binding` 可在**已明确选择字段角色、端点和 binding 的属性/关系声明**上拒绝错误绑定类型、缺少标签覆盖、错误列等（`field_bindings.py:173-216`）。裸实体 type facet 没有对应的选择和端点角色，不能机械套用这些检查。

### 已核验桥接有明确端点，但不会自动覆盖裸类型引文

已有 `BridgeStepView` 含 `subject_ref/object_ref/predicate_iri/source_refs`（`claim_protocol.py:818-841`）；共指绑定也检查明确的 source/target、scope、内容引用和独立核验结果（`source_assertions.py:243-281`、`:338-379`）。这些可以用于检查一条**已经被显式选择并核验的连接**是否涉及正确端点。

但实体候选没有类型桥接引用；现有桥接闭合主要服务 property/relation（`verification.py:452-511`）。`type.support` 只是原文引用数组，没有声明它借用的是哪个已有绑定端点。更不能把候选属性反向作为类型证明，造成“先假设类型才能解释属性，再由属性证明类型”的循环。

因此现有桥接并不提供“这个附近类型描述已经确定属于冻结实体”的可直接复用事实。本次上下文中的项目字段与剂型字段仅因共同阅读而出现，尚不足以确定它们同指，也不足以确定它们不同指。

## 哪些可以确定拒绝，哪些不能

| 情形 | 可作出的确定判断 |
|---|---|
| 引文不在授权来源、文本不匹配、来源版本或 target/hash 不匹配 | 按现有来源与冻结契约拒绝 |
| supported 没有有效非空引文；记录 referent 未覆盖冻结组成 | 按现有证据覆盖门降为未决，不生成对应事实 |
| 明确选定的字段 binding 与列、标签、端点不匹配 | 对该属性/关系声明拒绝；不能据此猜测实体 type 的未声明角色 |
| 已明确引用的桥接或共指证明不覆盖指定端点、scope 或版本 | 按既有桥接契约拒绝该连接 |
| type 与 referent 分别引用不同字段、段落、单元或行 | 仅能发现结构上尚未显式闭合，不能直接断言两个对象不同 |
| type 与 mention 出现在同段，或把两段同时放进 support | 不能据此断言同指；也不能证明该类型属于当前状态 |
| 连续字段包含名称、化学属性和剂型 | 物理分组不足以区分项目、物料、产品、关联对象或不同阶段；需语义核验 |
| 类型证据描述计划、可能性、后续状态 | 当前实体契约没有可直接执行的阶段/模态约束；需语义判定，不能用领域词过滤替代 |

## 最小处置建议与本轮质量记录

本次不建议增加新的形式绑定 Schema、不建议强制 type support 另附 mention、不建议限制同 fragment/row，也不建议把所有辅助上下文排除。上述做法要么仍允许模型把无关引用凑在一起，要么会误杀合法的跨字段、跨段落类型证据。

可以确定修复的，应限于已有明确声明及已证端点上的实际结构矛盾；本次没有找到一个既存、权威的类型证据所有者边，足以为当前误判添加这样的拒绝条件。若未来要让跨对象或跨阶段归属成为确定性门的输入，必须先确认输入里真的有相应已证明关系，而非再增加一个让模型自行断言的字段。这不是当前一处引用检查即可兑现的修复。

因此当前结论是：**存在类型证据结构关联不足的设计边界，但本次原料/剂型层级混用不能仅靠已有结构确定修正，真实语义质量仍未达标。** 更强提示尚未消除错误，不能把工程校验通过、图节点增加或处理批次继续执行当作语义成功。

当前运行 `20bf2d25…` 首组的逐项审阅已记录：revision 99 / event 9 实际为 6 个文档局部 DrugProduct 节点、2 个属性、0 关系，类型或实例归属存在错误/存疑，2 个局部字段值可定位但主体语义仍未通过。详见[该运行的候选审查](candidate-review-20bf2d25.md)及[逐项证据附件](candidate-review-20bf2d25-evidence.json)。该审查没有把口服片剂信息直接当作不存在，也没有把字段值正确等同于主体类型正确。

上述初次类型调查的运行计数与逐项结论引用该独立审阅；当时没有重复读取该运行原始制品。下方来源角色补查则另外读取了保存的实际请求、实体结果及原文，范围和依据单列。后续真实批次应继续按实际结果记录错误、未决、图谱输出和完成度，不能因已有结构门保护其他路径而隐去这些类型问题。

## 补查：首次 describes 的输入来源角色混合

本节只读核对运行 `20bf2d25…` 的原始实体结果、冻结提案、reference resolution、首次
`describes` 的实际保留请求以及原文。完整引用、锚点、结果键和输入文件 SHA-256 见
[来源角色证据附件](entity-source-role-review-20bf2d25.json)。没有新增模型调用或运行变更。
该节描述已暂停运行当时的行为；协调方随后进行的修复与测试须单独验收。

目标实体 `095ae2bf…@1` 来自 lineage `a9860bc8…`，关系来自 `49da0e1c…`：

| 阶段 | 实际来源 | 含义 |
|---|---|---|
| 原冻结实体及保存的 reference resolution | 1 个：P33 `[0,13)`，“项目名称：HRS-1597” | 物理指称 |
| 实体 type / referent / subject_role | 分别 14 / 2 / 1 个支持锚点 | 不同 facet 的语义支持；type 覆盖分子式、化学名、剂型与分类等字段 |
| 当时的 GraphNode.evidence_refs | 14 个，为物理来源和实体 facet 支持的并集 | 背景被装入后续物理来源入口 |
| 实际保留请求的 registered_entities.source_refs | 同样 14 个，proposal 仍只有 P33 一个 mention | 输入端已经角色混合，并非只有模型输出时才扩大 |
| 关系 object_support / predicate_support | 19 / 1 个 | 对象覆盖输入 14 个并多加 5 个；谓词只有 P33 |

14 个输入的段落编号为 P33、34、42、31、32、35、36、38、43–48；模型另外增加 P37、39、
40、41、49，P42 从 `[0,9)` 扩为含末尾空格的 `[0,10)`。这些化学或属性字段是被**实体
type facet**使用后进入节点，不能写成六条属性候选的证明被直接挪用。

调用链是：当时 `claim_protocol.finalize_claims` 建节点时并入每个 entity decision
的 support_refs；`executor.tool_dependencies` 把 node.evidence_refs 全量写入
`EntityDependencyView.source_refs`；`REGISTERED_RELATION_INSTRUCTIONS` 要求
object_support 完整覆盖该集合。实际保存的是关系 verification 阶段的 stage_input_items；
全覆盖发现指令来自源码检查，不能将其表述为取得了原 discovery 阶段的完整请求。

`source_assertions.document_description` 正确允许文档根缺少正文 mention，再要求
谓词来源覆盖全部提交的对象来源。此次谓词仅覆盖 1/19；即使模型只输出被指令要求的
14 个，也只有 1/14。因此原先把链失败仅归因于模型擅自多引背景不准确。模型额外增加
5 个字段是后续扩张，输入构造已经预先制造了同一问题。

### 其他调用方与最小修复范围

| 路径 | 只读检查结论 |
|---|---|
| `source_assertions._entity_sources` 与普通 endpoint grounding | 从冻结 proposal 的 mentions 或 record subject 组成推导签名，不把全部 node.evidence_refs 当身份证明；record 无 subject 时退回全部组成 |
| `source_assertions.validate_reference_binding`、`reference_dependencies` | 从冻结 source/target proposal 构造物理签名；没有要求所有 type support 都成为端点来源 |
| `reference_context.select_reference_entities` | 使用 node.evidence_refs 判断 physical overlap 和邻近；混合背景会扩大候选命中，不能把该命中解释为已证身份 |
| `executor.prepare_member_context` → `field_bindings.validate_local_owner` | 非根主体把 node.evidence_refs 放进 subject_evidence_refs；owner 检查要求全部覆盖，因此有相同的过宽绑定风险。本次只确认机制，未另跑真实失败案例 |
| `retrieval_query.build_subject_queries`、executor 的排序 mentions/seed records | node.evidence_refs 被作为主体提及或检索种子；类型背景可能被当作主体文字参与检索 |
| `tool_runtime.find_referent_candidates` | 实际名称定位来自 proposal；但返回 source texts 使用整个 dependency.source_refs，仍会传递混合后的集合 |
| `public_projection._registry/_node` | node.evidence_refs 是 entity source_selection_refs 的直接来源；修复后节点来源列表应只呈现物理指称，类型支持仍留在独立决策制品 |
| `projection` 的 node_valid 与 dependency registration | 核验非空物理来源及精确 decision/dependency refs；未找到必须把 type 引文塞在 node.evidence_refs 才能成立的关键校验 |

最小修复可以直接收紧 GraphNode.evidence_refs 的生产语义：仅存原 EntityProposal 的
全部 mentions 或**全部 record_components**；各 facet 的支持引文继续保存在
SemanticDecision/decision_payloads，并沿用 node 上的 type/referent/composition 决策引用与
dependency_refs。无需新增第二份物理状态或恢复分支，也无需从不精确的同名文字重建锚点。
记录实体应保留完整组成，不应为了缩短来源任取第一个名称或只保留一个 subject 单元。

应继续保留来源授权、版本依赖、原文重放、端点闭合和独立语义核验；不需要放松 `_covered`、
禁止使用背景进行类型核验或删除类型支持。该修复只能解决物理指称与 facet 证据的角色污染。
项目是否成品、剂型是否属于该对象、关系谓词是否成立，仍须单独证明；修复后的某条关系是否
通过，必须以新的实际运行核验，不能把机械来源集合收紧直接记成关系已实证。

## 补查：路由可达类型不等于本次发现获准类型

本节只读核对现有导出和调用链，没有请求模型、修改运行或调整本体。核对材料是 `/tmp/ontology-target-main-20260921/` 下的 `routing-scope-db-readonly.json`、`routing-parser9-db-readonly.json` 和 `context-first-verification.json`。前两份是协调调查已有的冻结本体重编译结果，均记录 catalog hash 与运行保存值相符；本节复核其类型集合，没有重新访问数据库。

两个运行 `8b97fa57…`、`20bf2d25…` 的冻结本体快照均为 `54c748b83397a74fd7268f96c8f72826c572bc0b5a457fec8e8e18da055831f2`。12 张根关系路由卡的直接 range 合集为 53 类，**没有 ActivePharmaceuticalIngredient**；API 出现在 `describes`、`hasSynthesisRoute`、`usesEquipment` 三条分支的多跳路由范围中。

| 核对层次 | 本次实际范围 | 能证明或授权的内容 |
|---|---|---|
| `describes` Routing Card 的 `class_iris` | 33 类，含 DrugProduct、API 及其他下游类型 | 用于章节/区域检索，不是模型可输出的实体类型菜单 |
| 同卡的 `range_class_iris` | 仅 DrugProduct | 当前根关系的正式对象范围；不能把 API 作为 CMCReport describes 的对象 |
| 首批真实核验的 `RecordDiscoverySchemaCard.class_cards` | schema `72ace4a266b08a6f71b03978d34761f781997b60cd496a29dd04034e1d9831b7`，仅 DrugProduct | 本次实体发现及核验没有 API 类型候选；冻结定义仍是 “A finished dosage form drug product that bears a clinical drug role.” |

`describes` 的完整 33 类清单（按保存顺序列本地名，不把本地名当 IRI）：DevelopmentPhase、CrudeProduct、ProcessIntermediate、StorageCondition、AcidBaseSolubility、ActivePharmaceuticalIngredient、ActiveSubstanceResidue、AcuteToxicity、ChemicalInactivatable、ChronicToxicity、CleaningAgentResidue、CytotoxicAPI、DrugProduct、GenotoxicityProfile、HeatInactivatable、HighPotencyAPI、HormonalAPI、InactivationDisposition、MicrobialResidue、NonInactivatable、OEB1、OEB2、OEB3、OEB4、OEB5、OEBClassification、OrganicSolventSolubility、ParticulateResidue、Residue、SensitizationPotential、SolubilityCharacteristic、ToxicityProfile、WaterSolubility。

这两个范围的分离是明确规范，而非本次误判后新增的解释：[spec 的 MVP 边界和成功口径](../../../specs/034-schema-region-routing/spec.md)要求不扩展 CMCReport describes 的正式 range，根层执行卡只包含直接 range；[基础方案第 7.1–7.2 节](../../基于本体指引的文档结构和摘要元数据的关系图谱识别方案.md)要求非当前 range 的正确解释保留为类型分歧/观察，不能直接产出该边对象。允许附竞争类定义作 contrast_context，也不等于扩大 allowed_output_types。

实现对应 `schema_region_routing.py:161-207` 的多跳范围与直接 range 分离、`executor.py:662-685` 按直接 range 编译根层 ordinary cards，以及 `record_discovery.py:173-174` 从实际 class_cards 取得可选类型。`RecordSearch._projected_card`（`record_search.py:138-159`）还要求所选类型来自源执行卡，不能从 routing class_iris 自行补入 API。

### 下游 API 何时获准，与当前实现的差距

按方案，只有具体 DrugProduct 的精确根入边已经证实后，才展开该实体的 `hasActiveIngredient`；或在具体 SynthesisRoute 的根入边证实后，展开 `producesFinalProduct`，后者也可允许 API。这里“展开”只给下一步检索和核验提供合法菜单，组成关系或最终产物关系本身仍须独立证明。API 与 DrugProduct 同名也不允许自动合并。

当前代码确认了**下游关系计划**的准入门：`register_entity_subject` 只保存新实体及局部菜单，不自动展开（`executor.py:3503-3543`）；`expand_tool_relations` 核验精确入边后才调用 `add_subject`（`:3770-3837`）。但不能据此声称“API 实体发现卡稍后一定会出现”：

- `add_subject` 建立已有主体的属性/关系检索计划，没有按新的关系 range 动态编译 RecordDiscoverySchemaCard；本次追踪到的实体卡编译入口仍在初始化阶段。
- 缺少已登记对象时，`hold_relation_for_endpoints`（`:3586-3612`）只优先已有发现卡。`admit_dependency`（`record_search.py:454-535`）只从当前未消费卡取得所需类型交集，不创造缺少的类型卡；其调用者 `prioritize_waiting_endpoint_discovery` 在已有任一符合条件的根边通过后直接返回（`executor.py:3692-3707`）。
- 另有必须单列的范围例外：`executor.py:780-791` 将**整个 discovery_catalog 的类型集合**传给 `compile_attribute_card`。该函数留下字段匹配的类和属性（`attribute_disambiguation.py:213-246`），区域合批又可将这类附加卡与所选根卡合并（`record_search.py:321-326`），未在此按根 range 再过滤。因此不能断言所有实际 record 卡永远只含根直接 range；若字段匹配下游类，它可能经附加卡进入。是否发生，必须核对该请求的实际 class_cards，不能用路由卡或普通卡代替。

本次直接核对的 `72ace4…` 请求确实没有 API；没有取得已在后续请求中发出 API 专卡的实证。因此“根关系通过后按需发现下游 API”目前只能区分为方案目标、已实现的关系计划准入和**尚未证实的实体发现卡调度**，不能合并写成已验收。字段附加卡的范围例外也不能当作这个后续机制已经实现的证据。本节只记录边界与差距，不据此扩大类型范围或修改调度。

### 与真实类型误判的关系

即使某段原文明示 API 并满足其定义，在只有 DrugProduct 的这次发现请求里，也不能合法输出 API；应保留观察/未决并等待合法识别机会。这是当前范围对 API 召回的限制。与此同时，菜单只有 DrugProduct **不要求、也不允许**把原料、化学物质、项目字段或属性整句强行赋成 DrugProduct；模型仍应逐一证明 finished dosage form 等关键限定及其主体归属。

因此，单类型菜单可能影响模型判断，但本次没有控制变量实验，不能断言它必然导致误判，也不能声称添加 API 菜单就能修复。已经观察到的“由分子式、溶解性、细胞毒性否定反推 DrugProduct”仍是核验语义错误；邻近真实片剂描述是否属于同一冻结对象还需单独判断。**类型发现范围受限、后续卡调度是否完整、当前类型证明错误是三个应分别报告的问题。** 不应靠放宽 CMCReport describes 的 range、同名合并或属性 domain 反推类型让图谱看起来完整。

材料 SHA-256：`routing-scope-db-readonly.json` 为 `d53e496e38ab474a5be5e71fe6172f9e55820b228f8546f000a686f7bc5a343e`；`routing-parser9-db-readonly.json` 为 `79e46ac6df79d30a2ee72ace5c5616849337add57a3c32b3a1d631036ac8f894`；`context-first-verification.json` 为 `897f870a5e836a0d19418bed4c24a14c7af63c68de46daa64092b0f5308156f9`。以上是已有导出的只读检查，没有新增模型实验或测试通过声明。
