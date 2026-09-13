# 候选完成与租约修复部署记录

日期：2026-09-13 UTC。用户在工程修复验收后明确授权“请重新部署”。
工程验证见[验证记录](candidate-completion-validation.md)。

## 实际部署

沿用现有基础 Compose + override、源码挂载与本地镜像。后端为无自动 reload 的
Uvicorn，前端为既有 `npm run dev` 服务；没有切换到 standalone 生产构建。
宿主入口仍为 `http://localhost:8081`，数据库、网关、模型服务和数据卷保持原样。
用户原链接的 8082 没有出现在宿主容器映射中，本次未修改端口或客户端转发。

1. 部署前仅故障运行 `d485c968-1bbd-43d3-8809-e61158765fc4` 活动。全部 6,280 条
   模型请求均已处于终态，没有在途模型请求；3 个 annotation execution 均为 paused。
   保存运行指纹、12 个制品头、候选/证明摘要及费用计数作为只读基线。
2. `docker compose stop --timeout 60 backend` 有界停止旧进程。旧 dispatcher 未在
   期限内退出，容器最终 exit 137、OOM=false；旧连接结束后未提交事务由数据库回滚。
   没有暂停/恢复或手动改 queued，保留故障运行原有无进展时间和执行身份。
3. 用现有后端镜像启动一次性迁移进程，显式执行并验证
   `0036_report_expert_opinions` → `0037_doc_analysis_liveness`。
   升级后旧执行 generation 127 保留，新三列为 0/0/null。
4. 启动后端、重启前端；后端进程启动于 01:57:25 UTC，前端于 01:57:27 UTC。
   数据库和网关的创建/启动时间保持不变，没有构建镜像、删除数据卷或创建识别运行。

实际成功的部署命令如下，迁移脚本在升级前后都显式核对数据库 revision：

```bash
docker compose config --quiet
docker compose stop --timeout 60 backend
docker compose run --rm --no-deps -T -e PGOPTIONS='-c lock_timeout=5000 -c statement_timeout=60000' backend python - < /tmp/ontology-candidate-redeploy-20260913-q8jVjU/migrate.py
docker compose up -d --no-deps --no-build --pull never backend
docker compose restart --no-deps --timeout 60 frontend
```

本机 Compose 的 `run` 子命令不接受 `--pull`；首次带该参数的调用在创建容器前
失败，核对本机帮助后移除。成功迁移使用已经存在的本地镜像。

## 生效状态及旧运行

- 实际数据库 revision 与容器 Alembic head 均为 `0037_doc_analysis_liveness`。
- 新运行冻结 `candidate_planning=sparse-candidates-v1`、state storage v3、
  `heuristic-first-v4`。保留原有 `trial` 检索配置，没有修改剪枝阈值或模型配置。
- 生效租期 120 秒；无进展上限 900 秒、连续无进展恢复上限 3 次、发布上限 30 秒。
- 旧故障运行在 01:57:51 UTC 由新 worker 接管，按最后业务事件时间
  `2026-09-11 23:02:14 UTC` 触发 `ANALYSIS_STALLED`。
  数据库终态为 failed，公开 API 为 retryable_failure，租约已 revoked；自动重领停止。
  该结果表示原永久运行故障已结束，不表示旧文档识别已完成。
- 旧运行指纹、全部 12 个制品头、制品数量、候选/证明摘要均与部署前完全一致。
  模型请求总数仍为 6,280，目标运行累计调用仍为 986；没有新增模型费用。
  仅新增终态事件，event head 从 5,936 变为 5,937，并更新运行/执行终态。

使用新候选策略须新建识别运行；旧运行保留原冻结策略、检查点与费用，不原地升级。

## 部署后验证

通过实际网关进行只读 HTTP 检查：

| 检查 | 结果 |
| --- | --- |
| `/api/health`、`/login`、原报告页面 | 200 |
| `/openapi.json` | 200，包含 candidate_policy 与 policy_complete |
| 未认证访问原运行 graph | 401 |
| 已认证访问原运行 | 200，retryable_failure / ANALYSIS_STALLED |
| 已认证访问原运行 graph | 200，既有部分图谱可读，25 个实体、29 条关系、42 个属性 |

验证凭证仅在后端验证进程内短时生成并使用，没有输出或保存令牌。GET 未触发识别。
15 个后端/迁移文件和 5 个前端文件的容器内 SHA256 与已验证工作区一致。
未进行真实模型新运行的完整耗时或事实质量验收。

本机部署基线、迁移/核验脚本、对账与 HTTP 结果位于
`/tmp/ontology-candidate-redeploy-20260913-q8jVjU/`；不包含文档原文、模型提示词或密钥。
