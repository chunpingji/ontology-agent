# 验证

复用 backend/.venv 和当前隔离 fixture，不读取密钥或修改上传原件。

1. 工具结果：同跨度多角色、同字不同位置、未处理单元及超限拒返。
2. 引用：往返、未知/碰撞拒绝、原文不变、成员权限、函数参数及恢复不重付。
3. Schema：空数组约束、引用完整、可达定义；本地端点结构化回答及工具往返。
4. 卡片：同 IRI 不同类型约束、身份键、范围及发现候选不丢失。
5. 分词：固定规范请求比较实际模型视图，只输出大小统计。

## 工程回归（2026-09-19）

从 `backend/` 运行 `.venv/bin/python -m pytest -p no:cacheprovider -q
tests/test_extraction/<文件名>`。各文件独立进程运行，避免既有同名 `source` fixture
在合并收集时相互覆盖。定向覆盖如下：

| 范围 | 文件 | 结果 |
|---|---|---:|
| 提及投影及预算边界 | `test_tool_result_projection.py` | 6 passed |
| 短引用、分片回答、新候选编号 | `test_model_reference_projection.py` | 23 passed |
| 实际发送、计量、哈希、工具继续 | `test_compact_model_transport.py` | 3 passed |
| 非法短引用热/冷恢复 | `test_model_reference_recovery.py` | 11 passed |
| 卡片及上下文投影 | `test_model_context_projection.py`、`test_model_context_card_projection.py` | 20 passed |
| 工具派发和提及召回 | `test_tool_runtime.py`、`test_tool_runtime_recall.py` | 72 passed |
| 单任务/批量 adapter | `test_tool_engine_adapter.py`、`test_batch_model_adapter.py` | 90 passed |
| 单任务补证恢复 | `test_tool_engine_recovery.py` | 13 passed |
| 记录/批量回答纠正 | `test_record_answer_corrections.py`、`test_batch_answer_corrections.py` | 30 passed |
| 记录/关系/上下文 adapter | `test_record_model_adapter.py`、`test_record_relation_adapter.py`、`test_contextual_record_adapter.py` | 34 passed |
| 记录/批量执行器 | `test_record_executor.py`、`test_batch_executor.py` | 52 passed |
| 已付费结果持久化 | `test_tool_engine_resume.py`、`test_batch_current_state.py`、`test_record_correction_persistence.py` | 65 passed |
| 配置及共享核心边界 | `test_tool_engine_configuration.py`、`test_ontology_guided_boundaries.py` | 28 passed |

Schema 测试在默认环境为 5 passed / 1 skipped（缺少可选 `jsonschema`）；复用本机已有包补跑，
6 passed，无新增依赖：

```sh
.venv/bin/python - <<'PY'
import sys
sys.path.append('/root/anaconda3/lib/python3.12/site-packages')
import pytest
raise SystemExit(pytest.main([
    '-p', 'no:cacheprovider', '-q',
    'tests/test_extraction/test_model_schema_projection.py',
]))
PY
```

定向 Ruff 使用 `.venv/bin/ruff check --no-cache <本次源码和测试>`；差异空白检查
使用 `git diff --check`。没有用 SQLite 测试代替 PostgreSQL 锁/并发验收。
上述不同测试合计 **453 passed**（包括复用已有 `jsonschema` 后补跑通过的用例，不重复累计重跑）。

## 固定请求体积

固定原运行一份核验请求，使用当前本地模型词表的 `ServerTokenizer.count(canonical_json(request))`：

| 累计优化 | tokens |
|---|---:|
| 保存的原请求 | 15,659 |
| 提及分组（此请求没有相关工具结果） | 15,659 |
| 请求短引用 | 10,647 |
| Schema 精简、移除副本，保留短字段说明 | 10,266 |
| 核验卡片裁剪 | **7,985** |

最终减少 **7,674 tokens（49.01%）**。原文和任务数据不变；卡片从 4 类缩至 1 类。
该计量针对固定请求 JSON，不等于服务端聊天模板处理后的 `usage.input_tokens`；没有用它
宣称整份文档的平均成本或识别准确率改善。

独立合成提及样本（6 单元、18 个物理提及、54 个角色）完整结果 **10,084 → 分组 4,409 →
短引用 2,601 tokens**，展开后各字段相同。真实失败的超限完整结果未保存，无法计算其压缩率。

