# CUDA 12.6 独立语义排序环境

应用 Settings 与基础 Compose 默认使用 GPU 排序环境；排序能力仍需显式启用并配置模型。
GPU 使用 `.venv-cuda12`、`requirements-cuda12.lock` 和 `Dockerfile.cuda12`。
CPU 继续保留 `.venv`、`uv.lock`、`Dockerfile` 及 `pyproject.toml` 中的 CPU torch 源，
通过独立 Compose 覆盖回切，不将 CUDA 依赖写回 CPU 锁。
下文区分准备/验证与版本切换命令；切换共享后端前须等待相关任务完成或暂停。

## 固定平台与版本

| 项目 | 固定值 |
|---|---|
| 平台 | Linux x86_64，glibc ≥ 2.28，CPython 3.12 |
| PyTorch | `2.7.1+cu126`，官方 CUDA 12.6 wheel |
| CUDA 用户态运行库 | 锁文件中的 CUDA 12.6 / NVIDIA wheel 版本；宿主提供驱动 |
| sentence-transformers | `5.6.0` |
| transformers / tokenizers | `5.6.2` / `0.22.2` |
| 默认 GPU 精度 | `float16`；对照可显式使用 `float32` |
| 注意力实现 | GPU 适配器使用 `eager`，不依赖 P100 不支持的 Flash Attention |
| 权重 | 沿用已校验的离线 BGE-M3 / BGE-reranker-v2-m3 制品，不重新下载 |

Python wheel 已包含所需 CUDA 用户态库，不要求宿主安装相同版本的 `nvcc`。
`nvidia-smi` 的 CUDA 版本表示驱动所支持的上限，不是 Python 环境实际链接的 CUDA 版本；
以 `torch.version.cuda` 和实际 kernel 为准。GPU 安装锁是 CPython 3.12 专用，
不能拿它安装到 Python 3.11、ARM64 或 Windows。

