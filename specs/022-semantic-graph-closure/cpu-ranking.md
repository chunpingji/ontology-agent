# 022 CPU 排序环境与实测

本记录区分依赖、模型制品、真实排序执行和 T017 质量验收。CPU 环境测试只调用本地
embedding / reranker；图谱的提议、独立证明及最终事实质量另行验收。

**最终结果：CPU 依赖及两套校验制品就绪；宿主、Compose 容器与指定文档真实排序均通过。**
指定文档的根直接谓词 `describes` 完成 86 条记录、172 个双意图输入对，耗时 843.29 秒，
全部提交且未降级，恢复新增调用 0。54 项定向回归通过。共享服务未重启，T017 保持待验。

## 环境准备

在 `backend/` 执行：

```bash
uv sync --frozen --extra semantic --extra llm --inexact
```

本次已安装并实际 import：Python 3.12.7、torch 2.12.1+cpu、sentence-transformers 5.6.0、
transformers 5.6.2、tokenizers 0.22.2、safetensors 0.8.0。
`torch.version.cuda is None`，`torch.cuda.is_available()` 为 false。锁文件未变动。
现有 Compose 镜像的库版本相同，但 torch 为 2.13.0+cpu；宿主验证不能代替该镜像实测。

制品准备脚本为 [prepare_semantic_ranking_models.py](../../backend/scripts/prepare_semantic_ranking_models.py)。
制品固定为 `BAAI/bge-m3@5617a9f61b028005a4858fdac845db406aefb181` 与
`BAAI/bge-reranker-v2-m3@953dc6f6f85a1b2dbfca4c34a2796e7dde08d41e`。
两个模型原生支持 8192 tokens；实际输入仍取配置及模型上限的较小值，不静默截断。

两套发布制品共 4,586,476,535 bytes（约 4.27 GiB），应用严格 `verify_artifact` 均通过。
清单身份如下，路径为各模型 revision 目录同级的 `<revision>.sha256`：

| 模型 | 清单 SHA256 |
|---|---|
| BGE-M3 | `55020286f89e3f4aff275a70035f677605e15065921afe7832cad842f855993a` |
| bge-reranker-v2-m3 | `505e20b19c5937824d54b3b91e2b64f3f023413d183e8d18ccdbd0f51f74197a` |

官方 BGE-M3 revision 只提供 `pytorch_model.bin`；准备阶段校验官方内容 hash 后用 CPU
`torch.load(weights_only=True)` 转为 safetensors，并逐 tensor 比较。运行目录只交付安全格式，
下载文件、来源信息和转换结果保留于独立 staging/provenance；不将权重写入 Git。

首次制品准备命令（工作目录 `backend/`）：

```bash
.venv/bin/python scripts/prepare_semantic_ranking_models.py --connections 8 --direct-cdn
```

`--direct-cdn` 仅让官方 CDN 下载绕过代理；官方 API、固定 revision、ETag 和完整内容 hash
仍校验。该选项适用于本次主站需代理、官方 CDN 可直连的环境；普通网络可不传。
中断重跑复用 staging 分段；已经发布的模型目录会拒绝覆盖，不重复运行准备命令。

## 获准测试文档

Compose 中 `upload-23c872fb-3ab1-41de-a705-dd4b162dfa09` 的登记根为
`https://ontology.pharma-gmp.cn/slpra/drug-development/CMCReport`。
旧作业 ID 为 `e8b24ffa-9fbc-40e1-987f-9585d9b42676`，查询时状态 `paused`。
仅只读查询所需字段并复制原件，未恢复旧作业。

原件 178078 bytes，SHA256 为
`e94822808601e73f4ba4c715c664812d4a01e42f0e4e84a6b08a3653b915daf7`。
新目录 `evaluations/022-cpu-upload-23c872fb-20260908/prepared/` 已冻结原件、IR、本体与运行源码，
解析得到 447 个 evidence units、5398 个原文字符。原件内容不写入技术文档。

真实 tokenizer 预检得到 86 条 RecordIndex 记录，完整记录视图 73–776 tokens；
两条查询分别 846/842 tokens。172 个完整 pair 为 915–1622 tokens，总计 188664 tokens，
没有超过 4096 的输入。此预检只加载 tokenizer，不作为权重推理通过依据。

## 执行和验收范围

