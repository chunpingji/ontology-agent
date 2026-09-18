# GLiNER2.5 升级研究（2026-09-16）

**建议将下一轮隔离验证的首选模型调整为 `fastino/gliner2.5-multi-v1`，先通过加载和原文坐标验收，再执行 C 组 Qwen 对照。** 它确实消除了旧 span 架构的固定宽度限制，适合本次长属性值抽取需求。本次完成文档、源码及无权重预检；尚未下载 2.5 权重、完成真实报告推理或切换线上模型。

此前因用户使用“GLiNER2”名称而选择旧 span checkpoint 不够准确：GLiNER2 是模型家族及包名，2.5 boundary 同样属于该家族。新方案不需要改变 SKOS 词表来源、Qwen 强模型或确定性验证职责。

## 版本与可用性

| 项目 | 核验结果 |
| --- | --- |
| 模型 | 官方 `fastino/gliner2.5-multi-v1`，多语言，Apache-2.0 |
| 固定 revision | `aaecfe45db1d828c963717054ccb868e8ad1f1d5` |
| 模型架构 | `boundary`；287,355,159 参数，mDeBERTa-v3-base |
| Python 包 | 当前 PyPI `gliner2==2.0.0` 已包含 2.5 架构；不是安装 `gliner2==2.5` |
| 加载入口 | `AutoExtractor.from_pretrained(...)` 自动选择 `BoundaryExtractor` |
| 权重兼容 | 旧 `GLiNER2.from_pretrained(...)` 只加载 span；旧权重不能靠修改 `max_width` 转换 |
| 实际文件大小 | Hub 元数据为 1,149,461,028 bytes，参数为 F32；模型卡“约 594 MB、主要 FP16”的文字与该 revision 不一致 |

应使用多语言 checkpoint；官方模型卡没有提供足以判断本项目中文 CMC 属性质量的评测结果，不能仅因标注 multilingual 就宣称优于现有方案。

## `max_width` 的取消解决了什么

已发布包 `configuration.py:726–746` 明确 boundary 忽略 `max_width`；其共享候选池用起止边界配对，合法性检查不要求跨度小于固定宽度。当前旧 checkpoint 为 `max_width=8`，本实验 char splitter 对汉字逐字、ASCII 词串成组，所以它限制的是分词单元数量，不是统一的 8 字符。

本次直接执行发布包的 `DocumentCandidatePool` 源码，在 CPU 上构造小张量，采用新 checkpoint 的真实配置，三项断言通过：

1. 成功生成 `[0,96)`，跨度 96，超过旧上限 8。
2. 将该起点的排名降至第 33，目标候选消失。
3. 1,024 个合法边界组合最终只保留 192 个。

这些结果证明候选机制，不代表训练权重会识别出这些跨度。对本报告最值得检验的是完整工艺条件、IPC 标准、包装/溶解性描述和储存要求；PDE 的试验记录归属问题不能仅靠放宽跨度解决。

## 仍然存在的约束

- **共享候选预算**：本 checkpoint 的 `candidate_pool=shared`，最多各选 32 个起点和终点，再保留每窗口最多 192 个去重候选。每 query 优先选取的 8 对也来自这些边界。不能把配置中另一条 per-query 路径的参数当作实际主路径预算。
- **窗口与分词**：只有起止点同时进入一个窗口才可抽取；分块合并不会自动补出跨窗口长值。中文仍须显式配置合适的 splitter。首轮比较继续固定现有 160 字窗口、24 字重叠、每批一个标签、阈值 0.5 和 `overlap_policy=allow`；不同时扩大窗口以混淆差异。模型默认 `flat` 会排除重叠 span，不能无意恢复该默认值。
- **输入长度**：`config.max_len=4096` 是源 word token 配置；实际 encoder 输入还包含标签、描述及 subword。编码器配置另有 `max_position_embeddings=512`，但 `position_biased_input=false` 且使用相对位置，不能仅凭该字段断言 512 是绝对架构上限，也不能据 4096 保证任意中文长输入可用。第一轮沿用项目保守的 512 encoder token 门，长上下文能力另行实测。
- **语义与置信度**：无固定宽度不保证边界正确、无漏召回或 score 已校准。长值也可能吞入否定词、相邻字段或其他主体的信息，必须复核完整引用和归属。

## 已实际发现的迁移问题

**Tokenizer：不能直接照搬当前加载假设。** 下载并校验官方 tokenizer，使用现有隔离环境 `gliner2 2.0.0 / transformers 4.57.6 / tokenizers 0.22.2` 离线预检。checkpoint 的 `extra_special_tokens` 是列表，Transformers 4 对该字段期待字典；GLiNER2 自带针对 `AttributeError` 的兼容重试，但本环境缺少 protobuf，Transformers 异常处理又抛出 `ImportError`，使其未能进入重试。

