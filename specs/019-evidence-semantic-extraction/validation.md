# 实现验证记录

日期：2026-09-05。此文件记录已执行检查，不代替真实模型质量或发布验收。

## 基础与 US1

- 证据 schema、身份、无正则结构 scanner：33 passed；对应 T004—T008。
- IR、嵌套表/合并单元格、模板骨架、三个入口及既有模板/标注/格式回归：181 passed（合并基础测试）。
- 追加两列字段独立 label/value anchor、叙述骨架和真实 SQLite 0025 升/降级保留历史行测试；定向 26 passed。
- 新模块 Ruff 检查通过；前端 `npx tsc --noEmit` 通过，改动的四个 TS/TSX 文件定向 ESLint 通过。
- 全量 `npm run lint` 未通过：7 个既有错误位于未修改的模板管理页、ast-tree-view、extraction-drawer、mock/edit-dialog、auth-guard；另有 6 个既有警告。后续 T057 跟踪，不能宣称全量前端门禁通过。
- pytest 有 4 条既有 Starlette/Pydantic 警告。pytest/Ruff 使用无缓存模式，避免已有缓存目录权限问题。

## 本轮续作：US2 与 US3

- 通用 runner 已接入 Word 主入口：实体先行、标题主体/正文值、独立绑定判定、真实父实例/边版本的多跳调度、循环停止、完整范围减排除区间、共享值联合判定和 checkpoint 恢复。否定边不驱动正向下一跳。模型输出仍是待审核候选。
- 首批语义/本地客户端及 GLiNER/typer/API 回归 100 passed；增加多跳、共享值拒答与恢复测试后，相关语义/提交组合 19 passed。
- 修复 CandidateStore 注解被同名 list 方法遮蔽的问题；实现逐候选持久化、CAS 审核、编辑新版本、同作业归并和传递依赖失效。
- 使用真实临时 Owlready2 World 验证高精度小数、实例/谓词类型、独立否定及条件断言、无正向边、失败不发布、同清单重试、幂等键内容冲突及快照继承。不同键并发测试以屏障强制读取同一快照头，最终保留两批事实及共享依赖，审计链校验通过。
- `0026_evidence_facts` 在真实临时 SQLite 上完成 upgrade → ORM 写入/回读 → downgrade，外键开启，历史报告行不丢失。未对用户生产数据库执行迁移。
- 新 API 测试验证 senior_analyst 门禁、忽略客户端 passed/confirmed/succeeded、服务端人工身份盖章、规范值重算、外部字段/版本/身份独立校验、Word 共享 IR 持久化、真实 writer 失败/重试和来源回读。旧确认即提交入口明确 `409 legacy_unverified`。
- 外部记录适配器提供现有 mock 设备档案的实际内容哈希版本及字段映射；不把设备编号原文当成材质来源，也不把 mock 声称为真实主数据服务。
- 定向新增链路组合曾运行 83 passed；追加 Word 持久化及正式消费迁移门禁后，审核/抽取/迁移组合 26 passed，完整定向组合 **84 passed**（IR、证据契约/身份、骨架、scope/context、字面量、语义绑定、runner、真实提交/外部来源/迁移、三个 API 与结构化客户端）。
- 新后端模块及修改的 extraction/main/schema 定向 Ruff 通过；前端 tsc、修改文件定向 ESLint、`npm run build` 通过（Next.js 提示仓库存在多个 lockfile，不影响本次构建）。修复本次接入涉及的 extraction-drawer 三处 effect 状态重置问题；其余原有 lint 问题未扩大修改范围。

## 扩大回归的真实结果（未通过发布门禁）

命令：`pytest -p no:cacheprovider -q --tb=no tests/test_extraction tests/test_api/test_word_evidence.py tests/test_api/test_document_analysis.py tests/test_api/test_evidence_api.py`。

结果：**521 passed / 14 failed**。失败构成：

- 9 项旧审核/溯源用例仍假定 `_commit_candidate` 的“确认即提交”和字典注入来源；现在被 `409 legacy_unverified` 阻止。须在 T034 中迁移为保留历史可读性、逐值来源及新提交链路的等价测试，不能恢复假成功逻辑来变绿。
- 4 项旧 relation_extractor 集成用例用不存在的文件名和 Profile/finder 桩验证旧调度；通用适配器要求真实共享 IR。这些用例需要随 T025/US5 迁移，旧 finder 定义目前尚未全部退役。
- 1 项未修改的 `test_production_area_source.py::test_resolve_642_has_description` 期望“连云港”，当前未修改源记录实际为“XXXX医药股份有限公司”。本轮未改动该源或其测试。
- 全量前端 lint 仍有 **4 个既有错误、6 个警告**：错误位于模板列表页、ast-tree-view、mock/edit-dialog、auth-guard。不能将定向检查/构建通过写成全量 lint 通过。

## 历史阶段的实施缺口（以下状态已由本次模板专项记录更新）

清单当前 33/59 已标记完成；未完成项继续保留：