脚本 `/tmp/compact-context-token-measurement-20260919.py --reuse-snapshot-only` 强制使用
同一冻结请求，原请求哈希 `186552aa9db9771a020e9221203795ffacc6e0ec421e2420c865c36563980769`。
统计在 `/tmp/compact-context-token-measurement-20260919.json`；仅调用 `/props` 和 `/tokenize`，
没有执行推理请求。

## 当前模型端点

本机 llama.cpp 已应用 Responses Schema 补丁；实际 `test-chat` 通过。
3 次合成探针均通过 `responses_create` 和共享调度执行，使用 `strict=false` 回答 Schema：

- `auto`：即使提示要求 Markdown、漏 required 字段、额外字段和非空禁用数组，仍返回合法回答。
- `required`：Schema 与合法工具调用共存。
- `none`：读取真实工具往返数据后返回符合 Schema 的回答。

探针运行 `compact-context-probe-20260919-2b59f945`；总输入 1156、输出 107 tokens。
探针客户端单独关闭 thinking，生产配置不变。日志 `/tmp/compact-context-live-probe-20260919.log`。

首个真实 adapter 合成运行 `d491bb75-e1c6-4aa6-b58c-424300d3728b` 的新候选编号误用
`@r:`，被引用门禁拒绝；已付费结果保存，未进入核验，未产生事实。该结果不能算端到端通过。
据此在模型输出 Schema 中明确新候选编号格式，并使用剩余受限预算重新验收。
第二个运行 `6318e404-2d8b-4e70-913f-ee7aed43b84e` 正确还原引用，但把明确属性值放入
`observations(kind=unbound)`，结果保持未决。随后补充简短的属性/核验字段说明，
不恢复完整 Schema 文本副本。两个运行分别使用 1 次模型生成。

运行 `ad669132-354c-4a13-8a8c-caf56088879a` 在同一原文上使用 2 次生成：发现成功，
核验回答在验收脚本临时设置的 4096 token 输出上限处截断，无法完成 JSON 校验；
输入 3774、输出 7801 tokens。生产输出上限实际为 20480，因此另按生产配置启动
最多两次生成的验收；不把截断计为核验语义失败，也不宣称本轮完整通过。

最终生产预算运行 **`998ffb6e-2920-4777-9a2e-04b0ca98b0dc` 通过**：沿用同一合成原文
“样品甲的质量为 5 mg。”及本体，真实 adapter 完成发现、独立核验和确定性门禁。
`complete=true`、`fact_passed=true`、`record_supported`，属性规范值 `5 mg`，主体及原文引用
正确还原为服务端完整身份。8 个核验 facet 均为 supported，binding/metric/shacl 各执行一次。
使用生产输入 32768、输出 20480 token 和 600 秒调用超时，单工具上限 8192；没有关闭 thinking。

| 阶段 | 实际调用次数 | 输入/输出 tokens |
|---|---:|---:|
| 发现 | 1 | 1772 / 4505 |
| 独立核验 | 1 | 2014 / 6707 |
| 合计 | **2** | **3786 / 11212** |

总耗时 190.167 秒，两份完整 JSON，两个共享调度账本均 complete。实际 wire 确认短引用、
唯一回答 Schema 和隐藏重复原文工具生效。产物位于
`/tmp/ontology-context-real-20260919/998ffb6e-2920-4777-9a2e-04b0ca98b0dc/`。
前面的低输出预算验收共 4 次生成；本次生产配置验收另使用 2 次，没有重写失败产物。

工程回归、模型协议探针和完整文档质量评测是不同验证范围。

## 服务加载

后端 `ontology-agent-backend-1` 已于 2026-09-19 重启加载。重启前文档、抽取、模型有效
租约及待运行文档数均为 0，没有对历史文档运行发出继续命令。
`/api/health` 返回 `status=ok`、`modules_loaded=true`、`module_count=10`。
数据库 revision 与代码 head 均为 `0041_template_engine`。
运行配置确认输入 32768、输出 20480 tokens；单工具结果上限 **8192 tokens**。
四项优化均默认生效，没有新增开关；未修改前端或公共 HTTP 契约。
