# 027 工具抽取引擎工程验证记录

日期：2026-09-16。这里记录受控 Responses 返回与真实协调器、线程和数据库代码的工程验证，不代表真实 Qwen 抽取质量评测或部署完成。

## 当前状态、范围与协议集成

在 `backend/` 使用现有 `.venv` 运行：

```bash
.venv/bin/python -m pytest -p no:cacheprovider -q \
  tests/test_extraction/test_tool_engine_execution.py \
  tests/test_extraction/test_current_work_resume.py \
  tests/test_extraction/test_tool_engine_resume.py \
  tests/test_extraction/test_document_current_state.py \
  tests/test_extraction/test_document_analysis_execution_recovery.py \
  tests/test_extraction/test_group_scope_scheduling.py \
  tests/test_extraction/test_ontology_guided_node_revisions.py \
  tests/test_extraction/test_ontology_guided_core.py
```

结果：**144 passed**。随后增加“已保存 pending_request 时收到暂停”与“阶段初始输入只能填充一次”的反例，再运行：

```bash
.venv/bin/python -m pytest -p no:cacheprovider -q \
  tests/test_extraction/test_tool_engine_execution.py \
  tests/test_extraction/test_tool_engine_resume.py
```

结果：**30 passed**。随后补充预留失败反例后，同命令 **34 passed**；各次计数有重叠，不相加。

集成测试通过真实 `ToolModelRecognitionAdapter`、`RecognitionCall` 工作线程，以及 SQLite 上的调用、工作状态、证明和展示发布路径执行；仅 Responses 传输返回受控结果。覆盖：

- 模型结果提交后的暂停，以及关系组提交后子任务的冷继续；已确认响应不再次请求。
- 新 pending_request 与对应 reservation 在既有 `persist_calls` 同一事务提交；后续模型前置回调只按 attempt、task、stage 和完整请求 hash 幂等确认。确认后发生暂停，当前请求在结果提交边界暂停。
- `one_of` 整组保留，两个成员按各自 scope 调度；不生成伪造二元边。
- 子任务从已有主体登记中的精确 discovery 引用取得完整实体提案；只有协调器读取结果，工作线程不访问该数据库会话。
- 组、scope 覆盖统计和展示缓存经过实际持久化与读取；已验证否定组不启动成员任务。
- 已最终核验的未决结果保留未完成状态，不作为技术失败重复排队。

追加反例分别在预留回调提交前抛错、事务内写入请求行后抛错，以及上下文组装后关闭调用预算。三者均验证 **0 个已保存 reservation、0 次 HTTP 请求**，持久协议没有孤立 pending 或已增加的 attempt；冷继续使用同一个首次 attempt，提交失败前后请求 hash 一致。已经预留但响应未知的请求仍保持阻断，不因此允许重发。

该调整后，以下旧协议与适配器回归 **57 passed**：

```bash
.venv/bin/python -m pytest -p no:cacheprovider -q \
  tests/test_extraction/test_current_work_resume.py \
  tests/test_extraction/test_document_current_state.py \
  tests/test_extraction/test_document_analysis_execution_recovery.py \
  tests/test_extraction/test_tool_engine_adapter.py
```

## PostgreSQL 事务与冷继续

使用本次浏览器验证启动的临时 PostgreSQL 集群，另建唯一命名的 `document_analysis_027_runtime_*_test` 数据库。测试连接使用该集群的非管理员角色；未连接共享业务 PostgreSQL，未复用或清空浏览器数据库。

首次尝试在空库运行 `.venv/bin/python -m alembic upgrade head`，被已有迁移链问题阻断：`0001_ontology_meta` 使用当前 `Base.metadata.create_all()` 建立了 `generated_reports`，随后 `0007_add_generated_reports` 再次创建该表，报 `DuplicateTable`。该测试库已删除；本次未修改旧迁移。

为完成事务行为验证，第二个独立空库使用**当前模型 schema 测试夹具**：执行 `Base.metadata.create_all(engine)`，再通过 `alembic.command.stamp(Config("alembic.ini"), "head")` 标记夹具版本。该次 head 为 `0041_template_engine`。这只使已有 PostgreSQL fixture 的版本和表检查能够执行，**不表示完整迁移链通过**。

Python 调度脚本只在子进程环境内注入该独立测试库的 `DATABASE_URL` 与 `DOCUMENT_ANALYSIS_TEST_DATABASE_URL`，随后实际运行：

```bash
.venv/bin/python -m pytest -p no:cacheprovider -q \
  tests/test_extraction/test_tool_engine_postgresql.py \
  tests/test_extraction/test_current_state_transactions.py \
  tests/test_extraction/test_document_run_execution_postgresql.py
```

结果：**11 passed，0 skipped**，耗时 6.03 秒。其中 8 项使用 PostgreSQL，3 项沿用 SQLite fixture：