- T023/T025：文档元数据根主体、部分本体基数/跨来源冲突处理、完整 scope 扩展调度和旧关系集成测试迁移尚需补齐。实体召回及多跳工程测试不是固定真实模型效果证据。
- T034/T035：旧公共事实/历史接口全面迁移、其他外部源及派生来源执行器尚未完成。人工/外部 API 不允许客户端自行声明 derived 可信计算结果；编辑文档候选必须重新抽取验证或显式改为人工来源，不把人的编辑动作伪装成模型验证。
- US4：FactSelector、实例 coverage、受控补抽、报告冻结尚未实现。为防止新候选预览经旧报告代码变成正式结论，新证据作业的旧报告/覆盖入口暂返回 `409 snapshot_consumer_pending`；历史报告下载保持可读。这是过渡门禁，不是已完成报告功能。
- US5/US6：旧生产正则/finder、报告期富化、本体执行注解退役、最终产物审计及评测工具尚未完成；不能宣称已完成无规则生产链。
- 独立人工金标、固定真实模型、生产 p95/内存和源自正式配置的质量/SLO 门禁尚无实测，T055/T056 不勾选。

全部修改未 commit/push；Spec Kit before/after implement 的可选 git commit 钩子未执行。

## 风险模板专项续作：CMCReport → 风险评估报告

目标：`dea037a2-f4b5-478a-8e0e-d5471fc45cbc`，线上名称“风险评估文档”、版本 v18、状态 published。该模板是业务端到端验收用例，不是独立人工标注集。

### 已完成的工程验证

- 定向回归 **179 passed**：共享 IR、证据契约/身份、通用召回/绑定、规范化、继承菜单、精确 tokenizer、CAS 审核/提交与补抽预约、真实 World/迁移、FactSelector、实例覆盖、真实快照报告及 API、DOCX/格式 scanner、规则解释器。实际命令覆盖 29 个测试文件，均使用 `-p no:cacheprovider`。
- 新接口集成使用真实临时 World 和独立报告 worker Session：请求返回 202 前冻结模板/事实/规则/覆盖清单；随后修改模板，不改变已经排队的报告；生成 DOCX 与历史下载通过。
- 覆盖按完整路径、实际主体和逐值属性展开。多跳设备检查先沿 CMCReport→usesEquipment 限定主体，再逐台检查 constructedOf/locatedIn：A 有材质不遮盖 B，无关设备不进入该路径。开放对象集合需要基于当前快照/发现修订的显式审核确认。
- 补抽先做持久化 CAS 预约，重复输入不重复推理，最多两轮；候选待审停止；源文档重跑不重置历史预算。崩溃时保留 running 预约供显式对账，不静默重试。逆向补抽、完整宕机对账入口尚需完成，T040 未勾选。
- 数值身份以规范值/单位比较，保留原始拼写和各自来源，`1.0 kg` 与规范后相等的 `1000 g` 不形成伪基数冲突。快照规则使用严格类身份、Decimal 比较和真实布尔值；规划的控制措施不自动变成已落实、低风险。
- 正式报告只读冻结快照；未发布模板拒绝正式生成。模板样例只复用版式，正文及所有页眉/页脚变体中的示例签署不进入新报告，原样例文件字节保持不变。
- Word 元数据可创建待审 CMCReport 根节点，不能自动审核/提交。补齐类继承菜单、OWL 联合值域真实 IRI 和 RDF 字面量类型读取；精确 tokenizer 中途失败保留 IR/根节点并明确 incomplete，无字符估算回退。
- 模板源文档页与文档抽屉已接入逐值审核/提交、实例缺口、补抽停止原因与对象集合确认。前端 tsc、三个修改文件定向 ESLint、`npm run build` 均通过；build 的多个 lockfile 提示仍存在。已还原 build 自动改写的 next-env.d.ts，不纳入功能修改。
- 关键新增 Python 模块与新增 API/报告测试定向 Ruff 通过。全量旧链路/配置/产物审计未完成，不把定向检查称为全量通过。

### v18 → v19 配置升级方案

- 原始线上 schema 原样保存在 `backend/tests/fixtures/risk_template_dea037a2.json`，规范哈希 `78721d33afb40f12e77d2e048b5d613945c3442fafe759256561fbb4bce6b8d1`。
- `risk-template-upgrade.json` 是显式、带源版本/哈希前置条件的声明迁移；通用 `prepare_template_upgrade` 校验每段路径及末端属性，拒绝源模板漂移；不改变原 v18，也不把声明写成抽取 finder。
- v19 主要章节增加 12 项覆盖，14 个业务插槽改为精确快照来源，7 个风险矩阵列绑定冻结规则；源文件名使用独立元数据来源。其余插槽保留人工填写/签署，不引用模板提示词示例作事实。
- 保留输出报告的两项会签关系覆盖。它们属于 RiskAssessmentReport，而不是 CMCReport；没有独立报告主体/会签证据时仍是缺口，不自动生成 QA 或审批人。
- 本体 `facility/hasCleanlinessClass` 未声明 range，当前无法独立完成洁净级别关系校验。迁移显式保留诊断，生产区域已有值不等于洁净级别已满足；未擅自新增类/公理。
- 仅生成隔离 v19 草稿，**未修改/发布线上模板，未执行生产数据库迁移，未重启后台**。模型服务后来经用户明确授权单独重启，见下节。线上数据库此前实查停在 0024；部署新后端前必须先完成 0025/0026 的备份与升级。

