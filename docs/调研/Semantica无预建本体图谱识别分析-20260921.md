# Semantica 无预建本体的实体与关系图谱识别分析

日期：2026-09-21。对象：[semantica-agi/semantica](https://github.com/semantica-agi/semantica)。
源码固定为 `c5e2fbffca38cb9e44ba5dc52369832a47f22379`，依赖清单版本为 `0.7.0`。
依据为 Context7 文档查询与该提交的源码调用链；没有安装 Semantica、运行其模型或执行 CMCReport 实证。
本报告不变更当前识别管线、权威本体、运行状态或分析页面。

## 1. 结论

Semantica 可以在没有预先人工建立领域本体的条件下构建图谱，因为抽取器直接从文本预测实体标签和
关系谓词，再将这些结果组织为节点与边。先验来自预训练模型、规则和提示词；输出仍有固定的数据
结构，但不要求实体标签和谓词已经属于一份 OWL 领域本体。

核心流程是：文本 → 实体及类型候选 → 关系/三元组候选 → 端点匹配 → 图谱组装。
实体归并、抽取验证、领域本体校验、共指消解和本体生成是按调用路径配置或显式调用的能力，不能
因为仓库存在相应模块，就认为每次 `GraphBuilder.build()` 都执行了这些步骤。

这解释了它如何产出图谱，但不能证明图中的每条边已经通过原文语义核验，也不能证明文档中的关系
与属性已全部识别。其通用抽取链与我们讨论的“实证后实线化”采用不同的接纳口径。

## 2. 实际抽取机制

### 2.1 实体类型从哪里来

`NERExtractor` 默认 `method="ml"`，ML 路径调用 spaCy，默认模型是 `en_core_web_sm`，直接读取
`doc.ents` 的实体文本、标签和字符位置。此时标签来自模型训练时学习的通用分类，不需要事先
建立 CMCReport、SynthesisStep、ProcessIntermediate 等业务本体。源码没有给标准 spaCy 输出
虚构逐实体概率：模型不提供分数时使用 `None`。[S1]

LLM 路径要求模型输出 `text / label / confidence`。未指定实体类型时，提示词提供 PERSON、ORG、
GPE、DATE、EVENT、PRODUCT、CONCEPT 等通用类别；指定 `entity_types` 后，提示词称它们为
“Preferred entity types”，仍允许相关、同义或领域类型。输出 schema 的 `label` 是字符串，
不是领域类 IRI 的强制枚举。[S2]

因此“无需人工先建本体”的关键是由通用模型及提示词提供初始语义，而非消除了类型判断问题。
默认英语通用 NER 的存在也不构成中文 CMC 文档识别质量的验证。

### 2.2 关系谓词从哪里来

| 路径 | 实际机制 | 语义来源 |
|---|---|---|
| pattern | 匹配 founded_by、located_in、works_for 等内置语言模式 | 人工规则及关系词 |
| dependency | 从依存分析寻找主语、动词、宾语，以动词 lemma 作为谓词 | 句法模型与动词 |
| LLM | 给模型原文及实体列表，生成 subject、predicate、object、confidence | 模型语义判断与提示词 |
| triplet | 由关系转换，或直接从原文生成三元组 | 所选规则或生成模型 |

LLM 关系抽取未配置关系类型时，提示词要求生成有意义的关系，并列举 `related_to`、`part_of`、
`located_in`、`created_by`、`uses` 等示例。配置 `relation_types` 后仍是 preferred types，
允许相关谓词。`RelationOut.predicate` 是字符串；JSON 字段合法不等于谓词满足领域本体。[S3]

直接 LLM 三元组提示词还要求主客体是原文子串。这是生成要求；不能仅凭提示词就断言每项输出
都通过了字符定位、主体归属、否定及条件等服务端校验。[S4]

### 2.3 一个 CMC 语句为什么也能被抽取

以“步骤 S1 使用反应釜 R101”为概念示例，模型可以依据“步骤”“反应釜”“使用”的语言语义，提出
`S1 — uses → R101`，而不必先获得 `SynthesisStep — usesEquipment → Equipment` 的本体定义。

但输出中的 Step、ProcessStep、SynthesisStep，以及 uses、uses_equipment、使用设备，是否
指向同一个规范概念，仍需对齐。R101 属于哪个项目、批次或局部表格，是否与别处同名设备相同，
也不能从三元组字符串本身确定。这是说明机制的示例，不是本次模型实测输出。

## 3. 图谱如何组装，哪些能力默认没有执行

`GraphBuilder._extract_from_text()` 的默认方法为 NER=`ml`、relation=`pattern`、triplet=`pattern`。
单独关系抽取的开关默认是关闭的，三元组抽取默认开启；三元组抽取器在缺少关系输入时还会调用
关系抽取器。因此不能将 `extract_relations=False` 理解为默认图谱没有任何边。[S5]

实体和关系/三元组转换为统一字典后进入图谱。LLM 关系端点会与实体列表进行匹配；匹配失败时，
解析器可构造 `UNKNOWN`、`synthetic=True` 的实体。GraphBuilder 提供补入这些端点或拒绝关系
的策略。这解决的是图的端点引用问题，不能证明补入实体在原文中的真实位置及类型。[S6]

`GraphBuilder` 默认 `merge_entities=False`。开启归并或显式传入 resolver 后，`EntityResolver`
识别重复组、执行归并，GraphBuilder 再根据 `merged_from` 重写边的端点。精确或相似匹配可以帮助
聚合同名、别名，但其结果仍需符合业务身份范围；不能直接将相似度当作本项目的身份实证。[S7]

源码中的 `GraphBuilder` 默认链没有自动调用 `SchemaValidator`、`ExtractionValidator`、
`GraphValidator`、`CoreferenceResolver` 或 `OntologyGenerator`。调用方可以显式组合这些模块。

共指模块本身也需要区分实现：`CoreferenceResolver` 的代词解析选择最近的前置兼容类型实体，
实体共指按小写文本相同分组；配置 `method="llm"` 会影响其 NER 子步骤，不能据此声称整套
共指判定使用了 LLM 原文推理。[S13]

## 4. “识别到了边”与“边已实证”的差别

### 4.1 共现与相邻位置可以产生边

`RelationExtractor.extract_relations()` 在配置的方法未产生通过阈值的结果时，会进入内部模式
回退。内部 `_extract_with_patterns()` 除规则匹配外，还会将字符距离小于 100 的实体对生成
`related_to`、confidence=0.5 的边。若仍没有边，则按实体列表中的相邻项生成 confidence=0.3
的 `related_to`。[S8]

该函数对前面的所选方法执行阈值过滤，但在上述回退结果返回前没有再次执行同一阈值过滤。
这是本提交中该入口的具体控制流；下游是否另行过滤，要看调用方。不能泛化为所有调用最终都
保留弱边，也不能将这些边解释为“使用”“组成”“产出”等业务事实。

### 4.2 校验主要保证什么

| 校验入口 | 核对内容 | 能否单独证明原文支持该关系 |
|---|---|---|
| `RelationExtractor.validate_relations` | 主客体、谓词非空，主客体文本不同 | 不能 |
| `ExtractionValidator` | 置信度、空内容、关系基本结构等 | 不能 |
| `GraphValidator` | 字段、ID、端点存在、类型规则、环和孤点等 | 不能 |
| `SchemaValidator` | 类和谓词词表，以及 domain/range | 不能；它核对领域合法性 |

上述结论来自对应函数的输入与检查逻辑，不是根据模块名称判断。[S9]

规则路径中的 0.7、句法路径中的 0.8 是实现给定的分数，LLM 路径的 confidence 来自模型输出或
schema 默认值。它们不能直接解释为经过独立标注数据校准的事实正确概率。

可选 `LLMExtraction.enhance_relations()` 确实在提示词中要求检查关系、补漏和改进分类，但实现采用
增量合并：原关系未出现在模型回答中仍保留，新谓词作为新边追加，失败则返回原列表。因此该模块
不能直接等同于会拒绝错误边的独立证据门。[S10]

关系结构可以保存 context 和 metadata，但基础 `RelationOut` 没有强制逐关系证据片段、极性或
条件字段；LLM 实体位置字段默认允许 0。来源记录、类型约束和模型分数分别有用途，均不能单独
代替可定位原文及其语义证明。

## 5. 本体可以后置生成，也可以前置约束

该项目并未排除本体。`bootstrap_schema()` 接受已抽取的实体与关系，复用 `OntologyGenerator`
归纳类、属性与 domain/range，再导出 TTL。默认频次门槛为 2；结果标记 `draft=True`，源码明确
规定不自动应用，须经人工确认后再约束未来抽取。[S11]

另一路径可通过 `ExtractionSchema` 读取本体，由 `SchemaValidator` 校验类、关系及端点类型。
因此它既能支持开放发现，也提供将发现结果收敛到领域模型的工具。后置生成本体反映已有样本，
不能自动发现样本中从未识别出的应有关系或属性。

## 6. 它的完整度不是本次提出的文档识别完整度

`OntologyEvaluator._calculate_completeness()` 检查类是否有 name、uri、label，以及属性是否有
name、type、uri，然后计算两种字段完整比例的平均值。
`evaluate_relation_completeness()` 统计在属性 domain 中出现的类占比及孤立类。[S12]

这些指标针对本体描述与结构，不是在计算“文档应有的设备、工艺步骤、关系和属性已实证多少”。
在已检查的默认抽取与构图链中，也没有我们所需的待识别目标分母、未发现目标占位图和实线化门槛。
生成结果自身无法提供全部未识别事实的分母。

## 7. 对 CMCReport 图谱分析的设计启示

2026-09-21 已按用户要求将本节有价值部分合并到
[基础方案第 7.5、11.5 节](../基于本体指引的文档结构和摘要元数据的关系图谱识别方案.md)及
[034 区域执行增量](../../specs/034-schema-region-routing/plan.md)。合并状态仅指设计文档；
区域关系批量发现、虚拟图谱页面和指定报告实证仍待实施与验证。

建议沿用已讨论的本体虚拟图谱，同时将模型执行组织为“按文档区域批量提出候选”：[原方案](../基于本体指引的文档结构和摘要元数据的关系图谱识别方案.md)。

1. 从 CMCReport 本体编译本次范围内的关系和属性目标，初始化虚线、占位节点和完整度分母。
2. 对同一段落组或表格区域批量发现实体、属性与关系候选，保留原文定位和局部主体范围；只向模型
   提供该区域相关的本体类型与谓词，减少任意标签及后续对齐歧义。
3. 将候选对齐到规范类/谓词，核验类型、主体归属、方向、单位、否定和条件，并验证原文证据。
4. 只有通过证据核验的关系变实线；共现、相邻和相似度用于检索或候选提示，不作为实线依据。
5. 根据缺失目标定向补查；实例展开带来的新目标计入当前范围。关系与属性完整度独立展示，技术
   执行进度单列，未发现证据不得自动从分母中消失。

这里借鉴的是文档驱动的候选生成与批量处理，不需要引入第二份事实图、自动改写权威本体或整套
Semantica 依赖。是否减少调用量、提高召回或缩短时间，需要在同一份 CMCReport 原件和模型上
实际测量；本报告没有据源码推算出性能或质量收益。

## 源码依据

以下链接均固定到本次检查的提交，避免后续 main 更新改变依据。

- [S1：NER 默认接口](https://github.com/semantica-agi/semantica/blob/c5e2fbffca38cb9e44ba5dc52369832a47f22379/semantica/semantic_extract/ner_extractor.py#L135)；[ML 实体实现](https://github.com/semantica-agi/semantica/blob/c5e2fbffca38cb9e44ba5dc52369832a47f22379/semantica/semantic_extract/methods.py#L985)。
- [S2：LLM 实体提示词](https://github.com/semantica-agi/semantica/blob/c5e2fbffca38cb9e44ba5dc52369832a47f22379/semantica/semantic_extract/methods.py#L1280)；[输出字段](https://github.com/semantica-agi/semantica/blob/c5e2fbffca38cb9e44ba5dc52369832a47f22379/semantica/semantic_extract/schemas.py#L4)。
- [S3：LLM 关系提示词](https://github.com/semantica-agi/semantica/blob/c5e2fbffca38cb9e44ba5dc52369832a47f22379/semantica/semantic_extract/methods.py#L2053)；[规则路径](https://github.com/semantica-agi/semantica/blob/c5e2fbffca38cb9e44ba5dc52369832a47f22379/semantica/semantic_extract/methods.py#L1455)；[依存路径](https://github.com/semantica-agi/semantica/blob/c5e2fbffca38cb9e44ba5dc52369832a47f22379/semantica/semantic_extract/methods.py#L1760)。
- [S4：LLM 三元组提示词](https://github.com/semantica-agi/semantica/blob/c5e2fbffca38cb9e44ba5dc52369832a47f22379/semantica/semantic_extract/methods.py#L2708)。
- [S5：GraphBuilder 文本入口](https://github.com/semantica-agi/semantica/blob/c5e2fbffca38cb9e44ba5dc52369832a47f22379/semantica/kg/graph_builder.py#L448)；[三元组补抽关系](https://github.com/semantica-agi/semantica/blob/c5e2fbffca38cb9e44ba5dc52369832a47f22379/semantica/semantic_extract/triplet_extractor.py#L353)。
- [S6：端点解析](https://github.com/semantica-agi/semantica/blob/c5e2fbffca38cb9e44ba5dc52369832a47f22379/semantica/semantic_extract/methods.py#L2306)；[构图处理](https://github.com/semantica-agi/semantica/blob/c5e2fbffca38cb9e44ba5dc52369832a47f22379/semantica/kg/graph_builder.py#L185)。
- [S7：构图默认值](https://github.com/semantica-agi/semantica/blob/c5e2fbffca38cb9e44ba5dc52369832a47f22379/semantica/kg/graph_builder.py#L57)；[实体归并](https://github.com/semantica-agi/semantica/blob/c5e2fbffca38cb9e44ba5dc52369832a47f22379/semantica/kg/entity_resolver.py#L93)；[端点更新](https://github.com/semantica-agi/semantica/blob/c5e2fbffca38cb9e44ba5dc52369832a47f22379/semantica/kg/graph_builder.py#L845)。
- [S8：阈值及回退](https://github.com/semantica-agi/semantica/blob/c5e2fbffca38cb9e44ba5dc52369832a47f22379/semantica/semantic_extract/relation_extractor.py#L455)；[相邻回退](https://github.com/semantica-agi/semantica/blob/c5e2fbffca38cb9e44ba5dc52369832a47f22379/semantica/semantic_extract/relation_extractor.py#L505)；[共现回退](https://github.com/semantica-agi/semantica/blob/c5e2fbffca38cb9e44ba5dc52369832a47f22379/semantica/semantic_extract/relation_extractor.py#L575)。
- [S9：抽取验证](https://github.com/semantica-agi/semantica/blob/c5e2fbffca38cb9e44ba5dc52369832a47f22379/semantica/semantic_extract/extraction_validator.py#L204)；[图验证](https://github.com/semantica-agi/semantica/blob/c5e2fbffca38cb9e44ba5dc52369832a47f22379/semantica/kg/graph_validator.py#L148)；[本体验证](https://github.com/semantica-agi/semantica/blob/c5e2fbffca38cb9e44ba5dc52369832a47f22379/semantica/semantic_extract/schema_validator.py#L99)。
- [S10：LLM 增强合并语义](https://github.com/semantica-agi/semantica/blob/c5e2fbffca38cb9e44ba5dc52369832a47f22379/semantica/semantic_extract/llm_extraction.py#L528)。
- [S11：本体草稿生成](https://github.com/semantica-agi/semantica/blob/c5e2fbffca38cb9e44ba5dc52369832a47f22379/semantica/ontology/bootstrap_schema.py#L61)。
- [S12：本体字段完整度](https://github.com/semantica-agi/semantica/blob/c5e2fbffca38cb9e44ba5dc52369832a47f22379/semantica/ontology/ontology_evaluator.py#L176)；[类关系覆盖](https://github.com/semantica-agi/semantica/blob/c5e2fbffca38cb9e44ba5dc52369832a47f22379/semantica/ontology/ontology_evaluator.py#L332)。
- [S13：共指入口及 NER 配置](https://github.com/semantica-agi/semantica/blob/c5e2fbffca38cb9e44ba5dc52369832a47f22379/semantica/semantic_extract/coreference_resolver.py#L95)；[代词与同名规则](https://github.com/semantica-agi/semantica/blob/c5e2fbffca38cb9e44ba5dc52369832a47f22379/semantica/semantic_extract/coreference_resolver.py#L494)。