合成冒烟入口为 [smoke_semantic_ranking.py](../../backend/scripts/smoke_semantic_ranking.py)，
真实文档入口为 [check_document_semantic_ranking.py](../../backend/scripts/check_document_semantic_ranking.py)。
每次使用新的输出目录与独立 SQLite 调度库，记录真实分词、完整记录编码、双意图精排、
整池提交和恢复请求数。CPU 首个 epoch 会预处理全文并冷加载模型，候选池 64 不意味着
只编码 64 条记录；超时必须覆盖全部准备和推理成本。

下列结果证明排序环境及执行链路。T017 保持未完成：还需要专家参考、
独立文档样本、预注册质量/成本阈值和 A–D 对照，环境与单文档排序不能替代事实 precision/recall。

### 已完成的宿主真实冒烟

机器为 Intel Xeon E5-2697 v4 2.30 GHz，72 个逻辑核；本次 `OMP_NUM_THREADS=4`、
`MKL_NUM_THREADS=4`、batch 4、输入上限 4096、每 epoch 超时 600 秒。属于共享宿主实测，
期间另有文档/容器验证运行，不作为独占机器性能基准。

`evaluations/022-cpu-upload-23c872fb-20260908/smoke-host-01/report.json` 为 `passed`：

- 1024 维向量 L2 范数约 1；原始 reranker 分数为 1.91794、-11.00643，未做概率化或批内 softmax。
- 冷加载及首轮直接调用 60.65 秒；5 条完整合成记录、10 个双意图观察均成功 `committed`，
  `actual_ranking_mode=semantic`、`degraded=false`；暖模型排序 15.12 秒。
- 4063-token 单文本编码和 4067-token pair 精排成功；7171-token 文本超过配置上限时，
  embed/pair 均返回 `ranking_input_too_long`，工作进程被清理，随后小输入调用恢复成功。
- 恢复新增请求 0、持久状态不变。47 个调度请求中 45 个成功，2 个为上述预期超限拒绝；
  13561 个实际模型输入 tokens，拒绝请求的未知 token 成本单列，未伪装成零成本。
- 包含长度边界和重新加载的整轮耗时 252.88 秒；最大已回收单个子进程 RSS 4,494,584 KiB
  （约 4.29 GiB），不是全进程树内存之和；`/usr/bin/time -v` 同时记录了此值。

实际配置工厂检查 `configuration-check.json` 通过，`unavailable_reason=null`；该项只核验
制品与配置，不加载权重。旧 60 秒默认不足以覆盖本次冷直接调用，因此 CPU 示例预先冻结
600 秒作为首次试跑预算；实际文档随后触及该预算，最终建议改为 1200 秒。
不能只延长 HTTP 超时而漏掉整 epoch 的时间预算。

### 已完成的 Compose 真实冒烟

`smoke-compose-01/report.json` 为 `passed`，复制到宿主后全部制品 hash 一致。
使用现有 backend 容器，在新 `/tmp` 目录执行同一脚本及独立 SQLite；PyTorch 为
2.13.0+cpu，未用宿主结果替代镜像验证。该轮不重复长度边界。

整轮 109.25 秒，冷 probe 65.47 秒、暖排序 14.24 秒；5 条记录、10 个双意图观察成功
提交，未降级，恢复新增请求 0。18/18 调度请求完成，实际输入 5417 tokens、未知成本 0，
排队合计 0.292 秒。最大单个子进程 RSS 4,209,556 KiB（约 4.01 GiB）。
退出后出现一次 `resource_tracker` semaphore 清理 warning，单列为清理诊断，未算作模型请求失败。

### 文档 600 秒试跑与修正

`document-host-01/report.json` 如实保留 `failed`：第一池在完整提交前触发
`semantic_pool_failed:ranking_timeout`，无已提交 epoch。整轮 615.52 秒（含 epoch 外的
工厂校验和制品收尾），142 个调度请求中 141 个完成、1 个超时；已测输入 155372 tokens，
超时请求未知 token 数单列，预扣 159594 tokens。不把该轮当语义排序成功。

最终 CPU 配置采用 1200 秒，新运行目录 `document-host-02/`，原件/本体及 token 预算不变。
宿主交付环境文件为 `evaluations/022-cpu-upload-23c872fb-20260908/cpu-ranking-host-1200.env`；
初始 600 秒文件保留供回放。

