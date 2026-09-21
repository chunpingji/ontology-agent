# 8b97fa57 运行前两组候选与核验审查

审查日期：2026-09-21。运行：`8b97fa57-e2a2-477e-b836-1e8094813dc0`。输入报告：`upload-a255fd30-192c-4f81-92df-5f76a56b8383`。

**前两组最终产生 1 个文档局部实体节点、0 属性、0 关系。** 来源引用、缺失值、组成覆盖和依赖校验拦住了多项错误；类型语义核验仍把共有化学属性当作 DrugProduct 类型证据，且第二组的项目代码节点实际通过了事实门。因此不能把“没有错误属性入图”解释为类型识别已正确，也不能把模型的 `supported` 直接当作最终图谱结果。

本报告只审查此运行前两组。未读取新提示的独立 probe 输出，不评价后续组或整份报告的召回率。

## 证据与独立预期

本次读取的运行导出位于 `/tmp/ontology-target-main-20260921/`，仅分析 `results-type-paused.json` 的 `results['calls:results']` 域；未使用 ranking 向量。另读取 `context-first-verification.json` 中实际请求及 `source-v2.json` 中原文。

第二组首批 6 个目标的[独立预期附件](expected-first-verification.json)于 `2026-09-21T04:49:30.836093+00:00` 固定，当时尚未读取核验输出。附件原样复制，未按实测结果改写。它覆盖 `e1/p1/p3/p4/p5/p8`；后续 `e2/p2` 的判断属于本报告事后审阅，不冒称预先盲评。

| 文件 | SHA-256 |
|---|---|
| `results-type-paused.json` | `1ba5f82d97caf35493864f113fc8a98879c19151208205956be7e8a5383a9578` |
| `context-first-verification.json` | `897f870a5e836a0d19418bed4c24a14c7af63c68de46daa64092b0f5308156f9` |
| `source-v2.json` | `864f2c4e72763fef6dd2aea3e7b5c1849a5a2794113360c58a4513a4ebca6265` |
| `expected-first-verification.json` | `13f9de1a485846d05aa88c620e8243e9710c8e73701fdee6efb49c548b444e17` |

## 第一组：结构纠正后仍全部被来源门拦截

Lineage：`4632c19257887b7884ecac21c14e10d5c79b65569c7a22bb821f6a59c45d2280`。

首答提出 3 个实体、3 个属性和 2 个观察。实体与属性重复使用 `e1/p1/e2`，重验实际得到 `duplicate_or_empty_local_id`，不能冻结。首答还存在以下语义问题：

- 将包含 pKa、logD、熔点的“理化性质”整句映射为 `targetIndication`，与适应症定义明确不符。
- 将“理化性质”“制剂剂型”“溶解性”字段整句分别作为 DrugProduct 提及；字段值可定位不能独立证明该类型的实例。
- `CAS：N/A` 的缺失提示有文本依据，但其主体未证；“理化性质段没有溶解性信息”的缺失提示不能推为整个对象缺少溶解性，同批已有明确溶解性字段。

纠正答改为 9 个记录实体、9 个属性和 9 个共指绑定。全部 27 个声明均有 `claim_issues`：

| 问题码 | 声明数，允许重叠 | 实际依据 |
|---|---:|---|
| `source_excerpt_mismatch` | 15 | 把 HRS-1597 引到“是否是高致敏药物：否”的来源，把 HRXS-P-2419 引到“是否是肿瘤药物：否”的来源；部分属性也错绑来源 ID |
| `record_composition_source_invalid` | 9 | 9 个记录实体的组成引用均无效 |
| `reference_binding_endpoint_invalid` | 9 | `rb1…rb9` 均为 `source_id == target_id` 的自指共指 |
| `ambiguous_source_quote` | 4 | `p2/p4/p5/p6` 只引用“否”，该字既出现于“是否”也出现于末尾值，且 `context_text=null` |
| `entity_dependency_invalid` | 2 | `p1` 溶解性、`p3` 剂型局部值可定位，但主体无效 |

最终 `verification.targets=[]`，没有执行这组的 LLM 语义核验。`outcome` 为 `undetermined`、`complete=false`，实体、属性、关系、证明及决策输出均为空。来源拒绝与依赖传播符合预期。

