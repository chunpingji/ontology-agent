# 隔离验证

在 backend 既有虚拟环境安装 shacl extra，运行新增 test_tool_* 和 test_schema_card_tools.py，以及相关既有证据/字面量与依赖边界测试。

真实实验入口为 `python -m app.evaluation.schema_card_tools --baseline <上一轮冻结run目录> --output <新目录>`。默认 A/B 各 8 scopes，最多 48 次 Qwen。`--prepare-only` 仅冻结输入，`--ner-only` 只运行本地 GLiNER；具体输出和命令以本轮实测文档为准。

验收区分结构合约、机械/SHACL、Qwen语义与银标清单，不将空输出或 shape未命中算通过。后端不重启，原件、本体、原实验输出不覆盖。

本次引用解释器修正后的补核入口：`python -m app.evaluation.schema_card_tools_finalize --source <已结束原工具实验> --output <新目录>`。它不重生成计划或候选，只补每片段未用的第三次请求，累计预算仍为 48。失败的模型请求不重试，已有语义核验必须目标完全相等才可复用。

本轮已完成：272 项定向工程测试通过；Qwen 共 47 次请求，46 次成功、1 次超时。详见[实测报告、命令和返回样例](../../docs/调研/cmc-qwen-tool-assisted-validation-20260916/results/README.md)。这些数字不代表质量全面通过或已部署。

最后的零模型重算脚本随[最终制品 code 目录](../../output/schema-card-qwen-cmc-tools-20260916/checked/code/cmc-recheck-tools.py)保存，输入为已结束的 completed 目录，输出须使用新目录。它禁用模型调用，逐项断言原 HTTP 账本未变化，并校验复用语义目标一致；不要用新的识别运行替代该确定性重算。

## GLiNER2.5 / SKOS C 组

独立环境使用 [固定依赖](../../backend/app/evaluation/fixtures/gliner2_runtime_requirements.txt)，GLiNER2.5 模型由 `gliner2==2.0.0` 包加载。使用已校验的本地 `fastino/gliner2.5-multi-v1` revision `aaecfe45db1d828c963717054ccb868e8ad1f1d5`；不得把旧 span 权重作为 boundary 使用。推理进程启动前设置 `HF_HUB_OFFLINE=1` 与 `TRANSFORMERS_OFFLINE=1`。

先运行 `python -m app.evaluation.schema_card_gliner2 --baseline <旧 checked 目录> --output <新的 NER 目录> --ontology-dir <本地 TTL 目录> --model-path <已校验模型目录> --device cuda:0 --ner-only`。全部 NER 完成后，以新的输出目录去掉 `--ner-only`，加 `--prepared-tools <刚完成的 NER 目录>`，复用工具结果进入至多 16 次 Qwen 候选/核验请求。

两阶段之间不修改词表、检查器或依赖；runner 会拒绝身份不一致的复用。技术失败保留在结果中，不自动重试模型请求。该入口仍不切换线上默认识别器。

本轮真实 2.5 实验已于 2026-09-16 完成运行：NER 8/8；Qwen 共 15 次新请求，14 次返回、1 次 PDE HTTP 400，7/8 scopes 完成。403 项定向工程测试通过。详见[真实结果、Schema 卡片、返回样例与限制](../../docs/调研/cmc-gliner2-skos-validation-20260916/results-2.5/README.md)；运行完成不代表质量通过或已部署。

## 现有摘要检索 D 组

入口为 `python -m app.evaluation.schema_card_summary_tools`，沿用 C 组隔离依赖、模型和离线开关。`--baseline` 指向冻结 C 组，`--metadata-dir` 指向同源摘要元数据目录，另指定全新 `--output`、`--ontology-dir`、`--model-path` 和 `--device cuda:0`。

元数据目录包含 `metadata.json`、`section-tree.json`、`summaries.json`、`manifest.json`；使用[本轮准备脚本](../../output/schema-card-qwen-cmc-summary-retrieval-20260916/metadata/prepare_metadata.py)核对 Word、IR、摘要节点及快照身份。复用的历史章节摘要保留原提示版本、部分／回退状态和生成成本，不新增摘要请求。

依次以独立输出目录运行 `--prepare-only`、`--ner-only`，再以新目录加 `--prepared-tools <D 的 NER 目录>` 执行 Qwen。每组最多两次请求，全轮最多 16 次，不重试。两阶段间输入、代码、词表、依赖不可变；每组最多 6 个正分完整记录、12,000 个去重原文字符，超限记录留作未执行，不截断。

同一检索产物保存摘要启用／屏蔽的分量和选择结果。摘要只参与现有 `plan_slot` 排序，模型证据由 IR 原文重建；没有使用旧片段选择器或银标选取记录。八组查询分数的等权汇总属于本轮评测适配，并非生产完整图谱执行器；也没有接入 embedding／cross-encoder 精排。详见[协议、实测与独立复核](../../docs/调研/cmc-summary-retrieval-validation-20260916/README.md)。

本轮 D 执行已结束：77 项定向工程测试通过，NER 8/8，真实 Qwen 共 9 次请求、0/8 组完整核验（7 次 HTTP 400；存放候选通过、核验截断）。摘要启用／屏蔽模型输入相同；单位与数值 SHACL 未评估。不得把 run 的 completed 状态解读为质量验收通过。

## 静态 Mock 设备 E0/E1/E2

入口 `python -m app.evaluation.schema_card_mock_tools`，使用既有 GLiNER2.5 隔离环境、OFFLINE 开关和 pySHACL 路径。参数为 `--baseline <冻结D完整制品目录>`、`--mock-file <静态equipment_archive.json>`、`--output <新目录>`、`--ontology-dir <TTL目录>`、`--model-path <已校验2.5权重>`、`--device cuda:0`。

先 `--prepare-only` 检查检索和身份，再以新目录 `--ner-only` 完成本地NER，最后以新目录加 `--prepared-tools <刚完成NER目录>` 运行至多18次Qwen。代码、词表、记录、来源和依赖均在阶段间核对，不覆盖旧输出，不重试失败。

此协议只验证设备实例：E0/E1原文与NER相同，E2检索可变化。当前Mock静态文件与管理界面数据库分别对待；不运行旧seed脚本、不启动服务或提交事实。独立参考只用于运行后的评分。[实测、Schema卡片和来源边界](../../docs/调研/cmc-mock-entity-validation-20260916/README.md)明确区分结构、身份、语义、字符串SHACL与数值校准未评估。

本轮 E0/E1/E2 已完成：126 项工程测试通过；真实 GLiNER2.5 为 5 份不同输入、207 个 span；Qwen 18 次请求、9/9 case 完整、18/18 原始 JSON Schema 通过。独立复核 45 项候选，程序接受结果中支持比例分别为 12/12、13/13、9/10（E2 另 1 项未决）。E1 有 2 个精确外部身份链接，未知 DE64603 未错配；E2 编号检索覆盖 4/6，但最终只保留本组选中编号的 2/4，清洗 usesEquipment 仍有语义漏门。数值单位校准未评估，没有切换线上识别器。
