# CMCReport 文档图谱实测环境

## 质量优先实验（新增）

`quality_guided_summary` 使用当前主体的直接本体菜单逐批审阅逻辑记录；每条记录完成对象发现和关系验证后，再展开已验证对象。范围外记录必须通过生产独立绑定和 `verify_reference`，不能由摘要或同名自动授权。表格引用逐单元原子回放；可靠身份归并后重写版本化端点和依赖，晚到竞争主体会触发保守撤销。

该模式不以性能收益验收。新建输入/代码快照后，使用以下方式运行；省略 `--deadline-seconds` 和 `--pause-after`，仍保留单请求安全超时和总任务上限。不要把旧 600 秒预算实验与此模式的全程运行作速度比。

```bash
export CMC_EVAL_IMAGE=sha256:cda2fd0551b14071dccba56c19888315e96b7ce7e04dd9610434eb57ba7f6c15
CMC_QUALITY_DIR=/app/data/evaluations/cmc-quality-example

docker compose -f docker-compose.yml -f backend/app/evaluation/compose.yaml \
  run --rm --no-deps --entrypoint python -T backend \
  -m app.evaluation.fork_experiment \
  --source /app/data/evaluations/cmc-root-guided-20260908-03 \
  --output "$CMC_QUALITY_DIR"

docker compose -f docker-compose.yml -f backend/app/evaluation/compose.yaml \
  run --rm --no-deps --entrypoint python -T \
  -w "$CMC_QUALITY_DIR" -e "PYTHONPATH=$CMC_QUALITY_DIR/runtime" backend \
  -m app.evaluation.cmc_benchmark run \
  --prepared "$CMC_QUALITY_DIR" --output "$CMC_QUALITY_DIR/quality-01" \
  --mode quality_guided_summary --timeout 600 --timeout-retries 0
```

本模式的暂停在逻辑记录边界进行，一条记录的发现与绑定不会被软截止拆开。检查 `plan.json` 的 `coverage/routes/instance_merges/invalidations`，以及 checkpoint 中的未完成队列；`route_negative_not_extracted` 表示路由筛除、并未穷尽事实抽取，不等于原文没有事实。即便已选中记录都处理完，也不能据此宣称全文图谱完整。

评分继续使用下文的 `score` 命令与同一冻结参考，不给模型提供参考答案。已知 CMCReport 根排除在主抽取评分外；引用可回放、生产语义验证通过、独立参考匹配须分别报告。

当前工作区质量实现为 runner v5.2 / atomic-citations v4：模型引用仅允许 `evidence_id` 与可选精确 `text`，模型坐标与自由 `context` 被 schema 和解码器共同拒绝；程序负责唯一定位。路由表格按物理单元格、多段原文、实际列头和合并行列配对。标题/摘要及检索排序均不授予事实权限。

v5.1 另修复恢复 checkpoint 时的审核门禁：上游边即使 validation=passed，只要 review=rejected，也不能继续驱动下游调用；同时检查路径方向、根、当前端点和依赖版本。冻结 08 实际执行 v5，不包含此后续修复；该实验是新运行、候选审核状态保持 pending，两者的验证结论不得混写。

08 已在确认身份验证状态丢失后安全软暂停：第一跳通过，识别到独立 API，但 API 归属与产品属性未通过 reference，完整链路尚未验证。工作区模型上下文现为 `model-context-v6-verified-identity`，把类型/身份验证状态保留到模型投影，未支持的身份键只留原始审计和初次实体类型验证，不作为下游可靠身份。runner v5.2 同时修复记录路由的独立提示出口，并将身份验证状态纳入路由缓存键。冻结 08 不含这些修复，不能用工作区代码直接恢复或声称修复已在该真实实验中生效。全部原始输出、剩余队列和独立评分见质量报告第 6 节。

新增 `--focus-path` 用于验证正式本体内的一条递归关系路径。例如在上述 run 命令中追加：

```text
--focus-path https://ontology.pharma-gmp.cn/slpra/drug-development/describes https://ontology.pharma-gmp.cn/slpra/drug/hasActiveIngredient
```

