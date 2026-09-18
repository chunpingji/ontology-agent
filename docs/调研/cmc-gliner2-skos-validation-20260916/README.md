# CMCReport：GLiNER2.5 与 SKOS 词表替换验证

用户于 2026-09-16 要求试用 GLiNER2 和 `skos:altLabel`，缺少受控词时允许按通用知识手工补充。本轮在独立实验路径替换旧标签派生，保留[上一轮工具验证](../cmc-qwen-tool-assisted-validation-20260916/results/README.md)的原始制品和线上默认配置。

用户随后要求研究并实施 GLiNER2.5 的真实 Qwen 验证。[升级研究](gliner25-upgrade-assessment.md)记录研究阶段的结论；当前已切换独立实验入口到官方多语言 boundary checkpoint，完成真实 NER 和冻结的 C 组。**NER 8/8 完成；Qwen 15 次请求、14 次返回、1 次 PDE HTTP 400，7/8 片段完成。** 详见[真实结果、返回样例与 Schema 卡片](results-2.5/README.md)。运行完成不等于质量通过，没有切换线上默认识别器。

## 固定范围

- 报告：`upload-23c872fb-3ab1-41de-a705-dd4b162dfa09`，同一 Word、447 单元 IR、329 张卡片；评分仍限原 8 片段，涉及 86 个不同原文单元。
- 新 C 组复用上一轮 B 的工具计划及主体槽位，替换 NER 模型、标签与描述；只新增候选生成和冻结语义核验，每片段最多 2 次、总计最多 16 次项目 Qwen 请求，无隐式重试。
- Qwen 模型、Schema、提示词及最终证据/单位/SHACL 门沿用上一轮。C 对 B 是组合方案对照，同时改变模型和词汇输入，不能单独归因于模型升级或 SKOS。
- 先运行本地 NER 探针，再复用已保存工具结果进入 Qwen；NER 技术失败不计作零命中。原计划调用成本不重复计入。

## 词表来源与边界

[实验 SKOS overlay](../../../backend/app/resources/cmc_extraction_vocabulary.ttl)覆盖所选 15 个类、75 个属性，另定义 18 个独立单位词条，按所选字段选择相关单位。每条人工词汇标明 `ManualGeneralKnowledge` 来源；这不是领域专家金标。

[编译器](../../../backend/app/services/extraction/tool_validation/vocabulary.py)只读冻结卡片与本地 TTL，不接收报告文字或银标。直接读取同一 IRI 的 `skos:altLabel`，缺失时使用手工 overlay；不把子类词汇继承为父类同义词。没有对应词条则明确返回 missing，不再根据原文命中排序并截取前 6 个属性名。

权威本体中有少量 SKOS 别名，但当前 B 计划所选的类和属性没有直接可用的 `skos:altLabel`。因此本轮实际同义词来自人工 overlay，不能把观察到的效果描述为“现有本体受控词库”的效果。词表作者已知上一轮错误类型，这不是盲法设计的新金标。

可读抽取标签表达目标角色；定义与 SKOS 别名进入 GLiNER2 的标签描述。字段名只作上下文提示，要求返回字段值。返回 span 保留标签、原概念 IRI、角色、原文坐标及未经概率校准的 score。标签或词表命中不能证明专业类型、关系或全局身份。

特别分开产品名称/唯一标识、共享 API/试验记录、PDE/NOAEL/给药剂量、条件/动作与单位分母。综合安全系数不把泛称“F值”作为别名；mg/kg 不由单位本身断言 kg 是体重。权威 `ontology/slpra/` 未修改。

## 模型与运行约束

当前模型为 `fastino/gliner2.5-multi-v1`，revision `aaecfe45db1d828c963717054ccb868e8ad1f1d5`；Python 包仍是 `gliner2==2.0.0`，通过 `AutoExtractor` 加载 boundary 架构。通过 Context7、官方模型卡与固定版本源码核验接口；模型版本 2.5 不等于 Python 包版本 2.5。旧 span 模型 `fastino/gliner2-multi-v1` 的前期失败记录保留，不作为本轮结果。

GLiNER2 依赖 Transformers 4.x，运行服务实际使用 5.6.2；因此在独立实验虚拟环境安装，复用容器 PyTorch 2.7.1+cu126，不更改服务全局依赖。固定依赖见[实验 requirements](../../../backend/app/evaluation/fixtures/gliner2_runtime_requirements.txt)。权重下载只发生在准备阶段，正式推理通过本地目录和两项 OFFLINE 开关运行。

[strict 适配器](../../../backend/app/services/extraction/gliner2_extractor.py)采用显式 `cuda:0`、字符分词、160 字窗口、24 字重叠、每批 1 个带描述标签及 `overlap_policy=allow`。2.5 没有固定 `max_width`，但仍受窗口及共享候选池限制：最多 32 个起点、32 个终点、192 个候选。字符分词不同于 checkpoint 默认 whitespace，汉字逐字、ASCII 词串成组；本轮保守检查 512 encoder token 输入门，实际最大 406，不把 512 声称为架构绝对上限。

适配器保留原文并构建官方 boundary metadata，禁止默认补句点；解码前检查有效候选的有限分数、坐标和批次对应，返回后再次逐字回放。隔离依赖补入 `protobuf==6.33.5`，使官方 tokenizer 的兼容加载分支可用；未修改下载的 tokenizer/config，也未更改运行服务全局依赖。

本轮 Qwen 工具提示保留所有提及及其引用目录，批次、计时及重复按组 span 列表只保留在工具制品中，避免例如 PDE 片段的 1,665 条批次明细挤占语义上下文。没有按分数再筛掉候选；这种提示组织变化同样计入组合方案的比较边界。

[评测入口](../../../backend/app/evaluation/schema_card_gliner2.py)先保存词表与模型摘要，再运行模型；冻结后不按结果改词。C 组复用已完成的 NER 制品，并核对源 IR、计划、词表、代码与依赖身份，不重复 NER 或计划请求。

## 当前验证状态

旧 span checkpoint 的离线短句冒烟成功。首轮完整 8 片段 NER 探针发生返回适配失败：官方省略空标签，以及默认批处理补句点导致原文外跨度；该轮未进入 Qwen。失败制品保留在容器 `/app/data/evaluations/schema-card-cmc-gliner2-skos-ner-20260916-01`，不视为召回率或质量结果。

后续按用户授权迁移到 2.5 boundary。8/8 片段真实 NER 成功，2694 个文本×标签窗口产生 852 条带标签提及，仅对应 112 个片段内物理跨度（跨片段 111）。坐标全部匹配原文，但多标签错配明显，例如 `mg→微克`、`DCM→API`、表头温度被当作温度值。852 不能解释为 852 个实体或召回率提升。

2.5 迁移后的 13 个定向工程测试文件 **403 passed**，4 条既有 warning，定向 Ruff 通过；工程检查不替代真实模型质量评测。[upgrade-evidence.json](upgrade-evidence.json)中的 340 项及未真实推理状态属于前一研究阶段。
