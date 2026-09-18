# 关系图谱 PostgreSQL 自动化测试

本机已于 2026-09-14 配置独立测试数据库，供文档分析、关系图谱暂停继续及并发测试使用。
复用现有 PostgreSQL 实例，数据库和账号与业务库分开；测试数据可销毁。

| 项目 | 当前配置 |
|---|---|
| PostgreSQL | 16.14，现有容器 `ontology-agent-db-1` |
| 宿主机地址 | `127.0.0.1:55432` |
| 测试数据库 | `ontology_document_analysis_test` |
| 专用账号 | `ontology_test`，无超级用户、建库或建角色权限 |
| 本地连接配置 | `backend/.env.postgresql-test`，权限 `0600`，已被 Git 忽略 |
| 配置键 | `DOCUMENT_ANALYSIS_TEST_DATABASE_URL` |

测试账号拥有测试库，已撤销 PUBLIC 对测试库的数据库权限；已核验该账号没有业务库
`public.document_analysis_runs` 的 SELECT/INSERT/UPDATE/DELETE/TRUNCATE 权限。
测试库配置阶段未迁移业务库或重启业务服务；后续按用户授权执行的业务升级见
[部署记录](../../specs/022-semantic-graph-closure/current-state-validation.md)。

## 执行测试

在仓库根目录运行：

```bash
backend/.venv/bin/python backend/scripts/test_document_analysis_postgresql.py
```

入口优先使用进程中的 `DOCUMENT_ANALYSIS_TEST_DATABASE_URL`，否则读取上述本地文件。
它不会回退到应用数据库，也不会把 PostgreSQL 连接替换到全局 SQLite fixture。
默认执行文档运行竞争、状态原子提交、精排预算控制、已付费响应恢复及当前工作版本竞争，
本次结果为 **20 passed、0 skipped**。

在 `backend/` 运行指定模块及 pytest 参数：

```bash
.venv/bin/python scripts/test_document_analysis_postgresql.py \
  tests/test_extraction/test_evidence_repair_postgresql.py -x
```

传入参数时，参数整体作为 pytest 的选择参数；省略时使用默认验收组合。
直接执行 `python -m pytest` 不会自动读取 `.env.postgresql-test`，因此推荐使用此入口。
fixture 会对测试表执行 `TRUNCATE ... CASCADE`；同一测试库的测试应串行运行，不能同时启动
多个 pytest 进程或使用 pytest-xdist。此配置仅用于文档分析测试，不设置其他测试域的 URL。

## 测试结构初始化

当前测试库已经初始化，无需再次执行。为其他环境准备符合命名要求的新空测试库并配置连接后，
可在仓库根目录运行：

```bash
backend/.venv/bin/python backend/scripts/test_document_analysis_postgresql.py --init
```

该操作拒绝非空库。初始化使用当前 `Base.metadata.create_all` 建表，再将 Alembic 版本标记为
代码 head；本次实际核验为 `0040_current_recognition_state`，包含 `document_analysis_current_state`、
`document_analysis_results` 和 `document_analysis_requests`。

这是测试结构初始化，**没有执行完整历史迁移链**。早期迁移导入当前模型建表，会与后续迁移
重复创建对象，既有边界见[历史验证记录](../../specs/022-semantic-graph-closure/validation.md)。
不能据此声称业务库升级通过。后续结构变化应先按对应迁移核对测试库，版本不一致时 fixture 会拒绝运行。
