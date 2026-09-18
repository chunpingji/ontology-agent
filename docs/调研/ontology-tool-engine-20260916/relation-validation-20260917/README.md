# 关系校验分工实施与验收（2026-09-17）

任务：T32。模型提出候选并判断原文语义；关系必须经过模型实际调用的确定性工具校验，最终由 Harness 根据服务端结果决定能否接纳。

## 问题与修复

运行 `31246dbe-1da8-4e7b-a8de-069363ea79f5` 的问题关系实际使用 `hasDegradationPathway`（含降解途径）。现有本体允许 `DegradationPathway` 作为其主体和对象，`OxidativeDegradation` 是该类的子类。因此菜单允许这一类型组合，并不能证明文档陈述了两个氧化降解实体之间的关系。原运行出现了相同原文位置和指称对应多个实体的情况。

此前关系仅有 binding 必检，模型可以直接提交语义回答；`validate_graph` 只供控制器检查属性表示。现在按以下边界执行：

1. **冻结候选**：同类型且 grounding mention 的精确原文锚点集合完全相同，视为重复提及，拒绝重复实体及依赖它的关系。已在此阶段拒绝的候选不进入工具核验。不同位置的同名实体、相同支持段落或同类实体不据此合并。
2. **调用工具**：对进入核验的冻结关系，缺少当前有效结果时，模型只能调用 `validate_graph`，使用 `tool_choice=required`。按单响应工具上限分批，并预留语义回答轮；调用与 HTTP 请求都计入已有预算，默认四次上限不变。预算不足或模型一直直接回答时保留未完成。
3. **确定性校验**：固定 `ontology-relation-v1` 检查任务主体、菜单谓词、range、来源绑定和精确提及身份，分别返回约束与身份状态。`semantic_status` 固定为 `not_checked`，工具通过不等于原文证明。这一分支不是完整 OWL 推理，也不声称运行了 SHACL。
4. **最终接纳**：校验结果必须匹配当前 claim/version、content hash、context hash、本体快照和菜单 hash。模型回答 `supported` 不能覆盖缺失、过期、失败或未完成的工具结果；原文语义证明仍独立必需。

属性规范化和 SHACL 沿用控制器路径。本次未修改本体公理，未增加非自反性、无环性等未声明约束，未新增依赖、外部服务、数据库迁移或权威状态存储。

## 实现位置

- [claim_identity.py](../../../../backend/app/services/extraction/ontology_guided/claim_identity.py) 与 [claim_freeze.py](../../../../backend/app/services/extraction/ontology_guided/claim_freeze.py)：精确提及检查与依赖阻断。
- [tool_contracts.py](../../../../backend/app/services/extraction/ontology_guided/tool_contracts.py) 与 [tool_runtime.py](../../../../backend/app/services/extraction/ontology_guided/tool_runtime.py)：类型化关系结果、阶段权限与校验。
- [tool_model_adapter.py](../../../../backend/app/services/extraction/ontology_guided/tool_model_adapter.py)、[verification.py](../../../../backend/app/services/extraction/ontology_guided/verification.py)：强制调用、真实结果消费、最终门与冷恢复。
- [回归测试](../../../../backend/tests/test_extraction/test_relation_validation_harness.py)：漏调、预算、失败、过期、重复提及与暂停继续反例。

结果正文仅保存于既有 `tool_result`，`materialized_refs` 增加 `relation_validation` 引用；`ToolContext.relation_checks` 是恢复后的派生视图。每轮检查批次放在当轮 instructions 中，完整会话 input/output 和 `call_id` 配对按既有协议续传；已确认工具在冷恢复时不重复执行。界面沿用实际工具观察，标签改为“本体与图谱约束校验”。

## 工程验证

最终实现的定向集合执行命令（`backend/`）：

```bash
.venv/bin/python -m pytest -p no:cacheprovider -q \
  tests/test_extraction/test_tool_engine_*.py \
  tests/test_extraction/test_relation_validation_harness.py \
  tests/test_extraction/test_tool_runtime*.py \
  tests/test_extraction/test_relationship_groups.py \
  tests/test_extraction/test_group_scope_scheduling.py \
  tests/test_extraction/test_ontology_guided_boundaries.py --tb=short
```

结果：**506 passed，9 skipped，4 warnings，64.82 秒**。随后新增直接反例“工具实际返回身份失败，但模型仍全部回答 supported”，执行：

```bash
.venv/bin/python -m pytest -p no:cacheprovider -q \
  tests/test_extraction/test_relation_validation_harness.py::test_model_supported_cannot_override_failed_relation_tool \
  --tb=short
```

结果：**1 passed，4 warnings，0.38 秒**。该反例确认没有关系入图，保留工具失败原因。合计验证 507 个不同用例；没有把新增用例后的全组测试数写成单次执行结果。

9 个跳过项需要专用可销毁 PostgreSQL 测试库，当前未配置 `DOCUMENT_ANALYSIS_TEST_DATABASE_URL`；SQLite 协调器和冷恢复通过不代替 PostgreSQL 锁/并发验收。4 类警告来自已有 Starlette/Pydantic 使用方式。

以下检查也已执行通过：

- 本次修改的 8 个后端模块与 8 个测试文件定向 Ruff。
- `frontend/` 下 `npm run lint -- src/components/analysis/document-harness.tsx`。
- `python specs/027-ontology-extraction-engine-v2/check_design.py --self-test`，包含工具目录、32 项任务依赖及反例检查。
- `git diff --check`。

## 真实模型工具协议

[原生探针摘要](native-probe-summary.json)使用 Qwen3.6-35B-A3B 和隔离合成文档。正例、重复实体反例各两次真实请求：必调工具一次，接收配对工具结果后返回其三个状态字段一次，共 **4 次请求、2,971 输入 token、1,290 输出 token**。

| 场景 | ontology_status | identity_status | semantic_status | 结果消费 |
|---|---|---|---|---|
| 合法候选 | passed | passed | not_checked | 通过 |
| 重复提及 | passed | failed | not_checked | 通过 |

这验证真实 `function_call` / `function_call_output` 往返和工具结论消费；未执行整份用户文档的抽取质量或 F1 验收，不能据此宣称全文精度达标。

## 部署状态

实施验收完成时未部署；随后用户明确要求重新部署，已于 **2026-09-17 15:12 UTC** 完成前后端更新。

当前使用基础 Compose 与本机 override，前后端源码均为 bind mount，本次无依赖或数据库迁移变更。部署前从后端实际数据库连接确认无 running/queued/pausing 文档任务、活动标注租约或待执行补证操作，执行 `docker compose restart backend frontend`；没有恢复原先暂停或失败的任务。

- 后端启动时间：`2026-09-17T15:12:32.579602824Z`；前端：`2026-09-17T15:12:28.189517742Z`。
- 网关 `/api/health` 返回 200、`status=ok`、`modules_loaded=true`、`module_count=10`；`/analysis?tab=document` 返回 200。
- 容器内 8 个受影响后端模块及前端 Harness 组件的 SHA-256 与工作区一致；导入核验确认关系 profile、强制调用门、精确提及检查和 `semantic_status=not_checked` 已就绪。
- 页面实际提供的 JavaScript 已包含“本体与图谱约束校验”标签。
- 启动后 Alembic current 与 heads 均为 `0041_template_engine`；数据库和网关未重启。
- 部署后活动文档任务数仍为 0，没有为验证部署新建模型任务。

新分工已部署，适用于后续核验。本次没有执行用户全文质量评测；既有运行已经提交的图谱未自动删除、合并或重新计算。
