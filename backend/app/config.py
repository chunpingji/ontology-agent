from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str = "postgresql://slpra:slpra_dev@localhost:5432/slpra"
    ontology_dir: Path = Path(__file__).resolve().parent.parent.parent / "ontology" / "slpra"
    owl_store_path: Path = Path(__file__).resolve().parent.parent / "data" / "slpra.sqlite3"
    anthropic_api_key: str = ""

    # 实体对齐 · 语义模糊匹配（aligner.align_entity）：前置类别相等 + 标签向量
    # 余弦相似度过阈即判 merge。本地 sentence-transformers 后端，启用需安装可选
    # 依赖 `uv sync --extra semantic`；未安装时优雅回退到字面匹配。
    semantic_alignment_enabled: bool = True
    # 本地权重目录（相对 backend/，由 scripts/fetch_models.sh 预置；air-gap 运行期
    # 以 local_files_only=True 加载，零外发）。改默认为本地路径，去 HF repo id 解析。
    semantic_embedding_model: str = "models/bge-small-zh-v1.5"  # 中文域小模型(~100MB)
    semantic_match_threshold: float = 0.82  # 语义余弦阈值
    lexical_match_threshold: float = 0.85  # 字面 SequenceMatcher 阈值

    # 能力八：离线本地实体抽取（008-gliner-ner-extraction）。air-gap 环境无法访问云端
    # LLM，故抽取引擎以本地为默认：结构化源确定性映射、自由文本经本地零样本 NER
    # （GLiNER），云端 LLM 降为 opt-in（默认关）。启用本地 NER 需 `uv sync --extra
    # gliner` 并预置权重；缺包/缺权重时优雅降级、跳过 NER（结构化主路径零回归）。
    gliner_extraction_enabled: bool = True
    gliner_model_path: str = "models/gliner_multi-v2.1"  # 本地权重目录（相对 backend/）
    gliner_threshold: float = 0.5  # 零样本 NER 置信阈值（research R10，待中文样本标定）
    # 云端 LLM 触发**双条件门控**：仅 llm_cloud_enabled AND anthropic_api_key 才调云端。
    # 默认关——离线为正常态，绝不因「无 Key」误标 degraded（FR-002/003，research R2）。
    llm_cloud_enabled: bool = False

    # 能力十二：本地 LLM 补抽（012-ast-template-llm-pipeline）。AST 覆盖缺口二次抽取，
    # 使用 OpenAI 兼容端点连接本地 LLM（Ollama / vLLM）。默认关——启用需 `uv sync
    # --extra llm` 并确保本地端点可达；未启用或不可达时优雅降级、跳过补抽（零回归）。
    local_llm_enabled: bool = False
    local_llm_base_url: str = "http://localhost:11434/v1"
    local_llm_model: str = "qwen2.5:14b"
    local_llm_model_revision: str = ""  # immutable local model artifact identity
    local_llm_tokenizer_path: str = ""  # local tokenizer.json from the same model artifact
    local_llm_tokenizer_backend: str = "file"  # file | llama_server (explicit local deployment)
    local_llm_server_model_path: str = ""  # must match /props; revision pins the delivered GGUF
    evidence_max_input_tokens: int = 16384
    evidence_max_output_tokens: int = 2048
    evidence_max_tasks: int = 2048
    evidence_max_regions_per_task: int = 32
    evidence_max_objects_per_task: int = 8
    evidence_timeout_s: float = 600.0
    evidence_timeout_retries: int = 3
    evidence_world_dir: Path = Path(__file__).resolve().parent.parent / "data" / "evidence-worlds"
    local_llm_api_key: str = "not-needed"
    local_llm_max_tokens: int = 200000
    local_llm_temperature: float = 0.1
    # Operational limits intentionally excluded from semantic TaskBudget/input_id.
    local_llm_max_concurrency: int = 2
    local_llm_total_timeout_s: float = 600.0
    evidence_total_timeout_s: float = 600.0

    # Word 章节树分层摘要（018）。仅当本开关与 local_llm_enabled 同时开启才调用
    # 本地端点；关闭是 air-gap 正常态，树和分页仍完整返回。
    llm_word_tree_summary_enabled: bool = True
    word_tree_summary_timeout_s: int = 120
    word_tree_summary_max_input_chars_per_node: int = 6000
    word_tree_summary_max_batch_chars: int = 24000
    word_tree_summary_max_nodes_per_batch: int = 20
    word_tree_summary_max_concurrency: int = Field(default=2, ge=1, le=8)
    word_tree_summary_max_output_chars: int = 300
    word_tree_summary_prompt_version: str = "word-tree-summary-v2"

    # Ontology-guided document analysis (021).  Source files and derived
    # artifacts are owned by a DocumentAnalysisRun, never by ExtractionJob.
    document_analysis_storage_dir: Path = (
        Path(__file__).resolve().parent.parent / "data" / "document-analysis-runs"
    )
    document_analysis_max_upload_bytes: int = 50 * 1024 * 1024
    document_analysis_retention_days: int = 7
    document_analysis_lease_seconds: int = 120
    document_analysis_dispatch_poll_seconds: float = 1.0
    document_analysis_dispatch_concurrency: int = 1
    document_analysis_sse_window_seconds: int = 30
    document_analysis_worker_id: str = "document-analysis-worker"
    document_analysis_max_model_calls_per_record: int = Field(default=6, ge=1, le=32)
    document_analysis_performance_enabled: bool = True
    document_analysis_template_interleaving: bool = False

    # 022: optional offline ranking; independent from entity alignment and
    # the required recognition model. CUDA 12.6/FP16 is the deployment default;
    # CPU remains an explicit cpu/float32 option. Limits are frozen into each run.
    semantic_ranking_enabled: bool = False
    # Default for newly created runs. Existing runs use their audited control flag.
    semantic_ranking_budget_enabled: bool = True
    semantic_ranking_mode: Literal["deterministic", "semantic"] = "semantic"
    semantic_ranking_failure_policy: Literal["deterministic", "pause"] = "pause"
    semantic_ranking_embedding_path: str = ""
    semantic_ranking_embedding_manifest_path: str = ""
    semantic_ranking_reranker_path: str = ""
    semantic_ranking_reranker_manifest_path: str = ""
    semantic_ranking_device: str = Field(default="cuda:0", pattern=r"^(cpu|cuda:(0|[1-9][0-9]*))$")
    semantic_ranking_dtype: Literal["float32", "float16"] = "float16"
    semantic_ranking_cuda_version: Literal["12.6"] = "12.6"
    semantic_ranking_pool_size: int = Field(default=64, ge=1, le=1024)
    semantic_ranking_batch_size: int = Field(default=4, ge=1, le=256)
    semantic_ranking_max_tokens_per_pair: int = Field(default=4096, ge=64, le=32768)
    semantic_ranking_max_tokens_per_slot: int = Field(default=524288, ge=64)
    semantic_ranking_max_tokens_per_run: int = Field(default=4194304, ge=64)
    semantic_ranking_timeout_seconds: float = Field(default=1200.0, gt=0, le=3600)
    semantic_ranking_retry_limit: int = Field(default=1, ge=0, le=3)

    @model_validator(mode="after")
    def semantic_ranking_precision_matches_device(self):
        if self.semantic_ranking_device == "cpu" and self.semantic_ranking_dtype != "float32":
            raise ValueError("CPU semantic ranking requires float32")
        return self

    # 能力十三：LLM 模板设计辅助 + 报告生成增强（013-llm-template-report-enhance）。
    # 三个独立开关默认关——离线为正常态（Constitution VI）。
    llm_suggest_slots_enabled: bool = True
    llm_report_merge_values: bool = True
    llm_report_narrative_enabled: bool = True
    suggest_slots_timeout_s: int = 30
    suggest_slots_max: int = 50

    # 能力三：实时事实源轮询与报告输出（002-extraction-realtime-reasoning, R4/R12）
    aps_poll_interval_seconds: int = 2
    realtime_polling_enabled: bool = False  # 启动期 asyncio 轮询任务开关（测试默认关）
    report_output_dir: Path = Path(__file__).resolve().parent.parent / "data" / "reports"
    # APS 连接凭据仅经 env 引用，不入库（R7）；连接器 connection_config 仅存 dsn_ref 键名

    # 能力六：QA 21 CFR Part 11 电子签名重认证密钥（经 env 注入，不入库, R7/R10）。
    # 身份层可插拔：企业 SSO 接入前以共享重认证密钥占位（SSO 不在本特性范围）。
    qa_reauth_secret: str = "qa-reauth"

    # 认证层：登录 + API 访问控制。air-gap 环境仅用标准库自签令牌（app/auth.py），
    # 不引 jwt/passlib。auth_secret 经 env 注入、不入库（同 qa_reauth_secret 范式）。
    # auth_required 默认关：为 False 时 get_current_user 沿用信任 `X-User`/`X-Role` 头
    # 的旧行为（200+ 头认证测试零回归）；运行时经 docker env 置 True 后，中间件对
    # `/api/*`（除 /api/auth/login、/api/health）强制校验 Bearer 令牌。
    auth_secret: str = "dev-slpra-auth-secret-change-me"
    auth_required: bool = False
    auth_token_ttl_seconds: int = 43200  # 令牌有效期，默认 12h

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


settings = Settings()
