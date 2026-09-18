# Research and Baseline

核对日期：2026-09-08。本文记录 022 的实现选择、已检查的接口及外部验收条件；具体工程命令和结果以 [validation.md](validation.md) 为准，不将文件存在或受控模型测试解释为真实质量通过。

2026-09-10 性能增量采用运行内不可变块、写边界生成展示制品、单协调器确认和惰性前沿；
设计与代价见 [plan.md](plan.md)，新实测见 [performance-validation.md](performance-validation.md)。
保留每任务原子提交、完整浮点精度和逻辑覆盖，不引入向量库或跨运行缓存。用户后续仅要求测试新方案，停止相对原实现的性能提升对照，
本次报告记录绝对指标和稳定性，体感由用户评价。
Context7另核对TanStack Query v5的enabled/取消与独立refetchInterval、SQLAlchemy 2.0
Session线程独占，以及Transformers批量tokenizer；真实逐条/批量计数另做离线复算。

| 决策 | 依据/代价 |
|---|---|
| 增量 022，继承 021 | 021 已有在线运行/事件/证明投影；不重建旧域或覆盖历史验收 |
| 必要闭环与精排同时实施 | 基线缺口包括主体检索上下文、谓词/阶段公平、验证主体原文和迟到反证通知；022 在共享 executor 内补齐，避免只交付打分模块 |
| 修正文档历史判断 | executor 已 register_dependencies 并传图投影，缺口是订阅/失效触发，不再说索引恒为空 |
| 整池排名、整池失败政策 | 不跨批比较 rank，不让缺项因少融合项被降权 |
| 首期整记录保守输入 | 超长完整视图标为 not_rerankable 并走完整台账；避免无 tokenizer 证据的隐式截断 |
| 不复用实体对齐器缓存 | 旧 semantic.py 的缓存/同类型阈值服务身份对齐，且没有 local_files_only；新排序不授予身份 |
| 复用 semantic extra | `backend/uv.lock` 锁定 Sentence Transformers 5.6.0；不增加向量数据库，本地 CPU 依赖和固定 revision 制品准备见 [CPU 专项记录](cpu-ranking.md) |
| 硬取消与有限模型执行 | 私有进程在首次实际调用时启动；整 epoch 截止时间收紧逐调用预算，取消/超时先终止进程再释放共享 RequestTicket；调用前和返回前复核租约 |
| 两次加载边界核验 | 父进程冻结完整制品清单；worker 在每种 tokenizer 和权重首次加载前再次比对，防止分词后替换权重仍使用旧模型身份 |
| run 内排序缓存与追加制品 | 简化跨用户隔离与删除；哈希相等不授权共享 |
| 评分证据门修复 | 完整 tuple 包含方向、极性、条件和适用域，正确等价原文证明才计 TP；来源约束不能被弱名称匹配绕过，过期 endpoint 不匹配，多跳证明不跨实际路径拼接 |
| 独立质量计账 | 检索/事实/身份分区/路径/成本分别报告；未知、重复、未到达、未装配与路径枚举超限不能隐藏在通过指标中 |
| 公共核心消融 | A–D 使用同一查询、视图、证明核心和根/主体/谓词/章节公平规则；仅注册的召回、精排和阶段交错因素可变化，B/C 固定池子实验另验 |

## 工具与接口依据

