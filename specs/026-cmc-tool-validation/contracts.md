# 验证契约

## Mock 设备实验的附加契约

E0/E1 使用相同原文和 NER；E0 的 external_key 只能为空，E1/E2 只能选择工具实际返回的 key 或留空。候选 entities 包含局部 ID、ProcessEquipment 类、原文 anchor、equipment_id 引文、外部 key 与文档属性。关系固定为 document — usesEquipment — 局部实体，object_ids 配合 all/one_of/undetermined，保留原文引文、条件与极性；one_of 是实验声明的备选组，不是新本体谓词。

工具 search 返回冻结记录 key/version、三个已映射字段及匹配坐标；未匹配、同名多候选和预算排除显式返回。validate_link 校验版本、原文坐标与精确编号，不决定文档关系。外部属性必须带 system/dataset/record_key/record_version/field_path/value，不能伪装成文档引用。Qwen 输出中的未知/冲突保持原值，不用Mock覆盖原文。

每条实体、属性和关系在候选生成后分配固定核验ID；第二次Qwen只判 supported/unsupported/undetermined 并引用原文，不改候选。确定性检查同时检查引用、原文角色归属、候选编号不合并及备选范围。通过文本SHACL仅说明字段表示合规，不证明数值换算或关系语义。

计划 JSON 使用独立字段：get_schema_card（最多 6 个类槽位，可重复类以表示不同记录）、inspect_evidence（scope 内 ref）、propose_mentions（白名单标签组）。服务端分配 s1…s6，document 固定为 CMCReport。第二次 JSON 的各主体包含 anchor、该类允许的 attributes/relations；关系目标仅为允许类型的槽位，未使用槽位 anchor 为空且不得有声明。引用使用 ref、逐字 quote、可选 start/end。

check_claim_binding 输入冻结 candidate_id、subject_id、谓词字段和 proposal，返回原文/归属检查及原值，不自证语义。

validate_metric 输入原值、冻结 SlotSpec、已完成的 binding_issues 和 source_unit；只有外部 semantic_status=supported 后才提供规范化值。返回 execution_status、validation_status、quantity、SHACL 原始结果和覆盖、semantic_status，工具自身 fact_eligible=false。

第三次 JSON 按冻结 ID 返回类型、属性/关系、观察的分维度判定和原文支持/反证。控制器检查精确 ID、引用和所有必要维度；修改后的候选不得沿用旧结论。最终保留内容仍为验证候选，未自动提交事实。

真实前两次返回暴露 ID 被用作上下文锚点的情况后，引用解释器采用 `unique-nested-context-v2`：相等 quote/span 使用工具精确坐标；否则 quote 必须在整个同源单元逐字唯一（包含重叠出现检查），且与 span 区间完整包含。跨 ref、不相交、部分重叠、重复歧义和坏工具坐标均拒绝，不改 quote/raw。错误隔离到候选，不能使其他正确候选丢失。

保留原解析结果，复用原计划与候选，只为尚未执行的第三次请求补语义核验；原有请求全部计入同一 48 次预算。已有第三次响应仅在完整语义目标相等时复用；请求已失败不自动重试。该规则修复定位解释，不属于模型重新生成改善。

真实 PDE 返回另暴露跨行共享 API 被当作单一试验记录的问题，独立 Qwen 仍可能误支持。确定性绑定因此增加 `owner_record_ambiguous`：不同物理单元格的多行 owner 不能单凭行交集绑定到其严格子集行的值。同物理 cell/同跨度共享标识仍允许，单行试验锚点正例保持。未决原值不做正式规范化，不为通过而修改候选身份。

完整数量检查同样适用于带单位的 raw，不能从“不超过25℃”“50~55℃”或“25℃”截出精确“25℃”“55℃”或“5℃”。原文比较/区间可以完整保留为相应表示，不能为了适配标量字段丢掉限定。

观察的明确 unsupported 归拒绝，证据不足归未决。技术失败 scope 的验收标为未评估；计划已成功但候选超时，仍保留计划合约计数与原超时原因。复用第三阶段语义返回时，语义目标必须不变，但绑定和指标预检制品要更新为本次检查器的结果。

