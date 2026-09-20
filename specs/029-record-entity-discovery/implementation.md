# 029 实施与验证记录

日期：2026-09-19。工程实现及定向验证已完成，新运行在代码中默认使用 `record-entity-first-v1`。**正式真实模型质量验收 T32 尚未完成。工程验证阶段未重启业务服务；随后已按用户授权重新部署供手工测试，见 [deployment.md](deployment.md)。未提交 Git。**

## 1. 实际行为

部署后的手工反馈修正：已完成模型输入去重和 Harness 分栏，独立 218 + 13 项后端回归、7 项前端 SSR 及静态检查通过，尚未重启应用。修正范围、附件体积测量和目录/短章节/CMCReport 评估见 [context-input-review.md](context-input-review.md)，精确命令见 [quickstart.md](quickstart.md) 第 6 节。以下原实施和部署记录保留原时间范围，T32 状态不因本轮工程修正改变。

1. 每次发现处理一个完整原文记录及冻结的类型卡组，批量提出多个实体和属性。任务是明确的 `RecordDiscoveryTask`，没有伪造的主体或谓词。
2. 类型、指称、组成及属性按实际所属类型逐项核验。实体自身成立后即可登记，入边未证明不阻止其属性；某个声明失败不抹去同批独立成立的声明。
3. `CandidateChanges` 在现有执行器内安排受影响的关系工作。关系阶段只引用授权的已登记端点，经 `validate_graph` 和独立原文核验后发布关系。
4. 后到端点、有效新指称及原文锚定缺口可唤醒受影响工作。补发现沿原记录、卡组和 lineage 处理，保留原调用额度；无改进或相同反馈不形成无限循环。
5. 候选、证明、发现进度和关系待办通过既有事务原子发布。暂停继续读取当前状态与已保存响应；未知请求保持成本与未完成状态。
6. 新运行默认启用记录优先策略；旧运行继续原冻结策略、指纹及协议。已核验孤立节点保留，`effective` 根可达语义不变。

没有增加数据库表、消息中间件、事件回放、全量历史快照或第三方依赖，也没有启动新的模型服务。

## 2. 实现落点与设计细化

| 范围 | 实际文件/行为 |
|---|---|
| 记录契约和本体目录 | [record_discovery.py](../../backend/app/services/extraction/ontology_guided/record_discovery.py) 定义任务、目标、类型卡、策略、候选变化和端点分页；[ontology_plan.py](../../backend/app/services/extraction/ontology_guided/ontology_plan.py) 提取 `compile_class_predicates()`。 |
| 模型闭环 | [record_model_adapter.py](../../backend/app/services/extraction/ontology_guided/record_model_adapter.py) 复用现有传输、调用预留、响应保存和工具配对。完整目标独立核验；空目标不支付空核验，容量不足不裁剪声明。 |
| 授权和证明 | `context.py`、`claim_freeze.py`、`claim_protocol.py`、`verification.py`、`tool_runtime.py` 按声明实际所属类型检查约束和证明，普通实体不借用文档根权限。027 的 `stage-schemas.json` 同步新增可选绑定引用契约。 |
| 跨阶段指称 | [reference_dependencies.py](../../backend/app/services/extraction/ontology_guided/reference_dependencies.py) 验证授权依赖；[record_binding_evidence.py](../../backend/app/services/extraction/ontology_guided/record_binding_evidence.py) 从原发现/核验制品重建证明。旧指称证明不代替当前关系角色、方向及谓词核验。 |
| 调度和当前状态 | [executor.py](../../backend/app/services/extraction/ontology_guided/executor.py) 惰性准入、独立主体登记、候选唤醒、分页、补发现；`current_work.py` 与 `document_analysis/current_state.py` 使用既有 v4 当前状态和独立账本。 |
| 在线/API/界面 | `document_analysis/execution.py`、`harness.py`、`reviews.py`、后端 schema、前端 API 类型和 `document-harness.tsx` 识别任务联合，显示记录识别/核验标签和逐类型卡片。 |
| 共享评测 | `ontology_tool_engine.py` 与 `quality_guided_variant.py` 调用同一核心；记录请求受总物理请求预算约束，记录任务与谓词批次成员分别计数。 |

已落实的关键细化：