该路径已经由现有本体支持，不需要新增 CMCReport→API 直连或扩大 DrugProduct 定义。焦点关系使用 `staged_retrieval`，第一阶段按局部 range 类型及其字段线索召回最多三个高相关章节，第二阶段保留所有其余非标题记录；各阶段按章节轮转，首个类型/关系拒绝不会终止后续召回。相邻同章节完整段落字段块只加入 binding 上下文，不能冒充新事实目标。关系候选与产品属性任务各处理一个后轮转，通过的关系立即展开对象。

焦点实验只调度所给关系路径及已到达主体的直接属性，不覆盖其他 CMCReport 根关系。输入身份包含焦点路径，不能以不同路径恢复 checkpoint。阶段计划是检索清单，实际进度以 coverage/checkpoint 为准；出现一条完整路径也不代表全文无遗漏。API 不在既有银标中时须单独审阅原文归属，不能将 unscored 当作正确。

完成或正常软暂停后，可只读导出该次运行的图谱；下面命令输出 JSON，不读取参考答案，也不会合并其他运行的候选：

```bash
cd backend
.venv/bin/python -m app.evaluation.quality_graph_export /absolute/path/quality-01/run.json
```

输出含 `full_validated_candidate_graph` 与 `root_reachable_positive_graph`。前者完整保留 passed 候选（包括否定/条件），后者排除非肯定、条件、审核拒绝、陈旧引用和不可达依赖。导出并不等于专家确认。独立审计入口为 `app.evaluation.quality_analysis --prepared DIR --run DIR --reference FILE`，活动运行存在 marker 时拒绝正式评分。

新增只读拒绝审计入口 `python -m app.evaluation.rejection_analysis --run DIR`，在正常结束或安全软暂停后输出 JSON，不读取银标、不修改原始产物。它区分类型/绑定/归属拒绝、空召回、模型拒答和身份未获支持，预算/协议错误另计；拒绝理由缺失时如实标记。通过 trace 及真实任务顺序判断是否继续了不同记录，队列存在仅表示 pending，不算已经执行。未进入最终候选图的被拒绝关系/属性仍从 trace 审计；c0/e0 等别名只能在各自请求作用域内解释。该工具不判断拒绝语义正误或计算准确率，仍需独立原文审阅。09 已冻结后才新增此离线工具，它不参与09抽取。

本轮诊断与本体 API/制剂口径问题见[质量优先实测报告](../../../docs/CMCReport质量优先图谱识别实测报告.md)。不应为提高得分自行扩大 `DrugProduct` 定义或修改冻结参考。

此工具对 `upload-23c872fb-3ab1-41de-a705-dd4b162dfa09` 执行隔离对照实验。入口是 `python -m app.evaluation.cmc_benchmark`，使用 Docker Compose 的独立一次性容器，继承 `backend` 服务已配置的本地模型、精确 tokenizer、可选 GLiNER 权重及持久数据卷。

本次四组预算内试验已结束，结果见[实测报告](../../../docs/CMCReport结构摘要图谱识别实测报告.md)和[可离线复算归档](../../../docs/evaluations/cmc-23c872fb-20260907-02/README.md)。本次额外使用 `--deadline-seconds 600`，实际只尝试了 1/4/3/5 个任务，没有达到 24 次上限；所有组仍为全文未完成。以下命令是通用复现步骤，不应把只设任务上限的运行与本轮软时间预算直接混比。

`prepare` 在数据库只读事务中按文档引用定位最新抽取作业，核对其显式类型为 `CMCReport`，然后复制原文、本体和运行代码，生成独立证据 IR、语义 schema 及清单。准备阶段的 OWL 存储位于实验临时目录；`run` 使用冻结的 schema，不打开生产 OWL 存储。所有实验输出写入指定评测目录，不更新生产抽取作业、不提交候选、不写事实图谱。

这不等于整个实验没有数据库写入。摘要和抽取复用现有共享模型调度器，可能产生 `LocalModelPool`、`LocalModelRequest` 等调度与用量记录；这些属于模型运行记录。只读保证针对 `prepare` 的源作业查询，生产抽取作业、候选和事实数据不由本评测提交或更新。

## 1. 准备一个新的实验目录

以下命令在宿主机 Bash 的仓库根目录执行，并使用与当前部署相同的 Compose 项目和环境变量；本工作区为 `/opt/dev/chen/ontology-agent`。评测显式合并 `docker-compose.yml` 和 [compose.yaml](compose.yaml)，保留基础服务的网络、数据卷及配置，并锁定不可变镜像 ID。先将示例目录改为本次独有的名称；`prepare` 拒绝覆盖已存在的目录。数据库和本地模型服务需已经可用，`--no-deps` 不会启动它们。

