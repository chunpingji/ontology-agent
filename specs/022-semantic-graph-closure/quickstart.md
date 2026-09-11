# Quickstart

## 剪枝与增强检索视图：当前只进行设计修订

新增目标行为见[自适应检索契约](contracts/adaptive-retrieval.md)和
[专题方案](../../docs/剪枝和增强语义检索视图方案.md)。`heuristic-first-v4`、新的准入决定
和公开剪枝诊断尚未实现；当前没有可用于启用它们的已验证命令或开关。本次不启动测试、
模型、迁移或部署，旧 v1/v2/v3 运行继续使用原冻结行为。

后续先按 `tasks.md` 的 AR-P0-01–AR-P0-10 闭合契约，全部通过前 AR-P1 只能做不改变
准入/调度/结果的 instrumentation 原型。实现阶段验收应按以下场景登记，届时使用真实
新增的测试入口，不能把本文场景或历史测试数量当作本次执行证据：

1. 核验 v1 不写恢复状态且仍不支持恢复，回放 v2/v3 原快照及 v3 一个 supported 后
   `local_results_only` 的冻结反例；
   v4 则验证多值与反证继续，并单独验证 `resume_state.schema_version=1`。
2. 对比全拒、部分准入、已评分无新池、前置评估全拒无 epoch 和技术不可评分：均能持久
   推进、暂停或停止，不能卡在 `needs_semantic`；不伪造 epoch 或低分。
3. 重复、错 epoch、越池、错依赖和提交前 `AdmissionDecision` 均按契约拒绝或幂等处理；
   中断恢复不重新付费、不漏门评估/缓存/请求费用，也不重复创建原始 record 任务。
4. 同组兄弟、重叠组、低分必要来源、跨节点主体、否定/条件、长输入与单视图超限分别核验；
   组命中不等于全组已检查。覆盖恒等式不变，当前软剪枝仍属于 `unattempted`；公开状态
   区分 `adaptive_search_saturated` 暂停与 `in_scope_complete`，重激活实际检查后才更新覆盖。
5. P2/P3 变更视图后，按最终视图/查询/模型/策略重新采集影子数据，不能直接用 P1 旧视图
   数据做 P4 校准。HRS-1597 与 HRS-5592 均只作为开发暴露/回归样本；另准备未暴露保留
   文档、隔离金标和预注册门，继承至少三个真实新运行要求。重复运行不能替代独立样本。

正式实现的 API/前端与专用 PostgreSQL 验收仍分别登记；未配置专用可销毁库的 skip 不能
替代恢复/并发验收。完成文档检查不表示 AR-P0 验收、主动剪枝效果或部署完成。

2026-09-10证据修复的代码/实测状态见 [evidence-repair-validation.md](evidence-repair-validation.md)，
实际请求及恢复约束见 [evidence-repair契约](contracts/evidence-repair.md)。

## 证据修复增量验证

在 `backend/` 执行（隔离工程测试，不调用真实模型）：

```bash
.venv/bin/python -m pytest -p no:cacheprovider -q \
  tests/test_extraction/test_evidence_repair.py \
  tests/test_extraction/test_heuristic_search.py \
  tests/test_extraction/test_document_state_artifacts.py \
  tests/test_extraction/test_ontology_guided_boundaries.py
```

PostgreSQL 验收另用 `DOCUMENT_ANALYSIS_TEST_DATABASE_URL` 指向满足fixture要求的专用可销毁库，
运行 `test_evidence_repair_postgresql.py`、`test_performance_postgresql.py` 和
`test_document_run_execution_postgresql.py`；fixture会清表，不得指向共享业务库。

真实组先冻结到新目录，准备阶段不调用模型；模型配置仅采用脚本允许的非敏感字段：

```bash
.venv/bin/python scripts/benchmark_joint_evidence.py --prepare \
  --source /controlled/source.docx --model-config /controlled/model-config.json \
  --output /controlled/new-directed-run --evidence-repair
.venv/bin/python /controlled/new-directed-run/runtime/benchmark_joint_evidence.py \
  --execute /controlled/new-directed-run

.venv/bin/python scripts/benchmark_heuristic_document_run.py --prepare \
  --source-docx /controlled/source.docx --source-filename '原件完整文件名.docx' \
  --ontology-dir ../ontology/slpra --model-config /controlled/model-config.json \
  --output /controlled/new-auto-run --max-model-calls-per-record 8 \
  --max-tasks 24 --max-calls 48 --deadline-seconds 1200 --evidence-repair \
  --stop-after-focus-paths
.venv/bin/python /controlled/new-auto-run/runtime/benchmark_heuristic_document_run.py \
  --execute /controlled/new-auto-run
```

