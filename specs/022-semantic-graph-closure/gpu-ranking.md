# CUDA 12 GPU 排序与 CPU 对照

记录日期：2026-09-09 UTC。新增单卡 GPU 版本，**固定 CUDA 12.6 / PyTorch 2.7.1+cu126**，
保留默认 CPU/float32 与原 `backend/.venv`、`uv.lock`、CPU Dockerfile。
本轮 GPU 工程验收独立于 [T017 质量门](acceptance.md)，不修改专家参考或历史制品。

本页保留 GPU 增量验收时的结果；用户后续已要求默认引擎改为 GPU，当前默认配置与
最新镜像/共享服务状态见 [gpu-default.md](gpu-default.md)。

## 1. 实现与部署入口

- [适配器](../../backend/app/services/llm/semantic_ranking.py) 支持显式 `cpu` 或 `cuda:N`；
  dtype 为 float32/float16，CPU 仅接受 float32。GPU 实际 CUDA runtime 必须为 12.6。
- 模型只从完整 SHA256 清单覆盖的本地 safetensors 加载。factory 不加载权重；GPU
  使用独立 spawn 进程检查架构、设备 UUID、驱动、CUDA/cuDNN 与真实小矩阵 kernel。
  worker 再核验冻结环境和实际参数 device/dtype。P100 固定 eager attention，关闭 TF32
  和 FP16 reduced-precision reduction，不采用 FlashAttention 或 BF16。
- CPU/GPU、精度、逻辑设备/UUID、CUDA/驱动/库版本、线程/可见性/矩阵与 allocator
  环境进入运行与缓存身份。配置改变不能继续原运行；旧 GPU 信息缺失不会继承当前配置。
- 原始输入不截断；L2 embedding 与单标签 raw logit 契约不变。OOM/不可用按冻结政策
  暂停或整体确定性降级，不静默改用 CPU 语义模型；失败保留费用和原因。
- 在线 factory/probe 接入 owner 的取消/失租 scope。已冻结成功运行遇瞬态 GPU probe
  失败时保留水位并允许恢复；真正的配置漂移仍拒绝，未核验身份不派发识别。
- 每个 run 私有 worker，请求 capacity=1 并非显存租约；GPU 部署默认文档并发 1、batch 4。
  旧实体对齐器显式使用 CPU，GLiNER 保留原 CPU 加载及既有开关。

环境安装、独立镜像、Docker GPU runtime 与显式覆盖入口见
[GPU_CUDA12.md](../../backend/GPU_CUDA12.md)、
[Dockerfile.cuda12](../../backend/Dockerfile.cuda12)、
[docker-compose.cuda12.yml](../../docker-compose.cuda12.yml)。
GPU 依赖使用 [独立 hash 锁](../../backend/requirements-cuda12.lock)，不回写 CPU 锁。

## 2. 实际硬件与依赖

| 项目 | 本轮实测 |
|---|---|
| GPU | Tesla P100-PCIE-16GB，宿主 GPU 2，compute capability 6.0 |
| 驱动 | 580.126.09 |
| Python / torch / CUDA / cuDNN | 3.12.7 / 2.7.1+cu126 / 12.6 / 90501 |
| ST / Transformers / tokenizers | 5.6.0 / 5.6.2 / 0.22.2 |
| NumPy / safetensors | 2.5.0 / 0.8.0 |
| GPU UUID | 60555528-1e13-e855-3dcc-b0a96e686045 |
| wheel 架构 | sm_50、sm_60、sm_70、sm_75、sm_80、sm_86、sm_90 |
| CPU | Xeon E5-2697 v4；OMP/MKL 各 4 线程 |
| 权重 | 原已校验 BGE-M3 / BGE-reranker-v2-m3；两个模型 FP16 理论权重合计约 2.116 GiB |

