# Semantic ranking and public graph contract

2026-09-10 新增排序v2双意图重试、精确批量分词、独立摘要/定位读取及存储兼容契约，
见 [performance.md](performance.md)。既有v1运行的冻结身份和恢复语义保留。

## Internal ports

排序模型仅返回逐 query-view 原始分值或向量及不可变身份/技术成本；不返回事实、跨池 rank 或修改租约。服务协议由领域模型 Protocol 定义，适配层在 `services/llm/semantic_ranking.py`；在线与评测传同一实例接口。

`OntologyGuidedExecutor` 接收可选 ranking service/policy 和排序提交 hook。ExecutionBatch 与最终结果携带 ranking state；恢复传同一 state，已经 committed 的池不得重评分。提交 hook 检查运行 token、当前 fingerprint 及 query/subject/plan/epoch 精确身份，在首个依赖其顺序的记录调用前落盘。失败不推进调度。

新建本体快照按声明 IRI 及完整载荷 hash 规范化无序菜单；并行同 IRI 约束分别保留，不合并 range、不回写旧快照。相同声明的枚举顺序不改变 source/snapshot 身份，真实字段变化仍使身份改变。

完整 plan 额外绑定 frozen_record_ids/hash，必须与 RecordIndex 校验。任务 proof_dependency_hash 不包含 plan_id、epoch、排名；ranking_dependency_hash 独立记录。

### Device and numerical execution — CUDA 12 increment

新增配置契约为 `SEMANTIC_RANKING_DEVICE` 和 `SEMANTIC_RANKING_DTYPE`，缺省分别为
`cuda:0`、`float16`（按 2026-09-09 后续指令变更）；CPU 显式设 `cpu/float32`。
GPU 使用单卡 `cuda:N`，物理卡由部署配置选择；合法组合必须在
配置/适配器边界检查，不能将未识别设备或精度值默认为 CPU。
`SEMANTIC_RANKING_CUDA_VERSION` 固定 `12.6`，实际 wheel/runtime 必须匹配；
已验证组合为 PyTorch 2.7.1/cu126 与 P100，支持范围见 [实测记录](../gpu-ranking.md)。

运行身份和结果至少可追溯请求/实际设备、dtype、完整模型制品、torch/transformers/
sentence-transformers 版本、实际 CUDA runtime、驱动、所选卡型/计算能力，以及影响
结果的精度/确定性政策。`cuda:N` 是当前可见设备集合中的逻辑索引，须保留可见性及
实际设备映射依据，不能仅靠同一个索引认定恢复环境相同。变化要求新运行；进程尚未
验证实际环境时不能伪造硬件身份。GPU 不可用原因须明确且不包含凭据或用户原文。

tokenizer 与模型输入均遵守 `complete-no-truncation`，返回向量保持 L2 归一化，精排
保持 `raw_single_logit`。FP16 允许有限数值误差，不允许 NaN/Inf、漏项、softmax/sigmoid
或跨池概率解释。数值输出转为可序列化值不会使实际推理 dtype 变成 float32。

请求 GPU 但不可用、卡号越界、OOM 或模型输出失败，必须终结本次技术请求并保存成本；
适配器不偷偷在 CPU 重算，领域层只执行已冻结整池暂停或确定性降级。超时、取消及
所有权丢失终止私有 worker，并在其退出后释放调度槽；不得终止其他运行/服务进程。
同环境恢复已提交 epoch 零新请求，设备/dtype/数值政策漂移不能重新排序冒充恢复。
已冻结成功身份在恢复时遭遇瞬态 GPU probe 失败，保留原指纹与水位并允许重试核验；
不得把“暂时无法核验”直接包装为永久依赖漂移，也不得用未知身份派发新识别。
私有 worker 的进度锁只在线程间使用，强制退出不依赖父进程结束来回收该锁的 named semaphore。

默认 batch 4、超时 1200 秒、文档 dispatch 并发 1、失败 pause；能力启用与完整模型制品
要求保持独立。CPU 覆盖同时指定 CPU 镜像、runc、cpu/float32 和禁用 GPU 可见性，
不会继承宿主 GPU dtype。旧冻结设置和底层适配器的 legacy CPU 补值保持兼容，不能由
新的应用默认值将旧运行改为 GPU；诊断脚本仍保留原 CPU 缺省，并要求 GPU 验收显式传参。

不可精排是记录输入状态，保留 U 和探索资格。全部剩余记录不可精排时提交确定性探索顺序，`reason=no_rerankable_records`、`degraded=false`，逐记录 `score_status=not_selected`、分值为空；这不表示模型评分成功。真实技术失败仍遵循冻结的整池暂停/降级政策。取消、执行 owner 丢失及调度租约丢失原样中止，不技术重试或降级。

`run(model_call_state=..., model_call_hook=...)` 接收独立识别请求预扣端口。状态为 `version=1`、`recognition_run_id`、`run_fingerprint`、`lineage_calls` 和顺序追加 `reservations`；每项包含从 1 开始的 `sequence`、`task_id`、`stage`、从 1 开始的 `ordinal`、`lineage_id`。线上使用私有 `recognition-model-calls` 制品，并核验 owner/fingerprint、计数单调及历史前缀不可改写。恢复可用额度取完成调用数和预扣数的较大者；崩溃前预扣不被当作已完成模型响应。ExecutionBatch/Checkpoint/ExecutionResult 同时携带此状态。

## Public read contract

### Ranking budget control

