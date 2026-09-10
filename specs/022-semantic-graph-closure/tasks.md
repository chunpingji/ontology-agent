# Tasks: 022 Semantic Graph Closure

## Phase 1 — Specification and baseline

- [x] T001 完成 `spec.md` 的需求、澄清与 `checklists/requirements.md`，核对 021 实现差距。
- [x] T002 完成 `plan.md`、`research.md`、`data-model.md`、`contracts/ranking.md` 和工程/真实质量边界。

## Phase 2 — Subject-aware complete ranking (US1)

- [x] T003 [P] [US1] 在 `backend/tests/test_extraction/test_semantic_ranking.py` 覆盖查询隔离、视图/完整 U、配额去重、全池排名/失败、负分及多意图成本。
- [x] T004 [US1] 实现 `ontology_guided/retrieval_query.py`、`retrieval_views.py`、`semantic_retrieval.py`、`semantic_reranker.py`、`retrieval_fusion.py` 的纯领域模型/排序与预算。
- [x] T005 [US1] 修改 `ontology_guided/retrieval.py`、`contracts.py`，绑定冻结全集及检索/证明不同身份，记录排序观察。
- [x] T006 [P] [US1] 在 `backend/app/services/llm/semantic_ranking.py` 实现可选本地离线模型、不可变身份、有限执行及调度；添加模型适配测试。

## Phase 3 — Actual graph closure (US2)

- [x] T007 [P] [US2] 在 `ontology_guided/scheduler.py` 实现分支/主体/种类/谓词/阶段/章节/探索及有限续执行公平性，增加实际派发/恢复反例。
- [x] T008 [US2] 修改 `ontology_guided/executor.py`，接入惰性排序/持久回放、语义任务去重和逐槽位完整覆盖。
- [x] T009 [US2] 修改 `ontology_guided/context.py`、`model_adapter.py`，装配主体/必要原文、预算与条件/反证，严守主体桥接和原文权限。
- [x] T010 [US2] 修改 `ontology_guided/dependencies.py`、`executor.py`，接通迟到冲突订阅、旧断言及递归资格阻断/未决义务。
- [x] T011 [US2] 新增 `backend/tests/test_extraction/test_semantic_graph_closure.py`，用真实解析/本体/适配器受控响应验证两跳正例、高相似度假边、低分实际续检、迟到反证与必要闭包。

## Phase 4 — Online durability and visibility (US3)

- [x] T012 [P] [US3] 修改 `backend/app/config.py`、`document_analysis/execution.py`，冻结模型/政策与预算并持久提交排序状态，覆盖恢复/漂移/晚到响应。
- [x] T013 [US3] 修改 `document_analysis/public_projection.py`、`schemas/document_analysis.py`，公开只读排序与每阶段计划/实际计数，添加 API/公共投影测试。
- [x] T014 [US3] 修改 `frontend/src/lib/api.ts`、`document-relationship-graph.tsx` 及实际 Node 测试，显示独立检索诊断、降级与真实覆盖，读取不调用模型。

## Phase 5 — Quality measurement (US4)

- [x] T015 [P] [US4] 修复 `backend/app/evaluation/ontology_guided_scorer.py` 完整 tuple/等价引用/空预测/未裁决门；添加评分反例。
- [x] T016 [US4] 实现检索/路径/成本及固定池消融校验工具，接入 `quality_guided_variant.py` 和活动 `README.md`；参考与识别输入隔离；补充独立 Word 原件 preparation 入口，不依赖旧抽取作业。
- [ ] T017 [US4] 冻结专家标注、独立文档、模型制品和质量/成本阈值后运行至少三个真实新 run 及 A–D 固定池/动态前沿验收；记录外部材料缺口，不用模拟代替。

  CPU 后续准备已完成：锁定依赖、两套完整校验制品、宿主/Compose 真实冒烟，以及获准上传
  文档 86 条记录/172 个双意图输入对的完整排序与零调用恢复均通过，见 [cpu-ranking.md](cpu-ranking.md)。
  这些运行不提供专家参考或事实 P/R，也不是 A–D 三轮质量对照，因此 T017 保持未勾选。

  2026-09-09 按用户要求实际核验验收材料并调用正式评分入口，得到
  `pending_expert_reference / not_run`；新增可供裁决的原文审阅包与运行协议草案。
  本轮没有合格专家参考、独立文档或获批阈值，未把诊断运行追认为正式验收，详见
  [本次验收记录](acceptance.md)。