[真实 kernel 制品](../../evaluations/022-cuda12-environment-20260909/p100-kernel.json)
确认 FP32、FP16 均执行成功。`nvidia-smi` 的 CUDA 上限不当作运行库版本；新增环境未引入 CUDA 13 依赖。
官方依据：经 Context7 查询的 PyTorch 架构/设备 API、Sentence Transformers 的
`device`/精度配置，以及 [PyTorch 官方历史版本页](https://pytorch.org/get-started/previous-versions/)
提供的 cu126 wheel。P100 的兼容性由实际 kernel 和下述模型运行共同验证。

## 3. 同文档、同依赖、同输入对照

授权原件 `upload-23c872fb-3ab1-41de-a705-dd4b162dfa09`，SHA256
`e94822808601e73f4ba4c715c664812d4a01e42f0e4e84a6b08a3653b915daf7`。
两组使用同一个 `prepared-05` 原件、IR、本体快照和显式 CMCReport 根；谓词为
`drug-development/describes`，一池 86 records、172 双意图输入，batch 4、pair 上限 4096、
超时 1200 秒、失败 pause、无重试。两次均在 `.venv-cuda12` 内，固定相同包版本，
CPU 为 float32/default attention，GPU 为 float16/eager，数值设置差异完整登记。

[只读比较器](../../backend/scripts/compare_semantic_ranking_runs.py) 严格核对同一原件/IR/本体、
模型包/制品、执行源码 hash、完整 query/view 文本、record 顺序、预算、逐对真实 token、
调度归属及恢复，**共同输入门通过**。两次各有 155 个完成请求（88 tokenizer、23 embedding、
44 reranker），实测与预扣均为 212,100 tokens；无未知请求、无降级、无截断，恢复各新增 0 请求。
台账仍有 86 条 unattempted；本轮没有运行主识别模型或建立业务事实。

| 耗时（秒） | CPU float32 | GPU float16 | CPU/GPU |
|---|---:|---:|---:|
| 排序检查总墙钟，含初始化/检查/恢复 | 882.46 | 147.42 | 5.99× |
| 单个完整 epoch | 849.91 | 107.99 | 7.87× |
| reranker 请求合计，含加载等请求内开销 | 686.89 | 39.16 | 17.54× |
| reranker 推理调用，含结果拷回 CPU 的同步边界 | 675.93 | 28.50 | 23.72× |
| embedding 请求合计 | 109.85 | 15.48 | 7.10× |
| embedding 推理调用 | 98.73 | 4.25 | 23.23× |
| tokenizer 请求合计 | 40.70 | 41.96 | 0.97× |
| 两模型加载调用合计 | 5.56 | 6.38 | — |

这是共享宿主的一轮 CPU/GPU 版本观测，不是重复统计或只改变硬件的因果实验。
CPU 基线期间并行执行了 GPU 边界/清理及隔离回归；两轮文档基线本身没有同时运行。
没有重新测量主 LLM、完整图谱路径或端到端图谱识别速度，不将 5.99× 外推到整条流程。
冷态指新 worker/模型，未清除 OS 文件页缓存。`inference_seconds` 在返回 NumPy 后结束，
包含同步/搬运；它不是 CUDA event 测得的纯 kernel 时间。

排名对照：两个意图及最终融合的 top-10 集合都为 10/10 重合；最终 Spearman 为
**0.999849**，16/86 个最终名次改变。原始 logit 最大绝对差 **0.014221**、平均绝对差
**0.002381**。这证明数值近似与部分名次变化，不证明事实 precision/recall 改善。

制品：

- [CPU 新基线](../../evaluations/022-cuda12-upload-23c872fb-20260909/cpu-document-01/report.json)
- [GPU 新基线](../../evaluations/022-cuda12-upload-23c872fb-20260909/gpu-document-01/report.json)
- [严格比较结果](../../evaluations/022-cuda12-upload-23c872fb-20260909/cpu-gpu-comparison-01.json)
- [原 CPU 环境的真实冒烟](../../evaluations/022-cuda12-upload-23c872fb-20260909/cpu-original-smoke/report.json)：
  104.60 秒，完整双意图、无降级、恢复 0 新请求；它不与另一依赖环境混作相同版本基线。

## 4. 显存边界与四卡问题

| 输入 | PyTorch allocated 峰值 | reserved 峰值 | nvidia-smi 私有 worker 峰值 |
|---|---:|---:|---:|
| 指定文档，batch 4，最长 pair 1622 tokens | 2.83 GiB | 3.86 GiB | 4.15 GiB |
| 长度边界，4 × 4067-token pair | 6.23 GiB | 6.28 GiB | 6.57 GiB |

进程峰值以 0.5 秒采样匹配该运行的 worker PID；allocator 指标为 worker 真实请求返回值。
它们不是整卡总占用。长输入 embedding 为 4 × 4063 tokens，也完整成功；超长 7171-token
输入分别被 embedding/reranker 拒绝，worker 清理后恢复成功，两个预期失败的未知输入费用
明确保留。见 [长度边界报告](../../evaluations/022-cuda12-upload-23c872fb-20260909/gpu-boundaries-batch4-01/report.json)。

运行这份文档建议单卡至少约 5 GiB 空闲；batch 4 接近 4096 tokens 时建议至少 8 GiB 空闲，
并为其他 GPU 进程保留余量。本次主 LLM 仅保留既有驻留权重，没有并发主识别请求；尚未验收
主 LLM 新请求增加 KV cache 时的共同显存峰值。不能由 16 GiB 总容量推断实际空闲，也不能将四卡空闲相加。
本轮未证明 float32 GPU 模型的长输入显存上界，或更大 batch/更多并发的能力。

**本次实现是单卡。** 显存分布到四张卡需另增执行适配：可将两个模型分到两卡，或将
batch 分配到四卡的模型副本；单模型跨卡拆层还会引入传输，且 attention 中间内存未必同比
下降。当前权重可在单卡容纳，减少 batch 或两模型分卡通常比四卡拆单模型更直接。
这里没有宣称已实现多卡调度、张量并行或显存合并。

## 5. 故障、恢复与工程验证

- 首轮真实 GPU 清理 **6/6 通过**：embedding/reranker 分别取消、超时、OOM。
  取消/超时使用已加载双模型及约 3993-token record × batch 4；OOM 使用当前子进程
  `set_per_process_memory_fraction(0.03)` 将 allocator 限为约 488 MiB，触发真实 CUDA OOM，
  不占满共享显卡。98 个请求中的 6 个失败保留未知费用，active/late 均为 0。
- 每场在调度 finish 入口的**第一个**样本已无私有进程及 GPU PID，采样次数均为 1。
  0.1 秒是中断触发阈值，报告耗时包含清理/采样，不能当作生产取消延时。
  [首轮结果](../../evaluations/022-cuda12-upload-23c872fb-20260909/gpu-cleanup-01/result.json)
  与全部失败记录保留。
- 首轮退出时发现 4 个 tqdm 默认 multiprocessing lock 的 semaphore 清理警告；最终
  worker 改为私有 `threading.RLock`，不跨进程共享进度状态。定向实测结果见下方收尾记录。
- CPU 环境 14 个受影响测试文件 **194 passed**，涵盖 adapter、输入/预算、CUDA 边界、
  数值冻结、固定池、严格比较、在线恢复及 API。另有 GPU 环境的定向回归，见收尾记录。
  已有 4 项依赖警告保留；本轮没有重新运行前端或 PostgreSQL 测试。

对照实验完成后只增加了私有进度锁清理及旧对齐器 CPU 设备固定；两组性能制品保持原冻结
源码身份，不将它们改写成收尾后的 runtime。后续定向回归与真实取消复测验证这两项修正。
T017 专家质量/成本验收仍未完成；GPU 性能与工程验收不能替代该门。

## 6. 收尾记录

- 私有线程锁修正后，真实 GPU 取消/超时定向复测 **2/2 通过**；每场首个样本已无私有
  GPU PID，48 请求中 2 个预期失败，active/late 均为 0。退出 stderr 不再有
  semaphore/resource_tracker 警告，14 项制品 hash 一致。
  [复测结果](../../evaluations/022-cuda12-upload-23c872fb-20260909/gpu-cleanup-02/result.json)，
  [退出日志](../../evaluations/022-cuda12-upload-23c872fb-20260909/gpu-cleanup-02.stderr.log)。
- 最终修正后在 GPU 依赖环境运行 adapter、CUDA、CPU 对齐设备、smoke、调度、依赖边界
  五文件组合 **59 passed**，4 项既有依赖警告；Ruff 与差异空白检查通过。
- NVIDIA Container Toolkit **1.20.0-1** 安装完成，Docker 仅新增命名 nvidia runtime，
  通过 SIGHUP 热加载；默认 runc、dockerd PID 及全部 43 个既有容器 PID/StartedAt 未变。
  [热加载证据](../../evaluations/022-cuda12-environment-20260909/docker-runtime-reload.json)。
- 最终镜像 `ontology-agent-backend:cuda12-2.7.1` 已重建，image ID 为
  `sha256:c64455d8a298c6e2833f3b7e66e830a8f6f304229b0ecedc0416ff782823d67e`。
  03:31–03:33 UTC 以 `--network none --runtime nvidia`、GPU 2 单卡透传、只读模型挂载
  执行真实双模型冒烟：CUDA 12.6 / FP16，**passed、105.13 秒**，18 请求全部完成，
  3,977 实测输入 tokens、完整双意图、无降级/未知请求，恢复状态不变且新增 0 请求。
  [容器报告](../../evaluations/022-cuda12-upload-23c872fb-20260909/container-work/smoke-01/report.json)，
  [退出状态](../../evaluations/022-cuda12-upload-23c872fb-20260909/container-work/exit.json)。
  本次容器使用合成输入验证最终镜像；它不替代上面的指定文档对照和长输入边界。
- 临时容器正常退出，未启动应用 lifespan、迁移或主 LLM；没有部署或重启共享后端。
