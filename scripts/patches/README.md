# 本地 llama.cpp 结构化输出修复（2026-09-17）

补丁：[`llama-cpp-b9853-responses-schema.patch`](llama-cpp-b9853-responses-schema.patch)。
适用基线为 llama.cpp `b9853-7af4279f4`，部署源码位于 `/opt/llama.cpp`，
模型为 `Qwen3.6-35B-A3B`。这是本地安装补丁，尚未提交上游；升级或重新构建模型服务时需要重新核对。

## 原因与改动

1. Responses 转换层没有把 `text.format` 转为下游读取的 `response_format`，导致请求中的 JSON Schema 没有进入约束解码。补丁补齐转换与格式校验。
2. 应用原来只在强制回答轮次携带 Schema；`tool_choice=auto` 也可能直接回答。
   `backend/app/services/extraction/ontology_guided/tool_model_adapter.py` 现在每轮都发送当前阶段的 Schema。
3. llama.cpp 原有 Schema 分支会覆盖工具分支。补丁允许符合 Schema 的回答或合法工具调用，
   遵循 `auto` / `required` / `none`；工具分支必须实际调用工具，并限制调用前的正文为空白，避免无约束正文绕过 Schema。

原有实体、谓词、证据枚举以及应用层严格解析和事实核验保持生效。格式约束不保证事实正确，也不能消除推理超时或输出预算耗尽。

## 应用和构建

在上述干净基线检查并应用补丁：

```bash
git -C /opt/llama.cpp apply --check /opt/dev/chen/ontology-agent/scripts/patches/llama-cpp-b9853-responses-schema.patch
git -C /opt/llama.cpp apply /opt/dev/chen/ontology-agent/scripts/patches/llama-cpp-b9853-responses-schema.patch
```

已应用的安装可用 `git apply --reverse --check` 校验。当前服务通过 PM2 进程 `qwen3.6-35b`（部署时 ID 27）管理。
编译前必须暂停使用此模型的任务并停止进程，避免覆盖进程正在加载的共享库。

本次标准 `llama-server` 构建遇到独立 UI 构建问题：Node 20.18 不满足 UI 依赖要求，预构建 UI 嵌入也失败。
本次保留原有 `libllama-ui.a`，在既有 CUDA12 构建目录中重建测试及受影响的服务链接目标：

```bash
/root/anaconda3/envs/cuda12/bin/cmake --build /opt/llama.cpp/build-cuda12 --target test-chat -j4
gmake -C /opt/llama.cpp/build-cuda12 -f tools/server/CMakeFiles/llama-server-impl.dir/build.make tools/server/CMakeFiles/llama-server-impl.dir/build -j4
gmake -C /opt/llama.cpp/build-cuda12 -f tools/server/CMakeFiles/llama-server.dir/build.make tools/server/CMakeFiles/llama-server.dir/build -j4
```

以上是当前安装的部署记录，不是通用全新构建流程。未修改模型权重或生产推理配置。

## 实际验证

- 后端 `test_tool_engine_adapter.py`、`test_native_tool_protocol.py`、`test_tool_engine_turn_plan.py`、`test_tool_engine_resume.py`：**124 passed**，4 条既有警告。
- 两个受影响 Python 文件定向 Ruff 检查通过。
- llama.cpp **完整 `test-chat` 通过**，覆盖 Responses 转换、Schema 与工具组合、思考内容，以及缺必填字段、错误类型、额外字段和普通文本拒绝。断言使用 Release 构建下仍生效的 `assert_equals`。
- 真实模型四个合成探针通过：Schema 回答、auto 工具轮次、显式工具查询、工具结果后的 Schema 回答。auto 允许模型选择合法工具调用。
- 应用生成的完整 discovery / verification 嵌套 Schema 真实模型验证通过，分别耗时 28.546 / 54.205 秒；严格 Envelope 解析通过。
- 合成探针为控制验证耗时关闭 thinking；生产配置保持原样。这些耗时不是原文档端到端性能对比，也不代表全文事实质量评测完成。

## 部署

2026-09-17 UTC 已更新模型服务并重启 `ontology-agent-backend-1`，两个健康接口均返回 200。
实际数据库 revision 与代码 head 均为 `0041_template_engine`。

重启前，目标文档运行 `ce448621-9c47-4de0-bc7f-202315d9c4b3` 通过既有业务控制服务暂停，
92 次模型调用均已结算。只读核查确认模型队列为空、文档执行租约均已撤销；
741 条旧 `running` 抽取记录没有执行头或有效租约，未清理或修改这些历史记录。

后端部署后于 11:21 UTC 使用同一运行的 `resume` 操作恢复当前工作状态。
恢复后的第 93 次调用已完成：生产 thinking 配置下返回 discovery 回答，
`DiscoveryEnvelope.model_validate_json(..., strict=True)` 校验通过，
调度器已发起第 94 次 verification 调用。第 93 次输入 8997 tokens、输出 11141 tokens（含思考），
不能据此宣称单次推理延迟已消除。

没有重建运行、清空预算或回放历史执行。完整文档分析仍在继续。
