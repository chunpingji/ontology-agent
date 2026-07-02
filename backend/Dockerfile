# syntax=docker/dockerfile:1
# ↑ 启用 BuildKit 的 `--mount=type=cache`：pip 轮子缓存落在持久构建缓存里（不进
#   镜像层，镜像仍瘦），即使某层因 pyproject.toml 变动而重跑，torch/gliner 等大轮
#   子也从缓存复用，不再按 ~150KB/s 重下。
FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    default-jre-headless \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# —— 重、稳定依赖层（几乎不变）——————————————————————————————————————————————
# 把本地 NER（gliner）+ 语义对齐（semantic）的重依赖（torch/gliner/
# sentence-transformers 及其传递依赖）钉在 `COPY pyproject.toml` **之前**，使其
# 不被 pyproject.toml 的日常编辑（加 extra、调版本约束）打回重装。
# 关键：pip 不读 pyproject 的 [tool.uv.sources]，故先从 PyTorch CPU 索引单独装
# CPU 版 torch；随后 gliner/sentence-transformers 见 torch 已满足即不再从 PyPI 拉
# CUDA 版，排除 nvidia-cuda-*（多 GB），镜像瘦身。
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install torch --index-url https://download.pytorch.org/whl/cpu \
    && pip install gliner sentence-transformers

# —— 轻、频繁依赖层 ————————————————————————————————————————————————————————
# 仅当 pyproject.toml 变动时重跑；此时 torch/gliner/sentence-transformers 已满足，
# 只装增量（fastapi 等基础依赖 + llm extra 的 openai），秒级完成、无大轮子重下。
COPY pyproject.toml .
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install -e ".[gliner,semantic,llm]"

COPY . .

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]
