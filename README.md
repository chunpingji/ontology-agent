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
| `backend` | `./backend/Dockerfile.cuda12`；CPU 可显式覆盖 | 8000 | FastAPI，默认 CUDA 12.6 排序环境（当前关闭自动重载） |
| `frontend` | `./frontend/Dockerfile` | 3000 | Next.js,仅 `expose`,不直接对外 |
| `web` | nginx:1.27-alpine | 80 | 单入口反代,前端 + `/api` 路由到后端 |

前端用 **同源相对路径** `"/api/..."` 调后端,由 `web`(nginx)转发,无 CORS。

### 两套运行模式

本仓库根目录有 `docker-compose.override.yml`,**`docker compose` 会自动合并它**,用于本机开发:

| | 开发模式（默认,合并 override） | 生产模式（`-f docker-compose.yml` 忽略 override） |
|---|---|---|
| 前端 | `next dev` 热更新,源码 bind-mount | standalone 生产镜像（`runner`） |
| 后端排序环境 | CUDA 12.6；CPU 使用独立覆盖文件 | CUDA 12.6；CPU 使用独立覆盖文件 |
| 访问地址 | http://localhost:8081 | http://localhost:8081（可用 `WEB_HOST_PORT` 覆盖） |
| 数据库主机端口 | 55432 | 55432（可用 `DB_HOST_PORT` 覆盖） |
| 用途 | 日常开发,实时看修改 | 部署 / 验证生产构建 |

> 本机 :80 / :5432 已被占用，因此生产入口和数据库分别默认发布到 `8081`、`55432`；
> 容器内仍使用 `80`、`5432`。可用 `WEB_HOST_PORT`、`DB_HOST_PORT` 覆盖。

---

## 前置条件

- Docker + Docker Compose v2（`>= 2.24`,override 用到了 `!override` 标签）
- 默认后端镜像使用 NVIDIA Container Toolkit 的 `nvidia` runtime。GPU 排序须选择一张
  有足够余量的卡；无 GPU 主机使用下方 CPU 覆盖入口。安装和兼容性见
  [CUDA 12 部署说明](backend/GPU_CUDA12.md)。
- 抽取引擎**默认本地、离线优先**（008）：结构化源走确定性映射,自由文本（Word 正文 /
  Excel 自由文本列）走本地零样本 NER。**云端 LLM 为可选项,默认关闭**,仅在显式开启且
  配置 Key 时触发;关闭即离线正常态,**非降级**。
- 云端 LLM（可选）需要 `ANTHROPIC_API_KEY`,从宿主环境或 `.env` 注入,**不会**写进镜像;
  同时显式打开 `LLM_CLOUD_ENABLED`:

  ```bash
  echo 'ANTHROPIC_API_KEY=sk-ant-...' >> .env
  echo 'LLM_CLOUD_ENABLED=true'       >> .env   # 缺省 false → 纯本地,不出网
  ```

---

## 本地抽取模型（air-gap：GLiNER NER + 中文嵌入器）

平台部署于 **air-gap（无网络）** 环境,运行期**严禁出网**。本地 NER（`urchade/gliner_multi-v2.1`）
与语义对齐嵌入器（`BAAI/bge-small-zh-v1.5`）的权重在**有网的预备环境**一次性下载,随制品库
（artifact repo）连同 SHA256 校验和交付到 air-gap 主机,运行期纯本地加载。

权重**不入 git**（`.gitignore` 已忽略 `backend/models/`）;`backend/scripts/fetch_models.sh`
与生成的 `MODELS.sha256` 才是可审计的交付凭据。

**1. 安装可选依赖**(后端容器/环境):本地 NER 走 `gliner` extra,语义对齐走 `semantic` extra:

```bash
cd backend
uv sync --extra gliner --extra semantic     # 仅需 NER 用 --extra gliner
```

上述 `uv sync` 保留宿主 CPU 环境；GPU 环境使用独立锁与 `.venv-cuda12`，见
[CUDA 12 部署说明](backend/GPU_CUDA12.md)，不能用项目级 `uv sync` 替代。

