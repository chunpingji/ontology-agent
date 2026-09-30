"""Standalone browser fixture with real routers, PostgreSQL and an isolated ontology World.

Run from backend with ENTITY_QUERY_BROWSER_ROOT and a disposable *_test DATABASE_URL.
No application lifespan, scheduler, live World or shared Mock data is used.
"""

import os
from pathlib import Path

from fastapi import Depends, FastAPI
from sqlalchemy.engine import make_url

from app.config import settings

url = make_url(settings.database_url)
if url.get_backend_name() != "postgresql" or not (url.database or "").endswith("_test"):
    raise RuntimeError("Browser fixture requires a dedicated PostgreSQL *_test database")
root = Path(os.environ["ENTITY_QUERY_BROWSER_ROOT"])
root.mkdir(parents=True, exist_ok=True)
settings.auth_required = True
settings.auth_secret = "mapped-entity-query-disposable-browser-fixture"

from app.api import auth, entities, mock_sources, ontology  # noqa: E402
from app.auth import hash_password  # noqa: E402
from app.db import Base, SessionLocal  # noqa: E402
from app.db import engine as db_engine  # noqa: E402
from app.dependencies import get_current_user, get_ontology_engine  # noqa: E402
from app.models.mock_data import MockEquipment, MockProductionArea  # noqa: E402
from app.models.ontology_meta import AppRole, AppUser  # noqa: E402
from app.services.ontology_engine import OntologyEngine  # noqa: E402
from app.services.ontology_meta_store import OntologyMetaStore  # noqa: E402

Base.metadata.create_all(db_engine)
ontology_dir = Path(__file__).resolve().parents[3] / "ontology/slpra"
settings.ontology_dir = ontology_dir
engine = OntologyEngine(ontology_dir=ontology_dir, store_path=root / "world.sqlite3")
engine.load()
with SessionLocal() as db:
    OntologyMetaStore(db, engine).project_from_ttl()
    if not db.query(AppUser).filter_by(username="query-test").first():
        role = db.query(AppRole).filter_by(name="senior_analyst").first()
        if not role:
            role = AppRole(name="senior_analyst")
            db.add(role)
            db.flush()
        db.add(
            AppUser(
                username="query-test",
                role_id=role.id,
                password_hash=hash_password("query-test-only"),
            )
        )
        for code in ("642", "644", "646"):
            db.add(
                MockProductionArea(code=code, iri="urn:browser:area:" + code, label=code + "车间")
            )
        for i in range(1, 62):
            db.add(
                MockEquipment(
                    equipment_id=f"EQ-{i:03}",
                    label=f"验收设备 {i:03}",
                    iri=f"urn:browser:equipment:{i:03}",
                    workshop_code="642",
                    equipment_class_iri="https://ontology.pharma-gmp.cn/slpra/equipment/Equipment",
                )
            )
        db.commit()

app = FastAPI()
app.include_router(auth.router, prefix="/api/auth")
app.include_router(entities.router, prefix="/api/entities")
app.include_router(ontology.router, prefix="/api/ontology")
app.include_router(
    mock_sources.router, prefix="/api/mock-sources", dependencies=[Depends(get_current_user)]
)
app.dependency_overrides[get_ontology_engine] = lambda: engine
