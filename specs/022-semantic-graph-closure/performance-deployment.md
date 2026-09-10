# 性能增量本机部署记录

2026-09-10，按用户“请重新部署”指令执行；前端于01:43:55 UTC、后端于01:43:57 UTC重启。

沿用当前Compose基础文件与本机override：前端为Next.js开发服务，前后端源码均挂载自
当前工作区。依赖、镜像配置与容器环境未变化，因此执行：

```bash
docker compose config --quiet
docker compose restart --timeout 60 backend frontend
```

本次加载当前工作区的代码，包括同时存在的报告页面接入改动；没有重建镜像或运行前端
生产构建。数据库和反向代理未重启，具名数据卷及上传原件保留。

部署检查结果：

- 经`http://127.0.0.1:8081`访问`/api/health`返回200、`status=ok`，10个本体模块已加载；
  `/login`返回200，前端`/settings/ast-templates`实际编译访问返回200。
- 运行服务的OpenAPI包含`ranking-summary`和`source-selection`，匿名访问两端口均返回401。
- 容器配置读取确认`document_analysis_performance_enabled=true`、模板公平增强false、
  GPU `cuda:0/float16`、batch4、retry1、dispatch concurrency1；本地模型制品路径存在。
- 数据库实际revision与容器迁移head均为`0035_ranking_budget_control`；启动日志未见迁移
  失败。保留既有Pydantic字段遮蔽警告。
- 当前20个后端、6个前端变更源码文件的容器内SHA256与工作区逐个相等。
- 部署前后5个文档分析运行的身份、状态、水位相等，35个制品head及制品总数相等；
  模型调度请求新增0。数据库容器启动时间保持不变。

部署前没有在途文档分析、持有执行权的标注任务或模型请求。旧抽取/报告表中存在历史
running记录，其最近创建时间分别为09-05和08-03；本次未修改这些历史状态。
原始部署日志、前后快照及核验JSON保存在`/tmp/ontology-performance-deploy-20260910/`。

新建运行采用新性能策略；旧运行保留冻结策略，不因部署被自动恢复或升级。
本次仅验证部署可用性，没有启动识别或新旧性能对照，也没有改变
[性能验证](performance-validation.md)中完整模板质量尚未通过的结论。