正常关闭总是 `terminate()` 使 tqdm 的 multiprocessing 锁无法执行注销；宿主初轮也有
3 个 semaphore 清理 warning。现已改为正常完成及已返回错误时关闭 Pipe、等待 EOF 退出，
超时/取消继续立即强制终止。三项新增回归验证正常退出码、清理标记、幂等与强制关闭时延，
原有取消/超时测试保留。第二次真实容器冒烟独立核验修复后的退出行为。

`smoke-compose-02/report.json` 为 `passed`：整轮 113.94 秒，冷 probe 66.98 秒、暖排序
15.15 秒；18/18 请求完成、5417 个实际 tokens、未知成本 0，完整精排提交及恢复零请求
均通过。其父目录 `smoke-compose-02.log` 保存完整 stdout/stderr，退出码 0，未出现
`resource_tracker` / semaphore 清理警告。最大单个子进程 RSS 4,210,164 KiB（约 4.02 GiB）。

最终 1200 秒宿主环境也已通过实际 `configured_ranking_service` 工厂检查，结果位于
`configuration-check-1200.json`，`unavailable_reason=null`。

`smoke-host-02/report.json` 为 `passed`：在最终代码及 1200 秒配置下重复完整长度边界，
4063-token 编码、4067-token 精排成功，7171-token 超限输入两种操作均明确拒绝并恢复。
5 条记录、10 个双意图观察完整提交，未降级，恢复新增请求 0。47 个请求中 45 个完成、
2 个预期拒绝，实际输入 13561 tokens，未知成本仅来自上述拒绝。整轮 255.09 秒，
最大单个子进程 RSS 4,445,108 KiB（约 4.24 GiB）。退出码 0，`smoke-host-02.log`
无 warning，包括 `resource_tracker` / semaphore；脚本和适配器在运行前后 hash 一致。

### 最终真实文档结果

`document-host-02/report.json` 为 `passed`。实际选择冻结 CMC 根直接菜单中的
`https://ontology.pharma-gmp.cn/slpra/drug-development/describes`，没有虚构 `hasProduct`
谓词或将合成查询套入原件。使用冻结输入与修复后的源码，执行 runtime hash 为
`4e9e0a6dad9641e41fd5b4d6aaf3bac34f74249e3bd4fb4ead77c239e49ff978`。

| 项目 | 实测结果 |
|---|---|
| 冷第一池 | 64 条记录、128 个 pair，618.26 秒；55 个模型批次 |
| 暖第二池 | 22 条记录、44 个 pair，193.05 秒；12 个精排批次，24 次向量缓存命中 |
| 完整性 | 86/86 条记录入池，172/172 个双意图观察；长记录排除 0、未排序 0 |
| 提交与恢复 | 两池均 `committed`、`semantic`、未降级；验证/幂等重放 2 个 epoch，新增请求 0 |
| 真实费用 | 155/155 调度请求成功，未知成本 0；23 个 embedding 批次、44 个精排批次、88 次分词请求 |
| token 账 | 实测及预扣均为 212100 tokens；原文/查询编码 23436，pair 精排 188664 |
| 时间与内存 | 整轮 843.29 秒；排队共 1.19 秒；最大单个进程 RSS 5,034,048 KiB，约 4.80 GiB |
| 收尾 | 退出码 0，原始日志无 warning/Traceback；全部制品 SHA256 核验一致 |

所有 86 条语义台账仍为 `unattempted`，`recognition_records_examined=0`：该测试验证
排序，没有调用主提议/验证 LLM 或宣称事实已证明。14 分钟是这一个根谓词的两池排序耗时，
不能解释为整份文档关系图谱的端到端耗时或 T017 的事实精度结果。

最终三份报告的 SHA256：

| 报告 | SHA256 |
|---|---|
| `document-host-02/report.json` | `e1f109c77dd3dd628d42b070b6ca65298c8e73b8d4744275468e60441e526702` |
| `smoke-host-02/report.json` | `26544f7304a96030107438e976bcf9014ccdb0b2ce86efed1a3de83591228987` |
| `smoke-compose-02/report.json` | `b0cba26fc43cc718c174dbe038dd4fb146dccc3e886447dd37f6c67d491da2d3` |

