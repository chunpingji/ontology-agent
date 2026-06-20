from fastapi import APIRouter, Depends, HTTPException

from app.dependencies import get_ontology_engine
from app.schemas.ontology import ClassDetailResponse, ModuleResponse, TreeNodeResponse
from app.services.ontology_engine import OntologyEngine

router = APIRouter()


@router.get("/modules", response_model=list[ModuleResponse])
def list_modules(engine: OntologyEngine = Depends(get_ontology_engine)):
    modules = engine.get_modules()
    return [ModuleResponse(**m.__dict__) for m in modules]


@router.get("/{module}/classes", response_model=list[TreeNodeResponse])
def get_class_hierarchy(module: str, engine: OntologyEngine = Depends(get_ontology_engine)):
    tree = engine.get_class_hierarchy(module)
    if not tree:
        raise HTTPException(404, f"Module not found or empty: {module}")
    return [_tree_to_response(n) for n in tree]


@router.get("/classes/{class_iri:path}", response_model=ClassDetailResponse)
def get_class_detail(class_iri: str, engine: OntologyEngine = Depends(get_ontology_engine)):
    detail = engine.get_class_detail(class_iri)
    if detail is None:
        raise HTTPException(404, f"Class not found: {class_iri}")
    return ClassDetailResponse(**detail.__dict__)


def _tree_to_response(node) -> TreeNodeResponse:
    return TreeNodeResponse(
        iri=node.iri,
        name=node.name,
        label=node.label,
        individual_count=node.individual_count,
        children=[_tree_to_response(c) for c in node.children],
    )
