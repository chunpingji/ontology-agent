# 032 部署记录

日期：2026-09-19。用户在工程实现交付后明确要求“请部署”；本机部署与生效核验已完成。

## 执行范围

沿用本机实际运行的 `docker-compose.yml` + `docker-compose.override.yml`。容器配置哈希与当前 Compose 配置一致，前后端源码已挂载，依赖未变化，因此复用现有镜像：

```bash
docker compose -f docker-compose.yml -f docker-compose.override.yml restart --no-deps --timeout 45 backend frontend
```

| 服务 | 启动时间（UTC） | 结果 |
|---|---|---|
| backend | 2026-09-19 09:44:06 | running，直连和网关健康检查均为 200 |
| frontend | 2026-09-19 09:44:02 | running，开发模式源码挂载 |

数据库、网关和宿主模型服务未重启；未重建镜像、清理数据卷、删除运行或提交 Git。

## 后端生效核验

- 新运行实际冻结 `record-discovery-v2`：每组最多 2 张候选卡，最低相似度 0.25；当前本地语义排序已开启，模式为 `semantic`。
- 新运行实际冻结 `attribute_calibration=source-observations-v1`，字段最多两次消歧。
- 在容器内使用合成输入验证：`2026年02月` 解析为 `2026-02` / `xsd:gYearMonth` / 月精度；要求 `xsd:date` 时保留 `datatype_mismatch`，不补造日期。字符串编号 `00123` 保留前导零。
- 在线 `/openapi.json` 含 `GraphArtifactResponse.attribute_candidates`、候选原值/解析值/原因/原文引用和确定性值契约。
- 21 个相关后端文件 SHA256 与工作区一致。
- 后端直连及网关 `/api/health` 均返回 200，加载 10 个本体模块。
- 数据库 `alembic current`、`alembic_version` 与代码 `alembic heads` 均为 `0041_template_engine`；本次没有新迁移。

## 数据与运行

重启前无 queued/running/pausing 文档任务，无活动模型请求，文档和旧 annotation 执行均无有效租约。

部署前后的 7 个既有运行，状态、阶段、fingerprint、revision、work/request/control version 和 event_head 摘要保持一致：`1869a0354ff380e31de2ef3cec55f5ec`。部署检查未发起真实文档分析或模型请求。

## 前端入口核验

普通 `/analysis?tab=document` 使用 `DocumentRelationshipGraph`。本次将候选列表抽为共享 `attribute-calibration-list.tsx`，接入普通文档和模板两个入口；候选显示独立于实体/事实列表，原文按钮沿用各入口的授权来源定位回调。前端开发服务通过源码热更新加载了该修复，无需再次重启后端。

- 09:50:41 UTC，实际页面 HTTP 200，26 个引用 JS 全部 HTTP 200，无 500。
- 实际 `/_next/static/chunks/src_14ye3pt._.js` 含候选列表、解析值、类型、精度、字段/原值按钮、原文引用和“不用于推理/报告”文案；普通文档入口实际传入 `onSelectionRef`。
- 候选展示 5 项、图谱 9 项、性能状态展示 10 项，共 24 项前端定向用例通过；Node CLI 汇总为 3 个文件通过。
- 前端 TypeScript、涉及文件 ESLint 及 `git diff --check` 通过。

本次前端定向回归命令（在 `frontend/` 执行）：

```bash
node --test tests/attribute-calibration.test.mjs tests/document-graph.test.mjs tests/template-document-performance.test.mjs
```

## 使用与限制

刷新 [文档分析](http://localhost:8081/analysis?tab=document)，新建运行以使用新策略。部署未重跑或改写旧运行。工程验证见 [quickstart.md](quickstart.md)；部署检查不代表真实模型的识别准确率与耗时验收。