新实验开始前，从正在运行的后端容器读取一次实际镜像 ID 并保存到变量。之后所有准备、摘要、抽取和评分步骤保持该值；不要每次都重新读取可能已被重建的生产后端。

```bash
export CMC_EVAL_IMAGE="$(docker inspect --format '{{.Image}}' ontology-agent-backend-1)"
docker image inspect "$CMC_EVAL_IMAGE" --format '{{.Id}}'
```

本次摘要与四组对照实验统一锁定下列镜像；重放此冻结实验时直接恢复这个值，并使用新的输出目录：

```bash
export CMC_EVAL_IMAGE=sha256:cda2fd0551b14071dccba56c19888315e96b7ce7e04dd9610434eb57ba7f6c15
```

覆盖文件要求 `CMC_EVAL_IMAGE` 非空，通过 `build: !reset null` 移除基础服务的构建配置，并使用 `pull_policy: never` 禁止自动拉取。所指定镜像必须已在本机存在；缺失时应报错，不回退到新镜像。使用支持 `!reset` 的 Compose V2；本机已验证版本为 2.29.7。

```bash
CMC_EVAL_DIR=/app/data/evaluations/cmc-20260907-example01

docker compose -f docker-compose.yml -f backend/app/evaluation/compose.yaml \
  run --rm --no-deps --entrypoint python -T \
  backend -m app.evaluation.cmc_benchmark prepare \
  --document-ref upload-23c872fb-3ab1-41de-a705-dd4b162dfa09 \
  --output "$CMC_EVAL_DIR"
```

`manifest.json` 记录原文、代码、本体、schema、IR 的身份与哈希，以及模型配置、包版本、硬件信息、解析耗时及模型服务槽位状态。`/app/data/evaluations/` 位于现有 `backend_data` 命名卷内，容器退出后产物仍保留。模型权重和 Python 依赖继续使用现有环境；目录快照本身不包含权重，也不是完整容器镜像。

每一步都会启动独立进程与容器，生产 `backend` 服务容器被重建时不会顺带终止该一次性容器。`--entrypoint python` 直接执行评测模块，绕过服务启动脚本及其迁移步骤；`--rm` 在任务结束后清理一次性容器。没有使用 `--service-ports`，不会额外发布后端监听端口。

后续步骤必须运行冻结代码。工作目录设为实验目录，避免当前 `/app` 抢先导入仍在开发的代码；同时将快照目录加入 `PYTHONPATH`。以下辅助函数为每次调用创建独立的 Compose 一次性容器：

```bash
cmc_eval() {
  docker compose -f docker-compose.yml -f backend/app/evaluation/compose.yaml \
    run --rm --no-deps --entrypoint python -T \
    -w "$CMC_EVAL_DIR" \
    -e "PYTHONPATH=$CMC_EVAL_DIR/runtime" \
    backend -m app.evaluation.cmc_benchmark "$@"
}

cmc_eval --help
```

`summarize` 和 `run` 会检查冻结运行代码、原文、schema、IR，并核对重新解析得到的 `analysis_id`。输入不一致时需要准备新实验，不能把不同快照混为同一组对照。旧清单缺少的冻结参数会以 `legacy_manifest_settings_missing` 事件提示，并将实际值记入产物；不能假定旧清单已冻结所有参数。

一次性容器仍使用同一 Compose 项目的持久卷和网络。不要切换项目名，否则可能连到另一份数据卷。后续生产镜像重建不会改变已保存的 `CMC_EVAL_IMAGE`，各组继续使用同一镜像内的依赖；不要覆盖该变量或改回可变镜像标签。一次性容器隔离不涵盖 Docker 守护进程重启、项目级停止或模型服务重启。源码、模型权重和共享数据库仍由挂载及服务提供，分别受冻结代码和模型配置等检查约束，镜像固定不能代替这些检查。

## 2. 固定参考集并生成摘要

快照中包含独立源文参考集。应在查看任何预测结果之前固定参考版本并记录以下 SHA-256；参考仅用于评分，不传给摘要生成、标签规划或抽取模型。

