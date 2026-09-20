# 031 调用效率优化部署记录

2026-09-19，用户明确要求“请重新部署”。本机后端已重新部署，优化默认对新建运行启用。

## 执行

实际部署使用 `docker-compose.yml` + `docker-compose.override.yml`。当前配置哈希与容器一致，后端源码已挂载，依赖未变化，复用现有镜像：

```bash
docker compose -f docker-compose.yml -f docker-compose.override.yml restart --no-deps --timeout 45 backend
```

后端启动时间为 **2026-09-19 14:22:55 UTC**，容器状态 `running`。数据库、前端、网关和宿主模型服务未重启；没有重建镜像、清理卷或重跑原文档。

## 实际核验

- 后端直连及网关 `/api/health` 均正常，加载 10 个本体模块；网关 `/analysis?tab=document` 返回 HTTP 200。
- 容器中 `freeze_tool_engine_policy()` 实际返回 `record-entity-first-v1`、`record-discovery-v2`、`table_reading=bounded-table-rows-v2`，`max_table_rows_per_group=4`；原每任务 4 次调用预算保持不变。
- 6 个优化实现文件的容器/工作区 SHA256 完全一致：`executor.py`、`record_search.py`、`record_model_adapter.py`、`tool_model_adapter.py`、`table_reading.py`、`record_discovery.py`。
- `alembic current`、数据库 `alembic_version` 与容器代码 `alembic heads` 均为 `0041_template_engine`；没有新增迁移。
- 已配置本地主模型的 `/models` 返回 HTTP 200，包含当前配置的模型。
- 语义排序实际开启，模式为 `semantic`。既有工厂的制品哈希与 CUDA 探测通过：`model_available=true`、`unavailable_reason=null`、设备 `cuda:0`、精度 `float16`；没有执行 embedding、rerank 或文档生成请求。

## 运行状态与使用

重启前没有活动文档租约、模型请求或旧 annotation 租约。前后核对 7 个未删除运行，其执行状态、阶段、revision、work/request/control version 和 event_head 均保持一致；重启后活动模型请求仍为 0。

原运行 `466e2822-2276-415d-8c04-5f2520cb3e08` 保持 `paused`，原暂停原因为 `execution_model_call_budget_exhausted`。未修改其冻结策略、预算或结果。

刷新文档分析页面后**新建运行**使用本次优化。部署和生效核验已经完成；真实文档的模型质量和耗时对照尚未执行，不能由健康检查或受控工程用例推断提升幅度。工程验证见 [quickstart.md](quickstart.md)。