[PyTorch 官方历史版本页](https://pytorch.org/get-started/previous-versions/) 提供
`torch==2.7.1 --index-url https://download.pytorch.org/whl/cu126`。
[官方 cu126 wheel 索引](https://download.pytorch.org/whl/cu126/torch/) 中本次使用的
CPython 3.12 wheel SHA256 为
`63bce0590bc540fc16139e2be0177847585182b8c5e68d7f9213789d1d96c978`。
约束文件使用该索引给出的官方 `download-r2.pytorch.org` URL 和内容 hash。
Context7 已查询 PyTorch CUDA 架构与运行时检查资料，但未提供 2.7.1 精确历史版本的
完整架构保证，因此最终兼容性以本机 wheel 架构列表及真实 P100 kernel 检查为依据。

## 独立安装与锁更新

工作目录为 `backend/`。以下命令不会触碰已有 `.venv`。
首次准备依赖需要公开包源网络；应用运行期仍保持离线。

```bash
uv venv --python .venv/bin/python .venv-cuda12
uv pip sync --python .venv-cuda12/bin/python --require-hashes requirements-cuda12.lock
uv pip check --python .venv-cuda12/bin/python
```

在没有 CPU 环境的新机器上，第一条改为 `uv venv --python 3.12 .venv-cuda12`。
已有 `.venv-cuda12` 时直接执行 sync，不重新创建。锁文件包含基础后端及
`semantic`、`llm`、`gliner` extras 和 dev 测试工具，不包含模型权重。
复现使用已提交的锁；只有明确更新依赖时才重新解析：

```bash
uv pip compile pyproject.toml \
  --extra semantic --extra llm --extra gliner --group dev \
  --no-sources --constraints requirements-cuda12.in \
  --generate-hashes --python-version 3.12 \
  --python-platform x86_64-manylinux_2_28 \
  --output-file requirements-cuda12.lock
```

`--no-sources` 在此独立解析时忽略 `tool.uv.sources`，由 CUDA 约束指定 torch，
并不删除或修改 CPU 源。不要使用 `uv sync` 来准备 GPU 环境；项目级 sync 仍遵循 CPU 配置。
运行 GPU 命令时使用 `.venv-cuda12/bin/python`，不要依赖当前 shell 的 `python` 或
让 `uv run` 自动同步另一个环境。

离线部署应在联网制品准备阶段交付完整镜像，或同平台完整 wheelhouse/构建制品及其 hash。
不能仅复制本锁到离线机器就假设依赖已存在。依赖锁、权重清单和运行配置应共同纳入制品身份。

## 指定单卡与最小兼容探测

首次验证先读取 GPU 当前占用，选择有足够余量的一张卡。现有主 LLM 可能已驻留多张卡；
不能自动使用全部 GPU 或通过重启 LLM 腾挪显存。
宿主 `CUDA_VISIBLE_DEVICES` 可使用稳定的 GPU UUID，该进程内仅暴露一张卡，因此排序
device 应设为 `cuda:0`，不能继续沿用宿主原始编号。

```bash
CUDA_VISIBLE_DEVICES=GPU_REPLACE_WITH_SELECTED_UUID \
  .venv-cuda12/bin/python - <<'PY'
import json
import torch

assert torch.version.cuda == "12.6", torch.version.cuda
assert torch.cuda.is_available()
assert torch.cuda.device_count() == 1
assert "sm_60" in torch.cuda.get_arch_list()
torch.set_num_threads(1)
checks = []
for dtype in (torch.float32, torch.float16):
    value = torch.arange(64, dtype=torch.float32).reshape(8, 8) / 64
    original = value.to(device="cuda:0", dtype=dtype)
    identity = torch.eye(8, device="cuda:0", dtype=dtype)
    result = original @ identity
    result = result + 1
    torch.cuda.synchronize()
    assert torch.equal(result.cpu(), value.to(dtype=dtype) + 1)
    checks.append({"dtype": str(dtype), "sum": result.sum().item()})
print(json.dumps({
    "torch": torch.__version__, "cuda": torch.version.cuda,
    "device": torch.cuda.get_device_name(0),
    "capability": torch.cuda.get_device_capability(0),
    "architectures": torch.cuda.get_arch_list(), "checks": checks,
}, indent=2))
PY
```

这只证明 wheel、驱动和 FP32/FP16 kernel 能在 P100 上实际运行，不证明模型速度、
长输入显存、排序一致性或图谱质量。GPU 主模型对比使用既有真实文档/排序脚本，另存新运行；
CPU 同依赖对照可用 GPU 环境显式 `device=cpu, dtype=float32`，并继续保留原 CPU 环境基线。
FP16 与 FP32 的数值差异必须实测，不预先声称排名或质量完全相同。

## Docker 27 的 NVIDIA runtime

本机为 Docker Engine `27.3.1`、默认 runtime `runc`，安装了 NVIDIA 官方稳定仓库的
以下四个包，版本均固定为 `1.20.0-1`：`nvidia-container-toolkit`、
`nvidia-container-toolkit-base`、`libnvidia-container-tools`、`libnvidia-container1`。
仅安装 toolkit 不会自动为已经运行的 dockerd 注册 GPU 设备驱动。

同类 Debian/Ubuntu 主机可在制品准备阶段按以下步骤安装；命令需要 root 权限及
已有的 `curl`、`gpg`。仅刷新 NVIDIA 来源，不升级其他系统包：

```bash
curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey \
  | sudo gpg --dearmor --yes \
      --output /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg
echo 'deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://nvidia.github.io/libnvidia-container/stable/deb/$(ARCH) /' \
  | sudo tee /etc/apt/sources.list.d/nvidia-container-toolkit.list >/dev/null
sudo apt-get update \
  -o Dir::Etc::sourcelist=/etc/apt/sources.list.d/nvidia-container-toolkit.list \
  -o Dir::Etc::sourceparts=- -o APT::Get::List-Cleanup=0
sudo env DEBIAN_FRONTEND=noninteractive NEEDRESTART_MODE=l apt-get install -y \
  nvidia-container-toolkit=1.20.0-1 nvidia-container-toolkit-base=1.20.0-1 \
  libnvidia-container-tools=1.20.0-1 libnvidia-container1=1.20.0-1
```

本次下载的官方签名主密钥指纹为
`C95B321B61E88C1809C4F759DDCAE044F796ECB0`。安装前检查了四包的 maintainer scripts，
没有 Docker stop/restart；安装只新增 NVIDIA 自身的 CDI refresh service/path 并更新
systemd 单元缓存。`NEEDRESTART_MODE=l` 只列出需要重启的其他服务，不自动重启。

本次为保留现有容器，只热加载命名 runtime，保留 `default-runtime=runc`：

1. 在 root 独占的 `0700` 目录备份 `/etc/docker/daemon.json`（备份权限 `0600`），
   记录原文件权限/属主、dockerd PID、每个现有容器 PID 和 `StartedAt`。
2. 将备份复制为私有 proposed 文件；只对 proposed 运行以下配置与校验命令。
   不使用 `--set-as-default` 或 `--enable-cdi`，不输出已有 daemon 配置内容。
3. 解析两份 JSON，确认唯一变化为增加
   `runtimes.nvidia={"args":[],"path":"/usr/bin/nvidia-container-runtime"}`。
   写入前再次确认实际配置仍等于备份，然后保留原权限/属主原子替换。
4. 核验当前 systemd 单元的 `ExecReload` 确实是向主进程发送 SIGHUP，再执行
   `systemctl reload docker`。核验 runtime 已出现、默认仍为 `runc`，且上述 PID、
   `StartedAt` 全部不变。失败时保留诊断与备份，不改用 restart。

```bash
# proposed 文件是原始配置的私有副本，不能用空配置覆盖现有配置。
sudo nvidia-ctk runtime configure --runtime=docker \
  --config=/var/tmp/ontology-agent-cuda12-runtime/daemon.proposed.json \
  --nvidia-runtime-path=/usr/bin/nvidia-container-runtime
sudo dockerd --validate \
  --config-file=/var/tmp/ontology-agent-cuda12-runtime/daemon.proposed.json
systemctl show docker --property=MainPID --property=CanReload --property=ExecReload
# 完成上述 JSON 差异检查、原子写入和 ExecReload 核验后：
sudo systemctl reload docker
docker info --format '{{range $name, $_ := .Runtimes}}{{$name}} {{end}}default={{.DefaultRuntime}}'
```

此流程依据 [Moby v27.3.1 的 runtime reload 实现](https://github.com/moby/moby/blob/v27.3.1/daemon/reload_unix.go)，
不是将 NVIDIA 官方常规安装文档中的 restart 直接替换成 reload。
该 Docker 版本在进程启动时才注册
[原生 CDI driver](https://github.com/moby/moby/blob/v27.3.1/cmd/dockerd/daemon.go) 和
[`--gpus` 的 NVIDIA driver](https://github.com/moby/moby/blob/v27.3.1/daemon/nvidia_linux.go)：
启动后才安装 toolkit 时，仅 SIGHUP 不能保证这两条通路可用。因此 GPU 部署配置明确使用
`runtime: nvidia`、单卡 `NVIDIA_VISIBLE_DEVICES` 和
`NVIDIA_DRIVER_CAPABILITIES=compute,utility`，不使用 `--gpus` 或 Compose GPU device reservation。
Toolkit 1.20 的 NVIDIA runtime 内部可采用 JIT-CDI 注入，这与 dockerd 原生 CDI 是否启用
是两件事。其他 Docker 版本或 systemd 单元需要重新核验，不能照搬热加载结论。

参考：[NVIDIA 官方安装指南](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html)、
[指定 runtime 与 GPU UUID](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/docker-specialized.html)、
[Toolkit 1.20.0 变更记录](https://github.com/NVIDIA/nvidia-container-toolkit/blob/v1.20.0/CHANGELOG.md)。
本次结合 Context7 文档、精确版本 Moby 源码与本机实际 CLI/单元行为验证。

## 默认 GPU 部署与 CPU 回切

`Dockerfile.cuda12` 固定 `python:3.12.7-slim-bookworm`，与本机准备环境的 Python
版本一致，并严格按 hash 锁安装依赖；
权重、运行数据和本地虚拟环境不进入镜像。镜像运行时由 NVIDIA Container Toolkit
提供宿主驱动与指定设备，构建阶段不运行 CUDA kernel。

基础 [docker-compose.yml](../docker-compose.yml) 现在使用
`ontology-agent-backend:cuda12-2.7.1`、`Dockerfile.cuda12` 和 `runtime: nvidia`；
默认 `docker compose` 自动合并的开发 override 保留这一选择，显式只用基础文件的生产模式
也使用 GPU。鉴权、数据库、卷、离线配置及既有模型能力保持各自契约。

GPU UUID/编号必须由 `SEMANTIC_RANKING_GPU_ID` 显式指定，不自动选择卡。
基础配置未设置时使用 `NVIDIA_VISIBLE_DEVICES=none`，使 CPU 覆盖入口无需 GPU ID
也能解析；已启用的 GPU 排序遇到不可用设备时按默认 `pause` 处理。
应用不会自动改用 CPU 或确定性排序。强制 GPU 的
[docker-compose.cuda12.yml](../docker-compose.cuda12.yml) 仍要求 GPU ID，适合显式覆盖其他配置。

```bash
# 工作目录：仓库根；实际值为选定的一张物理 GPU UUID 或编号。
export SEMANTIC_RANKING_GPU_ID=GPU_REPLACE_WITH_SELECTED_UUID
docker compose config --quiet
docker compose build backend
# 生产模式与强制 GPU 入口均须显式列出文件。
docker compose -f docker-compose.yml config --quiet
docker compose -f docker-compose.yml -f docker-compose.cuda12.yml config --quiet
```

显式 `-f` 不会自动合并本机 `docker-compose.override.yml`；需要开发覆盖时应明确将该文件
加入文件序列。上述 build 不启动或重启服务；实际切换运行版本须按运行状态安排。

Settings 与基础 Compose 默认 `SEMANTIC_RANKING_DEVICE=cuda:0`、
`SEMANTIC_RANKING_DTYPE=float16`、CUDA `12.6`、batch 4、pair 上限 4096 tokens、
排序超时 1200 秒、失败策略 `pause`。排序能力仍默认为关闭，四个制品路径仍默认为空；
启用须设置 `SEMANTIC_RANKING_ENABLED=true`，并同时提供：

| 配置 | 所需内容 |
|---|---|
| `SEMANTIC_RANKING_EMBEDDING_PATH` | 已校验的 BGE-M3 模型目录 |
| `SEMANTIC_RANKING_EMBEDDING_MANIFEST_PATH` | 该模型的完整、独立 SHA256 清单 |
| `SEMANTIC_RANKING_RERANKER_PATH` | 已校验的 BGE-reranker-v2-m3 模型目录 |
| `SEMANTIC_RANKING_RERANKER_MANIFEST_PATH` | 该模型的完整、独立 SHA256 清单 |

Compose 中使用挂载后的 `/app/models/...` 路径，宿主进程使用相应宿主路径。
制品准备与身份要求见 [特性 quickstart](../specs/022-semantic-graph-closure/quickstart.md)。
缺制品时不因选择了 GPU 镜像就视为已启用或模型可用。旧对齐/NER 的可选容错不适用于
已开启的图谱排序；默认 `pause` 会保留未完成状态。

Compose 中 `${VAR:-default}` 会由当前 shell、项目 `.env` 中非空的同名值覆盖，
shell 优先；在 CPU/GPU 间切换时须同时核对设备、精度、单卡 ID 和模型路径。
不要用完整 `docker compose config` 输出公开鉴权等配置；先执行 `config --quiet`，
需要诊断时只检查相关非敏感字段。

`DOCUMENT_ANALYSIS_DISPATCH_CONCURRENCY` 默认 1，保持单个活跃文档运行；
每个 run 的私有 worker 会持续驻留双模型，请求 capacity=1 只限制请求并发，
不能限制多个驻留 worker 的总显存。还须避免同卡其他评测进程或自动选择 CUDA 的
NER/其他本地模型同时加载。上述并发参数不能充当跨进程 GPU 显存租约。
GPU 部署保留旧 `SEMANTIC_ALIGNMENT_ENABLED` 和 `GLINER_EXTRACTION_ENABLED` 的既有配置，
不以关闭既有能力释放显存。交付前核验 `app/services/extraction/semantic.py` 的旧对齐加载器
显式固定 `device="cpu"`；已安装 GLiNER 0.2.29 的 `from_pretrained` 默认
`map_location="cpu"`，保持 CPU 加载。
新图谱排序单独按 `SEMANTIC_RANKING_DEVICE` 使用指定 GPU。外部评测进程仍须另行控制。
失败策略仍可由受控环境显式
覆盖；诊断 GPU 验收应保持 pause，避免把 CPU/确定性降级误认为 GPU 成功。

CPU 使用 [docker-compose.cpu.yml](../docker-compose.cpu.yml)，显式指定独立
`ontology-agent-backend:cpu` 镜像、原 `Dockerfile` 和 `runtime: runc`，固定
`DEVICE=cpu`、`DTYPE=float32`、`NVIDIA_VISIBLE_DEVICES=void`。
这三个值不从 GPU `.env` 继承；CPU 与 GPU 构建不会覆盖对方镜像 tag。
排序启用开关、制品路径以及其余预算仍沿用基础受控配置。下面的 `config` 只校验；
`up` 会应用配置并可能重建后端，须等待任务完成或暂停后执行：

```bash
# CPU 开发模式：CPU 覆盖必须放在开发 override 后。
docker compose -f docker-compose.yml -f docker-compose.override.yml \
  -f docker-compose.cpu.yml config --quiet
docker compose -f docker-compose.yml -f docker-compose.override.yml \
  -f docker-compose.cpu.yml up -d --build backend

# CPU 生产模式：只合并基础与 CPU 覆盖。
docker compose -f docker-compose.yml -f docker-compose.cpu.yml config --quiet
docker compose -f docker-compose.yml -f docker-compose.cpu.yml up -d --build backend
```

之后的 `build`、`up`、`run` 保持同一文件序列；去掉 CPU 覆盖会恢复 GPU 默认。
暂停不会清除运行的冻结身份；旧 CPU 运行恢复仍需原配置，切换 GPU 后应新建运行。
宿主直接运行保留的 CPU `.venv` 时，必须同时设置
`SEMANTIC_RANKING_DEVICE=cpu` 和 `SEMANTIC_RANKING_DTYPE=float32`，不能只改设备。
设备和精度属于运行冻结身份；已有 CPU 运行的恢复须保持原配置，不能沿用原运行身份
直接改成 GPU。

参考：[NVIDIA 官方指定 GPU 文档](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/docker-specialized.html)、
[多文件合并文档](https://docs.docker.com/compose/how-tos/multiple-compose-files/merge/)、
[uv 锁定与同步](https://docs.astral.sh/uv/pip/compile/)。本次查询结合了 Context7 与本机
`uv 0.7.6` 的实际参数帮助。

## 独立环境的历史验证记录

以下为 2026-09-09 UTC 默认切换之前完成的独立验证，保留当时的镜像身份与结果。
它们证明指定制品的兼容性，不代表后续源码已重新构建或共享后端已切换。

2026-09-09 UTC 已完成独立环境安装：97 个包按 `--require-hashes` 同步，
`uv pip check --python .venv-cuda12/bin/python` 通过。基础依赖保持原 CPU 已验证版本，
包括 NumPy 2.5.0、Pydantic 2.13.4、SymPy 1.14.0；pytest 9.1.1 和 Ruff 0.15.18 可用。
已导入 `SentenceTransformer` 与 `CrossEncoder`，没有加载任何模型权重。

02:56:09 UTC 在单可见 P100 上实际执行 FP32/FP16 的 8×8 矩阵乘法及加法：

| 项目 | 实测结果 |
|---|---|
| GPU | Tesla P100-PCIE-16GB，capability `(6, 0)` |
| GPU UUID | `60555528-1e13-e855-3dcc-b0a96e686045`（宿主 GPU 2） |
| PyTorch / CUDA / cuDNN | `2.7.1+cu126` / `12.6` / `90501` |
| wheel architectures | `sm_50, sm_60, sm_70, sm_75, sm_80, sm_86, sm_90` |
| FP32 / FP16 kernel | 均通过，输出 sum 均为 95.5 |
| PyTorch 最大张量分配 | 8,521,728 bytes；不代表 CUDA context 总显存或模型运行显存 |
| GPU probe 耗时 | 0.409 秒；从导入完成后开始，不作为推理性能基准 |

原始记录：仓库内 `evaluations/022-cuda12-environment-20260909/p100-kernel.json`。
安装锁 SHA256 为 `7b587fde9e937e6c95c7267aa71b19b801dee16a42cfa610b672f5444297f44f`。
CPU `uv.lock` 仍为 `8e092fb4906e8c2a212452bb3b79dc45c112b75785114099ac6eef46d0344252`，
CPU `.venv` 未修改。GPU Compose 的 `config --quiet` 与文档 Bash 语法检查通过。

独立镜像 `ontology-agent-backend:cuda12-2.7.1` 已构建成功，首次构建 image ID 为
`sha256:d68f3d431b94fb50338babf39d8f92458f978387c758e8f156124f7badea9a24`，
本机显示大小 6.72 GB。使用 `docker run --rm --network none --entrypoint python`
执行依赖导入和 `pip check` 均通过；容器中的 Python 3.12.7、torch 2.7.1+cu126、
CUDA 12.6 及锁 hash 与上述环境一致。已检查镜像没有本地 `.venv`、`.venv-cuda12`
或 `models` 目录。该容器没有请求 GPU、加载模型或启动后端。

03:27 UTC 完成 NVIDIA runtime 配置与 SIGHUP 热加载：仅增加 `runtimes.nvidia`，
默认 runtime 保持 `runc`；dockerd PID 保持 `2552`，全部 43 个既有容器的 PID 和
`StartedAt` 不变。没有重启 Docker、后端或主 LLM。原始记录：
`evaluations/022-cuda12-environment-20260909/docker-runtime-reload.json`；含原 daemon
配置的私有备份保留在 `/var/tmp/ontology-agent-cuda12-runtime-20260909/`，不进入仓库。

随后按当时的应用源码完成增量构建，同一镜像 tag 更新为
`sha256:c64455d8a298c6e2833f3b7e66e830a8f6f304229b0ecedc0416ff782823d67e`。
03:28 UTC 用 `--network none --runtime=nvidia`、
`NVIDIA_VISIBLE_DEVICES=GPU-b46b10ee-a6f1-1037-3796-2b46496cc93a` 及
`NVIDIA_DRIVER_CAPABILITIES=compute,utility` 运行独立临时容器，确认只看到宿主 GPU 3，
FP32/FP16 的 8×8 kernel 均通过。torch/CUDA/cuDNN、锁 hash 与宿主 GPU 环境一致。
未加载模型，容器已退出；0.431 秒探测和 8,521,728 bytes 最大张量分配仍只代表该极小
kernel，不代表模型性能或总显存。原始记录：
`evaluations/022-cuda12-environment-20260909/container-p100-kernel.json`。

03:31–03:33 UTC 上述镜像进一步通过真实双模型冒烟：禁用网络、只读挂载已校验权重，
仅透传宿主 GPU 2，容器使用 `cuda:0` / `float16` / CUDA 12.6。合成输入的完整双意图
排序无降级，18 个请求全部完成，恢复新增 0 请求；总墙钟 105.13 秒，退出码 0。
原始记录：[容器模型报告](../evaluations/022-cuda12-upload-23c872fb-20260909/container-work/smoke-01/report.json)。
该临时容器已退出，没有启动应用 lifespan、数据库迁移或主 LLM，共享后端未切换部署。

指定文档的 CPU/GPU 性能、排名差异、显存边界及四卡限制见
[GPU 排序验收](../specs/022-semantic-graph-closure/gpu-ranking.md)。当前适配器使用单卡，
两模型分卡、多卡分批或单模型跨卡分片均需要额外实现；四张卡的空闲显存不能直接相加。

## 默认切换的初次交付状态

2026-09-09 UTC 本次修改将配置默认值与 Compose 部署入口改为 GPU，保留显式 CPU 入口。
检查时共享后端仍有 2 个有效执行租约，因此未重建或重启该服务。切换前共享服务没有
排序环境变量，按当时默认值为关闭、制品路径为空；不能将独立 GPU 验收解释为共享服务
已启用排序。本机根 `.env` 本次仅追加
`SEMANTIC_RANKING_GPU_ID=GPU-60555528-1e13-e855-3dcc-b0a96e686045`，选择宿主 GPU 2；
其他字段保留，排序能力仍关闭，四项模型路径尚未配置。
本轮配置验证、增量镜像与后续切换状态见
[GPU 默认配置交付记录](../specs/022-semantic-graph-closure/gpu-default.md)，
证据另存 `evaluations/022-gpu-default-20260909/`，不覆盖上述历史验收。
以上保留默认配置变更时的记录。用户后续已要求显式开启，线上后端现已加载
`SEMANTIC_RANKING_ENABLED=true`、`MODE=semantic` 和四项完整模型路径；最新切换、
旧运行状态及真实双模型验证见 [线上启用记录](../specs/022-semantic-graph-closure/gpu-online.md)。
