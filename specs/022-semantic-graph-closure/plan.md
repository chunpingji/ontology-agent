# Implementation Plan: 主体感知语义精排与图谱闭环

**Branch**: `022-semantic-graph-closure` | **Date**: 2026-09-08 | **Spec**: [spec.md](spec.md)

## Summary

模板面板迁移增量按 [kernel-panel-migration.md](kernel-panel-migration.md) 的 M1–M5 执行，
补齐新适配器并复用 DocumentAnalysisRun 生命周期，最终以真实模板验证新内核输出。
按用户最新指令，本轮仅完成工程收尾及相关 TDD/回归检查；M5 真实模型验收暂停，
在性能工作后继续，不以工程通过替代业务金标准。

在 021 唯一新核心中接入主体查询、完整视图、多路候选、池内精排和公平调度，同时补齐主体原文验证、必要闭包、迟到冲突失效、持久恢复与公共诊断。先保证确定性共同基线与闭环，再以同一执行器接入可选本地语义模型。排序永不赋予事实权限。

## Technical Context

- Python 3.11+；既有 FastAPI/Pydantic/SQLAlchemy，前端 Next.js/React/TypeScript，版本沿锁文件。
- 复用 `ontology_guided` 领域核心、DocumentAnalysisRun 租约/事件/制品和当前 API。
- 模型使用现有 semantic extra 的 Sentence Transformers，本地路径、local_files_only、trust_remote_code=False、完整文件 manifest hash；未安装或未配置时不隐式下载。
- CPU 排序依赖和两套完整模型制品已准备并真实验证，见 [cpu-ranking.md](cpu-ranking.md)；正式质量仍依赖专家参考、独立文档及预注册验收协议。
- CUDA 12 增量保留该 CPU 环境；另建已验证的 PyTorch `2.7.1` / `cu126` GPU 环境，使用
  显式 `cuda:N/float16`。官方 wheel 发布与本机实测分开，状态见 [gpu-ranking.md](gpu-ranking.md)。
- 测试采用可控逐对分数与本地模型客户端响应，不生成虚假真实模型报告。保留专用 PostgreSQL/真实浏览器的证据边界。
- 有界初值：pool 64、batch 16、discover/counterevidence 双意图、每意图至多一模板、整记录无法容纳则 not_rerankable；阶段冷启 1:1 后 4:1，每阶段五次一次原始台账探索。
- 精排耗时/输入对/token/重试分开计账；持久模型调度复用已有 request ticket；配置冻结到 run fingerprint。

## Constitution Check

| 原则 | 设计前/后检查 |
|---|---|
| 规范驱动 | specify/clarify 已完成；先契约再实现；tasks 与 SR/FR 追踪 |
| 本体权威/保真 | 不修改 TTL、domain/range 或事实提交边界 |
| 可追溯 | 排序仅追加观察；模型/输入/政策冻结；晚到响应校验 token 与精确版本 |
| 契约/测试 | 内部与公共契约先写；纯领域、API、恢复、前端和评分定向测试 |
| 最小复杂度 | 无向量库、无新运行域，缓存限 run 权限域；复用事件制品 |
| 离线 | 无隐式出网；排序可选降级与主验证失败分开 |

两次设计检查均无豁免。真实质量/发布/清理门不由本工程状态代替。

## Project Structure

- `backend/app/services/extraction/ontology_guided/retrieval_query.py`、`retrieval_views.py`、`semantic_retrieval.py`、`semantic_reranker.py`、`retrieval_fusion.py`：新增纯领域排序。
- 同目录 `retrieval.py`、`scheduler.py`、`contracts.py`：完整全集、阶段/槽位/章节轮转与语义任务身份。
- 同目录 `executor.py`、`context.py`、`model_adapter.py`、`dependencies.py`：调度接入、主体原文、必要闭包与冲突失效。
- `backend/app/services/llm/semantic_ranking.py`：本地模型适配、离线制品及调度。
- `backend/app/services/document_analysis/`、`backend/app/schemas/document_analysis.py`、`backend/app/config.py`：冻结配置、排序提交/恢复/只读投影。
- `frontend/src/lib/api.ts`、`frontend/src/components/analysis/document-relationship-graph.tsx`：阶段真实执行与排序诊断。
- `backend/app/evaluation/`：完整 tuple 引用门、检索/路径/消融指标及成本输出。

## Execution and Recovery

每个语义任务键仅依赖主体/谓词/record 与证明输入；不含 pool、plan 排位或 reranker。首次到达槽位惰性建池，保存完整 queries/views/epoch/observations；评分提交 hook 必须在调度消费前成功。`ranking_execution.py` 在私有线程准备，每次实际调用前由主线程持久化预扣成本屏障；其他已就绪槽位可继续执行。历史临时排除集合与每条任务结果一起保存，恢复不依赖新的线程时序。已提交结果从 checkpoint 回放，不能恢复时重新调用模型改变次序。缺少分值整池确定性降级或暂停；评分成功不更新 RecallLedger。