### 真实源文档运行结果：未通过金标

- 默认源文档在 `/tmp/ontology-019-acceptance.2ivvM5` 的副本上处理，原文件不变。共享 IR 为 **486 个证据单元**；分析身份 `6b184ad2616741c7d333ba285b9b4e48fd2657e24f86705b2d15bac882ad2efd`；真实本体菜单 **329 类**。
- 模型：本地 `Qwen3.6-35B-A3B`；实际 GGUF SHA256 `0b21525e972670ed59e1812e170b27c26355381f0656ecc4e25617ece7dac58b`。通过同服务器 `/tokenize` 使用真实词表；没有运行期下载或发送正文到外部服务。
- 本地服务 `/health`、`/props` 可响应，`/slots` 和推理持续超时。最新完整尝试首个 entity 请求在 **60.6 秒**后返回 `model_unavailable`。只有 **1 个元数据根节点**，没有成功的模型属性/关系候选；根节点验证 passed 但仍待审核。
- 隔离 SQL 候选持久化成功；`snapshot_id=null`、14 项必需缺口、模板 draft，`gold_template_passed=false`。脚本 `--reuse-run` 只复用同源/同本体/同模型的已记录结果，不新增推理，保留原 60.6 秒测量值。失败验收以 exit 2 结束，不能被 CI 误判成功。
- `scripts/run_template_acceptance.py` 目前提供真实模型诊断、隔离候选持久化、显式版本化审核文件入口、真实提交和覆盖输出；脚本自身的模板审批/规则导入/最终报告验收阶段仍需补齐。正式 API 的快照报告生成已在上述真实 World 集成测试通过，两者不可混同。
- 目录保留 `result.json`、`coverage.json`、`candidates-for-review.json`、`template-v19-draft.json`、IR 和隔离 SQL/World；文档正文、权重及审核候选未写入仓库。
- 上述为重启前结果，后续用户明确回复“允许”，模型重启及复跑见下节。恢复推理后仍需真实源候选审核/提交、模板声明审核/发布和最终报告复核，不能把服务恢复等同于整体验收完成。

### 授权模型重启与请求开销修订

- 2026-09-05 经用户授权，对 PM2 中实际应用 ID `27`（`qwen3.6-35b`）执行一次 `pm2 restart 27`。PID `3420` → `2615241`，原启动脚本、GGUF、上下文及并发配置均保留，没有重启 backend、数据库或其他模型。
- 模型加载完成后 `/health` 正常；最小聊天检查 HTTP 200，回复 `OK`，耗时 **0.507 秒**。服务历史日志存在正在推进的其他请求，因此此前超时不能单独证明模型死锁，排队/资源竞争也需要考虑。
- 旧调度复跑先完成 **11 个 entity 任务，均 0 候选**，单任务约 19—24 秒；随后暂停本次验收进程，保留 checkpoint。当前输入类的可达类型实际为 **136 类**，固定每 16 类分组会将同一证据批次重复推理 **9 次**。440 个非空单元仅初始召回就约需 495 个请求，超出 256 任务预算（还未计属性/关系任务）。
- 新增模型侧精简投影：完整 IR、task、anchor、scope 和缓存身份仍保留在服务端；模型副本去掉重复摘要哈希/调度元数据，保留主体/竞争主体版本、结构坐标、事实/绑定区域角色、极性、条件及逐值来源。类菜单按实际 tokenizer 预算打包，不再固定 16 类，不截断类型或源窗口。
- 第一版精简测量：同一旧任务 **5,658 → 2,812 tokens**；136 类可合并一组。第一版隔离实跑保存在 `/tmp/ontology-019-compact.YXXq7a`：前六个任务约 45—47 秒且为空，第七个推理达到 60 秒后终止；全程 **339.28 秒**，仍只有元数据根节点、无发布快照、14 项缺口、exit 2。该轮有一次并行的小型诊断请求，不能用其耗时充当生产 SLO 或无竞争基准。
- Context7 核对 llama.cpp 官方 `grammars/README.md`：JSON Schema 只用于解码约束，不自动加入模型提示词。原 runner 虽计算 schema tokens，却未将 schema 发送为提示内容。已先加失败测试，再显式发送同一输出契约；同时说明实体/属性/关系阶段、从零开始的 Unicode 坐标和窗口基址。小型本地对照能返回实体但曾给错偏移，证明严格原文回放仍不可省略；不把该对照当成质量金标。
- 第二版投影 `model-context-v2` / 当时调度版本 `generic-semantic-v4`：类 IRI 只在菜单键出现，不重复发送全量目标数组。136 类首批精确输入计数 **9,028 tokens**（含输出契约和 128 token framing 预留）。旧 checkpoint/`--reuse-run` 在策略、tokenizer 或预算变化时不能复用。
- 第二版真实有界运行保存在 `/tmp/ontology-019-contract.huo1vQ`：8 个任务、**402.7 秒**，第 6/7 个任务分别得到 6/5 个通过校验的模型实体，加根节点共 **12 个 passed / pending 候选**，隔离 SQL 持久化成功。其他任务失败构成为 2 次原文不匹配、1 次未知 evidence ID、2 次菜单外类型和 1 次模型超时；没有“纠正”未知 ID/类型或吞掉失败。尚无属性/关系候选、无发布快照、14 项缺口，exit 2，**金标仍未通过**。
- 继续增加 `generic-semantic-v5` 的显式逐字引用协议：不提供坐标时，仅接受指定 evidence/允许范围内唯一完全匹配的原文，由服务端产生可回放 Unicode Anchor；模型提供错误坐标时仍拒绝，重复引用/改写/越界均拒绝，无模糊查找、空白整理或最近实体绑定。独立否定绑定及待审核/未提交门禁回归通过。该修订不解决菜单外类型/证据 ID 抄写错误或任务总量问题。
- 验收脚本新增 `--pause-after`，可在保持总任务预算身份不变的情况下做有界分段复跑；暂停仍标记 incomplete，不能用部分源覆盖证明“不存在”。
- v5 引用模式四任务有界实跑保存在 `/tmp/ontology-019-quotes.BvGhng`，总预算 256、`--pause-after 4`，**174.39 秒**。结果为 1 次原文不匹配、2 次目录缺乏实体内容的模型拒答、1 次菜单外类型；只有元数据根节点，不能把它与 v4 的 11 个模型候选合并成一次完整成功运行，也不能据此声称真实定位错误率下降。未知 ID/错误类型不自动映射，拒答不自动转换成 confirmed_absent；仍是 snapshot null、14 项缺口、exit 2。
- 完整定向工程回归 **126 passed**（23 个测试文件），覆盖引用唯一性/Unicode/重复/改写/越界/错误坐标不修复、否定绑定、提示契约可见性、菜单不遗漏、缓存/暂停恢复、真实 World、审核/提交、实例覆盖及快照报告；修改 Python 文件定向 Ruff、`git diff --check` 通过。补抽 API/任务身份也纳入完整 runner 输入身份，策略更新不误复用旧失败结果且不重置两轮总预算，新增同接口回归验证。
- 本轮未做生产审核/提交、模板发布、数据库迁移或后端重启；没有自动批准候选，也没有伪造会签。剩余工程阻塞仍包括：真实模型非法 ID/类型/原文与拒答处理、全文到主体属性/关系的任务成本、最终脚本报告验收阶段，以及前述旧消费者/规则退役和发布门禁。模板审批、洁净级别值域设计和真实审核输入仍需各自明确处理。

