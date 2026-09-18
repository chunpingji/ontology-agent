# 独立手工词表输入

## Manual general knowledge

依据用户此前“如果没有 skos 受控词，请依据现有知识储备手动给定”的授权，助手于 2026-09-17 编写 [manual-vocabulary-overlay.json](manual-vocabulary-overlay.json)。它补充本次卡片中 25 个缺少定义的合法类型的定义与召回同义词，来源为通用知识；不是专家批准的参考，也不是原文事实。

词条按现有类型 IRI 显式提供，编译器不拆分标签、不继承父类或子类同义词。权威本体已有的定义和直接 skos:altLabel 优先，主标签继续来自现有本体。手工 aliases 仅填补直接 skos:altLabel 缺口；JSON 里的手工同义词不冒称权威本体三元组。

仅以本体类型与定义缺口确定词条范围，不读取报告具体名称、编号、数值或评分答案来编写词条。词条不能证明类型、实例身份、属性或关系，也不指定抽取算法。产品引擎没有内置设备或 CMC 特判。

此文件尚未用于任何已记录的真实 Qwen 运行；原有 manifest 和探针结果保持原样。后续使用时须将整个对象作为新冻结 manifest 的 `options.vocabulary_overlay`，另设 run_id 和输出目录；与无 overlay 的运行区别报告。编译检查见 [manual-vocabulary-check.json](manual-vocabulary-check.json)，不等于 NER 召回或 F1 验收。

2026-09-17 用户另行授权启用后，已将此对象配置到应用，并完成零 Qwen 请求的真实本地 NER/Mock 检查，见 [启用记录](tool-enablement-20260917/README.md)。这未改变上述真实 Qwen/F1 限制。