**2. 预备环境（有网）下载权重 + 生成校验和**:

```bash
pip install -U "huggingface_hub[cli]"        # 仅预备环境需要
backend/scripts/fetch_models.sh              # 下载两模型 → backend/models/ + MODELS.sha256
```

**3. air-gap 主机收货校验**(证明权重逐字未被篡改/截断):

```bash
backend/scripts/fetch_models.sh --verify     # 按 MODELS.sha256 校验本地权重
```

**4. 运行期强制离线**(env,杜绝任何远程 repo 解析):

```bash
echo 'HF_HUB_OFFLINE=1'      >> .env         # huggingface_hub 全程离线
echo 'TRANSFORMERS_OFFLINE=1' >> .env         # transformers 全程离线
```

> 配套 `local_files_only=True`(代码内已固定),即便环境变量遗漏也绝不外发。
> 缺权重/缺包/功能关闭时本地 NER 静默降级——结构化主路径零回归,作业不失败、不标 degraded。
> 启动期 `lifespan` 会预热两模型,消除首作业冷启动;预热失败同样不阻断启动。

---

## 关系图谱语义排序（默认 GPU，显式启用）

应用 Settings 与基础 Compose 默认使用 `cuda:0`、`float16`、CUDA `12.6`，
batch 4、排序超时 1200 秒、文档执行并发 1。默认 GPU 镜像为
`ontology-agent-backend:cuda12-2.7.1`；旧 NER 与实体对齐继续使用各自的 CPU 加载策略。

排序能力开关仍为 `SEMANTIC_RANKING_ENABLED=false`，模型路径仍默认为空。
要启用 embedding + reranker，须显式设 `SEMANTIC_RANKING_ENABLED=true`，并提供
`SEMANTIC_RANKING_EMBEDDING_PATH`、`SEMANTIC_RANKING_EMBEDDING_MANIFEST_PATH`、
`SEMANTIC_RANKING_RERANKER_PATH`、`SEMANTIC_RANKING_RERANKER_MANIFEST_PATH` 四项。
Compose 中使用挂载后的 `/app/models/...` 路径；两模型及各自完整 SHA256 清单须先准备和校验，
不能使用旧对齐模型路径替代。详见 [特性 quickstart](specs/022-semantic-graph-closure/quickstart.md)。

排序预算限制独立于模型开关，`SEMANTIC_RANKING_BUDGET_ENABLED=true` 是新建运行的缺省。
运行暂停后可在页面启用/禁用“排序预算限制”，再显式恢复同一运行。禁用期间不预扣、
不累计排序预算，已记账历史保留；重新启用从原累计量继续。单次输入限制、超时及
自动技术重试上限仍有效，禁用预算不会关闭 embedding/reranker。

在项目根 `.env` 或当前 shell 中显式选择单张物理 GPU UUID/编号，容器内统一为 `cuda:0`：

```bash
export SEMANTIC_RANKING_GPU_ID=GPU_REPLACE_WITH_SELECTED_UUID
docker compose config --quiet
docker compose build backend
```

未配置 GPU ID 时基础 Compose 使用 `NVIDIA_VISIBLE_DEVICES=none`，不会自动占卡。
排序已启用后，设备不可用或制品不完整按默认 `pause` 策略暂停，不自动切到 CPU 或
确定性排序。`docker-compose.cuda12.yml` 保留为强制 GPU 覆盖入口，其 GPU ID 必须提供。

CPU 部署保留独立镜像 `ontology-agent-backend:cpu`、原 `Dockerfile`、`.venv` 和 `uv.lock`。
CPU 覆盖固定 `runc`、`cpu`、`float32`；即使 `.env` 中保留 GPU 精度也不会覆盖这些值。
以下更新命令须在相关任务完成或暂停后执行：