以上为HRS-5592专用诊断入口，默认拒绝不符原件hash；两组各至少3个独立run，组内固定输入/代码/预算。
定向组手工固定来源，仅自动组能证明实际检索与父子调度。查看summary、逐任务outcome、实际请求/响应与原文；
不得以退出码0或非空图代替业务核对。模型串行运行，每次写独立调度库，不修改历史制品。
`--stop-after-focus-paths` 仅用于预登记的关键节点计时：同一父对象的目标属性均进入有效投影
即可停止，原文正确性仍须另查；省略该选项可继续至共同预算，均不自动代表全文完成。

在线新运行只在 `DOCUMENT_ANALYSIS_EVIDENCE_REPAIR_ENABLED=true` 时冻结新协议，默认false。
只改该配置不会升级旧运行；必要部署另按实际授权执行。恢复沿用已有显式操作，预算不会刷新。
新归属政策`source-owned-binding-v2`要求原主体来源与局部主体引用分别留存；
旧归属版本的冻结响应不得以新政策复用，复测使用独立新目录/身份。
后续同时冻结`source-quoted-scope-v1`、`source-integer-quotes-v2`和`evidence-work-v2`；
缺少这些政策的修复运行不能交给新适配器继续。新范围引用、整数引用及工单顺序回归入口为
`test_evidence_scope_protocol.py`、`test_evidence_integer_quotes.py`与`test_evidence_work_continuation.py`。
本次v5六轮与修复后v6单轮结果分别报告：定向协议通过，但自动完整路径稳定性未通过；
不要把v5的两次成功合并为最终代码的三轮通过记录。
后续v9同版本定向/自动各三轮已完成：自动每轮六目标及同父路径正确、约429–432秒/17请求，
定向36项正例正确、6项日期反例实际拒绝。该指定目标验收不等于全图或独立专家质量门通过。
v8仅执行自动首轮，其余五轮在发现整数引用技术失败后停止派发；不能算六轮验收完成。
v9以完整整数引用组合重新冻结独立诊断，自动runner另存私有`model-wire-responses.jsonl`，
记录引用解码前JSON；原文、响应与运行库不得复制到公共结果制品。

识别结束后，用独立审阅入口核对目标原文与主体归属（结果写入私有目录）：

```bash
.venv/bin/python scripts/review_hrs5592_evidence_repair.py \
  --run /controlled/new-auto-run --group automatic \
  --output /controlled/new-auto-run/source-review.json
```

定向组使用`--group directed`。该入口固定HRS-5592源hash，直接读取DOCX XML与冻结本体祖先，
逐项核对完整原值及引用区间；识别输出自称supported不能代替原文通过。错日期未实际拒绝、
父关系失败导致子任务未执行、额外未评分事实均保留；此为开发样本审阅，不是专家金标评分。
`test_evidence_repair_source_review.py`覆盖错误引用区间、同名异行、假条件及失败/未执行项保留。

本特性复用已有后端和前端环境。下面先执行隔离工程检查；真实评测步骤仅在本地制品、服务和独立参考就绪后执行。工程检查不启动应用、不迁移共享数据库、不调用真实模型。