| 测试范围 | 数量 | 数据库 |
|---|---:|---|
| Responses 在 pending_request 后暂停及关系组提交后暂停的冷继续 | 2 | PostgreSQL |
| `retrieve_evidence` 结果与授权引用同事务提交或回滚 | 1 | PostgreSQL |
| 两个独立连接竞争当前工作版本，仅一个成功 | 1 | PostgreSQL |
| 实际行锁等待后的租约时间复核、冷恢复准备不阻塞心跳、执行权竞争及旧执行者拒绝写入 | 4 | PostgreSQL |
| 独立结果数量不改变当前写入读取范围，以及当前写入的身份／版本约束 | 3 | SQLite |

脚本在 `finally` 中只删除自己创建的专用测试库。浏览器库和临时集群生命周期仍交由浏览器验证执行者管理。

### 最终接入复验

pending 原子修复后，同一范围首次复验为 **14 passed，0 skipped**（11 项 PostgreSQL、3 项 SQLite）。随后增加外部身份同 revision 追加的实际落库、精确核验目标和来源篡改反例，再次在唯一新建的专用测试库执行上述命令：**17 passed，0 skipped**，18.31 秒。其中 14 项 PostgreSQL、3 项 SQLite。本次仍采用当前 ORM schema + stamp head 夹具；唯一测试库在 finally 中删除，临时集群停止。

身份追加既核对冻结 external_link 的精确目标/hash/三个核验决定，也要求追加节点等于同一 lineage 已确认 finalized outcome 的节点。写入只允许纯身份字段追加，原根、类型、指称、revision、已有来源和决定均不得改变；旧协议仍保持原候选版本不可变。SQLite 正反例 **10 passed**，包含决定 revision/target/facet/support 与来源/类型/指称/根篡改；PostgreSQL 对正例及跨目标、来源篡改再次验证实际提交和冷读取。

最终受影响集合在 `backend/` 执行：

```bash
.venv/bin/python -m pytest -p no:cacheprovider -q -rs \
  tests/test_extraction/test_tool_*.py \
  tests/test_extraction/test_native_tool_protocol.py \
  tests/test_extraction/test_relationship_groups.py \
  tests/test_extraction/test_group_scope_scheduling.py \
  tests/test_extraction/test_ontology_guided_boundaries.py \
  tests/test_extraction/test_source_bound_units.py \
  tests/test_extraction/test_literal_normalizer.py \
  tests/test_extraction/test_current_work_resume.py \
  tests/test_extraction/test_document_current_state.py \
  tests/test_extraction/test_document_analysis_execution_recovery.py \
  tests/test_extraction/test_document_analysis_public_projection.py \
  tests/test_api/test_document_analysis.py
```

结果：**754 passed，6 skipped**，97.33 秒；6 项均因该批次未配置专用 PostgreSQL 而跳过，已由上述独立 PostgreSQL 批次实跑覆盖。该大集合在追加 3 项 PostgreSQL 身份测试前执行；后续没有将新增跳过数混算进去。该批次包括 Schema 纠错及冷恢复、补证后冷恢复、新旧评分器与 API 回归。

标准 Responses `reasoning` 透传后，真实 SDK/隔离 HTTP 传输测试 **16 passed**；默认省略该参数，不改变原请求形状。各批次测试存在重叠，计数不相加。

随后对应用/CLI 共用配置校验、冻结副本、完整请求 hash、工具编排、补证恢复与导入边界定向复验：**41 passed**。测试明确带 reasoning 的 wire 与已保存 input_hash 相等，移除此字段后 hash 必须改变；未知 effort/私有 budget 键/错误类型在冻结前拒绝。

真实 F4 正例第三轮暴露已登记实体 ID 被重复用作候选 local_id。冻结器继续拒绝此冲突；阶段校验增加同一命名空间检查，从已存原始回答派生 `entities.0.local_id` 等字段级反馈，允许在原有额度内修正。测试组合普通执行/冷继续与 Schema 错误/命名冲突，验证原始回答保留、请求预算不扩充、独立核验不继承发现纠错内容。在 `backend/` 执行：

```bash
.venv/bin/python -m pytest -p no:cacheprovider -q \
  tests/test_extraction/test_tool_engine_adapter.py \
  tests/test_extraction/test_tool_engine_recovery.py \
  tests/test_extraction/test_tool_engine_resume.py \
  tests/test_extraction/test_tool_engine_freeze.py \
  tests/test_extraction/test_native_tool_protocol.py
```

结果：**82 passed**，8.29 秒。随后仅补充 discovery instructions，明确 bridge_ref_ids 不接受 record_id/evidence_id；adapter 与对应测试的定向 Ruff 通过。真实新运行另见模型报告，不将上述受控返回测试视为真实校准成功。

