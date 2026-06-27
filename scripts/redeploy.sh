#!/usr/bin/env bash
#
# redeploy.sh — 重建并重部署 docker-compose 栈（开发 / 部署主机两用）。
#
# 数据库迁移**不在本脚本里显式执行**：backend 应用在 FastAPI `lifespan` 启动钩子
# 内自迁移——`_run_migrations()` → `alembic command.upgrade(cfg, "head")`，随后
# `_seed_from_ttl()` 幂等投影权威 TTL（见 backend/app/main.py:25-79）。因此只要
# backend 容器成功启动并对 `/api/health` 返回 200，迁移与播种即已完成。本脚本据此
# 负责 build + up，并**等待健康**作为迁移成功的判据，再只读打印 `alembic current`
# 供审计。脚本不调用 `down -v`，绝不触碰具名卷（pgdata / backend_data）数据。
#
# 用法：
#   scripts/redeploy.sh [--prod] [--pull] [--no-build] [--no-pull-base] [SERVICE ...]
#
#   --prod          只用 docker-compose.yml（忽略本机 override，规范端口 80/5432）。
#   --pull          先 `docker compose pull` 基础镜像（db=postgres、web=nginx）。
#   --no-build      只 `up -d`（重建容器、不 rebuild 镜像）；改了 requirements/package 时勿用。
#   --no-pull-base  跳过 build 前对 Dockerfile FROM 基础镜像的"带重试预拉取"（见下）。
#   SERVICE ...     指定要 build/up 的服务（缺省=整栈：db backend frontend web）。
#
# 防抖：本机无法直连 Docker Hub、只有单个镜像加速器，加速器换 token 偶发
# "Forwarding failure" 会让 `docker compose build` 在解析 FROM 元数据时整体失败。
# 故 build 前默认带重试预拉取 Dockerfile 依赖的镜像到本地缓存（已缓存则跳过）：
# `FROM` 基础镜像（python / node）+ `# syntax=` 指令镜像（docker/dockerfile:1）。
# `--no-pull-base` 可关闭。
#
# 例：
#   scripts/redeploy.sh                 # 本机：rebuild+up 整栈，等 backend 健康
#   scripts/redeploy.sh backend         # 只重建后端（最常用：改了后端代码/依赖）
#   scripts/redeploy.sh --prod --pull   # 部署主机：拉新基础镜像 + rebuild + up

set -euo pipefail

# --- 定位项目根（脚本可从任意目录调用）---------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$ROOT"

# --- 解析参数 ----------------------------------------------------------------
PROD=0
PULL=0
BUILD=1
PULL_BASE=1
SERVICES=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    --prod)     PROD=1 ;;
    --pull)     PULL=1 ;;
    --no-build) BUILD=0 ;;
    --no-pull-base) PULL_BASE=0 ;;
    -h|--help)  grep '^#' "$0" | grep -v '^#!' | sed 's/^# \{0,1\}//; s/^#$//'; exit 0 ;;
    --*)        echo "未知选项：$1" >&2; exit 2 ;;
    *)          SERVICES+=("$1") ;;
  esac
  shift
done

COMPOSE=(docker compose)
if [[ $PROD -eq 1 ]]; then
  COMPOSE+=(-f docker-compose.yml)   # 忽略 override → 规范端口
  echo "▶ 模式：生产（仅 docker-compose.yml，端口 80/5432）"
else
  echo "▶ 模式：开发（自动合并 docker-compose.override.yml，端口 8081/55432）"
fi

# --- 可选：拉取基础镜像 -------------------------------------------------------
if [[ $PULL -eq 1 ]]; then
  echo "▶ 拉取基础镜像（postgres / nginx）…"
  "${COMPOSE[@]}" pull
fi