CPU 不占用显存。按本次输入，单 worker 实测峰值约 4.8 GiB；应另给应用、文件页缓存和
并发运行留内存余量，不能将该峰值当作完整部署内存上限。当前最长真实 pair 1622 tokens，
长度边界验证了单个 4067-token pair；四个同时达到 4096 的满长批次未作为本次内存基准。
请求调度串行不等于只驻留一份模型，每个活动运行的私有 worker 都可能持有权重。
发布制品约 4.27 GiB；本机保留断点分段和原始来源后，准备目录总计约 13 GiB。

### 本轮工程检查

工作目录 `backend/`，本轮实际运行：

```bash
.venv/bin/python -m pytest -p no:cacheprovider -q \
  tests/test_extraction/test_semantic_ranking.py \
  tests/test_extraction/test_semantic_ranking_adapter.py \
  tests/test_extraction/test_ontology_guided_boundaries.py

.venv/bin/python -m pytest -p no:cacheprovider -q \
  tests/test_extraction/test_semantic_ranking_smoke_cli.py \
  tests/test_extraction/test_semantic_model_artifacts.py \
  tests/test_extraction/test_semantic_evaluation_prepare.py
```

分别为 36 passed 和 15 passed，累计 51 passed；每组沿用既有 4 项框架弃用/字段遮蔽 warning。
8 个本轮 Python 文件定向 Ruff 通过，Compose 默认及 CPU 示例配置解析通过。
脚本还保留下载中断续传/错误 Range/hash 拒绝、tensor 格式转换字节一致、原件及已有输出目录
保护、向量非法及费用留存等回归。适配器新增明确的超限原因，避免与其他模型执行故障混淆。

加入进程关闭修复后，上述六个文件合并再跑：**54 passed，4 warnings，12.10 秒**。

## 复用命令和 Compose 配置

在 `backend/` 使用新的绝对输出路径：

```bash
RANKING_ARTIFACTS_DIR="$PWD/models/semantic-ranking"
RANKING_EMBEDDING_DIR="$RANKING_ARTIFACTS_DIR/bge-m3/5617a9f61b028005a4858fdac845db406aefb181"
RANKING_RERANKER_DIR="$RANKING_ARTIFACTS_DIR/bge-reranker-v2-m3/953dc6f6f85a1b2dbfca4c34a2796e7dde08d41e"

OMP_NUM_THREADS=4 MKL_NUM_THREADS=4 .venv/bin/python scripts/smoke_semantic_ranking.py \
  --output-dir /controlled/evaluations/cpu-smoke-new \
  --embedding-path "$RANKING_EMBEDDING_DIR" \
  --embedding-manifest-path "$RANKING_EMBEDDING_DIR.sha256" \
  --reranker-path "$RANKING_RERANKER_DIR" \
  --reranker-manifest-path "$RANKING_RERANKER_DIR.sha256" \
  --batch-size 4 --max-tokens-per-pair 4096 --timeout-seconds 1200 \
  --long-record-repetitions 24 --length-boundaries

OMP_NUM_THREADS=4 MKL_NUM_THREADS=4 .venv/bin/python scripts/check_document_semantic_ranking.py \
  --prepared /controlled/evaluations/prepared \
  --output-dir /controlled/evaluations/cpu-document-new \
  --embedding-path "$RANKING_EMBEDDING_DIR" \
  --embedding-manifest-path "$RANKING_EMBEDDING_DIR.sha256" \
  --reranker-path "$RANKING_RERANKER_DIR" \
  --reranker-manifest-path "$RANKING_RERANKER_DIR.sha256" \
  --pool-size 64 --epochs 2 --batch-size 4 \
  --max-tokens-per-pair 4096 --timeout-seconds 1200 \
  --max-tokens-per-slot 524288 --max-tokens-per-run 4194304
```

`epochs=2` 对本次 86 条记录可覆盖两池；其他文档须查看 `eligible_not_ranked`，不能凭脚本
退出成功推断所有记录已排序。排序不会把台账的 `unattempted` 改为事实已检查。

Compose 配置示例见 [cpu-ranking.compose.env.example](cpu-ranking.compose.env.example)。
基础 Compose 已透传排序字段；原有 `/app/models` 挂载可见新制品。4 线程限制作用于
容器内所有本地 CPU 模型，现有服务未重启。启用字段应并入既有受控环境，保留其他应用配置，
不要直接用示例文件替换生产环境。`docker compose config --quiet` 可以验证配置解析；
该命令不会使新配置在已运行进程中生效。例中的 `pause` 用于明确报告排序失败，避免把
确定性降级结果计为本次 CPU 语义测试通过。