仓库 [.specify/init-options.json](../../.specify/init-options.json) 和
[integration.json](../../.specify/integration.json) 记录 Spec Kit 0.11.3；
[feature.json](../../.specify/feature.json) 指向 `specs/022-semantic-graph-closure`。
本特性按 `specify → clarify → plan → tasks → implement` 留存规范及契约，继承 021 的运行域和治理要求。
2026-09-10经Context7核对[官方Spec Kit工作流](https://github.com/github/spec-kit/blob/main/docs/quickstart.md)，
在已有022中同步澄清、依赖任务、契约及实际勾选状态；本次不升级仓库的Spec Kit版本。

模型适配前通过 Context7 查询 `/huggingface/sentence-transformers` 和 `/websites/sbert_net`，
核对本地构造、`local_files_only=True`、`trust_remote_code=False`、CPU、归一化编码，及
`CrossEncoder.predict(activation_fn=Identity(), apply_softmax=False)` 的原始逐对分值接口。
官方依据为 [SentenceTransformer 源码](https://github.com/huggingface/sentence-transformers)
和 [CrossEncoder API](https://www.sbert.net/docs/package_reference/cross_encoder/cross_encoder.html)。
实现采用真实 tokenizer 检查完整输入，超出配置或模型上限失败；分数没有正确概率含义。

在线与活动评测共用
[configured_ranking_service](../../backend/app/services/llm/semantic_ranking.py)。
正式配置使用 `SEMANTIC_RANKING_ENABLED`、`SEMANTIC_RANKING_MODE=deterministic|semantic`；
默认关闭排序模型，主提议/验证模型的必需性不变。候选池、批次、token、时间、重试和失败政策
在运行前冻结，名称和当前默认值见 [quickstart.md](quickstart.md)。

活动评测 `quality_guided` / `quality_guided_summary` 共享线上识别核心；CLI 的
`prepare --source-docx` 接受独立本地 DOCX，并要求显式 `--root-class-iri`。
准备过程验证 DOCX、复制原件、核验根类型属于冻结本体；活动 executor 使用该根类型，
结果与消融的共同 scope 同时记录根身份，不能比较不同根的实验并宣称同输入消融。
本地准备不查询旧作业数据库或模型端点，拒绝已经存在的输出目录，原件保持不变。
互斥的兼容入口 `--document-ref` 仍只查既有 `extraction_jobs` 的 CMCReport 原件，
不接受 DocumentAnalysisRun ID；旧评测模式继续限定 CMC 根，不限制活动模式的显式合法根。

## 已核验环境与真实验收材料

2026-09-08 初轮工程交付时检查：开发 `backend/.venv` 中没有 torch、sentence_transformers、
transformers、tokenizers；`backend/models` 仅有 BGE-small 中文嵌入和 GLiNER 目录，
未发现 reranker，旧 `MODELS.sha256` 为 0 字节。存在 BGE 目录不能证明其完整制品已通过核验。
`DOCUMENT_ANALYSIS_TEST_DATABASE_URL` 未设置；未读取或输出凭据。

同日按用户后续 CPU 指令已安装锁定依赖，准备 BGE-M3 与 bge-reranker-v2-m3 固定 revision
制品，应用 `verify_artifact` 校验通过。真实 CPU 执行、指定 Compose 上传文档及环境差异
单列于 [cpu-ranking.md](cpu-ranking.md)，上面的初轮缺项不代表补齐后的环境状态。

真实验收须冻结已准备的完整 SHA256 清单、两类本地模型和配套 tokenizer/锁定依赖；仍需
必需提议/验证模型的不可变身份与可用本地服务；按文档/模板族隔离的独立正反例；
专家批准的完整 tuple、mention 分区、路径及等价原文证明参考；覆盖完整池与尾部记录的
查询—原文相关性标注；预注册主指标、增量/非劣界限、置信水平、样本规模、预算及成本门槛。
关键正反例至少三个独立新 run，全部结果保留，不能挑最好一次或把重复运行当独立文档样本。
专用可销毁 PostgreSQL 环境和真实浏览器/服务验证继续作为对应工程外部门单列。

## A–D 与 P5 范围

A 为确定性基线，B 增加稠密召回，C 增加联合编码精排，D 再启用阶段交错；A–C 不交错阶段。
各组固定原件、本体、IR、摘要、查询/视图政策、提议/验证模型及预算。动态前沿各自产生合法主体，
不注入金标主体或历史成功边；B/C 固定池比较还必须有相同查询内容、记录集合和完整 view 映射。
`ranking.json`、`costs.json`、`ablation.json` 及原文执行轨迹使注册因素与实际运行可核对，
技术降级和未完成运行保留，不能冒充正常 C/D 或全范围质量结果。

本期交付 P0–P4 的工程机制，不宣称独立真实收益已经成立。
P5 的缺口驱动补充检索/补充联合编码精排及 E0/E1、F0/F1 扩展组尚未提供活动 CLI 组别，
需要另行冻结候选池、验证额度与实验协议；已知必要来源装配、反证处理与失效传播已经属于本期，
不等待 P5 才处理。精度提升必须由独立完整断言指标证明，排序改善或同预算召回改善不能更名为精度提升。
