# 030 重新部署记录

2026-09-19，用户明确要求“请重新部署”。当前工作区已部署到本机应用，四项功能对新普通文档分析运行默认启用。

## 执行

实际容器使用 `docker-compose.yml` + `docker-compose.override.yml`。配置哈希与运行容器一致，前后端代码和权威 TTL 均已挂载，依赖没有变化，复用已有镜像：

```bash
docker compose restart --no-deps --timeout 45 backend frontend
```

| 服务 | 本次启动时间（UTC） | 核验 |
|---|---|---|
| frontend | 2026-09-19 06:47:18 | running，网关分析页面及实际脚本正常 |
| backend | 2026-09-19 06:47:22 | running，健康正常，10 个本体模块加载 |

数据库、网关和宿主模型服务未重启；未重建镜像、清理卷、删除运行或提交 Git。网关健康接口与文档分析页面均返回 200。

## 功能实际生效

- 容器解析器版本为 **7**；14 个关键后端文件的 SHA256 与工作区完全一致，覆盖目录、阅读组、字段消歧、普通属性/身份键门禁及当前状态。
- `freeze_tool_engine_policy()` 返回 `record-entity-first-v1` 和默认 `contextual-discovery-v1`：单节 240 字、组 1200 字、最多 8 条记录、最多 8 个候选对、最多两轮消歧。旧运行的冻结策略未升级。
- 在线 OpenAPI 已含 `property_disambiguation` 调用类型、字段状态数组及 `active` 工作状态。
- 通过网关实际读取 `/analysis?tab=document` 及其 26 个脚本，确认包含字段消歧列表、专用阶段及暂停状态展示。
- 使用既有 QA 登录入口进行只读核验，令牌不输出、不保存；CMCReport 元数据列表实际为 **8 个属性**，其中编号和密级均为 **published/string/CMCReport**。
- 对正在运行的 World 执行 SPARQL SELECT，确认两新增属性共 **12 条声明**。没有在独立进程加载/删除共享 World。
- 数据库 `alembic current` 与代码 `alembic heads` 均为 **0041_template_engine**；不以健康接口代替迁移核验。

## 原有数据保持情况

部署前无 queued/running/pausing 文档分析运行，无活动模型请求。旧抽取表的 running/pending 状态均为历史记录，annotation 执行无有效租约；未清理这些状态。

部署前两个新增属性尚不存在；启动时按现有 TTL 投影补入。没有调用会聚合其他草稿的工作台发布接口。部署前后逐表摘要确认 **8 个未发布本体对象完全一致**：2 个类、1 个数据属性、1 个动作、4 个限制。

部署前的 **8 个分析运行**，状态、阶段、fingerprint、revision、work/request/control version 和 event_head 的逐运行摘要在部署后全部保持一致；检查期间无新增运行。未主动发起真实模型分析。

## 手工测试

打开 [文档分析](http://localhost:8081/analysis?tab=document)，刷新页面后新建普通文档分析运行，使用带目录、短字段章节以及编号/密级的 CMC 报告验证。继续旧运行仍使用其原冻结策略和本体快照。

工程验证与场景见 [quickstart.md](quickstart.md)。部署与生效核验不代表 T32 的真实模型质量验收已完成。

## 实体优先调度修复部署

2026-09-19 手工测试发现运行 `1f8454de-d146-4be4-beea-702c3108ecad` 在前 12 项后仍只有根节点。只读核验表明模型请求没有被禁用：普通发现轮实际暴露 `get_schema_card`、`inspect_evidence`、`resolve_source_anchor`、`propose_mentions`、`query_instances`、`find_referent_candidates`、`retrieve_evidence` 七个工具，但模型选择直接回答。根因是冻结的 v1 调度先连续准入字段消歧；该运行暂停前已有 15 个字段工作项，普通记录实体发现尚未开始。

修复新增 `contextual-discovery-v2`：字段仍在类卡扩散前识别并去重，执行时先做阅读组普通实体发现，再做对应字段消歧；前置纯字段最多延后至下一普通组。v1 分支完整保留旧游标含义，避免活动运行重启后跳过或重复任务。

部署前通过现有控制接口请求暂停，活动请求正常落账后状态为 `paused`，`model_calls_reserved=18`、`model_calls=18`、`model_calls_unresolved=0`。随后只重启 backend：

```bash
docker compose restart --no-deps --timeout 45 backend
```

- backend 启动时间为 **2026-09-19 07:33:09 UTC**，状态 running。
- 容器内新运行冻结策略实际为 `record-entity-first-v1` + `contextual-discovery-v2`。
- 重启后的首次核验中，原运行保持 `paused`，冻结策略仍为 `contextual-discovery-v1`，未改写已付响应。随后该运行在 **07:34:57 UTC** 收到带完整删除回执的显式删除请求并转为 tombstone；这不是部署或保留期清理造成的状态变化，现已不能继续。
- 后端直连及网关 `/api/health` 均返回 200，加载 10 个本体模块。
- 数据库 `alembic current` 与代码 `alembic heads` 均为 `0041_template_engine (head)`。
- 修复相关回归 **124 passed**，受影响文件 Ruff 与 `git diff --check` 通过。

人工复测须新建运行，旧 v1 运行不会自动升级为 v2。