- 类型卡默认每组最多 4 类，还受卡片 token 预算约束；请求前冻结，发送后不通过拆组领取新额度。记录协议为 `ontology-record-discovery-v1`。
- `work:record_discovery` 保存当前发现进度；`work:record_relation_inputs` 保存候选签名、当前页和剩余引用页。端点页默认 8 个；同记录端点和名称/别名相交的完整歧义组保留在一起，超限交由容量门处理。
- 同一候选签名下累积分页语义。后续页未完成时，先前已支持关系和统计保留；覆盖仍明确未完成。冷继续使用已付费协议冻结的端点，避免重复收费或漏页。
- 自动属性补充使用有来源锚点的原记录定向重提；人工否决后的属性修复生成真实主体/谓词任务。原发现协议与已保存结果保持原归属，空审核发布边界不允许改写候选、证明、协议或成本。
- 新增可选绑定字段在未提供时保持旧序列化形状。旧协议使用已保存的提示和输入，不因当前 Schema 增加可选字段而拒绝恢复。
- 焦点路径重复经过同一类型时，记录管线依冻结的合法类型—谓词路由安排关系机会；旧管线继续原来按 hop 的过滤。

## 3. 工程验证

以下是本次实际运行记录，不累计重叠用例，也不把受控模型回答当作真实质量评测。

| 验证 | 结果与依据 |
|---|---|
| 最终 40 模块整合回归 | **729 passed、4 skipped、4 warnings，237.30 秒**。精确模块与参数见 [verification-command.json](verification-command.json)，原始日志见 [verification.log](../../backend/data/evaluations/record-discovery-20260919/engineering/verification.log)。覆盖记录发现、执行/恢复、跨阶段指称、关系、人工修复、旧批次、原文/证明/图契约、当前评测及共享边界。 |
| 随后新增组成对象验收 | `.venv/bin/python -m pytest -p no:cacheprovider -q tests/test_extraction/test_record_model_adapter.py`：**14 passed、4 warnings，1.90 秒**。其中 4 项是整合回归之后增加的组成完整性和 generation 变化不能重复造对象用例；仅新增测试，未再改运行代码。 |
| 专用 PostgreSQL 及严格审核边界 | **22 passed、23 deselected、4 warnings，10.77 秒，无 skip**。其中 **5 项实际 PostgreSQL**、17 项 SQLite 严格审核边界。命令见 [quickstart.md](quickstart.md)，使用脚本确认的可销毁测试库；测试库已释放。 |
| SQLite 当前状态与旧批次回归 | `test_record_discovery_current_state.py`、`test_batch_expert_repair.py`、`test_batch_current_state.py`：**61 passed、4 skipped、4 warnings**。跳过项是专用 PostgreSQL 场景，另已实跑。该组与整合回归重叠。 |
| T28 后端 API/schema | **39 passed**；早期工具结果保留结论，精确组合及独立日志未留存，不补写不存在的命令或日志。 |
| 旧属性修复 API | `test_document_property_repair_execution.py`：**4 passed、4 warnings**。保留崩溃、恢复和预算断言，仅修复基线 fixture 的适配器工厂参数及显式历史冻结策略。 |
| 前端 | Harness 真实 SSR **4 个子测试通过**，命令为 `node tests/document-harness.test.mjs`（工作目录 `frontend/`）；既有 Node 测试、TypeScript 类型检查和定向 ESLint 通过。早期精确命令组合未留存，未执行或声称生产构建、真实后端浏览器验收。 |
| 静态与差异 | 本次受影响 **40 个 Python 文件**定向 Ruff：`All checks passed!`；`git diff --check` 通过。 |

整合运行中的 4 个 skip 不计作数据库通过证据；专用数据库命令实际覆盖这些场景及另一个现有原子性案例。警告为既有 Starlette/httpx 和 Pydantic 用法警告，未借本次重构升级依赖。

新增用例文件及对应业务边界见 [quickstart.md](quickstart.md)。T27 的增量持久化复核确认新分区沿用 `WorkMap` 按变更键写入，响应和候选正文不复制进进度行；未做全系统性能压测。

### 基线问题的处理与范围

- 旧属性修复 fixture 在实施前源码快照上实际复现 **4 failed**，均为测试工厂不接受调用方既有 `ir` 参数。修复测试 fixture 后原断言通过；未为此修改生产业务逻辑。
- CLI 总预算旧 fixture 的空声明只需一次发现调用，与原非空核验假设不符；基线复现后改用确实需要独立核验的非空实体，保留原预算断言。
- 额外探查历史 `test_evaluation_root_guided_variant.py::test_pause_resume_restores_exact_work_without_repeat_calls` 时，发现既有 `RootGuidedRunner.input_id()` 不接受 `output_split_version` 的失败。该旧入口、对应测试和相关任务模块均未由 029 修改；未把它纳入当前共享评测的通过结论，也未扩大本期范围修复历史入口。

## 4. 真实模型对照

正式质量评分需要经独立核验的金标。现有参考仅确认有 silver，尚未取得合格金标；模型对照不接收参考作为识别输入。实体/属性/关系 precision、recall、F1 及完整路径质量均为 **未评分**，T32 保持未勾选。