```bash
# CPU 开发：显式保留本机 override，并把 CPU 文件放最后。
docker compose -f docker-compose.yml -f docker-compose.override.yml \
  -f docker-compose.cpu.yml config --quiet
docker compose -f docker-compose.yml -f docker-compose.override.yml \
  -f docker-compose.cpu.yml up -d --build backend

# CPU 生产：不合并开发 override。
docker compose -f docker-compose.yml -f docker-compose.cpu.yml config --quiet
docker compose -f docker-compose.yml -f docker-compose.cpu.yml up -d --build backend
```

CPU 模式后续 `build`、`up`、`run` 均须保持同一文件序列；只用基础文件会恢复 GPU 默认。
已冻结的 CPU 运行即使暂停，恢复时仍需原配置；切换 GPU 后应新建运行。
默认配置变更不代表共享后端已经切换或排序能力已经开启，实际交付状态见
[GPU 默认配置交付记录](specs/022-semantic-graph-closure/gpu-default.md)。

---

## 开发模式（前端热更新）

默认合并 override，前端使用 `next dev`，前后端源码均 bind-mount。
前端改动自动热更新；后端当前关闭 `--reload`，避免重载中断长时间抽取任务，
修改 Python 代码后需手动重启后端。

```bash
cd /opt/dev/chen/ontology-agent

# 首次启动 / 改过依赖后:重建并刷新匿名卷
docker compose up -d --build -V

# 之后日常启动（应用配置变更；未变更的运行中容器不会重启）
docker compose up -d
```

- 访问 **http://localhost:8081**
- 改 `frontend/src/**` → 自动热更新，无需重建
- 改 `backend/app/**` → 执行 `docker compose restart backend`，无需重建镜像
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

访问 **http://localhost:8081**。

---

## 手动重启与更新

以下命令在项目根目录执行，默认使用本机开发配置：

```bash
cd /opt/dev/chen/ontology-agent
```

按需要选择操作：

| 场景 | 命令 |
|---|---|
| 只重启后端，加载已挂载的 Python 代码改动 | `docker compose restart backend` |
| 只重启前端 | `docker compose restart frontend` |
| 重启整套服务，包括数据库 | `docker compose restart` |
| 应用 `.env` 中传入容器的环境变量或 Compose 配置变更 | `docker compose up -d` |
| 后端依赖或 Dockerfile 变更，重建并更新后端 | `docker compose up -d --build backend` |
| 开发模式下前端依赖变更，重建并刷新依赖匿名卷 | `docker compose up -d --build -V frontend` |

`restart` 重启已有容器，不应用新的环境变量、Compose 配置或镜像。
`up -d` 会按配置和镜像变化决定是否重建容器；没有变化时不会重启运行中的服务。
重启后端会中断其进程内正在执行的任务，应在相关任务完成或暂停后操作。

检查状态和日志：

```bash
docker compose ps
docker compose logs --tail=100 backend
curl -fsS http://localhost:8081/api/health
```

生产模式保持使用 `docker compose -f docker-compose.yml`，例如：

```bash
docker compose -f docker-compose.yml up -d --build backend
```

---

## 数据库表结构升级（Alembic）

后端在 `backend/app/main.py` 的启动流程中自动尝试执行 `alembic upgrade head`，
应用当前数据库版本之后尚未执行的迁移。**迁移异常目前会被捕获并记录为
`Alembic migration skipped`，后端仍可能继续启动**，因此容器显示 `Up` 或
`/api/health` 返回 `200` 都不能单独证明迁移成功，应核对数据库版本：

```bash
docker compose exec -T backend alembic current  # 数据库已应用的版本
docker compose exec -T backend alembic heads    # 容器内迁移文件的最新版本
```

两者的 revision ID 应一致，`current` 应显示 `(head)`。升级到 head 后再次执行
`upgrade head` 不会重复执行已完成的迁移。

### 手动执行升级

下面步骤适用于当前默认开发配置。开发模式挂载了 `backend/alembic/`，新增迁移文件
无需重建镜像；如果还修改了后端依赖，应先执行 `docker compose build backend`。
**生产模式不挂载迁移目录**，新增或修改迁移文件后先执行
`docker compose -f docker-compose.yml build backend`，并在下列所有 Compose 命令中
保持使用 `-f docker-compose.yml`。