## Phase 6 — Integration and delivery

- [x] T018 运行必要领域/恢复/API/评分/边界/前端静态和类型检查，将命令、结果、SR01–SR26 与外部门写入 `validation.md`。
- [x] T019 完成 `quickstart.md` 可执行操作/验收说明，更新目标方案的实际实施状态与 021 继承关系，复核差异保护用户文件。

## Phase 7 — 本轮复核发现的闭环缺口

此前 T009 的受控验证复用了同一次模型回答，未满足继承方案要求的独立调用。
此前工程结果不能证明这一要求已落实；本轮增加以下明确验收项。

- [x] T020 [US2] 拆分真实 discovery / verification 请求，冻结候选与精确 target，独立核验桥接类别、主体、端点、极性、条件及必要反证；拒绝错目标、缺项和重复响应。
- [x] T021 [US2/US3] 每次实际模型请求前持久预扣预算，取消/owner 丢失中止派发；崩溃恢复不刷新额度；完成结果先保存再软暂停。
- [x] T022 [US1] 全部剩余记录不可精排时继续确定性原文探索，保留 not_rerankable 观察，不记录虚假排序成功。
- [x] T023 [US4] 活动评测隔离 scheduler 数据库并绑定 run/task/stage，冻结逐记录调用预算，异常也保留排序费用、请求和预扣状态。
- [x] T024 [US3] 在专用 PostgreSQL 实际验证执行租约/并发/模型调度，记录真实迁移边界；补验可执行的在线持久恢复。
- [x] T025 [US2/US4] 以获准 DOCX 创建新 preparation，验证真实主模型独立请求与 CPU 排序集成；工程实验不代替 T017 专家质量门。
- [x] T026 运行本轮受影响回归与静态检查，更新 validation/quickstart 和实际剩余项。
- [x] T027 [US4] 补可执行固定池导出、A–D 共池重放及至少三轮显式协议汇总，拒绝漏组、漂移、复制运行及未裁决参考；不以单池替代动态前沿或事实质量。
- [x] T028 [US3] 用隔离数据库/服务及真实浏览器验证图谱、排序、证据回放、切换与刷新，断言无新增模型调用和非 GET 写请求，保留截图与 trace。
- [x] T029 [US2] 修复真实诊断暴露的文档根引用协议：程序核对根身份/版本并约束空根主体引用，局部主体原文门禁不变；独立 verdict 显式必填，以新运行身份真实复测。
- [x] T030 [US3/US4] 修复同一 TTL 因属性枚举顺序不同而生成不同本体快照身份的问题；验证乱序等价、真实语义变更失效以及实际独立 preparation 重现，不改写旧快照。

- [x] T031 [US3] 区分真实有界任务预算停止与记录技术未完成；根/子槽位停止原因不把未尝试记录写成已尝试失败，保留原实验制品。

## Phase 8 — CUDA 12 GPU 增量与 CPU 保留

- [x] T032 [US1/US3/US4] 增补 spec/plan/contracts/quickstart 的显式设备、精度、离线
  环境、数值身份和失败清理契约；新增 [GPU 验收记录](gpu-ranking.md)，不改写 CPU 历史证据。
- [x] T033 [US1/US3] 在配置、`LocalSemanticRanking` 与运行冻结/恢复链增加显式单卡
  CUDA 12 / float16 支持；CPU/float32 默认不变；保持完整输入、L2 与 raw logit，
  GPU 不可用不静默 CPU 回退，失败/取消/超时清理并保留费用。
- [x] T034 [P] [US1] 准备独立 GPU 环境，核验 PyTorch 2.7.1/cu126 的实际驱动、
  卡型、runtime 和本地依赖/模型身份；保留现有 CPU 环境，以实测登记支持范围。
- [x] T035 [US1/US3] 完成 CPU 默认回归及 GPU 设备/dtype、数值漂移、输入完整性、
  非有限输出、不可用/OOM/取消/超时、零调用恢复的受影响测试，记录真实执行范围。
- [x] T036 [US4] 给现有冒烟/文档排序脚本补显式 device/dtype 和实际数值环境记录，
  使用同一冻结 DOCX、谓词、完整共同输入及预算分别执行 CPU/GPU；核验完整原文、
  双意图原始分数、排名差异、冷/热成本与恢复，把新制品链接和结果填入 GPU 记录。

