# scripts/

运维脚本目录。

## `redeploy.sh` — 重建并重部署 docker-compose 栈

开发机 / 部署主机两用的一键重部署脚本。`build → up → 等待健康 → 审计迁移版本`。

```bash
scripts/redeploy.sh [--prod] [--pull] [--no-build] [--no-pull-base] [SERVICE ...]
```

### 选项

| 选项 | 含义 |
|---|---|
| `--prod` | 只用 `docker-compose.yml`，**忽略本机 `docker-compose.override.yml`**，使用规范端口（web 80、db 5432）。 |
| `--pull` | 先 `docker compose pull` 拉取基础镜像（`db=postgres:16-alpine`、`web=nginx:1.27-alpine`）。 |
| `--no-build` | 只 `up -d`（重建容器，不 rebuild 镜像）。**改了 `requirements`/`package.json` 时勿用**。 |
| `--no-pull-base` | 跳过 build 前对 Dockerfile `FROM` 基础镜像的"带重试预拉取"（见下"镜像加速器防抖"）。 |
| `SERVICE ...` | 指定要 build/up 的服务（缺省 = 整栈 `db backend frontend web`）。 |
| `-h`, `--help` | 打印用法。 |

### 常用调用

```bash
scripts/redeploy.sh backend            # 改了后端代码/依赖（最常用）
scripts/redeploy.sh                     # 整栈 rebuild + up（本机开发）
scripts/redeploy.sh --prod --pull       # 部署主机：拉新基础镜像 + rebuild + up
scripts/redeploy.sh --no-build backend  # 仅重起容器（迁移仍会在启动时自动跑）
```

### 数据库迁移：脚本**不**显式执行

Alembic 迁移内建在 backend 应用自身的启动流程，**不在本脚本里**重复调用：

```
backend/app/main.py  →  lifespan 启动钩子
  ├─ _run_migrations()  →  alembic command.upgrade(cfg, "head")   # 迁移到 head
  └─ _seed_from_ttl()   →  幂等投影权威 TTL + 声明式规则播种
```

迁移在应用**开始对外服务之前**完成，因此 backend 对 `/api/health` 返回 `200` ⟺ **迁移已到 head 且 TTL 已播种**。脚本据此把"backend 健康"作为迁移成功的判据：

- ✅ 健康通过 → 打印 `alembic current` 供审计。
- ✗ backend 容器中途退出（坏迁移 → fail-fast）→ 立即 `docker compose logs --tail=60 backend` 并以非零退出，**不空等**。
- ✗ ~120s 仍不健康 → 同样打日志并失败。

> 健康探测走**容器内** `localhost:8000`（`docker compose exec backend`），与宿主端口映射、宿主是否装 `curl` 无关，开发/生产行为一致。

新增了 migration revision 后，**无需手动 `alembic upgrade head`**——本脚本重起 backend 容器即自动应用。

### 改动 ↔ 是否需要 rebuild

| 改动 | 开发模式（override 生效，frontend = `next dev`、backend 代码 bind-mount） | 生产模式（`--prod`） |
|---|---|---|
| `backend/app/**`（Python） | 无需 rebuild，`redeploy.sh --no-build backend` 重起即可（uvicorn `--reload` 亦会自动重载） | 需 rebuild：`redeploy.sh --prod backend` |
| `ontology/**`（TTL） | 无需 rebuild（bind-mount），重起 backend 重新播种 | 需 rebuild |
| `frontend/src/**` | 无需 rebuild（next dev 热重载） | 需 rebuild：`redeploy.sh --prod frontend` |
| `requirements` / `pyproject` / `package.json` | **需 rebuild**：`redeploy.sh backend`（带 `--build`，默认即带） | 需 rebuild |
| 基础镜像版本（postgres / nginx 的 tag） | `redeploy.sh --pull` | `redeploy.sh --prod --pull` |

### 镜像加速器防抖（基础镜像预拉取）

本机**无法直连 Docker Hub**（`registry-1.docker.io` 超时），只配了**单个**镜像加速器
（华为云 SWR，`/etc/docker/daemon.json` 的 `registry-mirrors`）。该加速器换匿名 token 时
偶发 `failed to authorize: ... Forwarding failure`，会让 `docker compose build` 在解析
`FROM` 镜像元数据那一步**整体失败**——没有 fallback 可退。

因此脚本在 build **之前**默认做一步**带重试的镜像预拉取**，从
`backend/Dockerfile` / `frontend/Dockerfile` **动态抽取**两类需联网拉取的镜像：

- **`FROM` 基础镜像**（当前 `python:3.12-slim`、`node:22-alpine`），自动排除多阶段
  构建里的 `FROM <别名>` 引用。
- **`# syntax=` 指令镜像**（当前 `docker/dockerfile:1`）——BuildKit 解析带
  `# syntax=...` 头的 Dockerfile 时必拉，与 `FROM` 同样会被加速器抖动卡住。

规则：已在本地缓存 → 跳过；未缓存 → `docker pull` 最多重试 5 次（间隔 3s），拉到本地后
`docker compose build` 的解析即有兜底；5 次仍失败 → 打印失败镜像并以非零退出。

`--no-build` 时不会预拉取（无 build 即无 `FROM` 解析）。`--no-pull-base` 可显式关闭。

> 若加速器持续故障：`docker pull <镜像>` 多重试几次通常即过（多为瞬时抖动）；
> 或在 `/etc/docker/daemon.json` 增配可用加速器后 `systemctl restart docker`
> （会重启所有容器，慎用）。

### 数据安全

- 脚本**绝不**调用 `docker compose down -v`，不触碰具名卷 `pgdata` / `backend_data` 的数据。
- `up -d --build` 与 `restart` 都不删卷。只有手动 `down -v` 才会删数据——请勿误用。
- postgres **跨大版本**升级（如 16→17）数据目录不兼容，需先 `pg_dump` 备份再迁移，**不能**直接改 tag `up`；本脚本的 `--pull` 仅用于同大版本内的小版本更新。

### 前置条件

- `docker compose` v2（本仓库 override 用了 `!override` 标签，需 Compose ≥ 2.24）。
- 生产路径会用到 `ANTHROPIC_API_KEY`：compose 从宿主环境读取（`${ANTHROPIC_API_KEY}`），不烤进镜像。重部署前确认 shell 已 `export`，否则 backend 可起但 LLM 调用失败。
