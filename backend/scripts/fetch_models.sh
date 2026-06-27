#!/usr/bin/env bash
#
# fetch_models.sh — 离线本地模型供给（GLiNER NER + 中文嵌入器）。
#
# 设计目标（research R8 / FR-013）：平台部署于 **air-gap（无网络）** 环境，运行期
# 严禁出网。因此模型权重在**有网的预备环境**经本脚本一次性下载到 backend/models/，
# 再随制品库（artifact repo）连同 SHA256 校验和交付到 air-gap 主机；运行期以
# `local_files_only=True` + `HF_HUB_OFFLINE=1`/`TRANSFORMERS_OFFLINE=1` 纯本地加载。
#
# 权重**不入 git**（.gitignore 已忽略 backend/models/）。本脚本与生成的
# `MODELS.sha256` 才是可审计的交付凭据。
#
# 用法（在**有网**的预备环境执行）：
#   backend/scripts/fetch_models.sh            # 下载两个模型 + 生成校验和清单
#   backend/scripts/fetch_models.sh --verify   # 仅按既有 MODELS.sha256 校验本地权重
#
# 前置：pip install -U "huggingface_hub[cli]"（仅预备环境需要，air-gap 主机不需要）。
#
set -euo pipefail

# 仓内固定布局：backend/models/<model-dir>，与 settings.gliner_model_path /
# settings.semantic_embedding_model 默认值（"models/<...>"，相对 backend/）一致。
BACKEND_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MODELS_DIR="${BACKEND_ROOT}/models"
MANIFEST="${MODELS_DIR}/MODELS.sha256"

# repo_id → 本地子目录名。子目录名 MUST 与 config.py 默认 *_model_path 末段一致。
GLINER_REPO="urchade/gliner_multi-v2.1"
GLINER_DIR="gliner_multi-v2.1"
EMBED_REPO="BAAI/bge-small-zh-v1.5"
EMBED_DIR="bge-small-zh-v1.5"

# 跨平台 sha256（macOS: shasum -a 256；Linux: sha256sum）。
_sha256() {
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$@"
  else
    shasum -a 256 "$@"
  fi
}

# 为 MODELS_DIR 下所有权重文件生成相对路径校验和清单（确定性排序，可逐字比对）。
write_manifest() {
  echo "[fetch_models] 生成校验和清单 → ${MANIFEST}"
  ( cd "${MODELS_DIR}" \
      && find . -type f ! -name "MODELS.sha256" -print0 \
      | LC_ALL=C sort -z \
      | xargs -0 _sha256 ) > "${MANIFEST}"
  echo "[fetch_models] 已登记 $(wc -l < "${MANIFEST}" | tr -d ' ') 个文件的 SHA256。"
}

# 按既有清单校验（air-gap 主机收货时执行，证明权重逐字未被篡改/截断）。
verify_manifest() {
  if [[ ! -f "${MANIFEST}" ]]; then
    echo "[fetch_models] ✗ 缺少 ${MANIFEST}，无法校验。" >&2
    exit 1
  fi
  echo "[fetch_models] 按 ${MANIFEST} 校验本地权重 …"
  ( cd "${MODELS_DIR}" && _sha256 -c "MODELS.sha256" )
  echo "[fetch_models] ✓ 校验通过。"
}

download_one() {
  local repo="$1" subdir="$2"
  echo "[fetch_models] 下载 ${repo} → models/${subdir}/"
  huggingface-cli download "${repo}" \
    --local-dir "${MODELS_DIR}/${subdir}" \
    --local-dir-use-symlinks False
}

main() {
  mkdir -p "${MODELS_DIR}"

  if [[ "${1:-}" == "--verify" ]]; then
    verify_manifest
    return
  fi

  if ! command -v huggingface-cli >/dev/null 2>&1; then
    echo "[fetch_models] ✗ 未找到 huggingface-cli。请在**有网**预备环境执行：" >&2
    echo "    pip install -U 'huggingface_hub[cli]'" >&2
    exit 1
  fi

  download_one "${GLINER_REPO}" "${GLINER_DIR}"
  download_one "${EMBED_REPO}" "${EMBED_DIR}"

  # GLiNER 的 HF repo 不含 tokenizer 文件；加载时需从 backbone
  # microsoft/mdeberta-v3-base 解析 SentencePiece tokenizer。有网环境自动下载，
  # 但 air-gap 下 local_files_only=True 会阻断。解决：用 transformers 把
  # backbone tokenizer 保存到 GLiNER 模型目录，使 gliner/model.py:_load_tokenizer
  # 检测到 tokenizer_config.json 后走纯本地分支。
  echo "[fetch_models] 补存 mdeberta-v3-base tokenizer → models/${GLINER_DIR}/"
  python3 -c "
from transformers import AutoTokenizer
tok = AutoTokenizer.from_pretrained('microsoft/mdeberta-v3-base')
tok.save_pretrained('${MODELS_DIR}/${GLINER_DIR}')
print('[fetch_models] tokenizer 已写入（tokenizer_config.json + spm.model 等）')
"

  # GLiNER 加载时若 gliner_config.json 缺少 encoder_config，会调用
  # AutoConfig.from_pretrained("microsoft/mdeberta-v3-base") 试图从 HF Hub
  # 拉取 backbone 架构配置。在 air-gap 下此调用必然失败。解决：将 backbone
  # 的 DebertaV2Config 嵌入 gliner_config.json，使加载路径纯本地。
  echo "[fetch_models] 注入 encoder_config → models/${GLINER_DIR}/gliner_config.json"
  python3 -c "
import json
from transformers import AutoConfig
path = '${MODELS_DIR}/${GLINER_DIR}/gliner_config.json'
cfg = json.loads(open(path).read())
if cfg.get('encoder_config') is None:
    enc = AutoConfig.from_pretrained('microsoft/mdeberta-v3-base')
    cfg['encoder_config'] = enc.to_dict()
    open(path, 'w').write(json.dumps(cfg, indent=2, ensure_ascii=False))
    print('[fetch_models] encoder_config 已注入')
else:
    print('[fetch_models] encoder_config 已存在，跳过')
"

  write_manifest

  cat <<EOF
[fetch_models] ✓ 完成。交付到 air-gap 主机后，于 backend/ 下执行校验：
    backend/scripts/fetch_models.sh --verify
运行期强制离线（已在 gliner_extractor / semantic 加载路径置 local_files_only=True）；
另建议导出 env：HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1（双保险，零外发）。
EOF
}

main "$@"