```bash
docker compose -f docker-compose.yml -f backend/app/evaluation/compose.yaml \
  run --rm --no-deps --entrypoint sha256sum -T backend \
  "$CMC_EVAL_DIR/runtime/app/evaluation/fixtures/cmc_upload_23c872fb_reference.json"

cmc_eval summarize --prepared "$CMC_EVAL_DIR" --timeout 600
```

`summarize` 使用项目现有分层摘要器调用真实本地模型，保存 `summaries.json`。其中 `generation_seconds` 是实际观察到的摘要阶段耗时，`source_counts` 区分 `llm`、`extractive_fallback` 等来源。应检查摘要来源；模型失败产生的摘录回退不能表述为成功的 LLM 摘要实验。同一准备目录只生成一次摘要；需要独立重复摘要实验时创建新的准备目录。

当前工具另存 `page_metadata`、`chapter_source_counts` 与 `page_source_counts`，`source_counts_scope=all_chapters_and_pages` 表示合计口径。没有此字段的旧冻结版本（包括本次 `cmc-23c872fb-20260907-02`）只统计章节，不能把它当作所有页面和章节的摘要成功率。`summaries.partial.json` 是中断诊断材料，并非完整摘要或自动恢复点。

## 3. 执行同预算对照

| 模式 | 实验处理 |
|---|---|
| `baseline` | 当前生产通用抽取器；已经具有原文层级上下文与既有调度能力 |
| `structure` | 基于祖先标题的节点规划、原文窗口/菜单排序及召回提示 |
| `structure_summary` | 在结构模式上加入冻结的分层摘要元数据 |
| `structure_summary_gliner` | 在结构与摘要模式上加入 GLiNER 原文片段召回提示 |

规划器使用确定性的本体标签/描述词法匹配，尚未实现 LLM 语义规划器。所有模式保留原文范围、适用类和剩余任务；提示只进入召回请求，实体类型和绑定验证继续使用原生产逻辑。GLiNER 模式要求本地权重实际可用，缺失时明确失败。

下面以每段最多 24 个任务、单次模型超时 600 秒、超时重试 0 次为例。各命令顺序执行，避免评测组之间争用同一模型；四组使用相同任务预算、模型超时及重试设置。

```bash
cmc_eval run --prepared "$CMC_EVAL_DIR" \
  --output "$CMC_EVAL_DIR/baseline-01" --mode baseline \
  --pause-after 24 --timeout 600 --timeout-retries 0

cmc_eval run --prepared "$CMC_EVAL_DIR" \
  --output "$CMC_EVAL_DIR/structure-01" --mode structure \
  --pause-after 24 --timeout 600 --timeout-retries 0

cmc_eval run --prepared "$CMC_EVAL_DIR" \
  --output "$CMC_EVAL_DIR/summary-01" --mode structure_summary \
  --pause-after 24 --timeout 600 --timeout-retries 0

cmc_eval run --prepared "$CMC_EVAL_DIR" \
  --output "$CMC_EVAL_DIR/gliner-01" --mode structure_summary_gliner \
  --pause-after 24 --timeout 600 --timeout-retries 0
```

`--pause-after` 限制本次片段尝试的任务数，不限制为相同模型调用数或相同原文位置。排序会改变先访问的节点，实体发现也会改变后续验证数量。因此这些结果反映固定任务预算下的早期表现，必须同时报告模型调用数、覆盖范围、未完成状态和参考集命中情况。

可增加相同的 `--deadline-seconds` 比较固定时间预算；该截止时间在任务边界检查，正在进行的模型调用或验证可能继续到任务结束。它不是严格的进程终止时限。

恢复同一组实验时使用相同输出目录、模式和预算参数，并增加 `--resume`。例如，继续基线的后续 24 个任务：

```bash
cmc_eval run --prepared "$CMC_EVAL_DIR" \
  --output "$CMC_EVAL_DIR/baseline-01" --mode baseline \
  --pause-after 24 --timeout 600 --timeout-retries 0 --resume
```

其余组应恢复到相同累计任务预算后再比较。checkpoint 身份不匹配会拒绝恢复；不要覆盖已有 `checkpoint.json` 或混用实验模式。恢复不会自动重试已记录的失败任务。