主体原文从当前有效节点/入边的精确引用装配，为 binding only，当前 target 原文才授予新事实提议权限。已知必要来源缺失或超预算返回 incomplete；条件/反证逐字引用。迟到冲突以主体/谓词/对象或属性值及适用域建立订阅，先阻断旧候选及递归资格；保存新反证来源和未决状态，有限重验不通过即保持阻断。

模型适配器以独立 discovery / verification 两次请求实现候选与验证隔离。验证输入只携带冻结候选、精确 target 及有权限的完整原文，不继承提议阶段 verdict。桥接类别必须有独立 bridge_entailment 决策。第二次请求无预算、完整上下文超限或响应目标不一致均保持未完成，不能凭提议直接入有效图。

真实诊断发现模型会将文档根实体 ID 和本体类名误写为原文引用。根验证请求须明确程序绑定，并在实际响应 schema 中约束根 `subject_support=[]`；局部主体继续要求可回放归属证明。类型、归属、反证等独立 verdict 及四组 support 数组均显式必填，缺项作为协议失败，不能与模型明确返回的 undetermined 混记。根绑定不授予谓词或桥接支持。

每次请求前由执行器调用 fenced 持久预扣 hook，在线保存独立 `recognition-model-calls` 制品，评测保存在独立运行目录。状态绑定 run/fingerprint，预扣历史单调追加；恢复按已完成与预扣额度的较大者约束后续调用，预扣未完成不冒充完成调用。软暂停保留刚完成的结果；owner 丢失、取消和持久化失败直接中止。全部剩余记录无法完整精排时仍进入确定性探索，明确标记不可精排且不产生精排分数。

## CUDA 12 Increment

1. 在既有 `LocalSemanticRanking` 和配置冻结链增加设备/dtype；按用户后续指令，应用与
   基础 Compose 默认 CUDA 12.6/float16，CPU 以独立覆盖保留。物理卡须显式选择并通过
   可用性、索引及实际 runtime 检查。线上、文档检查和
   评测继续使用同一适配器，不另建识别执行器或把 GPU 写入领域证明规则。
2. tokenizer 继续核对完整输入和真实长度；模型在冻结目标设备/dtype 上加载，权重读取
   前重验 manifest。embedding 保持 L2、reranker 保持原始单 logit；检查有限性、形状与
   全部输入对应，不以 FP16 作为允许截断或缺项的理由。
3. 模型身份冻结设备/dtype、包版本、实际 CUDA runtime、驱动/卡型及影响推理的数值
   政策；运行记录关联实际设备。CPU/GPU 或数值环境漂移要求新运行，不复用旧向量、
   分值或提交身份。同一环境的已提交 epoch 仍无调用恢复。
4. GPU 初始化失败、OOM、非法输出按技术失败处理，只允许既有整池暂停/确定性降级；
   不隐式改为 CPU 语义推理、不保留部分分数。超时、取消和租约丢失终止并回收私有
   worker 后释放调度槽，失败仍写调用/预扣与可核对原因。
5. GPU 依赖、完整模型制品和 CPU 环境各自冻结。先做受影响回归与真实合成冒烟，再用
   同一 prepared DOCX、显式谓词、完整共同记录池验证 CPU/GPU。输入/预算一致时分列
   冷加载、热态、排队、请求、总墙钟与内存；先验证输入身份，再计算分数/排名差异。
6. 固定池 A–D 仍要求共同数值环境。设备/dtype 对照另登记比较因素；不放宽既有共同
   环境验证来接纳混合 CPU/GPU 组，T017 的专家材料和正式质量门保持原要求。

## Dependencies and Ownership

T041 预算开关增量：新增 run 独立布尔字段及 Alembic 迁移（旧运行默认 true），复用控制
端口的 owner/角色、CAS、幂等与审计。领域 `RankingService.budget_enabled` 不进入
RankingPolicy/模型身份/fingerprint；恢复以当前 run 控制值优先于旧 snapshot。
关闭期间停止预算预扣、累计量及预算观察追加，缓存和语义结果仍持久化；每轮显式区分
未记账与零成本。只允许暂停/可恢复失败态修改，运行中先走现有软暂停边界。
前端通过同源 API 提供启用/禁用操作及状态；实际部署后以无模型调用的开关往返验证可用性。

本轮暂停修复（T040）：领域层追加明确未派发回执并复用原预留；执行器只在终态计算
停止原因，应用响应叠加当前暂停/恢复状态而不改 checkpoint；UI 以中文解释具体排序原因。
专项测试覆盖屏障超时、恢复崩溃、旧状态不补造额度、终态与 API 快照一致性。

1. 规范、研究、数据模型、契约与任务先完成。
2. 排序领域/调度、离线适配/评分、在线/API/UI 可以按文件独立实现；executor 集成由主代理串行完成。
3. 关键输入/顺序/恢复/证明反例先于实现或与对应实现同批建立，最终组合测试核对公共图谱水位。
4. 真实质量先冻结独立标注与主指标；CPU 制品已就绪，缺专家标注和协议时保留 pending。P5 可选补充精排另列，不让其阻塞首期必要闭包。
5. CUDA 增量由 T032 文档契约 → T033 适配器/冻结链，T034 隔离环境可并行；两者齐备后
   执行 T035 回归与 T036 同文档真实对照。只按实际结果更新 GPU 验收记录。
