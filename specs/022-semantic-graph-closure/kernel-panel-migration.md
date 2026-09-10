# 模板关系图谱正式迁移到共享新内核

## 需求与验收

2026-09-09 用户要求以关系图谱使用新内核为目标实施工程化改造。本增量取代
`citation-anchoring-fault-tolerance.md` 的临时 B 路径交付目标：模板图谱的创建、识别、
暂停/恢复、结果、分支状态和原文定位均使用 `DocumentAnalysisRun → OntologyGuidedExecutor`。
不把旧候选搬入新图，也不使用旧执行器包装冒充新内核。

用户随后明确：性能是当前全局堵点，先收尾编码，本轮以相关 TDD/回归用例通过为完成
标准。保留已实现的新内核迁移与排序等待屏障修复，不继续启动或恢复真实模型实验。
下述金标准作为后续业务验收保留，不阻塞本轮工程收尾，也不因工程测试通过而视为达标。

后续业务验收仍使用模板 `8542466b-d6e6-4052-ae7e-05ca9c99a1c3` 的原始 HRS-5592 文档。
必须有正确的 `describes → DrugProduct`、`hasSynthesisRoute → SynthesisRoute`、
`hasCleaningMethod → CleaningProcess` 及真实产品/路线属性；名称、属性、关系及条件
均精确回放。不把单步当路线、标题当描述、N/A 当值或其他主体的数值归入产品。
所有运行身份及来源归属经服务端核验，页面 GET/刷新不启动模型。

## 设计与公共契约

1. 新适配器使用记录上下文的原子引用传输：名称/值为精确子串，证明可仅给源 ID。
   源、类型和冻结候选使用封闭编号；fact 与 binding 权限分别约束。复用共享原文
   解析逻辑，保留独立 discovery/verification、ProofGate、累计预扣及完整提议拒绝。
   实体名称与类型证明分别表达，验证不能用类型描述句替代实际对象名称。
2. 新内核补全连续字段组、主体原文和命名整体章节的必要绑定上下文；仍仅当前完整
   RecordIndex 记录允许提出新事实。跨记录属性要求原主体与当前表单归属的双端证明。
   模板路径仅调度排序，不提供期待实体/属性值，不删除其他合法菜单和全文探索机会。
3. 新增 `/api/document-analysis/templates/{template_id}/sources/{job_id}/runs`：
   - GET 仅返回当前用户在该模板/源作业下的最新未删除运行，无运行则 `run: null`。
   - POST 接收 `request_key`，仅高级分析师可创建；服务端读取登记源文件、根类型和
     模板路径，以现有 `create_run_with_source` 创建运行。原件独立保存；origin 与模板
     hash/优先路径写入不可变源制品和请求指纹，不依赖浏览器 localStorage。
   - 读取/控制/事件/graph/source 继续使用已有 run API、owner、expected_revision 和
     request_key；无新数据库运行域，不改变旧候选审核/提交。创建新协议运行，旧指纹
     不匹配的历史运行保留，不强行恢复。
4. 模板侧栏使用新运行面板。结果按新 graph 的实体/边/属性组成树并以有效肯定投影为默认；
   通过 source selection_ref 获取该运行拥有的原文 anchor 后联动既有预览。
   新图的系统验证不得显示为人工确认，不调用旧审核、PDE 或事实提交端点。
   分支展示来自新运行 coverage，区分未尝试、失败、未决、有效关系及属性进度。
5. 适配器、必要上下文与优先级政策纳入冻结身份；旧断点不能重标为新协议。
   切换源码部署前确认无活动模型请求，真实验收制品独立保存。
6. 模型 HTTP 等待期间，在原工作线程处理排序计费/缓存的持久化屏障；不在排序线程
   操作 Session，也不在候选核验期间提交排序 epoch 或改变任务顺序。屏障失败立即取消
   HTTP 并传播执行错误，不按模型响应失败重试。跨线程兼容调用不得执行该屏障。
   实测发现原先每个排序批次会等待一次完整识别请求，此调整避免关键分支被串行等待。

## 实施顺序与必要测试

- [x] M1：新内核原子引用与独立名称/类型证明；权限、重复子串、错误源、条件反例。
- [x] M2：必要字段组/整体章节上下文和模板优先调度；主体归属、无饥饿与恢复测试。
- [x] M3：模板源运行桥接及公共投影；幂等、owner、源身份、只读和旧域隔离测试。
- [x] M4：新图侧栏、控制与原文定位的工程实现；运行契约、分支计数、类型检查和只读浏览器已验证。
- [ ] M5：本地真实模型运行、上述模板核心关系/属性验收；按最新指令暂停，留待性能工作后继续。

后续实测还需逐项回放原文、验证切换源时的迟到响应，以及识别阶段暂停/恢复。
准备阶段暂停成功不代表首轮识别阶段的暂停异常已修复。

工程测试、新内核真实模板结果、整文质量和业务发布分开记录。缺口不得以旧 B 路径结果
或局部合成响应替代。实际执行结果在完成后追加。

公共 HTTP/投影细节见 [模板运行契约](contracts/template-document-runs.md)。

## 当前实测记录