## GLiNER2 / SKOS 契约

词表编译器输入冻结卡片、所选类 IRI、本地权威 TTL 和实验 SKOS overlay；不接收报告文字或评分参考。输出保留 entities/values/units 三组标签，以及标签到 IRI、角色、prefLabel、altLabel、description 和来源的映射。缺项显式报告，禁止从原 label 任意截词回退或继承子类别名为父类同义词。

GLiNER2 适配器接收本地模型路径和标签描述，在已开启 Hugging Face/Transformers 离线模式的进程中加载。输出逐字可核对的 start/end/text/label/score；未知分数校准状态不作事实概率。沿用 160 字窗口/24 字重叠，本轮采用保守的 512 encoder token 门、每批 1 个带描述标签，实际编码超限或输入截断按失败报告。512 是本实验限制，不作为相对位置编码器的绝对架构上限声明。

追加真实验证使用 GLiNER2.5 boundary，经 `AutoExtractor` 加载并核验架构；没有固定 max_width，保留 checkpoint 实际共享候选预算。实例批处理保留原文并构建官方 boundary metadata，严格处理空结果、坏坐标和批次失配。模型权重、tokenizer 与依赖身份冻结；不修改原始下载配置或词表以追求命中。

C 组复用首轮 B 的工具计划，只新执行候选与冻结语义两阶段，最大 16 次请求。第一轮调用成本不重复计入；保留原计划来源和新词表/模型身份。原 Qwen schema、提示词和最终证据/单位/SHACL 门保持一致，不能用词表定义替代原文证据。

GLiNER2 单标签批次会产生较多执行明细。Qwen 工具提示保留全部 span、状态、IRI/角色及引用目录，省略重复的按组 span 列表和批次/计时明细；完整 tools.json 保留所有信息。这项输入组织变化也属于本轮组合方案，不作单变量收益归因。复用准备好的工具时校验实际文件摘要、原文对冻结 IR 的相等性及计划对历史 B 响应的一致性。

## 现有摘要检索 D 组契约

复用 `cmc-23c872fb-20260907-02/summaries.json` 的历史 `word-tree-summary-v1` 章节／子树摘要。按原文摘要身份、同一冻结 IR、全部节点 ID 与内容哈希核验，再通过现有 `prepare_metadata` 生成契约；保留 29 completed、2 partial、1 failed（根节点 extractive_fallback）状态，不生成新的摘要请求。

查询仅由旧 C 主体卡片中的实际类／谓词产生，按类和谓词去重；关系目标采用冻结卡片中的允许类型。现有 `plan_slot` 在全文 `RecordIndex` 中计算每个查询的 `4×原文标签 + 2×标题 + 摘要` 得分；跨查询等权求和，按总分降序、原文位置稳定破同分。固定取正分记录，最多 6 条；包含原文上下文后去重文本最多 12,000 字符。整条记录及其上下文超过预算即显式 deferred，不截断，不读取银标调分或回补原章节。

同一输入将摘要内容屏蔽为 None 再执行同预算检索，记录完整排序、分量、入选集合／顺序变化；消融阶段不调用 Qwen。两种检索都使用相同的类菜单和原文结构。既有类槽位来自旧固定片段实验，因此本轮仍不是从 CMCReport 根类自主遍历全部关系图的验证。

回放记录保存目标原文、表头、注释、父上下文、字段组和祖先标题的来源角色，实际引文由 IR 原单元生成。历史 parser 标为 header 的首行不自动当成真实表头，沿用 `build_sources` 的物理结构与字段校验。摘要不得出现在证据 source refs、候选锚点或属性原值中。

旧 inspect_evidence 的 uN 引用全部重建。NER／Qwen 用新 source refs、相同词表和检查器，最大 16 次新请求、每组 2 次；调用失败不重试。D/C 改变了记录选择和上下文范围，质量对照需以原文证据 ID 及覆盖范围解释，旧片段行号清单不直接作为 D 的完整评分。