### 本轮阻塞修复：封闭引用与可推进的断点

- 沿用 Spec Kit T023 的先失败测试后实现；requirements 检查清单 12/12，通过后继续，不新增依赖、不提交现有工作区。Context7 核对 llama.cpp 的枚举解码与提示不可见约定，以及 FastAPI 可选请求体兼容方式。
- `closed-model-references-v1` 为每次请求登记 e/t/c 短编号，enum 限定 evidence/class/subject/object 输出；服务端仅按当前表还原完整身份，拒绝未知编号、完整 ID 回填及结构非法输出。原文、数值、极性不做全局替换；最终候选和绑定保存完整 Anchor/IRI/版本。实际发出的短编号提示及输出 schema 使用服务器 tokenizer 精确计数，仍包含 128 token framing 预留。
- 首次封闭引用实跑保存在 `/tmp/ontology-019-closed.5rtp2J`：8 个请求、238.28 秒；请求输入约 6,961—7,363 tokens，无未知 ID/菜单外类型/超时。此时仍采用整任务丢弃策略，最后仅 8 个实体（含根）。逐条本地回放发现，第 4/6/8 个响应分别有 2/3/10 个来源可验证实体随其他错误引用一起丢弃。
- 修复实体同批丢弃：`PartialTaskFailure` 仅携带已完成校验的同批实体，失败诊断继续保留，任务仍 incomplete。补抽遇到这类结果也保存有效候选并返回 pending_review，不直接填 coverage。重复引用、改写原文和越界引用仍拒绝，未增加自动坐标纠正或模糊匹配。
- 修复恢复饥饿与预算重置：checkpoint 保存成功/失败状态、有效实体、累计 attempt_count/model_calls 和联合验证结果。默认恢复先推进未尝试任务；显式 retry_failed 才重试失败项，总预算不会重置。对失败、显式重试、总预算、暂停、部分实体和否定绑定均有回归。当前执行版本 `generic-semantic-v6`，输入身份包含引用协议；旧策略记录不冒充当前完整成功运行。
- v6 实跑保存在 `/tmp/ontology-019-resume.LAqi8g`：保持 `--max-tasks 256`，首批 `--pause-after 8` 用时 **218.51 秒**，得到 **20 个 passed/pending 实体（含根）**。随后同目录 `--pause-after 2` 仅调用两次模型，用时 **70.01 秒**，命中非空证据序号 65—80，未重跑之前窗口；累计 **10 次尝试 / 10 次模型调用 / 34 个 passed/pending 实体（含根，即 33 个模型候选）**，隔离 SQL 持久化通过。
- 这 10 个请求中，未知模型引用、菜单外类型和模型超时为 0；仍有 3 个拒答任务及 4 个部分失败任务（重复引用或原文不匹配）。因此保留 incomplete，不据此宣布“无实体”或 confirmed_absent。仅推进到 440 个非空证据单元中的前 80 个，尚无本轮完整属性/关系链或发布快照；14 项覆盖缺口、模板 draft、gold_template_passed=false，脚本以 exit 2 结束。以上是有界工程诊断，不是独立人工金标或生产 SLO。
- API 支持可选 `{retry_failed, reason, pause_after}`，空理由重试或客户端注入 checkpoint 拒绝；重试身份及理由写入审计。前端证据面板新增每批最多 8 项的继续/重试按钮，待审和未提交门禁不变。Web 当前批次内崩溃恢复/并发运行预约尚未实现，不把脚本逐任务落盘当成 Web 全生命周期保障。
- 完整定向后端回归 **133 passed**（24 个文件）；修改 Python 文件定向 Ruff、`git diff --check` 通过。前端 tsc、两个修改文件定向 ESLint、`npm run build` 通过；多个 lockfile 提示仍存在。已还原 build 对 next-env.d.ts 的自动改写。
- 未重启模型或后台、未修改/发布线上模板、未执行生产数据库迁移、未自动审核/提交事实。原有未完成规则退役、旧消费者迁移及最终报告验收仍保留；T023 不提前勾选。Spec Kit 可选 Git 提交钩子继续跳过，无 commit/push。

