FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    default-jre-headless \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY pyproject.toml .
# 有网 build 阶段「烤包」：把本地 NER（gliner）+ 语义对齐（semantic）的可选依赖
# 装进镜像，运行期（air-gap）无需联网装包。
# 关键：pip 不读 pyproject 的 [tool.uv.sources]，故先从 PyTorch CPU 索引单独装
# CPU 版 torch；随后 .[gliner,semantic] 见 torch 已满足即不再从 PyPI 拉 CUDA 版，
# 排除 nvidia-cuda-*（多 GB），镜像瘦身。
RUN pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu \
    && pip install --no-cache-dir -e ".[gliner,semantic]"

COPY . .

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]