首答中的错误适应症映射和 N/A 观察在纠正答中消失。因此第一组没有验证语义核验能否拒绝该错映射，也没有验证 N/A 属性值门；不能把这些能力归功于第一组的空图。

## 第二组：缺失值门有效，类型判断存在实际缺口

Lineage：`a5333ac3d98af15373571f3ed87c9caef2a13fb7f70ca4eae1d5e2c97ce38b19`。

发现结果为 2 个记录实体、16 个属性。`e1` 仅以项目名称 HRS-1597 作 subject 组成，`e2` 仅以项目代码 HRXS-P-2419 作 subject 组成；两者都声明为 DrugProduct，均未提出 `identifier_claims`。

冻结阶段拦截 10 个属性，留下 2 个实体、6 个属性进入语义核验。核验分为首批 6 个目标和第二批 `e2/p2`。

### N/A 与来源校验

`p7` 原候选是 `casNumber = N/A`，原文为 `CAS：N/A`。冻结结果实际记录 `claim_issues['p7'] = ['attribute_value_missing']`，并保留 `kind=missing`、引文为 `N/A` 的观察，理由为“原文完整字段值为缺失或未知标记，不能作为已识别属性实值”。`p7` 未进入 8 个语义核验目标，也未成为图谱属性。这一组真实验证了完整字段缺失标记门。

另有 9 个属性因 `source_excerpt_mismatch` 被拦：

- `p6` 的中文化学名值本身完整，但其 `field_support` 写成原文没有的“化学名：中文”；该单元实际前缀为“中文：”。这属于标签引用错误，不应说化学名值被截短。
- `p9` 把“口服片剂”挂到英文化学名来源；`p10…p16` 把其他字段内容挂到“性状”来源，引用错配。

发现模型自报的其他 `missing` 观察仍是未经证实的提示。例如“未证明溶解性归属于 e1”是归属未决，不能当作溶解性缺失；当前局部片段未出现适应症，也不能当作全文缺失。它们不构成事实或完整度证明。

### 首批 6 个目标：模型判断、来源校验与最终结果

固定本体的 DrugProduct 定义是：`A finished dosage form drug product that bears a clinical drug role.`

| 目标 | 预先固定的人工预期 | 完整纠正答的原始模型判断 | 校验及最终结果 |
|---|---|---|---|
| `e1`，HRS-1597 | 当前窗口未证明 finished dosage form，`type` 应未决，不应仅凭名称和化学属性接受 | `type/referent/subject_role` 全为 `supported` | `referent` 引用分子式，未覆盖冻结组成 HRS-1597；校验改为 `undetermined`，报 `record_composition_source_coverage_missing`，最终未落图 |
| `p1`，`regulatoryStatus=HRS-1597` | 项目名称不是申报状态，字段角色和谓词应拒绝 | `field_role/predicate=unsupported`；其他多项为 `supported` | 错映射被拒；`counterevidence` 空 support 另被改为未决；形成 `rejected_mapping` 属性候选，无属性事实 |
| `p3`，分子式 | 原字段和值正确；受 e1 类型与归属证明约束 | 全部 required facets 为 `supported` | `counterevidence:support_missing`，且 e1 未获证明；最终为 pending 候选，无属性事实 |
| `p4`，分子量 500.60 | 原值正确；单位政策是 `not_declared`，不补造单位 | 完整纠正答中全部 facets 为 `supported` | 空反证核查 support、主体依赖及数量表示等门未通过；pending，无属性事实 |
| `p5`，英文化学名 | 完整 150 字符，原值正确；受主体证明约束 | 全部 facets 为 `supported` | 空反证核查 support、主体依赖及 `field_column_mismatch` 等问题；pending，无属性事实 |
| `p8`，粉末性状 | 原字段和值正确，不能直接转为成品片剂的性状 | 全部 facets 为 `supported` | 空反证核查 support、主体依赖等问题；pending，无属性事实 |

这里的“原始模型判断”指第二组 `attempt=3` 的完整纠正答；此前 `attempt=2` 没有完整覆盖全部目标和 facets，不能按完整核验计分。

e1 的模型类型理由是：