逐步执行；备份或迁移报错时先处理错误，不要继续恢复后端服务。

1. 确保数据库运行，在相关后台任务完成或暂停后停止后端写入：

   ```bash
   docker compose up -d db
   docker compose stop backend
   ```

2. 备份 PostgreSQL。文件保存在宿主机的 `backups/` 目录，请保留备份，不要提交到仓库：

   ```bash
   mkdir -p backups
   docker compose exec -T db pg_dump -U slpra -d slpra -Fc \
     > "backups/slpra-$(date +%Y%m%d-%H%M%S).dump"
   ```

3. 使用临时容器应用迁移。`run` 可在后端停止时运行；`--no-deps` 避免启动依赖服务，
   数据库必须已在运行：

   ```bash
   docker compose run --rm --no-deps backend alembic upgrade head
   ```

4. 核对数据库已应用的版本与迁移文件的最新版本一致：

   ```bash
   docker compose run --rm --no-deps backend alembic current
   docker compose run --rm --no-deps backend alembic heads
   ```

5. 确认升级成功后恢复后端，并检查日志：

   ```bash
   docker compose up -d backend
   docker compose logs --tail=100 backend
   ```

如果迁移失败，保留错误输出并检查对应的 `backend/alembic/versions/` 文件。
不要用 `alembic stamp head` 跳过错误：它只修改版本记录，不会执行表结构变更。

上述步骤用于应用的表结构迁移。PostgreSQL 跨大版本升级（例如 16 → 17）需要另行
规划 `pg_upgrade` 或备份恢复，不能直接修改镜像标签后复用原数据目录。

参考：[Compose restart](https://docs.docker.com/reference/cli/docker/compose/restart/) ·
[Compose up](https://docs.docker.com/reference/cli/docker/compose/up/) ·
[Alembic 迁移](https://alembic.sqlalchemy.org/en/latest/tutorial.html) ·
[PostgreSQL 备份](https://www.postgresql.org/docs/16/backup-dump.html)。

---

## 其他常用命令

```bash
docker compose ps                         # 查看各服务状态
docker compose logs -f <service>          # 跟踪日志（frontend/backend/web/db）
docker compose build --no-cache frontend  # 依赖/缓存诡异时彻底重建
docker compose stop                       # 停止（保留容器与卷）
docker compose down                       # 移除容器（保留数据卷 pgdata/backend_data）
```

`restart` 和 `up -d --build` 会保留具名数据卷。不要将 `docker compose down -v`
用于日常重启：它会删除 `pgdata`、`backend_data` 等数据卷。

---

## 排障

- **构建在 `npm ci` 阶段报 `ERESOLVE`**:本项目存在 peer 依赖冲突,
  `frontend/Dockerfile` 已用 `npm ci --legacy-peer-deps`;本地装包同理需
  `npm install --legacy-peer-deps`。
- **改了代码但页面不更新**:确认走的是开发模式(http://localhost:8081,
  非 :80);看 `docker compose logs -f frontend` 是否在重新编译。
  当前使用本机文件事件监听，未启用 `WATCHPACK_POLLING`；后端 Python 改动需执行
  `docker compose restart backend`。
- **升级后接口报缺表 / 缺列**:按上面的数据库升级步骤核对 `alembic current` 与
  `alembic heads`，并检查后端日志中的 `Alembic migration skipped`。
  生产模式还需确认已重建后端镜像，使新迁移文件进入容器。
- **端口冲突**:本机 :80 / :5432 已被占用；开发模式使用 :8081 / :55432，
  生产模式默认使用 :8081 / :55432，可用 `WEB_HOST_PORT` / `DB_HOST_PORT` 覆盖。
- **数据库直连**:开发和生产模式默认均为 `localhost:55432`,
  账号见 `docker-compose.yml`（`slpra` / `slpra_dev`,默认仅开发用）。
