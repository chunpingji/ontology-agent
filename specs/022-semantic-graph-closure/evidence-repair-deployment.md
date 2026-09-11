# 证据修复新开关本机部署记录

2026-09-11，按用户“新开关打开，并重新部署”指令完成。
后端新容器于01:25:41 UTC启动，前端于01:25:49 UTC重启。

## 配置及执行

在基础 `docker-compose.yml` 增加
`DOCUMENT_ANALYSIS_EVIDENCE_REPAIR_ENABLED: ${DOCUMENT_ANALYSIS_EVIDENCE_REPAIR_ENABLED:-false}`，
并在本机被 Git 忽略的 `.env` 中持久设置该键为 `true`。代码及 Compose 的缺省值仍为
`false`，本机实例已开启。合并配置与部署前容器环境比较，仅此键发生变化。

沿用基础 Compose 与本机 override，前后端源码均挂载当前工作区，前端仍为开发服务。
环境变化通过重新创建后端容器生效；本次未重建镜像或执行前端生产构建。

```bash
docker compose config --quiet
docker compose up -d --no-deps --no-build --pull never --timeout 60 backend
docker compose restart --no-deps --timeout 60 frontend
```

数据库、反向代理未重启，本机 LLM 服务未操作；源码挂载、模型挂载及所有数据卷保持一致。

## 生效及可用性检查

- 容器实际 Settings：证据修复 `true`、性能策略 `true`、识别在途数1。
  独立模板公平开关仍为 `false`，但新证据修复策略冻结的 `template_interleaving=true`。
- 在容器内读取并计算新建运行代码的策略表达式，确认
  `evidence-repair-v1`、`heuristic-first-v2`、`source-owned-binding-v2`、
  `source-quoted-scope-v1`、`evidence-work-v2`、`source-integer-quotes-v2` 和
  `proof-menu-v1`；证据修复适配器、范围、值引用及主体绑定常量与之相符。
  此检查未创建实际运行、未派发模型调用。
- 既有语义排序仍开启，保留 `cuda:0/float16`、batch4；本机识别模型仍为
  Qwen3.6-35B-A3B。本次没有改变语义排序配置或验收其扩搜效果。
- 23个后端、3个前端改动源码文件（含新增模块）的容器内 SHA256 与工作区逐个相等。
- 经 `http://127.0.0.1:8081` 访问 `/api/health` 返回200、`status=ok`，10个本体模块已加载；
  `/login`、`/settings/ast-templates` 和 `/openapi.json` 均返回200。
  OpenAPI包含 `ranking-summary`、`source-selection`，两接口匿名请求均返回401。
- 数据库实际 revision 与容器代码 head 均为 `0035_ranking_budget_control`。
  启动日志未出现迁移/播种跳过、ERROR 或 Traceback；保留既有 Pydantic 字段遮蔽警告。

## 旧状态保护及验证边界

部署前没有在途文档识别、模型请求、持有执行权的标注或待恢复事实提交。
6个文档运行分别为阻塞1、取消3、暂停2；部署前后运行身份、状态、版本、水位、执行代次及
租约快照相等，47个制品head及制品总数相等，模型请求新增0。
旧抽取表中的741条历史 `running` 记录保持原状，最新创建时间为09-05，未据此恢复旧任务。

新开关仅影响新建运行；旧运行继续使用其冻结策略，不能通过恢复旧运行来切换协议。
本次完成部署可用性检查，没有启动新一轮真实识别，也没有将旧制品标为新协议。
[修复验证](evidence-repair-validation.md)中指定开发样本的通过结论、全文未完成及
ER06/ER07尚有未完成项的边界保持不变。

只读事务前后快照、配置差异、容器/源码核验、HTTP结果及启动日志保存在：
`/tmp/ontology-evidence-repair-deploy-20260911/`。快照不含凭据或原文正文，未改写历史评测制品。