### 续修：主体轮转、对象分批与关系必填契约

- Spec Kit prerequisites 与 requirements **12/12** 通过，沿用 T023 先失败测试后实现。新增根主体/多主体/谓词轮转、对象分批、调度身份三项测试先失败，修复后通过；无新增第三方依赖。Context7 核对 `/ggml-org/llama.cpp` 官方 grammar 文档的 `required`、枚举和数组长度约束；未向外部文档服务发送源文档、候选或 trace。
- 修复根实体全篇任务独占预算：`fair-subject-predicate-v1` 先完成实体阶段，再轮转主体任务流；主体内交替安排属性/关系谓词，并轮转各谓词的源窗口。真实正向边的下游路径加入同一队列，保留完整父边引用、跳数限制和环检查。测试中根实体、设备 A、设备 B 在首轮均获任务，恢复不重跑此前任务，关系不再排在全部属性和全文窗口之后。
- `TaskBudget.max_objects_per_task` 默认 8（1—128），本地适配器读取 `EVIDENCE_MAX_OBJECTS_PER_TASK`。对象先有界分批，完整召回请求仍超出真实 token 预算时进一步拆分对象或源窗口集合。测试证明对象/窗口不遗漏、竞争主体不丢弃、拆分 ID 稳定；不可再分的超限任务仍显式失败。独立绑定仍做自己的精确 token 检查。这不代表所有属性/关系都能在原总任务预算内完成，也未实现模板声明优先级。
- 第一组真实诊断 `/tmp/ontology-019-assertion-probe.Ene3NH`：对之前 33 个通过原文校验的模型实体重新回放来源，保留 CMCReport 根，**不新增全文实体推理**。旧的未处理窗口及跳过的断言任务均明确 incomplete，诊断身份与真实完整抽取隔离，不直接复用旧 checkpoint 为新策略背书。调度触达 **29 个主体 / 30 个断言任务**，只对最先两个属性和两个关系任务执行真实本地推理；4 请求、**41.11 秒**，无新断言。首个关系输出没有对象，服务端拒绝为 `unknown_object`；其他 3 项模型拒答。
- 据此修订 `closed-model-references-v2`：关系提案要求非空登记对象和非空证据数组，属性提案要求值引用；显式极性为必填，服务端检查缺失字段，不能让默认值补成肯定。绑定验证的对象 enum 收窄到被验证提案的准确对象。空断言数组和拒答仍可表达，无语法强制造事实；未知引用、原文回放、主体绑定及待审门禁不放宽。新协议、调度和对象预算进入 run identity，旧记录不能按新策略 `--reuse-run`。
- v2 对同一组任务的隔离复跑 `/tmp/ontology-019-assertion-v2.8czHac`：**4 个探测任务 / 5 次模型调用 / 55.47 秒**，输入 **1,487—3,041 tokens**。`CMCReport → describes` 得到 **1 条 affirmed / validation=passed / review=pending / commit=not_requested 的关系候选**，包括单独的 verify_binding 调用及原文回放。其余两个属性和一个关系仍拒答；没有未知对象/引用或模型超时。探测保存 34 个实体（含根）和 1 条关系；实体全部来自之前的部分召回，**不能和此前 10 次实体请求混算为一次全文成功运行**。未增加属性候选，snapshot 为 null，gold_template_passed=false。
- 上述仅是已有真实实体种子的有界断言诊断，不是全文验收或人工金标；脚本和保密 trace 留在各自 `/tmp` 隔离目录，未写入仓库。此前全文召回进度仍为 **80/440 非空证据单元**。补齐全文、模板优先级、属性/关系阶段剩余任务、Web 批次内崩溃/并发预约、最终报告验收与规则退役等仍待完成，T023 及其余未完成项保持未勾选。
- 本轮 **149 passed**（24 个定向测试文件），覆盖通用抽取/传输协议/暂停恢复、IR/来源、审核/真实 World 提交、补抽、实例覆盖及快照报告；改动 Python 文件 Ruff 与 `git diff --check` 通过。不将定向测试当成全量发布回归。未改变前端代码、重启服务、迁移生产库、审核/提交事实或发布模板；Spec Kit 可选 `/speckit-git-commit` 未执行。