- 151 项新内核/模板桥接/投影/恢复定向测试通过，另 13 项 B0 反馈域回归通过。
- HTTP 等待屏障、调度、暂停/恢复等 28 项定向测试通过；同一组结果包含重叠测试，
  不累加成全库测试总数。
- 前端类型检查通过；新运行契约及分支计数 16 项 Node 测试通过。
  定向 ESLint 无错误，模板编辑器仍有原有 `meta.status` 依赖警告。
- 首轮真实运行 `a0db2ad4-aab8-42e0-8e18-006a55c77d76` 经正式模板 POST 创建。
  已产出有效 `describes → DrugProduct / HRS-5592项目`，一级路线、清洗及属性尚未验收。
  原子引用可回放；不可将拒绝的类型穷举候选算作有效结果。
- 此轮首轮排序为 105 次模型请求，计费 210037 token，耗时约428秒。后续排序持久化
  被识别请求阻塞，因此增加上述等待屏障。暂停操作后该轮被记为 `ANALYSIS_FAILED`；
  已有图与检查点保留，暂停异常需在新一轮实测复验。它不是通过金标准的运行。
- 新一轮使用 `ontology-guided-executor-v5.1-ranking-durability` 创建新身份，
  不复用或改写前一轮指纹。实测制品位于 `/app/data/uploads/template-kernel-migration-20260909/`。
- 第二轮 `fbfe58e1-1752-46d6-aeba-0756be7d53d4` 从真实模板页面“重新识别”创建，
  浏览器只发出一次新模板桥接 POST。首次排序准备阶段暂停成功，状态 `paused`、
  `error=null`；尚未恢复或完成任何识别任务。工程收尾时只读复核该状态，并确认
  模型请求表中 `queued` / `running` 请求均为零。本次收尾未重启服务或发起模型调用。

## 工程收尾验证

新增失败诊断回归覆盖普通运行错误和排序持久化等待失败：内部仅保存异常类型及最多
8 个文件/行号/函数栈帧，不保存异常值与局部变量；状态、graph、SSE 公共接口均不暴露
这些内部字段。原有暂停异常仍保留为待定位项，不将诊断覆盖视为异常修复。

在 `backend/` 执行本轮相关测试合集：

```bash
.venv/bin/python -m pytest -p no:cacheprovider -q \
  tests/test_api/test_template_document_runs.py \
  tests/test_api/test_document_analysis.py \
  tests/test_extraction/test_citation_feedback_domains.py \
  tests/test_extraction/test_ontology_task_citations.py \
  tests/test_extraction/test_template_kernel_priorities.py \
  tests/test_extraction/test_independent_semantic_verification.py \
  tests/test_extraction/test_semantic_graph_closure.py \
  tests/test_extraction/test_ontology_guided_core.py \
  tests/test_extraction/test_semantic_proof_regressions.py \
  tests/test_extraction/test_semantic_proof_persistence.py \
  tests/test_extraction/test_ontology_guided_boundaries.py \
  tests/test_extraction/test_semantic_scheduler.py \
  tests/test_extraction/test_semantic_pause_recovery_regressions.py \
  tests/test_extraction/test_document_analysis_execution_recovery.py \
  tests/test_extraction/test_document_analysis_model_call_recovery.py \
  tests/test_extraction/test_document_analysis_public_projection.py \
  tests/test_extraction/test_model_wait_durability.py \
  tests/test_extraction/test_model_scheduler.py \
  tests/test_extraction/test_semantic_ranking_execution.py \
  tests/test_extraction/test_semantic_ranking_restore_priority.py
```

在 `frontend/` 执行：

```bash
node --test tests/document-analysis-runs.test.mjs tests/document-ranking.test.mjs
./node_modules/.bin/tsc --noEmit
npm run lint -- src/lib/api.ts \
  src/components/analysis/use-template-document-run.ts \
  src/components/analysis/template-document-graph-panel.tsx \
  src/components/extraction/template-slot-editor.tsx
```

后端上述合集 193 项通过、零失败、零跳过，耗时 46.85 秒；4 条既有依赖/模型定义警告。
新增诊断回归包含在 193 项中，不另行累加。前端 25 项通过、零跳过，类型检查通过；
ESLint 零错误，保留原有 `meta.status` 冗余
依赖警告。后端相关源码、测试和验收脚本 Ruff 检查通过，`git diff --check` 通过。
上述结果不累加之前重叠测试数量。本轮未执行生产构建或 PostgreSQL 专用锁/并发测试。

后续金标准可使用 `backend/scripts/validate_template_kernel_migration.py` 读取已有运行，
不发起模型请求；验收值仅保存在此脚本，不进入在线识别输入。在配置好目标数据库的
后端环境中运行以下命令，全部判据满足才退出 0，否则退出 1：

```bash
PYTHONPATH=. .venv/bin/python scripts/validate_template_kernel_migration.py \
  --run-id <待验收的新内核运行UUID> --output /tmp/template-kernel-acceptance.json
```

收尾仅验证该脚本 `--help` 入口，未重新执行真实金标准验收。新内核目前仅实测产出
首轮 DrugProduct 关系，产品属性、整体合成路线及清洗过程的完整金标准仍未完成。