继续核对发现，合法 JSON 的核验回答若缺目标、hash 错配或缺 facet，原来虽被最终校验拒绝，却不会进入预算内纠错。新增反例先复现 `verification_target_set_mismatch` 失败，再将已有集合检查提取为共享的 `validate_verification_targets`，阶段解析及最终核验共用。普通执行和冷继续均从已存原始回答派生字段反馈，冻结声明保持唯一；语义上的 unsupported/undetermined 不触发此类结构纠错。在上述五个测试文件之外加入 `test_tool_engine_verification.py` 与 `test_tool_engine_quantity.py`，定向结果：**153 passed**，9.51 秒。该批次包含正常/冷继续 × 缺目标/hash 错配/缺 facet 的 6 个新反例，不与前述计数相加。

真实 result04 进一步暴露 `_final_checks` 错读 `ShaclData.issues`。先以完整适配器数量正例重现 AttributeError，再按现有返回契约修复：SHACL 细节仍在 report/coverage/conforms，稳定错误放在外层 ToolResult.issues。4 个新增完整路径用例通过：5 mg 精确转换为 0.005 g 并入图；量纲不兼容、反证核查缺原文、pySHACL 技术失败均不写属性且正常保存结果/本地调用计数。发现提示明确当前任务主体，核验提示明确 counterevidence 需引用已核查原文，未改变语义规则。

在 `backend/` 执行：

```bash
.venv/bin/python -m pytest -p no:cacheprovider -q \
  tests/test_extraction/test_tool_engine_adapter.py \
  tests/test_extraction/test_tool_engine_verification.py \
  tests/test_extraction/test_tool_engine_recovery.py \
  tests/test_extraction/test_tool_engine_resume.py \
  tests/test_extraction/test_tool_engine_freeze.py \
  tests/test_extraction/test_native_tool_protocol.py \
  tests/test_extraction/test_tool_engine_quantity.py \
  tests/test_extraction/test_tool_runtime.py \
  tests/test_extraction/test_tool_runtime_recall.py \
  tests/test_extraction/test_ontology_guided_boundaries.py
```

结果：**207 passed**，14.30 秒。原始 4 个真实 ResponseTurn 的独立零请求复算另见 [复验摘要](fourth-run-offline-check-summary.json)，不改写原真实运行结果。

准备真实 Mock 接入时，发现模型可见的 query_instances 参数尚未列出合法 source_ids，原上下文也不提供此列表。回归先确认缺失枚举，再只在请求工具 Schema 中收紧 source_ids.items.enum 与 class_iri.enum；运行时继续独立核对同一权限集合。测试从模型实际可见枚举构造查询、返回冻结元数据，另验证越权来源拒绝和不同上下文不污染工具定义。对 `test_tool_runtime.py`、`test_tool_runtime_recall.py`、`test_tool_engine_tool_contracts.py`、`test_tool_engine_adapter.py`、`test_tool_engine_recovery.py`、`test_tool_engine_resume.py` 定向运行：**127 passed**，8.22 秒。

工具链探针中 Qwen 将 `context_text` 填为 `""`；正确的原文引用虽被拒绝，错误原先没有字段位置。新增空串及不包含 quote 两个反例先复现，再于同一工具入口返回 `/context_text` 并明确提示使用同源上下文或 null；不改写模型参数、不登记失败结果。工具参数描述同步说明成功返回的 mention_ref 和结果超额时缩小输入范围。再次执行上述六文件定向回归：**129 passed**，8.46 秒；没有新增真实 Qwen 请求。该检查对应 FR-17 的具体错误反馈，不新增实体、状态或自动重试流程。

独立真实工具链与 GLiNER 诊断分别见 [协议检查](tool-chain-probe01/protocol-checks.json) 和 [预算前输出诊断](tool-chain-probe01-diagnostics/diagnostic.json)。前者 4 次真实 Qwen 请求但工具均 blocked；后者 0 次 Qwen 请求、4 个真实 GLiNER 批次，完整结果 4,660 tokens 超过 4,096，仍有类型错误。没有修改预算或把原 handler 输出冒充返回给模型的结果。

[手工词表编译检查](manual-vocabulary-check.json) 仅读取冻结本体、卡片和显式 overlay；验证 25 条新增输入均属于卡片合法类、本体内容 hash 不变、别名保留手工来源，当前卡片由 2 个词条/25 个缺定义变为 27 个词条/0 个缺定义。此检查没有 Qwen、GLiNER 推理或图写入，原运行 manifest 没有变动。

## 静态检查

在 `backend/` 工作目录，对本次后端全部 71 个新增或修改的 Python 文件运行 `.venv/bin/ruff check --no-cache`，通过；工具探针反馈后的三个受影响 Python 文件另行复验通过。`git diff --check` 通过。设计检查使用已有系统 Python（应用 venv 缺少检查器锁定的 jsonschema 4.23.0，不为此调整应用依赖）：`python specs/027-ontology-extraction-engine-v2/check_design.py --self-test` 通过，最新覆盖 142 个本地链接、26 项无环任务与 22 个变异反例；本轮 README/engineering/手工词表说明另核对 39 个本地文件链接，均存在。测试输出中的 FastAPI／Pydantic 既有弃用警告未在本次范围内调整。