### 授权 Compose 部署：供手工测试

- 2026-09-05 用户明确授权备份数据库、执行 0025/0026、重新构建并启动 Compose。沿用运行容器记录的 `docker-compose.yml`，未自动合并本机开发 override，未切换前端到 dev 模式。
- 停止 web/frontend/backend 写入后，以 PostgreSQL 16 custom 格式备份正式库，并复制停止状态 backend 的完整 `/app/data`。备份位于 `/opt/dev/chen/ontology-agent/backend/data/backups/pre-0026.1zw7nn`，私有且被 Git/构建上下文忽略，保留原始文档、报告和本体存储；没有删除数据卷。`slpra.dump` SHA256 为 `842c4ed4f7b3b22760604d255251c4836a367ceffe182545df0e52df21820032`。
- 将备份实际恢复到单独临时库，以 `pg_restore --exit-on-error` 验证成功。确认 0024、18 个模板、761 个作业、130 份报告后，仅移除本轮新建的临时验证库；备份保留。保存原 backend/frontend 镜像标签 `pre-0026-1zw7nn`，但镜像标签本身不能恢复绑定挂载的工作区代码。
- 使用只读挂载工作区迁移目录的一次性 backend 容器，明确升级至 `0026_evidence_facts`，日志确认顺序为 0024→0025→0026、PostgreSQL transactional DDL。验证九张新表、sample_analysis 列及原记录数量；模板 ID/version/status/schema 聚合校验值迁移前后相同。
- 重新核验已有 GGUF 的完整 SHA256，与此前固定版本一致。忽略的本机 `.env` 补齐同一本地模型的 revision/path、`llama_server` tokenizer 和已验证的 32768 输入预算；没有切换模型、重启模型服务或发送源文档到外部。新增配置随 Compose 容器重建加载。
- Backend/frontend 镜像重建成功，前端生产构建及 TypeScript 通过；数据库重启，前后端重建启动，nginx 重启，四服务均运行。启动预热期间曾短暂返回 502，预热完成后健康检查 HTTP 200、十个本体模块就绪。
- 13:23 UTC 验证：8081 登录页、目标模板 HTML 路由均 HTTP 200；实际登录后，模板列表（含目标模板）、既有作业的 evidence 和 snapshot 接口均 HTTP 200；OpenAPI 包含新抽取接口，前端静态制品包含 pause_after 调用。目标模板详情 GET 会惰性创建源作业，因此烟测没有调用该接口或触发真实抽取。
- 从新 backend 容器验证当前 `closed-model-references-v2` / `fair-subject-predicate-v1`、精确服务器 tokenizer 及无源文档的最小结构化模型请求，均通过。部署后仍是 761 个作业、0 个新 evidence candidates/commits/snapshots，目标模板仍 published v18，全部模板内容校验值不变。未审核、提交事实或发布模板。
- 备份目录 README.md 记录恢复验证、镜像身份与部署检查。Dockerfile 仍使用现有依赖版本范围，重建镜像的烟测不能替代全量发布回归；此前 149 项本地定向测试也不冒称在新镜像中重新通过。整体特性、v19 发布及金标状态保持未完成。

### 手测阻塞修复：旧模板详情 GET 外键错误

- 2026-09-05 用户确认本机 8082 转发到服务器 8081，未修改端口或代理配置。目标模板有默认源文件但 `default_source_job_id=null`，首次详情 GET 惰性补建作业时，模板 UPDATE 先于作业 INSERT，触发 PostgreSQL `23503 / ast_templates_default_source_job_id_fkey`，返回 500；不是 0025/0026 缺失。
- `_ensure_default_source_job` 在 `db.add(job)` 后、赋值模板外键前增加 `db.flush()`，与默认源上传路径的顺序一致。经 Context7 核对 SQLAlchemy 2.0：flush 写入仍处于同一事务，未拆分提交、移除外键或手工修补业务数据。
- 新增独立 SQLite 外键开启回归，同时覆盖 autoflush 开/关：Word/Excel 首读、文件名回退、重复 GET 不重复作业/后台任务、模板定义保持、提交失败整体回滚和缺失文件不建作业。修复前 **8 failed / 2 passed**，均在建作业路径复现外键错误；修复后 **10 passed**。连同基本信息、默认源上传和模板元数据/行文相关测试共 **43 passed**（本地环境），未执行真实模型。新测试文件 Ruff、`git diff --check` 通过；API 文件全文件 Ruff 的 6 项既有告警在去除本次两行改动后仍相同，未扩展修整范围。
- 在运行镜像与正式 PostgreSQL 上执行真实 `get_template` 及 JSON 序列化验证，将该诊断 Session 的 commit 替换为 flush，最终 rollback。首读成功、重复读取复用作业、后台任务仅登记不执行；回滚后模板全列指纹与作业数量不变，没有遗留诊断作业。
- 仅执行 `docker compose -f docker-compose.yml restart --no-deps backend` 加载绑定挂载的修复，未重建镜像、重启数据库/前端/nginx/模型或再次迁移。启动预热时短暂 502，随后健康检查恢复 200。
- **13:43 UTC** 经实际登录和服务器 8081 的 nginx 入口，目标详情 GET 连续两次 HTTP 200，模板 HTML 路由 HTTP 200。真实首次 GET 按现有逻辑创建源作业 `19e291e5-f344-42eb-b5e0-ff40ae3e760b` 并启动后台解析，作业数 **761→762**；第二次复用同一作业。目标模板仍 published v18，version/status/iri_pattern/schema 校验一致。验证时源作业 running，不把详情恢复宣称为全文抽取、报告或金标完成；未自动审核/提交事实、发布模板或生成报告。

