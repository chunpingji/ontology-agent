from pathlib import Path

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str = "postgresql://slpra:slpra_dev@localhost:5432/slpra"
    ontology_dir: Path = Path(__file__).resolve().parent.parent.parent / "ontology" / "slpra"
    owl_store_path: Path = Path(__file__).resolve().parent.parent / "data" / "slpra.sqlite3"
    anthropic_api_key: str = ""

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
