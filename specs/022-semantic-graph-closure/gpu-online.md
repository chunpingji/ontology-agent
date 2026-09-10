# GPU 语义排序线上启用

日期：2026-09-09 UTC。用户明确要求打开线上排序配置；本次在已有默认 GPU 环境基础上
启用共享排序链，不修改依赖、模型权重、原文、本体或历史冻结制品。

## 生效配置

本机根 `.env` 增加 `SEMANTIC_RANKING_ENABLED=true`、`SEMANTIC_RANKING_MODE=semantic`
以及 embedding/reranker 的四项完整制品路径，保留其他字段。部署仍为 CUDA 12.6、
`cuda:0/float16`、batch 4、超时 1200 秒、失败 pause、文档并发 1。
物理卡为 GPU 2，UUID `GPU-60555528-1e13-e855-3dcc-b0a96e686045`。

| 模型 | 容器目录；清单为该目录路径追加 `.sha256` 的同级文件 |
|---|---|
| embedding | `/app/models/semantic-ranking/bge-m3/5617a9f61b028005a4858fdac845db406aefb181` |
| reranker | `/app/models/semantic-ranking/bge-reranker-v2-m3/953dc6f6f85a1b2dbfca4c34a2796e7dde08d41e` |

两个模型的 10 / 6 个文件均重新校验 SHA256 通过。线上 `_configured_ranking()` 使用
共享 `configured_ranking_service(settings)`；`enabled=true` 且 `mode=semantic` 时
适配器选择 `rerank`，策略中的 `enable_dense` 与 `enable_reranker` 均为 true。
这同时启用 dense 候选召回与 CrossEncoder 精排；旧实体对齐、GLiNER 保持独立 CPU 加载。

新配置适用于新建运行。既有运行的设备、策略和制品身份已冻结，不能直接继承新 GPU
配置或重写旧排名；相似度仍仅用于检索排序，不替代独立原文事实证明。
仓库通用开关缺省仍为 false，本机通过显式部署配置开启；CPU 覆盖入口继续保留。

## 切换与旧运行状态

核验期间观察到共享后端在 **04:16:50 UTC** 发生一次非本次操作发起的重启，仍为旧 CPU
镜像。两个旧运行随后被 dispatcher 标记为 `blocked_dependency / FINGERPRINT_MISMATCH`，
任务计数为 898 / 628，事件水位为 1815 / 1273，执行租约 revoked。它们没有自然完成，
也未被本次操作取消或转写为 GPU 运行；已有图、检查点与原文制品保留。

实际切换前再次检查：queued/running/pausing 运行数、有效租约及排队/执行中的模型请求
均为 0，数据库 revision 为 `0033_document_analysis_runs`，与代码 head 一致。
随后按本轮授权执行 `docker compose up -d --no-deps --no-build backend`，于 **04:22:36 UTC**
完成后端重建。新镜像为 `ontology-agent-backend:cuda12-2.7.1`，ID
`sha256:992bbffc11625bcc14fabe815316df708a7d473ecdfec1c2439f84fb4887cd3b`，runtime 为 nvidia。
数据库、前端、网关容器 ID/PID/StartedAt 未变；未重启 Docker 或主 LLM。

切换后独立核验环境变量与 Settings 均已开启，四项路径存在，离线变量保持为 1；
直连 `8000/api/health` 与代理 `8081/api/health` 均 200，无需 nginx reload。
再次核对数据库 revision=head；两个旧运行及制品水位保持不变。

## 实际模型验证

使用已运行的新 backend 容器执行独立 Python 进程，直接读取实际容器配置并调用共享
factory；不启动另一份应用 lifespan。两条合成文本执行一次 embedding 请求，两组
query/text 执行一次 reranker 请求；调度只写新建的私有 SQLite，不写共享模型请求表。
**验证通过**：两个请求全部 completed，实测输入共 60 tokens，总墙钟 **83.07 秒**
（含 factory、权重校验/加载与清理）。embedding 返回 1024 维有限、L2 归一化向量；
reranker 返回两项有限 raw logit。双策略均为 true，实际模型设备/精度验证为
`cuda:0/float16`，GPU UUID 与所选卡一致，私有 worker 正常关闭。
未调用主 LLM、未建立业务事实。GPU 卡占用在 probe 退出后回到调用前采样值。

[模型报告](../../evaluations/022-gpu-online-enable-20260909/live-model-probe/report.json)，
[退出状态](../../evaluations/022-gpu-online-enable-20260909/live-model-probe-exit.json)。

本次是线上环境启用与双模型调用验证，不包含新的完整图谱主 LLM 运行或专家质量验收。
T017 保持原状态。原始证据目录：`evaluations/022-gpu-online-enable-20260909/`。

- [合并后的配置](../../evaluations/022-gpu-online-enable-20260909/proposed-compose.json)
- [切换前检查与容器状态](../../evaluations/022-gpu-online-enable-20260909/switch-result.json)
- [实际容器配置与健康检查](../../evaluations/022-gpu-online-enable-20260909/live-configuration.json)

## 后续线上请求故障修复

本页合成冒烟的私有 SQLite 未覆盖共享 PostgreSQL 的运行 ID 长度约束。用户随后在线
遇到 `ranking_technical_failure:DataError`，已修复调度字段长度、paused epoch 恢复及
冷启动校验期间的续租，并无损迁移至 0034。**05:27:29 UTC** 同一用户运行已恢复，
真实图谱 API 显示 semantic、未暂停/降级；GPU 完成 73 次 embedding、32 次 reranker，
首轮完整排名提交且已继续原文识别。最终镜像、失败留痕与实际验收见
[ranking-dataerror.md](ranking-dataerror.md)。本页较早的部署/冒烟记录保留原时间和口径。