`ranking_budget_enabled` 为运行控制字段，默认 true，新建运行取
`SEMANTIC_RANKING_BUDGET_ENABLED`；旧运行在迁移后为 true。
`POST /api/document-analysis/runs/{id}/ranking-budget/enable` 与 `/disable`
复用 `{expected_revision, request_key, reason}`，内部操作名为
`ranking_budget_enable` / `ranking_budget_disable`。仅未删除、未过期的暂停/可恢复失败运行
允许修改，并沿用所有者及写入角色校验、CAS、幂等回执与控制事件。响应带当前布尔值；
操作保持运行暂停，后续显式 resume 才调用模型。禁止通过 GET、页面切换或配置写入自动恢复。

该字段不改变冻结模型/数值限额/query/epoch身份。禁用跳过累计 slot/run tokens、
逐记录调用次数和相同请求累计次数检查；所有预算预扣、累计成本、预算调用观察及
dispatch receipt 停止增加，旧账目不清零。缓存、完整排序及覆盖仍正常保存。
新的未记账轮次通过 `costs.budget_accounted=false` 标明；旧轮次缺省按已记账处理，
不为旧不可变 epoch 补写字段。公共轮次可读取 `budget_accounted`，成本区域显示启用
期间累计值。底层调度技术记录独立于预算统计；超时/单次输入/自动技术重试/取消边界保持。

恢复时当前运行开关覆盖 ranking snapshot 中的历史开关，预算暂停只有在显式禁用后
才可重新尝试；旧暂停归档、历史账目和已提交 epoch 保留。再次启用立即沿用原累计量。

预算启用时，请求预扣屏障返回后，若期限已到且尚未调用模型，可追加 `not_dispatched` 回执；
回执绑定原请求、尝试及完整输入依赖。累计费用、逐记录调用数和重试数保持单调。
恢复复用该笔预留前先持久化 `dispatch_claimed`；未知派发状态、旧快照无回执、输入或
权限变化均不能获得额外额度。派发认领后崩溃依旧按已占用处理，不根据调度表缺项回填回执。
恢复到检查点边界时，先核验并处理既有暂停排序；预算限制仍启用时，有效的预算暂停零新模型调用返回
`ranking_paused`。技术暂停恢复完成前不派发无关槽位，失效或替代的计划归档后继续。

沿用 `GET /api/document-analysis/runs/{run_id}/graph`，现有鉴权/owner、artifact revision、event head 及两个结果 Tab 不变。新增可选且有默认值的 `ranking` 诊断投影，至少包含 requested/actual mode、degraded、reason、已提交 epoch、记录/意图排名与成本；不存在时明确 deterministic/无诊断，不把历史记录臆造为已精排。

运行中的 `progress.stop_reason` 及图谱全局 `coverage.stop_reason` 为 null。
排序暂停时图谱响应使用当前运行的 `ranking_paused`，具体原因保留在 `ranking.reasons`；
不改写不可变 checkpoint/graph，覆盖计数和逐槽位历史结果仍来自图快照。

覆盖条目保留兼容的计划 phase_counts 并明确含义，新增实际 phase 计数（包括启动但技术未完成），真实 pending/stop reason。UI 分别显示计划/实际执行；排序详情为折叠只读信息，分数不是正确概率。所有 GET、刷新、Tab/过滤/详情均不得创建模型请求。

## Proof and context

物理节点首版观察不可被后续关系判定改写；查询提及信任由精确有效入边及其证明授权，依赖随计划冻结。断言变化采用递增 revision，同语义目标不同模型尝试保留独立不可变证明。主体绑定引用从当前有效节点/入边的原文解析；新 object/value 仍必须来自当前目标记录。所有必要 refs 无配额截断；找不到来源或预算装不下则 incomplete。条件、反证只能引用完整可回放上下文。候选缺主体原文支持不能凭非空 subject-role ID 通过；文档根的描述关系使用显式用户根绑定。

适配器 `v4.3-independent` 分别使用 `ontology_guided_discovery` 和 `ontology_guided_verification` 两个模型阶段。发现回答仅是候选；验证请求绑定精确 candidate/target 与完整源上下文，剥离第一阶段 verdict。第二阶段必须完整且唯一回应所有冻结目标，重新选择原文并提供独立 `bridge_entailment` 等决策。七项 verdict 和四组 support 数组均必填，遗漏为协议失败；不能冒充模型明确作出的未决判断。每阶段计量完整 prompt/schema/context，预算不足不截断。运行期 TaskContext callback 不进入序列化或证明 hash。

根主体请求显式给出 `programmatic_document_root`；请求前核对当前主体与 target、根 ID/revision/class，以及完整原文片段的 document_hash。根验证 schema 要求 `subject_support=[]`，根实体 ID 或类名不属于原文证据；非根仍须通过可回放的局部主体归属判断。程序根身份不赋予谓词、桥接或反证资格。

## Evaluation

完整 tuple 包含主体/谓词/对象或值/方向/极性/条件/适用域，并要求等价引用集之一完整匹配才 TP。未知/未裁决显式报告并阻断正式提升门。固定池排名诊断独立于在线动态前沿；B/C 仅允许登记因素变化且 pool/view/query 内容相同；全部排序成本纳入报告。

CPU/GPU 性能对照另注册设备/dtype 因素，保持原件/IR、本体/根/谓词、查询文本、原始
record 视图与池、模型制品及输入预算一致。run/epoch/query ID 中的运行归属可以不同，
不得据此忽略输入内容差异。先报告全池逐意图分数、最终排名和完整性，再报告耗时比例；
GPU 异步操作的设备耗时须同步计量，端到端时间包括完整结果回传。当前固定池 A–D
聚合不接受混合数值环境；设备对照不替代阶段交错、完整图谱或 T017 正式质量验收。
