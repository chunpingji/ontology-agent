import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import entities, extraction, integration, kg, ontology, reasoning
from app.services.ontology_engine import ontology_engine

logger = logging.getLogger(__name__)


def _run_migrations() -> None:
    """Apply Alembic migrations to head at startup (R6, T008)."""
    from alembic import command
    from alembic.config import Config

    backend_root = Path(__file__).resolve().parent.parent
    cfg = Config(str(backend_root / "alembic.ini"))
    cfg.set_main_option("script_location", str(backend_root / "alembic"))
    command.upgrade(cfg, "head")


def _seed_from_ttl() -> None:
    """Idempotently project the authoritative TTL into the metadata tables (T013)."""
    from app.db import SessionLocal
    from app.services.ontology_meta_store import OntologyMetaStore

    db = SessionLocal()
    try:
        OntologyMetaStore(db=db, engine=ontology_engine).project_from_ttl()
    finally:
        db.close()


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
    yield
    ontology_engine.close()


app = FastAPI(
    title="SLPRA Platform",
    description="Clinical Drug Intelligent Assisted Production Platform",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(ontology.router, prefix="/api/ontology", tags=["ontology"])
app.include_router(entities.router, prefix="/api/entities", tags=["entities"])
app.include_router(reasoning.router, prefix="/api/reasoning", tags=["reasoning"])
app.include_router(extraction.router, prefix="/api/extraction", tags=["extraction"])
app.include_router(kg.router, prefix="/api/kg", tags=["knowledge-graph"])
app.include_router(integration.router, prefix="/api/integration", tags=["integration"])


@app.get("/api/health")
def health():
    return {
        "status": "ok",
        "modules_loaded": ontology_engine.is_loaded,
        "module_count": len(ontology_engine.modules) if ontology_engine.is_loaded else 0,
    }
