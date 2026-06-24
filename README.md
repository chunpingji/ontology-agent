# Ontology Agent (SLPRA)

制药 GMP 本体管理平台:本体编辑、实体抽取与对齐、应用与治理。
后端 FastAPI + PostgreSQL,前端 Next.js（App Router，shadcn/ui + Tailwind），
统一由 nginx 单入口反代对外暴露。

---

## 架构 / 服务

`docker compose` 编排 4 个服务:

| 服务 | 镜像 / 构建 | 容器端口 | 说明 |
|------|-------------|----------|------|
| `db` | postgres:16-alpine | 5432 | 数据库,数据存于 `pgdata` 卷 |
| `backend` | `./backend/Dockerfile` | 8000 | FastAPI（`uvicorn --reload`） |
| `frontend` | `./frontend/Dockerfile` | 3000 | Next.js,仅 `expose`,不直接对外 |
| `web` | nginx:1.27-alpine | 80 | 单入口反代,前端 + `/api` 路由到后端 |

前端用 **同源相对路径** `"/api/..."` 调后端,由 `web`(nginx)转发,无 CORS。

### 两套运行模式

本仓库根目录有 `docker-compose.override.yml`,**`docker compose` 会自动合并它**,用于本机开发:

| | 开发模式（默认,合并 override） | 生产模式（`-f docker-compose.yml` 忽略 override） |
|---|---|---|
| 前端 | `next dev` 热更新,源码 bind-mount | standalone 生产镜像（`runner`） |
| 访问地址 | http://localhost:8081 | http://localhost:80 |
| 数据库主机端口 | 55432 | 5432 |
| 用途 | 日常开发,实时看修改 | 部署 / 验证生产构建 |

> override 重映射端口是因为本机 :80 / :5432 已被占用。部署主机请显式用
> `-f docker-compose.yml` 走 canonical 端口。

---

## 前置条件

- Docker + Docker Compose v2（`>= 2.24`,override 用到了 `!override` 标签）
- LLM 抽取需要 `ANTHROPIC_API_KEY`,从宿主环境或 `.env` 注入,**不会**写进镜像:

  ```bash
  echo 'ANTHROPIC_API_KEY=sk-ant-...' >> .env
  ```

---

## 开发模式（实时看到修改效果）

默认合并 override，前端 `next dev`、后端 `uvicorn --reload`,源码均 bind-mount,
改动自动热更新。

```bash
cd /opt/dev/chen/ontology-agent

# 首次启动 / 改过依赖后:重建并刷新匿名卷
docker compose up -d --build -V

# 之后日常启动（依赖没变时,代码改动热更新,无需重建）
docker compose up -d
```

- 访问 **http://localhost:8081**
- 改 `frontend/src/**` 或 `backend/app/**` → 自动热重载,无需重建
- `-V`（`--renew-anon-volumes`）很重要:前端 `node_modules` 走匿名卷,
  **只改过 `package.json` / `package-lock.json` 时**必须带 `-V`,否则旧卷复用、新依赖看不到

```bash
# 跟踪前端编译日志
docker compose logs -f frontend
```

---

## 生产模式（standalone 构建 / 部署）

用 `-f docker-compose.yml` 忽略 override,跑真实生产编译（`npm run build`）:

```bash
cd /opt/dev/chen/ontology-agent

# 整套重建并拉起
docker compose -f docker-compose.yml up -d --build

# 只重建前端
docker compose -f docker-compose.yml up -d --build frontend
```

访问 **http://localhost:80**。

---

## 常用命令

```bash
docker compose ps                         # 查看各服务状态
docker compose logs -f <service>          # 跟踪日志（frontend/backend/web/db）
docker compose restart <service>          # 重启单个服务
docker compose build --no-cache frontend  # 依赖/缓存诡异时彻底重建
docker compose stop                       # 停止（保留容器与卷）
docker compose down                       # 移除容器（保留数据卷 pgdata/backend_data）
docker compose down -v                    # ⚠️ 连同数据卷一并删除
```

---

## 排障

- **构建在 `npm ci` 阶段报 `ERESOLVE`**:本项目存在 peer 依赖冲突,
  `frontend/Dockerfile` 已用 `npm ci --legacy-peer-deps`;本地装包同理需
  `npm install --legacy-peer-deps`。
- **改了代码但页面不更新**:确认走的是开发模式(http://localhost:8081,
  非 :80);看 `docker compose logs -f frontend` 是否在重新编译。
  override 已设 `WATCHPACK_POLLING=true` 以保证 bind-mount 下的文件监听。
- **端口冲突**:本机 :80 / :5432 被占时用开发模式的 :8081 / :55432;
  部署主机用 `-f docker-compose.yml` 走 :80 / :5432。
- **数据库直连**:开发模式 `localhost:55432`,生产模式 `localhost:5432`,
  账号见 `docker-compose.yml`（`slpra` / `slpra_dev`,默认仅开发用）。