CUDA 增量依赖：T032→T033；T034 可与 T033 并行；T033/T034→T035/T036。
GPU 工程/环境完成不会勾选 T017，也不改变独立专家、文档样本及预注册质量/成本门。

## Phase 9 — 默认引擎改为 GPU

- [x] T037 按后续指令将应用与基础 Compose 默认切到 CUDA 12.6/float16，采用已验收
  batch/超时/并发，保留 CPU 显式覆盖与旧冻结身份；验证默认和覆盖配置、受影响回归，
  核对共享运行状态并记录实际生效边界，见 [gpu-default.md](gpu-default.md)。

- [x] T038 按后续授权打开本机线上排序开关和完整模型路径，仅切换 backend；验证
  实际 Settings、双策略启用、GPU 双模型请求、接口健康及旧运行制品保留，见
  [gpu-online.md](gpu-online.md)。

- [x] T039 修复真实 PostgreSQL 运行 ID 长度导致的 `DataError`、暂停排序恢复及
  冷启动制品校验期间的调度续租；保留身份、费用与失败证据，通过专项回归并恢复
  用户当前任务，核验真实 GPU 双模型和 semantic epoch 提交，见
  [ranking-dataerror.md](ranking-dataerror.md)。

- [x] T040 修复排序预扣等待跨超时后的未派发记账与停止原因快照不一致；保留冻结额度、
  已提交图谱和旧失败记录，验证明确未派发预留的单次复用、旧状态保守恢复及中文诊断，
  记录当前运行实际状态和上线边界，见 [暂停修复记录](ranking-pause-followup.md)。

- [x] T041 支持运行级排序预算 enable/disable：默认启用，禁用期间不预扣也不累计预算，
  重新启用沿用历史量；增加暂停态 CAS/幂等/鉴权审计操作及 UI，同身份恢复预算暂停，
  保留原图谱、数值阈值与技术执行限制；完成迁移、领域/API/UI回归与实际可用性验证，
  见 [预算开关验收](budget-control.md)。

## Phase 10 — 模板面板迁移与工程收尾

- [x] T042 模板面板接通新运行桥接、共享内核、原文绑定、优先路径及分支状态；
  保留新旧执行域、原文权限、owner 与冻结身份边界，见
  [迁移与收尾记录](kernel-panel-migration.md)。
- [x] T043 完成排序持久化等待屏障修复及失败诊断隔离回归，执行受影响后端测试、
  前端契约/状态测试和静态检查。按用户最新指令以相关用例通过收尾。
- [ ] T044 性能工作后继续模板真实金标准、逐项原文回放、切源迟到响应及识别阶段
  暂停/恢复实测；当前运行保留暂停，旧 B 路径通过不能替代本项。

依赖：T001→T002→各测试/实现；T004–T007 分文件可并行，T008 串行整合；T012 与 T008 按契约协作，T013→T014。T017 依赖 T015/T016 与独立外部材料。P5 的可选补充精排 E0/E1、F0/F1 后续研究单列，不用它替代本期 T009/T010。T018 不因 T017 材料缺失停止可完成工程验收。T017 的明确缺口与补验步骤见 `validation.md` 和 `quickstart.md`，不以本次受控测试勾选。

## Phase 11 — 性能增量（2026-09-10）

