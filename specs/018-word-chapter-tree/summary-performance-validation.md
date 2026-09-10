# 2026-09-10 分层摘要优化验证

本次修正减少分层摘要的重复输入和无效重试，并增加同层有限并发；原文、章节/页范围与证据结构不变。模型仍为本地 `Qwen3.6-35B-A3B`，没有更换权重或调整服务启动参数。

## 实际文档验证

- 原运行：`e6f51101-f2a5-4eec-b482-d41300856134`；使用其冻结 Word 原件，未修改原运行及制品。
- 新验证身份：`summary-opt-f28cb5a158d442798e5e064b1b31aca3`。
- 模型版本：`0b21525e972670ed59e1812e170b27c26355381f0656ecc4e25617ece7dac58b`。
- 摘要版本：`word-tree-summary-v2`；实测并发设为 1，默认最多 2 的并发行为由工程测试验证。
- 独立制品：验证时后端容器的 `/tmp/summary-opt-f28cb5a158d442798e5e064b1b31aca3/{section_tree,metrics}.json`；容器临时目录不是长期制品存储。

| 指标 | 原运行 | 优化后实测 |
|---|---:|---:|
| 摘要阶段墙钟耗时 | 约 305 秒 | 244.152 秒 |
| 各节点输入材料字符总数 | 59,060 | 30,932 |
| 逻辑批次 | 7 | 6 |
| 实际模型请求（包含失败尝试） | 8 | 7 |
| 向模型请求摘要的节点数 | 61 | 40 |
| 同范围单页摘要复用节点数 | 0 | 21 |
| 最终摘要来源为 LLM 的非空节点 | 60 | 61 |
| 最终自动摘录回退节点 | 1 | 0 |

输入材料减少约 47.6%；本次单并发耗时减少约 20%。原输入字符数来自冻结页/章节摘要与原实现材料构造的回放统计，不是 token 数。原耗时取持久化阶段事件，新耗时取独立摘要调用的墙钟时间。两次测试时间不同，且新验证期间共享服务仍有识别请求，因此不能将单次耗时差作为稳定速度保证，也没有量化同层并发的实际加速比例。

新验证的模型请求累计排队 0.129 秒。一次输出截断后在提高预算的第二次尝试中成功。最终状态为 57 个 `completed / llm`、4 个 `partial / llm`、1 个 `completed / empty`。部分状态来自一页输出超过字符上限及其向叶章节、父章节和文档根传播，未静默标为全部完成。原文件 SHA-256 与解析正文块在生成前后相同。

实测确认了摘要生成与性能变化，没有开展完整人工金标语义质量评测；模型摘要仍用于定位与展示，不能替代原文事实证明。

## 工程验证范围

定向测试覆盖摘要顺序、单页复用与来源范围、失败/截短页原文保留、否定与条件材料传递、同层并发上限、父层等待、运行身份、回调线程、取消关闭 HTTP、增预算重试及总超时。还检查文档运行恢复、共享模型等待、既有截断恢复、标注和预览相关路径。

所有测试从 `backend/` 使用 `.venv/bin/python -m pytest -p no:cacheprovider -q` 执行；以下为实际分组结果，组间存在重复测试，不相加为唯一测试数量：

| 测试文件（相对 `tests/`） | 结果 |
|---|---|
| `test_extraction/test_word_tree_summarizer.py`、`test_model_scheduler.py`、`test_evaluation_cmc_benchmark.py`，及 `test_api/test_word_tree_annotation.py` | 43 passed |
| `test_extraction/test_document_analysis_execution_recovery.py`、`test_model_wait_durability.py`、`test_systemic_regression.py`、`test_output_truncation_recovery.py`，及 `test_api/test_annotation_rerun.py`、`test_document_preview.py` | 99 passed |
| `test_extraction/test_document_summary_execution.py`、`test_model_scheduler.py`、`test_document_analysis_execution_recovery.py`，及 `test_api/test_document_analysis.py` | 最终 54 passed |

受影响源码与测试的 Ruff `check --no-cache` 和 `git diff --check` 通过。取消回归还发现并修复了终止时把公开 `preparing_metadata` 错存为内部阶段的问题，现映射回 `metadata`。一轮复验中既有 150 ms 总超时测试在 HTTP 请求开始前耗尽预算，未放宽断言，最终复跑通过。

工程验证结束时未重启后端，随后按用户明确授权完成下述部署。已冻结摘要仍按原制品恢复，不因代码更新而重新生成。

## 授权部署与恢复验证

2026-09-10（UTC）使用现有开发 Compose 配置和 CUDA 镜像，仅重启源码挂载的后端：`docker compose restart --timeout 60 backend`。无需重建镜像，未重启数据库或删除数据卷。

- 部署前通过运行控制服务请求暂停原运行；02:49:04 已确认 `paused`，保留 27 项已处理任务、38 次识别调用，事件水位 281、制品版本 553。
- 容器于 02:49:16.747 UTC 重新启动。新配置为 `word-tree-summary-v2`、摘要并发上限 2、共享模型并发上限 2。
- 代理入口 `/api/health` 返回 `status=ok`、`modules_loaded=true`、10 个模块；原报告页面入口返回 HTTP 200。
- Alembic current 与 heads 均为 `0035_ranking_budget_control`。
- 通过审计控制操作恢复同一个运行，执行租约 generation 为 4。02:55:49 检查时事件水位已到 284，重启后新发现调用已完成，核验调用正在执行，运行无错误或依赖阻断。

维护控制请求键为 `deploy-summary-v2-20260910-pause` 和 `deploy-summary-v2-20260910-resume`，原因明确记录用户授权部署及健康检查后的恢复。当前运行继续使用原冻结摘要；后续生成摘要使用新版本。
