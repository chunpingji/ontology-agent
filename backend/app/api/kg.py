from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.dependencies import get_kg_store, get_ontology_engine
from app.services.kg_store import KGStore
from app.services.ontology_engine import OntologyEngine

router = APIRouter()


class SPARQLRequest(BaseModel):
    query: str


class GraphNode(BaseModel):
    id: str
    label: str | None = None
    type: str
    module: str | None = None


class GraphEdge(BaseModel):
    source: str
    target: str
    label: str


class GraphResponse(BaseModel):
    nodes: list[GraphNode]
    edges: list[GraphEdge]


@router.post("/sparql", response_model=list[dict[str, Any]])
def sparql_query(req: SPARQLRequest, engine: OntologyEngine = Depends(get_ontology_engine)):
    try:
        return engine.sparql_query(req.query)
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.get("/stats")
def kg_stats(kg: KGStore = Depends(get_kg_store)):
    return kg.get_stats()


@router.get("/graph", response_model=GraphResponse)
def get_graph(
    center_iri: str | None = None,
    depth: int = 1,
    engine: OntologyEngine = Depends(get_ontology_engine),
):
    nodes = []
    edges = []
    all_individuals = engine.get_all_individuals()

    seen_iris = set()
    for ind in all_individuals:
        if center_iri and center_iri != ind.iri:
            continue
        if ind.iri in seen_iris:
            continue
        seen_iris.add(ind.iri)

        module = _detect_module(ind.class_iris)
        nodes.append(GraphNode(
            id=ind.iri,
            label=ind.label_zh or ind.label_en or ind.name,
            type=ind.class_iris[0] if ind.class_iris else "unknown",
            module=module,
        ))

        for prop_iri, val in ind.properties.items():
            prop_name = prop_iri.rsplit("/", 1)[-1].rsplit("#", 1)[-1]
            if isinstance(val, dict) and "iri" in val:
                target_iri = val["iri"]
                edges.append(GraphEdge(source=ind.iri, target=target_iri, label=prop_name))
                if target_iri not in seen_iris:
                    seen_iris.add(target_iri)
                    target = engine.get_individual(target_iri)
                    if target:
                        nodes.append(GraphNode(
                            id=target_iri,
                            label=target.label_zh or target.label_en or target.name,
                            type=target.class_iris[0] if target.class_iris else "unknown",
                            module=_detect_module(target.class_iris),
                        ))

        if center_iri:
            break

    return GraphResponse(nodes=nodes, edges=edges)


def _detect_module(class_iris: list[str]) -> str:
    prefixes = {
        "drug": "/slpra/drug/", "equipment": "/slpra/equipment/",
        "contamination": "/slpra/contamination/", "risk": "/slpra/risk/",
        "cleaning": "/slpra/cleaning/", "facility": "/slpra/facility/",
        "document": "/slpra/document/",
    }
    for cls_iri in class_iris:
        for mod, prefix in prefixes.items():
            if prefix in cls_iri:
                return mod
    return "integration"
