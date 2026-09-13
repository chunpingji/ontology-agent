# 增强检索默认开启与部署记录

日期：2026-09-11（UTC）。用户授权：“新开关默认打开，并重新部署”。

已在本机重新部署后端和前端，入口为 http://localhost:8081 。新建文档分析运行默认
使用增强检索和 `heuristic-first-v4`。本次启用范围不包含尚未完成专家校准的低分剪枝。

## 生效配置

| 配置 | 默认值及本机有效值 | 行为 |
| --- | --- | --- |
| `DOCUMENT_ANALYSIS_EVIDENCE_REPAIR_ENABLED` | `true` | 开启证据修复前置能力 |
| `DOCUMENT_ANALYSIS_ADAPTIVE_RETRIEVAL_MODE` | `enhanced` | v4 搜索、增强视图、受控结构组召回、处置及费用审计 |
| `DOCUMENT_ANALYSIS_ADAPTIVE_CALIBRATION_PATH` | 空 | enhanced 不加载校准，不按低分剪枝 |

默认值同时写入 `backend/app/config.py` 和 `docker-compose.yml`。本机已有的语义排序
与证据修复配置保持启用，Compose 新增模式及校准路径的环境注入。

新增 `enhanced` 明确区别于保持 v3 顺序、请求及停止行为的 `observation`，并禁止携带
校准。`enforce` 仍要求专家审核且精确匹配模型/视图的制品，开发阈值和
`evaluation_only` 仍不能在线使用。没有伪造校准、修改本体或把质量门标为通过。

## 部署与旧运行

沿用当前基础 Compose + override 的源码挂载方式。后端无自动 reload，故创建新容器
以应用环境；前端为已安装依赖的开发服务，重启加载代码，无需构建或拉取镜像：

```bash
docker compose config --quiet
docker compose up -d --no-deps --no-build --pull never --timeout 60 backend
docker compose restart --no-deps --timeout 60 frontend
```

部署前发现 HRS-1597 运行 `352f8d3d-d305-4099-90ad-f5e20ec561c2` 正在执行。
通过现有应用控制服务、版本检查和控制回执请求暂停；首次遇到并发版本冲突未生效，
按仓储锁顺序重新读取最新版本后成功。确认暂停且无活动模型/标注任务后才重启。
部署验证后通过同一服务恢复，该运行已回到 `running`，指纹保持一致。

该旧运行冻结的是 `heuristic-first-v2` / state storage v2；恢复不会升级到新默认策略。
使用新策略需要创建新的分析运行。数据库、网关、模型服务及数据卷未重建，未创建新的识别运行。

## 实际验证

- 容器内新运行政策表达式产生 `heuristic-first-v4`、state storage v3、
  `adaptive_retrieval.mode=enhanced`、`calibration=null`、`evaluation_only=false`；
  在线适配器接受该配置，协议为 `evidence-repair-v1`。
- 容器内 9 个关键后端文件的 SHA256 与工作区一致；后端容器已重建，前端已重启，
  数据库与网关容器身份不变。
- 实际数据库 revision 与代码 head 均为 `0035_ranking_budget_control`。
  该结果来自数据库与 Alembic 脚本目录比较，不由健康接口推断。
- `/api/health`、`/login`、HRS-1597 报告页面及 `/openapi.json` 均返回 200；
  新公开 schema 包含剪枝数量及搜索状态，未认证图谱请求返回 401。
- 暂停后至恢复前，全部运行状态、制品头、制品数量及模型请求数量的快照严格一致；
  模型请求数保持 4756。恢复原任务后允许其继续正常产生进度和调用。

本轮定向测试：

| 范围 | 实际结果 |
| --- | --- |
| `test_adaptive_retrieval.py`、`test_incremental_performance.py`、`test_heuristic_search.py` | 65 passed（18.61 秒） |
| 文档 API、performance API、execution recovery、terminal progress、model call recovery、budget execution 六模块 | 最终扩展批次 57 passed / 1 failed；将原协议模拟响应显式固定到原策略后，失败用例单独复测 1 passed（2.27 秒） |
| 变更 Python 文件 Ruff、Compose 配置、diff 空白检查 | 通过 |

两个范围合计 123 个相关用例已分别通过；未声称最后一次六模块整批全绿。
新增验证覆盖默认配置、新运行冻结、无校准低分保留、增强组视图和任务中断恢复。
旧协议测试夹具显式冻结旧策略，原证明、覆盖和恢复断言未放宽。
沙箱内 API 测试曾停在 TestClient 本地线程唤醒，已终止；允许本地 IPC 后在隔离
SQLite / 模拟模型环境完成上述测试。此前工程验证见[实施记录](adaptive-retrieval-validation.md)。

本轮没有执行真实文档质量/性能评测或专用 PostgreSQL 并发验收，不宣称已经提速、
提高事实召回或通过专家质量门。HRS-1597/HRS-5592 仍为开发暴露样本。

部署快照、HTTP/运行策略/源文件 hash 检查、测试日志及控制脚本位于
`/tmp/ontology-adaptive-deploy-20260911/`；这些是本机验证制品，不作为独立保留集。

## 回退新默认值

在部署配置中持久设置 `DOCUMENT_ANALYSIS_ADAPTIVE_RETRIEVAL_MODE=disabled`，
重新执行上述后端 `up` 命令。新运行将沿用证据修复 v3 路径；已有运行保持其冻结策略。
若同时关闭证据修复，也必须关闭自适应模式，避免前置能力不一致导致新运行配置校验失败。

后续用户进一步要求开启低分剪枝，实际启用范围见[低分剪枝试运行记录](pruning-trial-deployment.md)。本文前述 enhanced 状态保留为历史部署事实。
