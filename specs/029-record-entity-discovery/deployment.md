# 029 重新部署记录

2026-09-19，按用户“请重新部署，我来手动测试”的授权，将当前工作区重新部署到本机应用。**新普通文档分析运行已启用 `record-entity-first-v1`，手工测试进行中；T32 正式质量验收仍待完成。**

## 执行

沿用运行容器实际使用的 `docker-compose.yml` + `docker-compose.override.yml`。前后端均挂载工作区源码，依赖及容器配置没有变化，复用现有镜像：

```bash
docker compose restart --timeout 45 backend frontend
docker compose restart --timeout 30 web
```

| 服务 | 启动时间（UTC） | 核验 |
|---|---|---|
| frontend | 2026-09-19 05:18:53 | running，分析页面及实际脚本可访问 |
| backend | 2026-09-19 05:18:55 | running，健康接口正常，10 个本体模块已加载 |
| web | 2026-09-19 05:19:49 | running，网关健康及分析页面均为 200 |

数据库和宿主模型服务保持运行；未清理数据卷、历史运行或结果，未提交 Git。

## 生效核验

- 容器中 `freeze_tool_engine_policy()` 返回 `recognition_pipeline=record-entity-first-v1`、`record_discovery={version:record-discovery-v1,max_classes_per_card:4,endpoint_page_size:8}`。工作状态版本 4、调用状态版本 3、谓词批次最多 4 个成员。
- 在线执行窗口沿用现有 **32 次模型请求 / 18,000 秒** 配置；此前对照中的每组 8 次仅是独立评测预算。
- 实际运行的 OpenAPI 已包含 `HarnessCall.task_kind=record_discovery`。前端实际返回的 26 个脚本中包含“识别记录中的实体和属性”“核验实体和属性”和逐类型 `class_cards` 展示。
- 六个关键后端文件及两个前端文件的容器 SHA256 与工作区一致，包含最后的焦点路径、别名分组和分页语义修复。
- 部署前后 `alembic current` 和 `alembic heads` 均为 **`0041_template_engine`**；独立核对迁移版本，不以健康接口代替。
- 检查期间于 **05:20:43 UTC** 新增运行 `34d2b68e-6bc2-45dd-8dc4-45638e42e765`；其实际 source 制品已冻结 `record-entity-first-v1` 和完整默认记录策略，检查时处于 `running / metadata`。后续仅执行只读核验，保留任务继续运行；尚未据此声称实体识别或质量验收通过。

## 原运行保持情况

重启前没有 queued/running/pausing 文档分析运行，活动模型请求为 0。一个历史 `active` 租约已过期，其公共运行状态为 paused，无需中断模型。

重启前的 10 条运行，在重启后按原创建时间范围核对，其状态、阶段、fingerprint、revision、work/request/control version 和 event_head 摘要保持一致：`bfeb656661eb987368ae5d4e258b5335727e9f599eabc305009734ae237f8661`。

部署前运行、请求、结果及制品 head 数量分别为 **10、170、432、74**。检查期间新测试运行出现后，总运行数为 11、制品 head 为 78，并出现活动模型请求；这是新增测试运行的进展，未将全库计数变化误报为原运行被改写。

## 手工测试入口

访问 [文档分析](http://localhost:8081/analysis?tab=document)，新建普通文档分析运行以测试 029。已有运行保留其原冻结策略；模板来源的运行继续现有模板流程。工程验证及有限模型对照见 [implementation.md](implementation.md)，正式质量验收状态见 [tasks.md](tasks.md)。
