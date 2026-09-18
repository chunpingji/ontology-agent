# 2026-09-17 重新部署

用户明确要求重新部署后，于约 00:40 UTC 完成当前工作区前后端更新。沿用正在运行的 `docker-compose.yml` + `docker-compose.override.yml`：后端 CUDA 镜像、前端 Next.js 开发模式，统一入口为 `http://localhost:8081`。本次没有切换生产构建模式。

## 执行

```bash
docker compose -f docker-compose.yml -f docker-compose.override.yml config --quiet
docker compose -f docker-compose.yml -f docker-compose.override.yml --progress plain build backend frontend
docker compose -f docker-compose.yml -f docker-compose.override.yml up -d --no-deps --no-build --force-recreate --renew-anon-volumes backend frontend
docker compose -f docker-compose.yml -f docker-compose.override.yml exec -T web nginx -t
docker compose -f docker-compose.yml -f docker-compose.override.yml exec -T web nginx -s reload
```

构建完成后先以新后端镜像执行无网络导入/接口契约烟测，再切换容器。前端刷新 node_modules 和 .next 匿名卷；数据库容器未重建，`ontology-agent_pgdata` 与 `ontology-agent_backend_data` 均保留。Nginx 仅重新加载配置以刷新上游地址。没有清理历史任务或重启宿主 Qwen 服务。

| 部署制品 | SHA256 |
|---|---|
| 后端镜像 | `fbc23146d1b6f501478439d41bbc5f70cceda82c8aa53f4899ffce0ecb990072` |
| 前端镜像 | `da2b07766badc45a989f25d2a51f42fb415ac5246e4816643f94f9771e49fead` |
| CUDA 依赖锁 | `ce27ce9278af8f2f392884d5fb1fae48d61ef38301dbe77e7b11333c0bf7789c` |
| 工具运行时源码 | `b347261b43864a8457ed9bf3b256edaeb698e7b2d153e5f218ec68182eb8b363` |

容器内依赖锁与挂载源码 hash 均与工作区一致。后端和前端容器均 running、重启次数为 0。

## 部署核验

- 新镜像成功导入应用、GLiNER2、SHACL 和 Responses 工具模块；注册 10 个函数，其中 8 个具备模型可调用资格，实际可见集合仍由阶段和配置决定。
- `pip check`：No broken requirements found。实际版本为 torch 2.7.1+cu126、gliner2 2.0.0、Transformers 4.57.6、OpenAI SDK 2.44.0、pySHACL 0.31.0。gliner2 Python 包版本与 GLiNER2.5 权重版本是不同标识。
- 容器只看到一张 Tesla P100，CUDA 小矩阵运算结果正确。
- 部署前后 `alembic current` 与 `alembic heads` 均为 `0041_template_engine (head)`；独立核对，不以健康接口代替迁移核验。
- `:8000/api/health`、`:8081/api/health`、统一入口首页、`/extraction`、`/login` 均 HTTP 200。
- 线上 `/openapi.json` 包含新版 `GraphArtifactResponse.relationship_groups` 与 `extraction_protocol`；新非模板运行策略为 `ontology-tool-extraction-v1` / `responses`。
- 只读比较部署前后关键表数量：document_analysis_runs 15、requests 28,912、results 23,040、extraction_jobs 773、generated_reports 154，均未变化。

部署时 `ontology_extraction_options` 没有可选工具配置，未自动启用 GLiNER 权重、外部 Mock 快照或实验词表。代码和依赖部署完成不表示这些可选工具已参与线上抽取。本次验证没有创建文档运行、调用 Qwen 或重新评测 F1；此前 CMC partial 与 T25/T26 未完成的结论不变。

随后用户明确要求全部启用，已于约 01:10 UTC 完成三项配置接入与后端切换，见 [启用记录](tool-enablement-20260917/README.md)。上段描述的是首次重新部署时的状态。