仅正常软暂停可以按上述方式续跑。当前工具通过 `run.in_progress.json` 标记未正常结束的运行；硬中断后拒绝恢复，防止新 checkpoint 与旧计时结果混合。请保留诊断目录并启动新组。本次冻结 02 不含该保护，因此正式测试均为独立单段运行，不使用硬中断恢复结果。

需要全量结果时，启动新组并省略 `--pause-after`，或者恢复已有组时省略该参数。例如：

```bash
cmc_eval run --prepared "$CMC_EVAL_DIR" \
  --output "$CMC_EVAL_DIR/baseline-01" --mode baseline \
  --timeout 600 --timeout-retries 0 --resume
```

省略暂停参数仍受项目总任务预算和失败状态约束。只有 `completion=complete` 才能讨论该运行的完整处理耗时；`incomplete` 不能作为整篇提速结果，也不能把提前停止解释为同质量加速。

## 4. 独立评分

```bash
cmc_eval score --prepared "$CMC_EVAL_DIR" \
  --run "$CMC_EVAL_DIR/baseline-01" \
  --reference "$CMC_EVAL_DIR/runtime/app/evaluation/fixtures/cmc_upload_23c872fb_reference.json"

cmc_eval score --prepared "$CMC_EVAL_DIR" \
  --run "$CMC_EVAL_DIR/summary-01" \
  --reference "$CMC_EVAL_DIR/runtime/app/evaluation/fixtures/cmc_upload_23c872fb_reference.json"
```

替换 `--run` 可评分其余组。省略 `--reference` 时仅提供候选和证据回放统计，不生成语义准确率。

当前参考声明为 `assistant_silver`，由独立助手依据源文和本体建立，尚未经领域专家复核。仅在明确标注的类别、谓词和原文范围内报告匹配精确率、召回率、F1 及禁止断言检查；范围外的预测保留为未评分。不能将这些数值称为人工金标准确率或整篇图谱准确率。具体范围和歧义处理见 [reference_notes.md](fixtures/reference_notes.md)。

优先读取 `metrics.json` 中 `raw.extracted_only` 与 `validated.extracted_only`。这些主指标排除外部提供的文档根实体，避免把已知 `CMCReport` 类型当成模型识别成功；文档根仍可用于属性和关系端点对齐。生产验证通过率与原文引用正确率均不能替代独立语义评分。

## 5. 时间与结果的解释

每组的 `result.json` 记录以下时间口径：

- `elapsed_seconds_this_segment`：当前片段从解析开始至抽取返回的实际墙钟时间，包含模型调用、规划及过程记录等开销，不包含此前独立生成摘要的时间。
- `elapsed_seconds_total`：该组所有恢复片段的累计时间，包含恢复时重新解析和规划的实际开销。
- `summary_generation_seconds_separate`：先前独立观察到的摘要生成时间。
- `cold_metadata_accounted_seconds`：累计运行时间加一次摘要生成时间。这是将冷摘要成本计入的核算值，不是同一冷启动端到端执行的直接观测值。
- `first_seconds_this_segment`：本片段首次可见的已验证实体/关系时间。恢复后可能立即发布已有结果，不能据此覆盖首次完整实验的首结果时间。

摘要组读取预先生成的元数据，因此其抽取运行属于摘要缓存命中的场景。此处的“冷/热”仅描述摘要元数据；模型服务已经加载的权重、服务端提示缓存和其他业务请求不会被工具清空。`slots_before`、`slots_after` 及调用日志可辅助说明共享负载，但不能保证各组获得相同资源。

正式比较宜使用新输出目录进行独立重复，并交替组别顺序；不要把 checkpoint 恢复当作独立重复。统计波动及共享服务负载后，分别回答“同预算下是否更早得到正确结果”与“完整执行是否更快”。GLiNER、摘要和新增验证的开销都应计入相关口径。

主要产物如下：

| 文件 | 内容 |
|---|---|
| `manifest.json`、`source.docx`、`ir.json`、`schema.json` | 冻结输入与环境身份 |
| `runtime/app/`、`ontology/` | 本次使用的代码与本体副本 |
| `summaries.json` | 摘要内容、来源及独立生成耗时 |
| 各组 `plan.json` | 变体的节点提示、依赖指纹与规划信息 |
| 各组 `run.json`、`result.json` | 候选、任务状态、诊断和计时 |
| 各组 `checkpoint.json`、`latest_candidates.json` | 恢复点及最近可见候选 |
| 各组 `calls.jsonl`、`trace.jsonl` | 调用耗时及请求/响应审计；含原文和模型输出 |
| 各组 `metrics.json` | 独立参考评分和证据回放结果 |

