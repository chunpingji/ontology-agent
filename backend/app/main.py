import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api import (
    actions,
    ast_templates,
    auth,
    compliance,
    document_analysis,
    entities,
    extraction,
    integration,
    kg,
    mock_sources,
    ontology,
    pde_conflict,
    reasoning,
    reports,
    system_config,
)
from app.config import settings
from app.dependencies import identity_from_authorization, identity_from_token
from app.services.ontology_engine import ontology_engine

logger = logging.getLogger(__name__)


def _run_migrations() -> None:
    """Apply Alembic migrations to head at startup (R6, T008)."""
    from alembic.config import Config

    from alembic import command

    backend_root = Path(__file__).resolve().parent.parent
    cfg = Config(str(backend_root / "alembic.ini"))
    cfg.set_main_option("script_location", str(backend_root / "alembic"))
    command.upgrade(cfg, "head")


def _seed_from_ttl() -> None:
    """Idempotently project the authoritative TTL into the metadata tables (T013).

    Then seed the declarative rule layer (spec 006 T013/T014): the new
    `hasBetaLactamRing` data property + R-DC1~4 classification criteria, which
    reference the E1–E3 entities seeded above and so must run after them.
    """
    from app.db import SessionLocal
    from app.services.ontology_meta_store import OntologyMetaStore
    from app.services.reasoning.seed_declarative import seed_declarative_rules

    db = SessionLocal()
    try:
        OntologyMetaStore(db=db, engine=ontology_engine).project_from_ttl()
        seed_declarative_rules(db)
    finally:
        db.close()


# 认证账号播种：确保三角色 + 默认账号存在（幂等）。仅在账号无口令摘要时写入，避免
# 每次重启覆盖已改口令；明文仅此处即时哈希，绝不入库。密钥/口令经 env 注入为宜。
_SEED_USERS = (
    ("admin", "admin1234!", "senior_analyst", "管理员"),
    ("operator", "operator1234!", "operator", "操作员"),
    ("qa", "qa1234!", "qa", "质量员"),
)


def _seed_auth_users() -> None:
    """幂等播种登录账号（认证层）。启动期调用；测试不跑 lifespan 故不受影响。"""
    from app.auth import hash_password
    from app.db import SessionLocal
    from app.models.ontology_meta import ROLE_NAMES, AppRole, AppUser

    db = SessionLocal()
    try:
        roles: dict[str, AppRole] = {}
        for rname in ROLE_NAMES:
            role = db.query(AppRole).filter_by(name=rname).first()
            if role is None:
                role = AppRole(name=rname, description=rname)
                db.add(role)
                db.flush()
            roles[rname] = role
        for username, password, role_name, display in _SEED_USERS:
            user = db.query(AppUser).filter_by(username=username).first()
            if user is None:
                db.add(
                    AppUser(
                        username=username,
                        display_name=display,
                        role_id=roles[role_name].id,
                        password_hash=hash_password(password),
                    )
                )
            elif not user.password_hash:
                user.password_hash = hash_password(password)
                if user.role_id is None:
                    user.role_id = roles[role_name].id
        db.commit()
    finally:
        db.close()


_recompute_subscriber_registered = False


def _register_recompute_subscriber() -> None:
    """幂等注册自动重算订阅者到 `fact_event_bus`（FR-010）。"""
    global _recompute_subscriber_registered
    if _recompute_subscriber_registered:
        return
    from app.services.integration.events import fact_event_bus
    from app.services.reasoning.recompute_subscriber import make_recompute_subscriber

    fact_event_bus.subscribe(make_recompute_subscriber())
    _recompute_subscriber_registered = True
    logger.info("auto-recompute subscriber registered on fact_event_bus")


