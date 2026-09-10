# 默认 GPU 排序配置

日期：2026-09-09 UTC。用户在 CUDA 12 增量验收后要求默认 reranker 改为 GPU。
本次变更替代原 CPU 默认约定；不改写 [GPU 实测](gpu-ranking.md) 或 CPU 历史制品。

本页保留默认配置变更时的状态；用户后续已明确开启线上排序，最新生效与验证记录见
[gpu-online.md](gpu-online.md)。

应用 `Settings` 与基础 Compose 默认 `cuda:0 / float16 / CUDA 12.6`，batch 4、
超时 1200 秒、文档 dispatch 并发 1、失败 pause。共享排序适配器同时执行 embedding
和 reranker，二者使用同一张卡；旧实体对齐与 GLiNER 仍使用 CPU。

基础镜像为 `ontology-agent-backend:cuda12-2.7.1`，由 `Dockerfile.cuda12` 构建，
使用 `runtime: nvidia`。部署须指定 `SEMANTIC_RANKING_GPU_ID`；未指定时
`NVIDIA_VISIBLE_DEVICES=none`，不会自动占用 GPU 0 或全部卡。
排序能力仍须显式开启且提供完整校验的模型路径；新默认不把缺配置包装为成功。

CPU 使用 [独立覆盖](../../docker-compose.cpu.yml)，固定 CPU 镜像、Dockerfile、runc、
`cpu/float32` 及 `NVIDIA_VISIBLE_DEVICES=void`，即使宿主有 GPU dtype 配置也不会继承。
GPU 覆盖仍可显式用于覆盖 CPU 配置，但须再次选择物理卡。
安装与操作见 [GPU_CUDA12.md](../../backend/GPU_CUDA12.md)。

新默认只适用于重新加载配置后的新运行。已冻结设置、legacy CPU 补值和历史制品不修改，
设备或政策漂移不能冒充恢复。诊断脚本保留 CPU 缺省，真实 GPU 验收继续显式传参。

## 验证与生效状态

共享服务预检发现两个持有效租约且已冻结指纹的 recognition 运行，未请求暂停或取消。
当前共享后端仍为 CPU/runc 环境，排序能力关闭、排序制品路径未配置；数据库 revision
与代码 head 均为 `0033_document_analysis_runs`。本次不打断这些运行，不重启共享服务。
03:59:17→04:00:48 UTC 两运行的事件水位各增加 10、11，租约持续更新，确认仍在推进。
根 `.env` 仅追加已验收 GPU 2 的 UUID，其他配置保留；排序能力开关仍关闭。

- 五种隔离 Compose 组合通过：基础 GPU、开发 GPU、无 GPU ID 的 CPU、宿主 GPU
  device/dtype 环境下的 CPU、CPU 文件之后强制 GPU；另核验本机实际合并为 GPU 2。
  [配置证据](../../evaluations/022-gpu-default-20260909/compose-checks.json)，
  [本机非敏感配置](../../evaluations/022-gpu-default-20260909/local-compose.json)。
- 五个受影响测试文件 **95 passed / 4 既有依赖警告**：数值配置、GPU 适配器、
  基础适配器、在线恢复及排序执行。初次为 91 passed / 2 failed：旧漂移测试依赖 CPU
  默认，改默认后“切到 GPU”变成无变化；已显式冻结起始设备/精度，并补 CPU↔GPU、
  float32↔float16 两个方向，仍要求漂移拒绝且零新调用，没有放宽生产门禁。
  [最终回归日志](../../evaluations/022-gpu-default-20260909/regression-final.log)。
- 默认 GPU 镜像构建成功，新 ID 为
  `sha256:992bbffc11625bcc14fabe815316df708a7d473ecdfec1c2439f84fb4887cd3b`。
  独立禁网、无模型挂载的单卡容器从镜像直接读取 Settings 默认，验证
  `cuda:0/float16/12.6`、实际 GPU 2 UUID 和真实 CUDA kernel 均通过。
  未加载权重、未启动应用 lifespan；这次验证默认配置与运行库，不重跑已验收的模型性能。
  [镜像](../../evaluations/022-gpu-default-20260909/image.json)，
  [容器默认探测](../../evaluations/022-gpu-default-20260909/container-default-probe.json)。
- 定向 Ruff 通过；CPU `.venv`、`uv.lock`、Dockerfile 及 GPU 依赖锁未修改。

文件默认已修改，镜像已构建；线上 GPU 排序尚未启用。旧冻结运行恢复仍需原配置，
不能仅暂停后直接让其继承新 GPU 默认。T017 专家质量门保持原状态。
