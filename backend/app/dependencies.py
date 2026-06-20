from fastapi import Depends
from sqlalchemy.orm import Session

from app.db import get_db
from app.services.kg_store import KGStore
from app.services.ontology_engine import OntologyEngine, ontology_engine


def get_ontology_engine() -> OntologyEngine:
    if not ontology_engine.is_loaded:
        raise RuntimeError("Ontology not loaded")
    return ontology_engine


def get_kg_store(
    db: Session = Depends(get_db),
    engine: OntologyEngine = Depends(get_ontology_engine),
) -> KGStore:
    return KGStore(db=db, onto_engine=engine)