def _warmup_local_models() -> None:
    """预热本地 NER / 语义嵌入模型（008 FR-014，消除首作业冷启动，SC-007）。

    ``get_gliner_extractor()`` / ``get_embedder()`` 在对应功能关闭或缺包时返回
    ``None``（零开销）；返回实例时 ``is_available()`` 触发本地权重惰性加载，缺权重则
    静默降级。任何异常都不阻断启动（air-gap / 缺权重环境仍可启动，结构化主路径零回归）。
    """
    from app.services.extraction.gliner_extractor import get_gliner_extractor
    from app.services.extraction.semantic import get_embedder

    for name, factory in (("GLiNER NER", get_gliner_extractor), ("语义嵌入", get_embedder)):
        try:
            backend = factory()
            if backend is not None and backend.is_available():
                logger.info("%s 模型预热完成", name)
        except Exception as exc:  # pragma: no cover - 缺权重/加载失败不阻断启动
            logger.warning("%s 模型预热跳过：%s", name, exc)


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        _run_migrations()
    except Exception as exc:  # pragma: no cover - keep app bootable in dev
        logger.warning("Alembic migration skipped: %s", exc)
    ontology_engine.load()
    try:
        _seed_from_ttl()
    except Exception as exc:  # pragma: no cover
        logger.warning("TTL projection seeding skipped: %s", exc)

    # 认证层：幂等播种登录账号（admin/operator/qa）。缺列/建库前失败不阻断启动。
    try:
        _seed_auth_users()
    except Exception as exc:  # pragma: no cover
        logger.warning("Auth user seeding skipped: %s", exc)

    # 003 G3：注册事实变更 → 增量重算订阅者（FR-010）。幂等守卫避免重复注册
    # （reload / 多次 lifespan）导致一次事件触发多次重算。
    _register_recompute_subscriber()

    # 008 FR-014/SC-007：启动期预热本地 NER / 嵌入模型，消除首作业冷启动。
    # 功能关闭/缺包/缺权重均零开销或静默降级，不阻断启动。
    _warmup_local_models()

    # 能力三：启动期 asyncio 轮询后台任务挂载点（R4, T037）。默认关闭，避免测试期起任务。
    poller_task = None
    if settings.realtime_polling_enabled:  # pragma: no cover - 仅生产/手动开启
        import asyncio

        from app.services.integration.poller import run_polling_loop

        poller_task = asyncio.create_task(run_polling_loop())

    yield

    if poller_task is not None:  # pragma: no cover
        poller_task.cancel()
    ontology_engine.close()


app = FastAPI(
    title="SLPRA Platform",
    description="Clinical Drug Intelligent Assisted Production Platform",
    version="0.1.0",
    lifespan=lifespan,
)

# API 访问控制（FR-033）：`auth_required=True` 时对 `/api/*` 强制校验 Bearer 令牌，
# 放行预检 OPTIONS、健康检查与登录端点。默认关——为 False 时完全放行（沿用信任头旧行为，
# 200+ 头认证测试零回归）。此中间件先注册 → 内层；CORS 随后注册 → 最外层，保证 401
# 响应亦携带 CORS 头（浏览器方能读取错误）。
_AUTH_ALLOWLIST_EXACT = {"/api/health", "/api/auth/login"}


@app.middleware("http")
async def enforce_auth(request: Request, call_next):
    if not settings.auth_required:
        return await call_next(request)
    path = request.url.path
    if (
        request.method == "OPTIONS"
        or not path.startswith("/api/")
        or path in _AUTH_ALLOWLIST_EXACT
    ):
        return await call_next(request)
    identity = identity_from_authorization(
        request.headers.get("authorization")
    ) or identity_from_token(request.query_params.get("token"))
    if identity is None:
        return JSONResponse(status_code=401, content={"detail": "未认证：请先登录"})
    request.state.identity = identity
    return await call_next(request)


app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router, prefix="/api/auth", tags=["auth"])
app.include_router(ontology.router, prefix="/api/ontology", tags=["ontology"])
app.include_router(entities.router, prefix="/api/entities", tags=["entities"])
app.include_router(reasoning.router, prefix="/api/reasoning", tags=["reasoning"])
app.include_router(
    document_analysis.router,
    prefix="/api/document-analysis",
    tags=["document-analysis"],
)
app.include_router(extraction.router, prefix="/api/extraction", tags=["extraction"])
app.include_router(pde_conflict.router, prefix="/api/extraction", tags=["pde-conflict"])
app.include_router(kg.router, prefix="/api/kg", tags=["knowledge-graph"])
app.include_router(integration.router, prefix="/api/integration", tags=["integration"])
app.include_router(actions.router, prefix="/api/actions", tags=["actions"])
app.include_router(reports.router, prefix="/api/reports", tags=["reports"])
app.include_router(compliance.router, prefix="/api/compliance", tags=["compliance"])
app.include_router(system_config.router, prefix="/api/system-config", tags=["system-config"])
app.include_router(ast_templates.router, prefix="/api/ast-templates", tags=["ast-templates"])
app.include_router(mock_sources.router, prefix="/api/mock-sources", tags=["mock-sources"])


@app.get("/api/health")
def health():
    return {
        "status": "ok",
        "modules_loaded": ontology_engine.is_loaded,
        "module_count": len(ontology_engine.modules) if ontology_engine.is_loaded else 0,
    }
