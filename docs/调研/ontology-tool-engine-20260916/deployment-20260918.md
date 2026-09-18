# 2026-09-18 重新部署

根据用户“请重新部署”的明确指令，已部署指代/共指消解、关系主体证据核验和连续执行预算相关代码。前端于 00:39:27 UTC、后端于 00:41:53 UTC 重启完成，入口为 `http://localhost:8081/analysis?tab=document`。

## 执行范围

沿用 `docker-compose.yml` + `docker-compose.override.yml`、CUDA 后端和 Next.js 开发模式。前后端源码均通过 bind mount 加载。后端容器内 CUDA 依赖锁与工作区一致，前端已安装依赖与 package-lock 核对无差异，因此本次复用镜像、重启进程加载代码，没有重建镜像或刷新匿名卷。

```bash
docker compose -f docker-compose.yml -f docker-compose.override.yml config --quiet
docker compose -f docker-compose.yml -f docker-compose.override.yml restart -t 60 frontend
docker compose -f docker-compose.yml -f docker-compose.override.yml restart -t 60 backend
```

后端重启前，发现 `bbb6814f-97ca-4aae-bddd-a0dc23289d6a` 仍处于 running。通过现有 `DocumentAnalysisRunStore.request_control` 发出带 control-version 检查和幂等键的暂停操作，等待当前模型调用完成并保存；确认运行进入 paused、活动模型请求数为 0 后才重启后端。

该旧运行保留 paused，暂停原因 `operator_pause`，revision 为 2777、work_version 为 192、request_version 为 2042、control_version 为 1。没有自动继续、替换冻结策略或重新计算图谱。数据库、Nginx 容器和宿主模型服务未重启；`ontology-agent_pgdata`、`ontology-agent_backend_data` 保留。

## 核验结果

| 检查 | 本次结果 |
|---|---|
| 后端与前端容器 | running，restart_count 均为 0 |
| 后端 `:8000/api/health` | HTTP 200，10 个本体模块已加载 |
| 网关 `:8081/api/health` | HTTP 200 |
| `/analysis?tab=document` | HTTP 200 |
| 页面实际返回的 JavaScript | 包含连续运行时间预算和模型请求预算两种新暂停提示 |
| `alembic current` / `alembic heads` | 均为 `0041_template_engine (head)` |
| 新运行策略 | `reference_resolution_version=1`，1800 秒 / 32 次识别请求，lineage 上限仍为 4 |
| 工具注册 | 11 项，10 项具备模型可调用资格；`find_referent_candidates` 定义和 handler 均已注册，实际可见集合仍依赖阶段和配置 |
| 容器源码 | execution、reference_context、source_assertions、tool_contracts、tool_runtime、verification 及前端暂停文案的 SHA256 与工作区一致 |
| 重启前后数据比较 | 安全暂停后的全部运行状态摘要、旧运行冻结策略、请求账本及制品 head 摘要均未变化 |

表数量也保持一致：document_analysis_runs 15、requests 30,678、results 24,270、current_state 217,075、artifact_heads 112、extraction_jobs 773、generated_reports 154。

| 部署制品 | SHA256 |
|---|---|
| 后端镜像（复用） | `fbc23146d1b6f501478439d41bbc5f70cceda82c8aa53f4899ffce0ecb990072` |
| 前端镜像（复用） | `da2b07766badc45a989f25d2a51f42fb415ac5246e4816643f94f9771e49fead` |
| CUDA 依赖锁 | `ce27ce9278af8f2f392884d5fb1fae48d61ef38301dbe77e7b11333c0bf7789c` |
| 后端执行入口源码 | `2bb963b96953d540347477f248f92c3b0bf7d243cdba4fb30336de542cb83027` |
| 工具运行时源码 | `579be058ad6cecfc0b729c48fcc9870c63499f37664c4c913ab060b6a5dba408` |
| 前端暂停文案源码 | `dba61bdadfc9430e030a71c4d823c5a5b4cf3283f1b45be3ab7d08b263dc4fcf` |

## 使用边界

指定旧运行的冻结策略中 `reference_resolution_version` 和 `execution_budget` 均缺省，部署保持原样。新建文档分析才启用新冻结策略；继续旧运行仍沿用旧策略。

本次仅完成部署及只读烟测，没有创建分析任务或发起模型请求，等待结束的调用来自部署前的旧运行。部署通过不代表本例真实 Qwen 质量评测完成；[实施方案](../../文档分析指代共指消解与关系证据修正方案.md)中的 T37 仍未完成，既有工程回归及 PostgreSQL 验收限制不变。