# --- build 前：带重试预拉取 Dockerfile 依赖的镜像（规避加速器瞬时抖动）----------
# 从各 Dockerfile 动态抽取需联网拉取的镜像，已缓存则跳过、未缓存则带重试拉取——拉到
# 本地后 `docker compose build` 解析时即有兜底。两类镜像都要覆盖：
#   1. `FROM` 基础镜像（排除多阶段 `FROM <别名>` 引用）；
#   2. `# syntax=...` 指令镜像（BuildKit 解析 Dockerfile 必拉，如 docker/dockerfile:1）。
prepull_base_images() {
  local dockerfiles=(backend/Dockerfile frontend/Dockerfile) existing=()
  local f
  for f in "${dockerfiles[@]}"; do [[ -f "$f" ]] && existing+=("$f"); done
  [[ ${#existing[@]} -eq 0 ]] && return 0

  local aliases from_imgs syntax_imgs images img
  aliases="$(grep -hiE '^[[:space:]]*FROM ' "${existing[@]}" \
    | grep -ioE ' AS +[A-Za-z0-9_.-]+' | awk '{print $2}')"
  from_imgs="$(grep -hiE '^[[:space:]]*FROM ' "${existing[@]}" | awk '{print $2}')"
  # `# syntax=docker/dockerfile:1` → 取 = 右侧首个 token（BuildKit frontend 镜像）。
  syntax_imgs="$(grep -hiE '^#[[:space:]]*syntax[[:space:]]*=' "${existing[@]}" \
    | sed -E 's/^#[[:space:]]*syntax[[:space:]]*=[[:space:]]*//I' | awk '{print $1}')"
  images="$(printf '%s\n%s\n' "$from_imgs" "$syntax_imgs" | grep -v '^$' | sort -u)"

  for img in $images; do
    grep -qixF "$img" <<<"$aliases" && continue          # 多阶段别名（非镜像）→ 跳过
    if docker image inspect "$img" >/dev/null 2>&1; then
      echo "  ✓ $img 已在本地缓存"
      continue
    fi
    local ok=0 attempt
    for attempt in 1 2 3 4 5; do
      echo "  ▶ 拉取 $img（第 $attempt/5 次）…"
      if docker pull "$img"; then ok=1; break; fi
      sleep 3
    done
    [[ $ok -eq 1 ]] || { echo "✗ 基础镜像 $img 拉取失败（加速器疑似抖动，已重试 5 次）。" >&2; exit 1; }
  done
}

if [[ $BUILD -eq 1 && $PULL_BASE -eq 1 ]]; then
  echo "▶ 预拉取 Dockerfile FROM 基础镜像（带重试，规避加速器抖动）…"
  prepull_base_images
fi

# --- build + up（迁移在容器内 lifespan 自动应用）-----------------------------
# 匿名卷防腐：docker-compose.override.yml 用匿名卷挂载 /app/node_modules，旧容器的
# 匿名卷会遮盖镜像内新装的包。rebuild 前先 rm 目标容器释放旧匿名卷，再 --build -V
# 确保用镜像内最新的 node_modules。具名卷（pgdata / backend_data）不受影响。
UP_ARGS=(up -d)
if [[ $BUILD -eq 1 ]]; then
  UP_ARGS+=(--build -V)
  if [[ ${#SERVICES[@]} -gt 0 ]]; then
    echo "▶ 清理旧容器（释放匿名卷）：${SERVICES[*]}"
    "${COMPOSE[@]}" rm -f -s "${SERVICES[@]}" 2>/dev/null || true
  fi
fi
echo "▶ ${COMPOSE[*]} ${UP_ARGS[*]} ${SERVICES[*]:-(整栈)}"
"${COMPOSE[@]}" "${UP_ARGS[@]}" "${SERVICES[@]}"

# --- 等待 backend 健康（= 迁移 + TTL 播种成功）-------------------------------
# backend 始终映射宿主 8000:8000（override 不改它），但探测走容器内 localhost，
# 与端口映射/宿主是否装 curl 无关，开发/生产一致。
echo "▶ 等待 backend 健康（/api/health；迁移在此期间于容器内自动完成）…"
HEALTH_PY='import urllib.request,sys; sys.exit(0) if urllib.request.urlopen("http://localhost:8000/api/health", timeout=2).status==200 else sys.exit(1)'

backend_running() {
  "${COMPOSE[@]}" ps --status running --services 2>/dev/null | grep -qx backend
}

dump_and_fail() {
  echo "✗ $1" >&2
  echo "—— backend 最近 60 行日志 ——" >&2
  "${COMPOSE[@]}" logs --tail=60 backend >&2 || true
  exit 1
}

tries=60   # 60 × 2s = 最多 ~2 分钟
for ((i = 1; i <= tries; i++)); do
  if "${COMPOSE[@]}" exec -T backend python -c "$HEALTH_PY" >/dev/null 2>&1; then
    echo "✓ backend 健康（迁移已到 head、TTL 已播种）"
    break
  fi
  # backend 已退出（迁移失败 → fail-fast）：立即打日志退出，别空等。
  if (( i > 2 )) && ! backend_running; then
    dump_and_fail "backend 容器未在运行（迁移可能失败）。"
  fi
  if (( i == tries )); then
    dump_and_fail "等待 backend 健康超时（~120s）。"
  fi
  sleep 2
done

# --- 只读审计：当前迁移版本 --------------------------------------------------
echo "▶ 当前数据库迁移版本（alembic current）："
"${COMPOSE[@]}" exec -T backend alembic current 2>/dev/null || \
  echo "  （alembic current 不可用，跳过——不影响部署）"

echo "✓ 重部署完成。"
if [[ $PROD -eq 1 ]]; then
  echo "  前端 http://localhost/  ·  API http://localhost:8000/api/health"
else
  echo "  前端 http://localhost:8081/  ·  API http://localhost:8000/api/health  ·  PG localhost:55432"
fi