运行期间通过标准输出的 JSON 事件查看进度。结果目录包含源文和完整提示，应按源文相同权限管理；工具不会将这些材料提交给外部评分服务。

Compose 一次性容器选项依据 [Docker Compose run 官方文档](https://github.com/docker/compose/blob/main/docs/reference/compose_run.md) 核对：继承服务配置及卷，`--no-deps` 不启动依赖，`--entrypoint` 覆盖镜像入口，`--rm` 在退出时清理容器。评测保持显式 `-w` 与 `PYTHONPATH` 以运行冻结代码。

## 6. 根关系驱动调度实验

`root_guided` / `root_guided_summary` 使用独立的 [root_guided_variant.py](root_guided_variant.py)，不修改生产调度。已知文档根入队后，按当前主体的直接谓词选择最多三个章节，在关系的直接 range 及合法子类小菜单中发现目标，复用生产类型验证和关系绑定验证；只有肯定且可用的关系才继续展开目标实例。子实例工作与剩余根关系交错推进。摘要仅参与选章，不能作为原文证据。

这是带显式覆盖缺口的试验版本：选中范围以外的章节保留为 deferred，尚未实现自动逐级扩大范围的补查；验证阶段仍保留生产竞争类型展开，不能声称每次请求都只有一个类型。后续发现竞争主体时保守撤销受影响的归属，不自动补造事实。应检查 `plan.json` / checkpoint 中的 coverage 及最终 diagnostics。

为避免重新生成摘要改变输入，可从上一轮冻结输入创建新的运行代码快照。`fork_experiment` 只读取旧文档、IR、schema、本体、摘要及评分参考，核对哈希、模型身份和重现的章节 content_hash；原文件不改，目标目录必须不存在。摘要字节保持一致，历史摘要生成成本单独保留，新一轮不冒称重新测过摘要。

```bash
export CMC_EVAL_IMAGE=sha256:cda2fd0551b14071dccba56c19888315e96b7ce7e04dd9610434eb57ba7f6c15
CMC_EVAL_DIR=/app/data/evaluations/cmc-root-guided-20260908-example

docker compose -f docker-compose.yml -f backend/app/evaluation/compose.yaml \
  run --rm --no-deps --entrypoint python -T backend \
  -m app.evaluation.fork_experiment \
  --source /app/data/evaluations/cmc-23c872fb-20260907-02 \
  --output "$CMC_EVAL_DIR"

# 使用前文 cmc_eval 辅助函数，始终运行新目录的冻结 runtime。
cmc_eval run --prepared "$CMC_EVAL_DIR" \
  --output "$CMC_EVAL_DIR/root-guided-01" --mode root_guided_summary \
  --pause-after 24 --deadline-seconds 600 --timeout 600 --timeout-retries 0 \
  --max-sections-per-predicate 3
```

基线和 `structure_summary` 对照也必须在同一新快照、新输出目录执行，使用相同任务与时间预算；旧四组仅供历史参考。协议见 [root_guided_protocol.json](root_guided_protocol.json)。评分步骤同前，`--reference "$CMC_EVAL_DIR/reference.json"` 仅用于评分进程。

本轮另记录 `execution_limits`、模型 trace 对应的调用编号和 `snapshots.jsonl` 完整候选快照。运行结束后可用 [root_guided_analysis.py](root_guided_analysis.py) 只读复算首条参考匹配根路径及菜单/token统计：

```bash
python -m app.evaluation.root_guided_analysis \
  --prepared "$CMC_EVAL_DIR" \
  --runs "$CMC_EVAL_DIR/baseline-01" "$CMC_EVAL_DIR/root-guided-01" \
  --reference "$CMC_EVAL_DIR/reference.json"
```

该命令输出 JSON 到 stdout，不修改输入。首路径要求参考匹配的实体和关系端点真实连接；它是补充指标，不改变主评分的参考分母。没有快照时不会用模型自评或调用结束时间推算首个正确结果。