仅在无权重预检中通过 `extra_special_tokens={}` 覆盖该元数据，再让官方 processor 注册标记，预处理成功。逐项核对全部 **250,112** 个 token 的 ID 均与原 `tokenizer.json` 一致，10 个结构标记 ID 不变；未修改下载文件。合成中文输入得到 25 个源分词单元、87 个 encoder tokens 和 1 条 boundary query。此结果证明有可行的兼容方式，**没有证明 `AutoExtractor` 的完整权重加载已通过**。实施时优先在隔离环境补齐并固定缺少的 protobuf，验证官方兼容分支；不为此直接升级线上 Transformers。

**原文坐标：2.5 仍共享会补句点的默认 collator。** 无权重预检确认输入 `编号：ABC-123` 被变为 `编号：ABC-123.`。旧真实探针已经出现这类源外跨度。因此 boundary 适配必须保留原文、拒绝静默截断/替代记录，同时调用官方 boundary metadata 构建；不能只复制当前 span 适配器的 `transform_and_format + _pad_batch`。预检中追加官方 `_add_boundary_metadata` 后已保留原文并生成 query 布局，完整模型推理尚待验证。

原文半开坐标逐字回放、未知标签拒绝、合法空标签、批次对齐、离线开关及异常传播均继续保留。错误坐标不裁剪、不搜索替代引用。

## 与 SKOS、强模型和 deterministic engine 的关系

SKOS 编译器仍按本体 IRI 限定可抽取的类和属性，`skos:altLabel` 与定义进入标签描述；当前所选 IRI 缺少直接 SKOS 别名，仍采用已标明人工来源的 overlay。2.5 不会自动生成可信受控词，也不应把同一 IRI 的多个别名拆成相互竞争的独立类型。

2.5 的 `span attributes` 主要是在已定位 span 上选择受控分类标签，并不自动覆盖任意字符串、数值属性和单位归一化。记录、关系头可提供后续候选，但本次先验证既有 entities/values/units 接口，避免同时改变关系生成机制。

单位原文识别交给抽取工具；数值解析、量纲及分母角色检查、精确换算、SHACL 约束仍由 deterministic engine 执行。Qwen 继续核验专业语义、主体归属与关系方向；学习模型输出不直接成为事实。

## 下一轮最小验证路径

1. 固定上述 boundary revision、实际权重 SHA256 和隔离依赖；修正 tokenizer 加载，使用 `AutoExtractor`，显式核验 `architecture=boundary`。新目录保存制品，保留旧 span 探针失败记录。
2. 实现原文保真的 boundary 批处理，分别检查源分词和实际 encoder 长度；先验收纯离线加载、空输出、ASCII 尾部、中文长 span、重叠值和源外坐标拒绝。
3. 对同一报告原 8 片段做 NER-only 对照，固定现有 SKOS 词表、标签描述、阈值、分词、窗口和工具计划；区分完整 span、截短、误归属与技术失败。与旧 span 使用同词表时比较的是架构与训练权重的组合，不宣称纯架构消融。
4. 工程门通过后，再用优选 NER 结果执行最多 **16 次项目 Qwen** 的 C 组候选/语义核验。与原 B 比较仍属模型、词表和提示组织的组合变化；旧计划调用不重复计入成本。
5. 分别报告属性完整性、错误保留/漏保留、单位及 SHACL 的实际覆盖、NER 时间与显存、Qwen 成本；不能以模型返回更多 span 或 JSON 合法宣称整体质量提升。

本次未新增 Qwen 请求，未执行 2.5 权重推理。相关现有工程回归结果另见[主记录](README.md)。研究机器证据见 [upgrade-evidence.json](upgrade-evidence.json)。

## 依据

- [固定 revision 的官方模型卡](https://huggingface.co/fastino/gliner2.5-multi-v1/blob/aaecfe45db1d828c963717054ccb868e8ad1f1d5/README.md)、[模型配置](https://huggingface.co/fastino/gliner2.5-multi-v1/blob/aaecfe45db1d828c963717054ccb868e8ad1f1d5/config.json)。
- [PyPI gliner2 2.0.0](https://pypi.org/project/gliner2/2.0.0/) 发布 wheel 源码：`auto.py`、`configuration.py`、`models/base.py`、`models/boundary/{model,pool}.py`、`processor.py`、`processing/word_splitter.py`；文件摘要见机器证据。
- Context7 获取 GLiNER2 官方架构与加载接口，以及 Transformers 4.57 的特殊 token 文档。其引用的两份架构指南在当前 GitHub main 返回 404，因此关键结论另以发布包源码、固定模型配置及实际预检核验。