### 手测阻塞修复：正文预览长期 pending

- 2026-09-05 用户报告历史作业 `f91e7b37-f690-4803-a798-7ed566b247cf` 的 evidence、两种 coverage、annotated-document 请求长期 pending，正文提示“正在加载文档正文…”。初始服务器直测 evidence **0.032 秒**、evidence/coverage **1.720 秒**、ast-coverage **1.460 秒**均 HTTP 200；健康检查正常，未复现三个只读接口的持续阻塞，未将浏览器侧 pending 全归因于数据库锁。目标作业有原文和旧标注缓存，但没有新证据状态/快照。
- 根因是 annotated-document 的 Word 缓存过期/缺失后，在 GET 内等待完整通用模型抽取和章节摘要；打开/切换文档可能启动重复推理。正文读取改为优先复用有效标注，否则仅用共享 IR 与 structure-only 渲染正文，返回 `preview_only=true / completion=incomplete`，不启动模型、不制造 evidence_run、不写候选/作业状态/抽取缓存。`refresh=true` 对 Word 只重读正文；显式抽取/后台任务保留原路径，Excel 行为未迁移。依 Context7 的 FastAPI 同步 I/O 约定，将该读路由放在线程池中执行。
- 前端按 Context7 的 TanStack Query v5 取消契约，把 Query 的 signal 传到正文和 AST coverage 的 fetch；证据面板和抽屉在切换/卸载时 abort 读取。四个 API helper 支持 signal，避免仅忽略旧结果却留下连接；正文预览单独标注“正文已加载”，不冒称语义识别完成。此次未直接操作用户浏览器，取消行为由测试与调用点检查验证，不声称已证明所有浏览器 pending 的成因。
- 首次无模型真实源诊断仍耗时 **40.536 秒**。本地 cProfile 定位到段落默认样式反复查找：rich 渲染每段重复解析最多 10 次。改为每段解析一次字体样式并复用，保留 run 直接格式优先级、原文与偏移，不修改源文件或样式定义。Context7 核对 python-docx 字体三态/直接格式契约。
- 先失败回归：缓存缺失/过期/损坏/强制刷新四种路径均因调用模型失败；修复后正文可独立返回，缓存与持久化状态保持。新增样式测试先复现 `10 != 1`，修复后验证继承/直接格式/文本偏移不变；四个前端取消测试先失败后通过。包含 IR、模型显式抽取、快照门禁、旧模板详情、上传和渲染等定向回归共 **95 passed**（本地环境），新增前端测试 **4 passed**；定向 Python Ruff、前端 TypeScript/组件 ESLint、`git diff --check` 通过。不是全量发布回归或真实金标验收。
- 部署前通过现有 pause API 暂停默认源作业 `19e291e5-f344-42eb-b5e0-ff40ae3e760b`，确认持久断点有效（累计 10 次尝试/模型调用，含完成及失败记录）。前端生产镜像重建并更新，仅重启 backend 加载绑定挂载源码；数据库、nginx、模型未重启，未再次迁移。部署后通过 resume API 验证 `has_checkpoint=true`，恢复此前作业，不清除累计预算。
- **14:06 UTC** 经实际登录、服务器 8081 的 nginx 并发复测四个原 URL：evidence **0.362 秒**、annotated-document **0.391 秒**、evidence/coverage **1.881 秒**、ast-coverage **2.195 秒**，全部 HTTP 200；正文包含 148 个顶层块。期间旧 GET 已生成当前版本的标注缓存，因此另测 `annotated-document?refresh=true`，强制走新的无模型正文解析路径，**9.307 秒 / HTTP 200 / preview_only=true**。单次实测并非 p95/SLO；不能以缓存命中耗时冒充首次解析耗时。
- 健康检查、模板 HTML 路由均 HTTP 200，用户继续使用本机 8082→服务器 8081。目标历史作业的新证据接口仍为 **0 candidates / snapshot null**；正文和旧关系预览可读不代表证据审核/提交或报告金标完成。未自动审核、提交事实、发布模板或生成报告。

### 既有扩大回归（未在本轮重新作为发布门禁通过）

`pytest -p no:cacheprovider -q tests/test_extraction tests/test_reporting --tb=short`：**857 passed / 31 failed / 4 skipped**（在本次最后增加的 tokenizer/页眉/逐台设备用例之前执行）。