> 实体e1在文本中被描述为具有分子式C23H28N6O5S，对应Schema中的DrugProduct类型（药物产品/原料药），该类型包含molecularFormula属性。

这条理由把属性菜单反推为类型证据，并自行把限定为 finished dosage form 的类型扩写成“药物产品/原料药”。e1 最终被组成覆盖门拦住，不意味着这个类型判断正确；若仅补齐 referent 引文，现有错误类型理由仍可能通过。独立预期与类型模型判断确实不一致。

`counterevidence` 在完整纠正答中被模型写为 `supported`，但这 5 个属性的 support 均为空，服务端实际改为 `undetermined`。报告不把模型“未发现反证”的文本当作已经核查原文的证明。其余 `bridge_kind_not_available`、`quantity_representation_missing`、`field_column_mismatch` 等是实际记录的问题码，本报告不因它们阻止落图，就推定每个问题码的业务判定已经独立验证正确。

### 第二批 e2/p2 与最终落图

`e2` 不在预先固定的 6 目标附件中。后续核验将其 `type/referent/subject_role` 全部判为 `supported`；引用实际可定位，组成覆盖也通过。类型理由仍为“具有明确的分子式、分子量和物理性状描述，符合药物产品（DrugProduct）类型的定义”，没有证明 finished dosage form 这一限定。

最终 e2 节点实际进入本次文档分析图谱：

- `entity_id = 2ef16370bff382b8679950b5bbd35a83671be0292ebddf6da370c1d4d8240425`
- `label = HRXS-P-2419`，`class_iri = …/drug/DrugProduct`
- `identity_status = document_local`，`grounding_kind = record`
- `decision_status = supported`，`independent_review = unreviewed`

事后人工审阅认为，项目代码、共同化学属性和局部记录归属不足以证明此节点的成品制剂类型，属于类型证据不足仍通过事实门的问题。**不能将其描述为“系统错误确定了项目代码的全局唯一身份”**：该节点明确只是文档局部身份，也没有 identifier claim。本报告亦不把分析图谱中的系统通过等同于人工确认或业务事实提交。

`p2 = projectCode` 的原值和字段对应正确，模型及来源层 facets 均通过；最终被 `bridge_kind_not_available` 拦住，保留为 pending 属性候选，未成为属性事实。

第二组 `outcome` 实际为 `semantic_outcome=supported`、`complete=false`：1 个节点、0 属性、0 边、0 关系组，另有 6 个属性候选（1 个 `rejected_mapping`、5 个 `pending`）。该 supported 汇总表示存在通过项，不表示整组完整或语义质量合格。

## 全文证据与核验可见范围

人工读取的全文简介将 HRS-1597 描述为新分子，说明先前供样形式为原料药，当前修改结晶纯化步骤；这些信息支持化学物质/物料解释。首批核验请求只提供“产品的结构”附近字段与辅助上下文，没有提供上述简介。故固定预期在该窗口对类型采用 `undetermined`，不能要求模型引用未收到的简介，也不能把未检索到远处反证归成仅由核验模型造成的错误。

同样，全文基本性质写分子量 500.60，设备主残留物表另有 500.18。后者没有进入首批核验窗口。应保留两处来源角色和范围后核对，不能静默统一，也不能要求本轮 `counterevidence` 凭空发现未提供的设备表。本报告没有把两值直接认定为同一具体化学形式的已证冲突。

## 对方案可行性的限定结论

本轮真实运行证明来源绑定、完整字段缺失值、记录组成覆盖和部分语义错映射能够拦截候选；同时也暴露了类型核验可用共有属性和项目语境替代类定义关键限定的问题，且存在实际通过的局部节点。仅靠这些前两组结果，不能宣称识别精度或关系/属性完整度已达标。

本报告审查的是记录发现与其候选核验；该阶段的关系数组按契约为空，0 关系不能单独用于评价后续关系识别能力。

针对此问题的通用提示修复要求按冻结类型定义核对关键限定，禁止从属性 domain、菜单或字段值反推类型。该修复属于本次旧结果之后的改动；新提示的独立 probe 和后续新运行不在本报告内，不能以工程测试通过替代真实语义效果验证。
