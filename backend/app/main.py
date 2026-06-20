from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import entities, extraction, integration, kg, ontology, reasoning
from app.services.ontology_engine import ontology_engine


@asynccontextmanager
async def lifespan(app: FastAPI):
    ontology_engine.load()
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