既有工程记录见 [validation.md](validation.md)；2026-09-10 性能增量的实际命令、指标与边界见
[performance-validation.md](performance-validation.md)，新增接口/恢复契约见
[contracts/performance.md](contracts/performance.md)。
模板面板迁移的定向测试合集、当前暂停状态及后续金标准脚本见
[工程收尾验证](kernel-panel-migration.md#工程收尾验证)。该模板迁移阶段于2026-09-09先以
TDD/回归收尾；后续性能实测与完整模板/专家质量分开登记。

## 1. 隔离工程检查

性能增量在 `backend/` 的最小定向入口：

```bash
.venv/bin/python -m pytest -p no:cacheprovider -q \
  tests/test_api/test_document_analysis_performance.py \
  tests/test_extraction/test_document_state_artifacts.py \
  tests/test_extraction/test_recognition_coordinator.py \
  tests/test_extraction/test_ranking_performance.py \
  tests/test_extraction/test_scheduler_performance.py \
  tests/test_extraction/test_document_analysis_execution_recovery.py \
  tests/test_extraction/test_ontology_guided_boundaries.py
```

在 `frontend/` 执行 `node --test tests/document-analysis-runs.test.mjs
tests/template-document-performance.test.mjs`；真实组件的模拟API浏览器入口为
`node tests/template-document-performance-browser.mjs`，另需可用的esbuild与Playwright/Chrome。
可用`ESBUILD_MODULE/PLAYWRIGHT_MODULE`指定已有安装；该脚本不代表真实后端集成。

无模型状态回放使用新输出目录：

```bash
.venv/bin/python scripts/benchmark_document_state.py \
  --input /controlled/exported-artifacts.jsonl.gz \
  --output-dir /controlled/new-performance-replay --samples 30
```

输入gzip JSONL须含`artifact_kind/revision/event_head/payload`，保留原状态各版本以及
最终ontology/metadata；工具只访问该文件和新SQLite目录。报告区分逻辑体积、服务计时与
线上性能，不把原文、向量或SQLite输出加入Git。专用PostgreSQL故障命令和清表限制见
[本次验证](performance-validation.md)。

新运行默认`DOCUMENT_ANALYSIS_PERFORMANCE_ENABLED=true`，冻结存储/前沿版本2、
识别在途1；`DOCUMENT_ANALYSIS_TEMPLATE_INTERLEAVING=false`，仅显式新实验开启。
总开关false只影响新建运行的冻结策略；已写版本2的运行仍需本版本兼容reader/writer。
默认batch保持4。按用户后续要求，只测试新方案，不执行新旧性能对照；
绝对指标与正确性结果不能直接代替模板首结果或双任务并行验收。

新方案真实模板的隔离入口为
[benchmark_template_document_run.py](../../backend/scripts/benchmark_template_document_run.py)。
先准备新的空PostgreSQL专库，名称必须以`_template_performance_test`结尾且位于loopback；
输入JSON包含真实模板/源作业元数据、完整优先路径、冻结本体和允许的非敏感模型配置，
不含留出金标。仅准备时创建独立运行但不调用模型；`--execute`才执行真实模型：

```bash
# 工作目录backend；使用专用新库和新输出目录，勿指向线上库。
.venv-cuda12/bin/python scripts/benchmark_template_document_run.py \
  --database-url postgresql+psycopg2://postgres@127.0.0.1:55440/new_template_performance_test \
  --input /controlled/frozen-template-input.json \
  --document /controlled/source.docx --output /controlled/new-template-diagnostic \
  --tasks 8 --pause-after-tasks 2
# 核对输出manifest后，用完全相同参数加 --execute 执行本次已准备运行。
```

该入口复用正式`TemplateDocumentRuns.create/dispatch_run`，保留真实解析与发现/独立验证，
仅以导出的精确本体替换创建时的本体获取边界。总任务预算8不因暂停/恢复重置，
每lineage6次，因此主请求上限48；排序费用另计。报告包含完整分母、暂停恢复、费用、
绝对耗时及原文回放结果；核心关系或路径未完成时明确保持未验收，不用进程退出码代表质量。

在 `backend/` 执行当前存在的定向测试：

```bash
.venv/bin/python -m pytest -p no:cacheprovider -q \
  tests/test_extraction/test_semantic_ranking.py \
  tests/test_extraction/test_semantic_ranking_adapter.py \
  tests/test_extraction/test_semantic_ranking_execution.py \
  tests/test_extraction/test_semantic_graph_closure.py \
  tests/test_extraction/test_independent_semantic_verification.py \
  tests/test_extraction/test_ontology_snapshot_ordering.py \
  tests/test_extraction/test_ontology_guided_stop_reasons.py \
  tests/test_extraction/test_semantic_model_call_budget.py \
  tests/test_extraction/test_semantic_pause_recovery_regressions.py \
  tests/test_extraction/test_evaluation_execution_accounting.py \
  tests/test_extraction/test_semantic_proof_regressions.py \
  tests/test_extraction/test_semantic_proof_persistence.py \
  tests/test_extraction/test_ontology_guided_node_revisions.py \
  tests/test_extraction/test_semantic_subject_versions.py \
  tests/test_extraction/test_semantic_evaluation_prepare.py \
  tests/test_extraction/test_semantic_ranking_evaluation.py \
  tests/test_extraction/test_ontology_guided_evaluation.py \
  tests/test_extraction/test_ontology_guided_boundaries.py
```

在 `frontend/` 执行已有状态测试及类型检查：

```bash
node --test tests/document-analysis-runs.test.mjs tests/document-ranking.test.mjs
./node_modules/.bin/tsc --noEmit
```

核对链路：显式创建运行 → 元数据就绪 → 主体/谓词排序先持久提交 → 两阶段原文实际执行 →
产品/API 组成两跳独立证明 → 图谱增量与只读排序详情 → 暂停/恢复保持已提交顺序 →
迟到同范围反证阻断旧路径。受控响应测试证明这条工程链路，不代表模型已理解真实制药语义。

必须保留摘要冒事实、编号角色错配、缺主体桥接、全负分、批次缺项/重复、超长必要上下文、
计划与台账同时漏记录、有效租约下旧查询返回、同 hash 跨用户、删除后晚到响应等反例。
GET、刷新、Tab/详情切换不增加模型请求。专用 PostgreSQL 并发与真实浏览器集成的结果
单列；SQLite 或合成浏览器拦截不能替代它们。

## 2. 冻结真实运行配置

在实际评测环境中配置以下非敏感字段，再建立新的 preparation；不在运行途中修改：

| 环境变量 | 当前默认值或取值 |
|---|---|
| `DOCUMENT_ANALYSIS_MAX_MODEL_CALLS_PER_RECORD` | `6`；语义主体/谓词/记录 lineage 的调用额度，重验继承 |
| `SEMANTIC_RANKING_ENABLED` | `false`；显式开启设 `true` |
| `SEMANTIC_RANKING_MODE` | `semantic`；合法值 `deterministic`、`semantic` |
| `SEMANTIC_RANKING_DEVICE`（CUDA 增量） | `cuda:0`；CPU 显式设 `cpu` |
| `SEMANTIC_RANKING_DTYPE`（CUDA 增量） | `float16`；CPU 须同时设 `float32` |
| `SEMANTIC_RANKING_CUDA_VERSION`（CUDA 增量） | 固定 `12.6`；CPU 不初始化 CUDA |
| `SEMANTIC_RANKING_FAILURE_POLICY` | `pause`；也可预先选 `deterministic` |
| `SEMANTIC_RANKING_EMBEDDING_PATH` / `SEMANTIC_RANKING_EMBEDDING_MANIFEST_PATH` | 本地模型目录 / 独立 SHA256 清单，默认空 |
| `SEMANTIC_RANKING_RERANKER_PATH` / `SEMANTIC_RANKING_RERANKER_MANIFEST_PATH` | 本地模型目录 / 独立 SHA256 清单，默认空 |
| `SEMANTIC_RANKING_POOL_SIZE` / `SEMANTIC_RANKING_BATCH_SIZE` | `64` / `4`；池记录数与请求输入数分开 |
| `SEMANTIC_RANKING_MAX_TOKENS_PER_PAIR` | `4096`；范围 `64..32768`，实际还受模型上限限制 |
| `SEMANTIC_RANKING_MAX_TOKENS_PER_SLOT` / `SEMANTIC_RANKING_MAX_TOKENS_PER_RUN` | `524288` / `4194304` |
| `SEMANTIC_RANKING_TIMEOUT_SECONDS` / `SEMANTIC_RANKING_RETRY_LIMIT` | `1200` / `1`；重试范围 `0..3` |
| `DOCUMENT_ANALYSIS_DISPATCH_CONCURRENCY` | `1`；不等同跨进程显存租约 |
| `SEMANTIC_RANKING_GPU_ID`（Compose） | 须指定一张物理 GPU UUID/编号；未配置不暴露 GPU |

每模型清单采用 `sha256sum` 格式，文件路径相对对应模型根，必须覆盖全部配置、
`tokenizer.json` 和 `.safetensors` 等文件；空清单、遗漏、哈希不符或符号链接失败。
只提供模型别名不构成不可变身份。加载固定为本地、禁止远程代码；设备按冻结配置选择，输入不静默截断。

排序关闭仅表示确定性检索；真实图谱仍需要已配置的主提议/验证模型。
沿用现有 `LOCAL_LLM_ENABLED`、`LOCAL_LLM_MODEL`、`LOCAL_LLM_MODEL_REVISION` 和
tokenizer 配置：`LOCAL_LLM_TOKENIZER_BACKEND=file` 时提供 `LOCAL_LLM_TOKENIZER_PATH`，
`llama_server` 时提供与实际服务一致的 `LOCAL_LLM_SERVER_MODEL_PATH`。凭据继续从受控环境注入。

CPU 排序环境的安装、制品准备和真实运行结果见 [cpu-ranking.md](cpu-ranking.md)。
当前基础 Compose 使用 GPU 镜像；CPU 生产入口为
`docker compose -f docker-compose.yml -f docker-compose.cpu.yml`，开发模式在 CPU 文件前
加入 `-f docker-compose.override.yml`。本地 `.venv` 使用 CPU 时必须显式配置
`SEMANTIC_RANKING_DEVICE=cpu SEMANTIC_RANKING_DTYPE=float32`；原诊断脚本仍保留 CPU
缺省，GPU 试验继续按下方要求显式传参。详见 [默认 GPU 变更](gpu-default.md)。
旧 BGE/GLiNER 目录的空 `MODELS.sha256` 不用于新排序模型身份；新制品使用独立完整清单。

`v4.3-independent` 的候选发现与验证分两次请求，逐记录预算至少 2 才能完成一次有候选的独立验证。验证的七项判定与四组证据数组须明确返回；根身份由程序校验并要求空根主体引用，局部主体仍须原文归属证明。每阶段按完整 prompt/schema/context 实测 token；发现无候选只用 1 次。连续暂停保留续执行机会，但不会刷新 lineage 预算。公开进度的 `model_calls` 为已记账调用，`model_calls_reserved` 为累计预扣，`model_calls_unresolved` 为预扣但无完整结果记录的部分；未知不能当作零成本。

真实活动评测运行目录保存 `scheduler.sqlite3`、`scheduler_requests.json`、`ranking_state.json`、`model_call_state.json`、`costs.json` 和 `execution_status.json`。这些是本轮独立调度/费用制品；失败也保留，不覆盖旧结果。暂停或技术未完成的运行不能因为进程正常退出就当作质量验收通过。
环境冒烟与文档排序不等于下面的质量验收：专家参考、独立文档和预注册协议仍需就绪。真实主模型独立验证、失败诊断与修复复测见 [independent-verification.md](independent-verification.md)，工程组合及数据库/浏览器证据见 [validation.md](validation.md)。

## 3. 新准备快照与 A–D 运行

以下命令工作目录为 `backend/`。独立入口 `prepare --source-docx` 接受获准用于评测的
本地 DOCX，并要求显式 `--root-class-iri`；根类型必须存在于冻结本体，活动模式可选择
任意合法根。本地准备校验 DOCX 并保留原件，不依赖旧抽取作业、不查询模型端点，
输出目录必须尚不存在。示例路径须替换为真实受控输入。

```bash
CMC_QUALITY_DIR=/controlled/evaluations/semantic-graph-022-cmc-01

.venv/bin/python -m app.evaluation.cmc_benchmark prepare \
  --source-docx /controlled/sources/independent-cmc-01.docx \
  --root-class-iri https://ontology.pharma-gmp.cn/slpra/drug-development/CMCReport \
  --output "$CMC_QUALITY_DIR"

.venv/bin/python -m app.evaluation.cmc_benchmark summarize \
  --prepared "$CMC_QUALITY_DIR" --timeout 600

for CMC_GROUP in A B C D; do
  .venv/bin/python -m app.evaluation.cmc_benchmark run \
    --prepared "$CMC_QUALITY_DIR" \
    --output "$CMC_QUALITY_DIR/${CMC_GROUP}-01" \
    --mode quality_guided_summary --ranking-ablation "$CMC_GROUP" \
    --timeout 600 --timeout-retries 0
done
```

这是一轮协议命令示例，不是本次执行记录。目录必须新建且唯一；prepare 冻结原件、IR、
本体、根类型、代码和配置，summarize 生成一次摘要供各组共用。summarize/run 会调用本地模型并写
调度、用量及制品记录，不能称为只读。实际超时和预算必须先在独立协议冻结，`600` 仅为示例。

兼容参数 `--document-ref` 与 `--source-docx` 互斥，仅接受既有 `extraction_jobs` 中
已登记为 CMCReport 的 doc_ref，不接受 DocumentAnalysisRun ID；显式根与登记类型冲突时失败。
旧评测模式仍限定 CMC 根，独立新文档不需要为此创建历史作业。

| 组别 | 注册因素 | 本地排序制品 |
|---|---|---|
| A | 确定性基线；阶段不交错 | 不调用排序模型 |
| B | A 增加稠密召回；阶段不交错 | embedding |
| C | B 增加联合编码精排；阶段不交错 | embedding + reranker |
| D | C 增加阶段交错 | embedding + reranker |

各组保留共同的根/主体/谓词/章节公平性及必要证据验证。CLI 的 `--ranking-ablation`
仅允许活动 `quality_guided` / `quality_guided_summary`，未指定时沿用正式配置。
不能把降级为 deterministic 的 C/D 算作正常语义组。按协议对关键正反例至少完成三个独立
新 run，使用 `A-02` 等新的输出目录，并保存全部结果；同一文档重复不能替代独立文档样本。

活动评测不支持 `--resume`：暂停/中断制品必须保留，再用新目录/运行身份重跑。
线上 DocumentAnalysisRun 的持久恢复另由工程测试验收，不能把两种恢复协议混用。
焦点评测可显式追加同一 `--focus-path`，其结论只覆盖冻结路径，不能声称全文完整。

## 4. 独立评分与固定池核验

正式参考使用 `ontology-guided-reference-v1`，要求批准的专家复核身份、时间与受控标注包 hash，
文档/本体/根/scope 与 run 相同；完整 tuple、方向/条件/适用域、等价原文证明、局部实体映射、
必要身份分区和路径在预测前冻结。助手生成样本或既有 assistant silver 不作为正式金标。

```bash
.venv/bin/python -m app.evaluation.cmc_benchmark score \
  --prepared "$CMC_QUALITY_DIR" --run "$CMC_QUALITY_DIR/C-01" \
  --reference /controlled/gold/ontology_guided_reference_v1.json

.venv/bin/python -m app.evaluation.semantic_ranking_evaluation \
  --reference /controlled/gold/query-record-reference.json \
  --observation /controlled/results/fixed-pool-observation.json \
  --k 10 --output /controlled/results/retrieval-metrics.json
```

查询—记录参考和观察的字段见[活动评测说明](../../backend/app/evaluation/README.md)。
评分按原始 record 去重，要求完整池排名，分别核对入池、实际读取和已装配的全部必要来源。
窗口、多意图命中和 binding-only 表头不能重复扩大分母；未裁决记录或缺少完整标注只作局部观察。

每轮输出 `run.json`、`result.json`、`events.json`、`retrieval-plans.json`、`ranking.json`、
`costs.json`、`ablation.json` 和实际发生调用时的 `calls.jsonl`。检查排序降级、覆盖未完成、
真实派发顺序及所有模型/排队成本，不能只读最终有效图。
`validate_ablation_pair(left, right, allowed_factor_changes=..., fixed_pool=False)`
可比较动态运行的 ablation 制品；允许因素列表必须来自预注册协议。
`build_ablation_manifest(result_manifest, run, epoch_id=...)` 可导出指定 epoch 身份，
再用 `fixed_pool=True` 验证 B/C 的相同查询内容、完整记录集合、pool/view hashes。
不同动态前沿未形成相同池时会拒绝，须另做共同候选池子实验，不能绕过检查。

共同池现有可执行入口为 `python -m app.evaluation.fixed_pool_benchmark` 的
`export`、`freeze-protocol`、`run`、`aggregate`。完整命令、至少三轮 A–D 登记格式及
参考文件门禁见 [FIXED_POOL.md](../../backend/app/evaluation/FIXED_POOL.md)。
聚合必须同时提供 `--pool` 与预登记 `--protocol-hash`，不能只靠各组彼此相同判断正确。
此工具只检验固定池排序；C/D 的阶段交错收益仍需动态前沿实验，空参考/空阈值协议不能通过质量门。

通过口径必须同时覆盖正确正例、关键反例、完整 tuple P/R/F1、身份/路径、原文回放和成本。
无预测时 P=N/A，有正例未输出时 R/F1=0；未裁决、错误证明、过期引用、跨路径借证或路径枚举
截断不能换算为成功。单轮 `formal_quality_gate=pass` 不等于精度增量或发布通过：
独立主指标、最小增量/非劣界限、置信水平、文档样本规模及成本门槛仍须预注册并完成比较。

## 5. P5 和交付边界

本期覆盖 P0–P4 工程机制，必要上下文装配和迟到反证失效已纳入主链。
P5 的有界缺口驱动补充检索、补充上下文联合编码及 E0/E1、F0/F1 组别属于后续扩展，
当前 CLI 不提供这些组选项；须另冻结补充候选池、验证额度与独立实验协议。
真实质量材料不足时保持研发待验；本快速验收不执行部署、本体修改、旧域清理或业务事实提交。

## 6. CUDA 12 GPU 增量验收

本节是新增执行契约，任务与实际状态见 [tasks.md](tasks.md) 和
[gpu-ranking.md](gpu-ranking.md)。CPU 继续使用原环境及 `cpu/float32`；GPU 另建隔离
环境，固定 PyTorch 2.7.1/cu126（CUDA 12.6）。安装与镜像步骤见
[GPU_CUDA12.md](../../backend/GPU_CUDA12.md)。迁移到其他机器时仍须核验
实际安装版本、`torch.version.cuda`、驱动、可见卡列表、所选 `cuda:N` 和 FP16 推理。
不在 CPU 环境中覆盖安装 CUDA 依赖，也不因 GPU 不可用自动改用 CPU。

新增配置与脚本已实现。复现时按以下次序执行，每次使用新输出路径：

1. 冻结实际 GPU 环境清单、完整模型 manifest 和设备/dtype；保留 CPU 环境清单。
   CUDA 版本以实际 PyTorch runtime 为依据，驱动报告的兼容上限与系统 toolkit 分列。
2. 使用 GPU 环境运行 [smoke_semantic_ranking.py](../../backend/scripts/smoke_semantic_ranking.py)，
   显式选择 `--device cuda:N --dtype float16 --cuda-version 12.6`，保留 CPU 冒烟及新的独立输出目录。
   验证完整长度、有限 raw logit、L2 向量、失败后 worker 清理及恢复；合成输入结果不计
   为真实文档质量结论。长输入整批边界可加 `--length-boundaries --length-boundary-batch-size 4`，
   同时配置 `--batch-size 4 --max-tokens-per-pair 4096`；按本轮实测预留约 8 GiB 空闲显存。
3. 使用 [check_document_semantic_ranking.py](../../backend/scripts/check_document_semantic_ranking.py)
   分别以 CPU/GPU 环境执行新目录，显式 `--device cpu --dtype float32` 与
   `--device cuda:N --dtype float16`。两次传入相同 `--prepared`、`--predicate-iri`、
   模型路径/manifest、batch/token/时间预算；首轮令池覆盖全部 eligible 记录并使用
   `--epochs 1`，避免排名差异改变下一轮池成员。若做热态试验，另冻结共同池和缓存口径。
4. 对照原件/IR/本体、查询实际文本及意图、每条完整视图、记录集合和 token 数；运行
   归属 ID 可以不同。核验原文全集和语义覆盖不变、双意图齐全、无截断/缺项、同环境
   committed epoch 零新请求恢复；GPU 分数和排名无需逐位等于 CPU。
   使用 [compare_semantic_ranking_runs.py](../../backend/scripts/compare_semantic_ranking_runs.py)
   的 `--left CPU_DIR --right GPU_DIR --output NEW.json` 严格检查共同输入后生成对照，
   两组使用相同 `.venv-cuda12` 包版本、`--pool-size 86 --epochs 1`；原 CPU `.venv` 独立保留。
5. 保存 GPU 冷启动/热态、调度/请求/排队/墙钟、模型与实际 GPU 内存数据，分列实际输入
   和预扣成本。GPU 异步内核计时须同步；只有完整调用边界与输入一致才计算耗时比例。
   OOM、取消或超时保留新失败制品，不删失败、不写入旧 CPU/GPU 实验。

设备/dtype 比较与 A–D 算法消融分开登记。固定池聚合仍拒绝数值环境漂移，不能为了
混合 CPU/GPU 结果放宽共同环境门。GPU 排序更快、分数近似或工程测试通过都不能代替
完整图谱证明、独立专家参考和 T017 正式质量/成本门。

### 排序暂停排查

查看运行状态及图谱展开提示中的具体原因。`ranking_paused` 是执行暂停，
`ranking_call_budget_exhausted` 是逐记录/请求额度耗尽，不表示 GPU 未开启；
`attempted_incomplete` 说明部分原文任务尝试后尚未完成。
运行中停止原因应为空，已提交 semantic 轮次应保持可读。
恢复不能重置冻结预算；旧请求无明确未派发回执时不得手工清账。
本次根因、修复与旧运行边界见 [暂停修复记录](ranking-pause-followup.md)。

## 7. 运行级排序预算开关

排序预算默认启用。新运行读取 `SEMANTIC_RANKING_BUDGET_ENABLED`（缺省 `true`），
历史运行通过迁移默认启用；修改新建默认值不会替代已有运行的显式控制操作。
该开关只控制累计排序 tokens、记录和请求额度，不关闭 embedding 召回或 reranker 精排。
单次输入完整性与长度、超时、自动技术重试、取消，以及独立的文档识别任务预算仍生效。

UI 验收使用有权操作的既有运行：

1. 在“文档分析”的运行状态卡查看“排序预算限制”。只有已暂停或可恢复失败、未过期且
   未删除的运行向有写入权限的所有者提供反向操作；运行中需先完成暂停。
2. 点击“禁用排序预算限制”，确认仍是同一运行且仍暂停，显示
   “预算统计已暂停（显示启用期间累计值）”。禁用期间不预扣、不累计排序预算，保留
   关闭前的账目；禁用期间的用量不会在重新启用时补记。
3. 如需继续识别，再显式点击“恢复”。切换预算开关、刷新和切换结果 Tab 均不会自动恢复。
   这一恢复步骤会启动实际模型处理，不属于只读浏览器检查。
4. 再次暂停后可“启用排序预算限制”，从关闭前的累计量继续检查额度，原有限额与
   冻结身份保持不变。图谱中未记账轮次标为“预算未计账”，不能把它解释为实际成本为零。

API 使用同源鉴权与运行控制请求体：

| 操作 | 路径 | `operation` / `available_actions` |
|---|---|---|
| 启用 | `POST /api/document-analysis/runs/{id}/ranking-budget/enable` | `ranking_budget_enable` |
| 禁用 | `POST /api/document-analysis/runs/{id}/ranking-budget/disable` | `ranking_budget_disable` |

```json
{
  "expected_revision": 8,
  "request_key": "a-unique-key-for-this-explicit-operation",
  "reason": "用户显式调整当前运行的排序预算限制"
}
```

`expected_revision` 必须来自刚读取的运行；版本冲突按 409 处理并重新读取。
同一操作重试沿用幂等键，新的操作使用新键。运行与控制回执顶层返回
`ranking_budget_enabled`，回执按 revision/event/artifact 水位合并；
图谱 `ranking.budget_enabled` 表示当前控制值，`epochs[].budget_accounted` 区分轮次是否计账。
旧字段缺失按启用/已计账兼容，不清空历史费用、已提交图谱、数值阈值或 fingerprint。

本轮工程验证、PostgreSQL 迁移范围、空库历史迁移缺口、真实控制往返及后端重载/健康
实测见 [排序预算控制记录](budget-control.md)。T041 已完成；合成浏览器、真实控制 API
往返与模型运行质量分别记载，T017 的正式质量门仍保持开放。


## 2026-09-11 增量性能策略

新建且开启证据修复的运行冻结 `incremental-performance-v1`、存储v3、启发式v3、
`bounded-semantic-v1`、`whole-method-field-v1`、`source-field-priority-v1`。
旧运行继续原格式与次序。本轮未部署，不能仅通过页面刷新让在途任务获得新策略。

在backend目录执行新增验证：

```bash
.venv/bin/python -m pytest -p no:cacheprovider -q \
  tests/test_extraction/test_incremental_state.py \
  tests/test_extraction/test_incremental_performance.py
.venv/bin/python -m scripts.benchmark_incremental_state \
  --input /private/frozen/decoded-state-1.json \
  --output /tmp/new-incremental-replay --immutable
```

回放只创建新SQLite库，不读写在线库、不调用模型；输出目录必须不存在。
完整受影响回归清单、原件结构诊断、热保存/基线/冷恢复的不同口径见
[增量性能验证](incremental-performance-validation.md)。缺少专用PostgreSQL配置的测试明确skip。
