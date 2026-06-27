from pathlib import Path

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

    # 能力三：实时事实源轮询与报告输出（002-extraction-realtime-reasoning, R4/R12）
    aps_poll_interval_seconds: int = 2
    realtime_polling_enabled: bool = False  # 启动期 asyncio 轮询任务开关（测试默认关）
    report_output_dir: Path = Path(__file__).resolve().parent.parent / "data" / "reports"
    # APS 连接凭据仅经 env 引用，不入库（R7）；连接器 connection_config 仅存 dsn_ref 键名

    # 能力六：QA 21 CFR Part 11 电子签名重认证密钥（经 env 注入，不入库, R7/R10）。
    # 身份层可插拔：企业 SSO 接入前以共享重认证密钥占位（SSO 不在本特性范围）。
    qa_reauth_secret: str = "qa-reauth"

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


settings = Settings()