- [x] T045 完成PF需求/澄清、计划与performance契约，执行Spec Kit前置检查（`spec.md`、`plan.md`、`contracts/performance.md`）。
- [x] T046 固定无模型回放/大队列/共享节点夹具与独立性能报告工具，保留历史原件（`backend/scripts/benchmark_document_state.py`、`backend/tests/test_extraction/test_scheduler_performance.py`）。
- [x] T047 紧凑公共图/排序摘要/定位索引，GET只读兼容、版本与ETag、权限回归（`backend/app/services/document_analysis/read_artifacts.py`、`backend/app/api/document_analysis.py`）。
- [x] T048 SSE合并刷新、断线退避、隐藏取消、图/摘要分离、文档缓存与树索引/引用（`frontend/src/components/analysis/use-template-document-run.ts`、`template-document-graph-panel.tsx`）。
- [x] T049 不可变运行制品引用、轻量快照/检查点、完整性与保留清理、逐批原子恢复（`backend/app/services/document_analysis/state_artifacts.py`、`execution.py`）。
- [x] T050 单协调器持续处理确认，冻结模型工作请求与预扣屏障、暂停/取消/失租语义（`backend/app/services/extraction/ontology_guided/recognition_execution.py`、`executor.py`）。
- [x] T051 新版本双意图重试预算，旧策略兼容与费用保全（`backend/app/services/extraction/ontology_guided/semantic_reranker.py`）。
- [x] T052 精确分词批量/缓存、检索视图与记录索引、无全队列复制的纯选择（`backend/app/services/llm/semantic_ranking.py`、`ontology_guided/retrieval_views.py`、`records.py`、`scheduler.py`）。
- [x] T053 惰性逻辑前沿与有界实例化、旧格式恢复、任务/公平/覆盖等价（`backend/app/services/extraction/ontology_guided/lazy_frontier.py`、`scheduler.py`）。
- [x] T054 模板路径公平增强与冻结批次实验入口，保留尾部探索与完整覆盖（`backend/app/services/extraction/ontology_guided/scheduler.py`、`backend/app/config.py`）。
- [x] T055 必要后端/API/边界、专用PG故障、前端静态/行为与无模型性能回放；更新验收（`performance-validation.md`、`backend/tests/test_extraction/test_performance_postgresql.py`）。
- [x] T056 仅测试新方案真实模板及batch4/8/16，固定输入/模型/预算，按实际证据记账（`backend/scripts/benchmark_template_document_run.py`、`check_document_semantic_ranking.py`）。
  batch4/8/16及单组新方案8任务诊断已结束；后者7/3444机会、11主请求，预算耗尽而未完成。
  暂停恢复同身份、无任务重做；模板核心断言未通过，否定候选存在实体类型疑点。
  本项表示测试已执行并记录，T017/T044质量门仍未通过；不做原方案性能对照，体感由用户评价。
- [ ] T057 条件门：有资源余量证据后才试两个在途任务；未通过门则维持单任务，不默认上线。

依赖：T045→T046/T047/T048；T049→T050；T051/T052可按文件并行；T052→T053→T054；
各实现→T055→T056。T057是条件实验，不把未启用并行称为工程缺陷或性能收益。
T017/T044仍是独立质量/真实模板门，不能以本阶段夹具通过替代。

## Phase 12 — 指定原件的启发式实验验证（2026-09-10）

此阶段对应用户授权的验证测试，范围见`heuristic-validation-plan.md`；不将方案H01–H06
生产工作包全部视为已完成，不覆盖或恢复旧评测身份。

- [x] T058 核验指定原件、本机模型与当前本体；冻结预算/源码/配置，建立识别外诊断参考。
- [x] T059 默认关闭的共享启发式策略及小池开关；保留完整U、技术失败、独立证明、累计费用、补搜公平和旧政策hash；新增37项定向测试，合计95项受影响测试通过。
- [x] T060 构建独立冻结/执行工具，记录请求、阶段、批次图、覆盖及首达时间；隔离调度库，不接生产业务状态。
- [x] T061 完成真实本机运行及独立结果核对，报告绝对耗时、节点未达原因和未实现范围（`heuristic-validation.md`）。48任务/73主请求，1244.01秒，预算耗尽，最终7条系统有效关系/0属性；全文未完成，业务质量未达标。

依赖：T058→T059/T060→T061。T061完成只表示实验已执行并报告，不表示全文识别成功、
专家金标验收或部署通过。生产恢复、完整pass/继续义务、关键路径4:1配额、属性协议改进及
H05/H06仍单列后续工作。

## Phase 13 — 跨记录互补证据定向实证（2026-09-10）

范围见[joint-evidence-validation-plan.md](joint-evidence-validation-plan.md)。本阶段是验证，
不把实验驱动器的显式证据干预称为生产自动聚合/重验功能。

- [x] T062 冻结原件、本体、本机模型、current/joint源选择及限额；登记类型张力、否定和归属反例。
- [x] T063 建立受控双阶段响应回归，核对证据权限、上下文身份、未决重验缺口和父关系后属性调度；新增独立验证runner，线上核心不修改。
- [x] T064 执行真实本机对照，独立核对关系/属性/原文引用并记录关键节点耗时和未达原因；见[joint-evidence-validation.md](joint-evidence-validation.md)。16任务/32请求，829.27秒，0有效属性；46项回归通过，全部原始响应离线精确复现。支持补证机制，未达成更快获得正确关系/属性的优化目标。

依赖：T062→T063→T064。实验执行完毕不等于质量门、自动检索、生产部署验收通过。