本次额外执行相同输入和 8 次总请求预算的有限诊断。基线使用同一代码快照中的 028 冻结策略分支，并非单独重建实施前代码；两条策略共用该快照中的基础证明与评测能力。制品保存于独立且已忽略的目录 [record-discovery-20260919](../../backend/data/evaluations/record-discovery-20260919/)，不覆盖历史评测。输入及模型身份、预算与代码归档 SHA 见 [conditions.json](../../backend/data/evaluations/record-discovery-20260919/conditions.json)。

固定条件：

- 相同 DOCX、冻结 IR、本体、元数据和根类型；`document_graph`，深度 2，最多 128 个任务。
- 模型 `Qwen3.6-35B-A3B`，revision `0b21525e972670ed59e1812e170b27c26355381f0656ecc4e25617ece7dac58b`；使用已运行的本地模型服务。
- 每组最多 8 次物理请求；每 lineage 最多 4 次；输入/输出预算 32,768 / 8,192 tokens，上下文预算 131,072，工具结果预算 4,096。
- 两组均启用相同既有谓词组包配置（最多 4 个成员），关闭 GLiNER 和外部实例工具；记录组另外冻结最多 4 类/卡及 8 个端点/页。
- 两组按顺序使用同一份代码快照，不在中途替换。快照之后，工作区补充了重复类型焦点过滤、端点别名完整分组和分页语义累计修复；最终工程测试覆盖这些修复。本对照为该冻结快照的诊断，不能替代最终版本完整质量验收。

两组均已按固定预算返回；结果汇总如下，完整机器可读摘要见 [comparison.json](comparison.json)。

| 指标 | 028 谓词批次基线 | 029 记录优先 |
|---|---|---|
| 实际物理请求 / 返回响应 | 8 / 8 | 8 / 8 |
| 输入 / 输出 tokens | 145,833 / 54,593 | 68,624 / 25,209 |
| 总耗时（秒） | 1535.84 | 698.86 |
| 最终非根候选 / 属性 / 边 / 关系组 | 1 / 0 / 0 / 0 | 0 / 0 / 0 / 0 |
| 已尝试逻辑工作 | 2 | 3 |
| 覆盖：已检查 / 未完成 / 未尝试 | 0 / 2 / 73 | 3 / 0 / 2480 |
| 活动/待继续工作单元 | 0 | 1 |
| 批次成员未完成（含尚无结果） | 2 | 1 |
| 未知请求 | 0 | 0 |
| 范围状态 | incomplete | incomplete |
| 实体、属性、关系及完整路径质量 | 未评分 | 未评分 |

覆盖行使用各管线的逻辑工作口径：旧管线为主体/谓词/记录，新管线还包含记录/类卡组；这些计数不是不同物理记录数，不能直接比较比例作为全文召回或速度指标。候选数量也是系统输出数，不是专家确认的正确事实数。

记录发现单独覆盖为 **86 个记录 × 28 个类卡组 = 2408 个发现任务**；本轮只尝试第一个记录的 2 个卡组，二者均返回 `record_no_claims`，其余 2406 个任务未尝试。另有 1 个关系工作单元待继续，不能把发现任务的 `tasks_incomplete=0` 理解为运行完成。这个结果不足以验证新管线的真实实体、属性和关系效果，也未证明效率提升。

基线的两个任务保留 `type_not_supported`、`source_assertion_fact_source_missing` 未完成原因；记录组还保留一项 `type_not_supported` 关系结果。所有逐项语义与未决账目见机器可读摘要及原始制品，未因没有金标而排除这些输出。

两组退出码均按评测入口的覆盖状态解释；`2` 表示范围未完成，不能记作整轮质量成功。详细调用、图谱、覆盖和协议检查保存在各自 `baseline-run/`、`record-run/`。


首次可用结果时间、峰值资源和缓存状态未测量；不据本轮总耗时声称提速。模型请求可能写入既有调度/用量记录，不能将这轮评测称为完全只读操作。

## 5. 交付边界

- [tasks.md](tasks.md) 已逐项回写实现及验证状态；T32 正式质量验收待合格金标与最终版本的完整对照，当前有限预算尚未覆盖完整文档。
- 代码中新运行默认策略和实际记录任务已通过工程测试；随后重新部署已核验运行环境默认策略及实际新运行的 source 制品，部署烟测不代替 T32 正式质量验收。
- 保留实施前未提交的 028 和无关改动；未切换分支或全局 feature 标记，未 reset/clean，未清理旧运行、账本或历史评测。
- Spec Kit 的 `after_implement` Git 提交 hook 为可选，本次跳过 `/speckit-git-commit`，不将现有未提交工作区自动打包提交。
