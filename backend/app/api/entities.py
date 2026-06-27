from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db import get_db
from app.dependencies import get_kg_store, get_ontology_engine
from app.schemas.entity import (
    CreateIndividualRequest,
    EntitySearchResponse,
    EntityShadowResponse,
    IndividualResponse,
    UpdateIndividualRequest,
)
from app.services.kg_store import KGStore
from app.services.ontology_engine import OntologyEngine

router = APIRouter()


@router.get("", response_model=EntitySearchResponse)
def list_entities(
    q: str | None = None,
    module: str | None = None,
    class_iri: str | None = None,
    development_phase: str | None = None,
    page: int = 1,
    page_size: int = 20,
    kg: KGStore = Depends(get_kg_store),
):
    items, total = kg.search_entities(query=q, module=module, class_iri=class_iri,
                                      development_phase=development_phase,
                                      page=page, page_size=page_size)
    return EntitySearchResponse(
        items=[EntityShadowResponse.model_validate(i) for i in items],
        total=total, page=page, page_size=page_size,
    )


@router.get("/{iri:path}", response_model=IndividualResponse)
def get_entity(
    iri: str,
    engine: OntologyEngine = Depends(get_ontology_engine),
    kg: KGStore = Depends(get_kg_store),
):
    # T-Box-declared individuals live in the owlready2 World; A-Box facts
    # (materialized records / documents / extracted entities, e.g. facts#...)
    # live only in the EntityShadow DB table. Resolve the World first, then
    # fall back to the shadow row so fact-source individuals open in detail view.
    info = engine.get_individual(iri)
    if info is not None:
        return IndividualResponse(**info.__dict__)
    shadow = kg.get_shadow(iri)
    if shadow is None:
        raise HTTPException(404, f"Entity not found: {iri}")
    return IndividualResponse(
        iri=shadow.iri,
        name=iri.rsplit("/", 1)[-1].rsplit("#", 1)[-1],
        class_iris=[shadow.class_iri] if shadow.class_iri else [],
        label_zh=shadow.label_zh,
        label_en=shadow.label_en,
        properties=shadow.properties_json or {},
    )


@router.post("", response_model=IndividualResponse, status_code=201)
def create_entity(
    req: CreateIndividualRequest,
    engine: OntologyEngine = Depends(get_ontology_engine),
    kg: KGStore = Depends(get_kg_store),
):
    try:
        info = engine.create_individual(req.class_iri, req.name, req.properties)
    except ValueError as e:
        raise HTTPException(400, str(e))
    kg.sync_individual_to_shadow(info)
    return IndividualResponse(**info.__dict__)


@router.put("/{iri:path}", response_model=IndividualResponse)
def update_entity(
    iri: str,
    req: UpdateIndividualRequest,
    engine: OntologyEngine = Depends(get_ontology_engine),
    kg: KGStore = Depends(get_kg_store),
):
    try:
        info = engine.update_individual(iri, req.properties)
    except ValueError as e:
        raise HTTPException(404, str(e))
    kg.sync_individual_to_shadow(info)
    return IndividualResponse(**info.__dict__)


@router.delete("/{iri:path}", status_code=204)
def delete_entity(
    iri: str,
    engine: OntologyEngine = Depends(get_ontology_engine),
    kg: KGStore = Depends(get_kg_store),
):
    try:
        engine.delete_individual(iri)
    except ValueError as e:
        raise HTTPException(404, str(e))
    kg.delete_shadow(iri)


@router.post("/sync", response_model=dict)
def sync_shadows(kg: KGStore = Depends(get_kg_store)):
    count = kg.sync_all_individuals()
    return {"synced": count}