- 原有 14 项失败仍在：9 项旧确认即提交/注入来源、4 项旧 Profile/finder/不存在文件的关系测试，以及 1 项源记录地址期望不匹配。
- 新纳入范围的 17 项：7 项旧 AST coverage/report-list 契约、1 项 coverage 字段集合精确相等断言、6 项旧标注缓存驱动风险报告、3 项旧模板建议 API 的模型关闭/响应形状契约。需要按新语义迁移并保留等价保障，不恢复旧的未发布事实消费来变绿。
- Spec Kit 当前 **37/59** 项完成。T038/T039/T041/T043 由本轮测试与前端验证闭合；T040/T042/T044 仍包含待完成部分。
- 模板行文预览仍走旧 annotation/enrichment 消费，旧 dismissal/公共接口迁移、所有外部源接入、finder/正则与 TTL 执行注解退役、审计/评测工具、全量回归和独立人工/SLO 门禁仍未完成。不能宣称本特性已整体完成或已经部署。

Spec Kit 可选 git commit 钩子继续跳过；没有 commit/push。

### 手测阻塞修复：报告中心重新识别提前空图谱

- 2026-09-05 用户报告“报告中心 → 上传文档 → 关系图谱 → 重新识别”立即反馈未抽取到关系。根因是 rerun 为后台任务，旧前端在 POST 返回后立即刷新；服务端此时已经删除旧标注缓存，读接口只能返回 `preview_only` 正文和空关系数组。前端现通过同一作业 SSE 等待 `complete/failed/paused`，期间保持“识别中…”遮罩，终态后才刷新正文和图谱；空状态区分模型/tokenizer 不可用、暂停/预算、预览态和真正完成无关系。
- 服务端 rerun/resume 先把作业置为 `annotating`、重置旧进度并发布新的 `queued`，同进程活跃运行重复启动返回 409。进程重启后只有遗留数据库状态而无本地活跃事件时允许恢复，避免永久 409。现场 resume 后第二次 resume 返回 **HTTP 409**；SSE 只回放本轮 `queued → typing → paused`，未回放旧终态。
- 历史作业 `f91e7b37-f690-4803-a798-7ed566b247cf` 的 `source_config` 缺少文档类型，但关联文档影子行明确为 `CMCReport`。rerun 现从文档到作业的既有反向引用回填 `doc_class_iri` 和 `doc_ref`，已有作业元数据优先。正式 PostgreSQL 回读为 `mode=auto / CMCReport / facts#upload-fe1c8b62-…`，不再全本体无类型扫描。
- 真实 IR 有 **447** 个证据单元、**400** 个非空。原每任务 8 区域至少产生 50 个实体窗口，关系断言长期排在后面；当前 Compose 使用实际 32768 输入 token 预算及最多 32 区域，超预算仍精确拆分。调度升级为 `fair-subject-predicate-v2-relationship-first`：实体阶段后，有候选对象的关系先于同主体数据属性，同时保持主体轮转、对象批量和独立绑定。调度/预算均进入 input identity，旧断点不能混用。
- 暂停时若 runner 已停止，不再继续等待 LLM 章节摘要，改用确定性回退摘要后立即保存有效候选/缓存/断点；完整非暂停运行仍保留原摘要路径。现场旧路径暂停曾因摘要等待数分钟，新路径关系任务结束后在下一次轮询即落到 paused。
- 目标文档新运行于 **15:09:53—15:20:20 UTC** 执行，在第 17 个任务后人工请求检查点暂停；累计 **19 次模型调用**、当前运行缓存含 **62 entity + 1 property + 1 relationship**。关系为 `HRXS-P-2419(HRS-1597) —描述(describes)→ 临床样品(DrugProduct)`；对象属性 `注册分类(registrationCategory)=新分子`。关系与属性均为 `affirmed / validation=passed / review=pending`，各有独立 binding；未自动审核、提交或发布事实。
- `annotated-document` 当前返回 1 条关系且对象属性随关系预览下发；`RelationPanel` 顶层默认展开数据属性。evidence API 当前包含该 passed 关系/属性；coverage 与 ast-coverage 均 HTTP 200，但因 `snapshot_id=null` 及 RiskAssessmentReport 会签主体未解决仍为 incomplete，这是正确的正式报告门禁。目标作业刻意停在 `paused`，供 8082→8081 手测部分结果；继续全量任务应使用 resume，点击“重新识别”则按产品语义从头开始新运行。
- 本轮定向后端组合最后为 **28 passed**（runner 调度、rerun/resume、暂停摘要、证据预览），另一次 API/预览/结构化客户端组合 **23 passed**；Ruff、`git diff --check`、前端 4 项取消测试、TypeScript、定向 ESLint和 production build 通过。扩大组合为 **589 passed / 14 failed**；14 项仍是前述 9 个旧确认即提交用例、4 个旧 finder/虚构文件用例和 1 个脱敏地址期望，不恢复旧不安全行为来变绿。
- Compose 仅重建/重启 bind-mounted backend 和开发 frontend；数据库保持 0026 head，模型服务未重启。健康检查经 8081 返回 200。未 commit/push，未发布 v19、未生成正式报告。
