"""Read current mapped sources through the existing service, within a frozen schema."""

from types import SimpleNamespace

from fastapi import HTTPException
from sqlalchemy import select

from app.models.ontology_meta import OntologyClassMapping
from app.schemas.entity_query import EntityQueryRequest
from app.services.entity_query import EntityQueryService

from .ontology import freeze_catalog


def lookup_current(db, ontology_engine, operation, catalog, argument):
    # No configured source is an ordinary capability absence. Do not load a World
    # just to discover that fact (including isolated, source-free executions).
    if db.scalar(select(OntologyClassMapping.id).where(
        OntologyClassMapping.mapping_type == "mock_dataset",
    ).limit(1)) is None:
        return {"capabilities": [], "results": [], "issues": ["no_queryable_mapping"]}
    if not ontology_engine.is_loaded:
        return {"capabilities": [], "results": [], "issues": ["lookup_ontology_unavailable"]}
    with ontology_engine.lexical_read_scope():
        current = freeze_catalog(ontology_engine, catalog.root_class_iri)
        graph = ontology_engine.entity_query_graph()
    if current.snapshot_id != catalog.snapshot_id:
        return {"capabilities": [], "results": [], "issues": ["lookup_ontology_changed"]}
    service = EntityQueryService(db, SimpleNamespace(entity_query_graph=lambda: graph))
    if operation == "capabilities":
        return {"capabilities": service.capabilities(argument), "issues": []}
    if operation != "query":
        raise ValueError("unknown_lookup_operation")
    capabilities = argument["capabilities"]
    available = {(c["mapping_id"], c["class_iri"]): c for c in service.capabilities(
        [c["class_iri"] for c in capabilities],
    )}
    for c in capabilities:
        actual = available.get((c["mapping_id"], c["class_iri"]))
        if actual is None or actual["mapping_revision"] != c["mapping_revision"]:
            return {"results": [], "issues": ["lookup_mapping_changed"]}
    request = EntityQueryRequest.model_validate({"queries": argument["queries"]})
    allowed = {(c["mapping_id"], c["class_iri"]) for c in capabilities}
    if any(not q.mapping_ids or any((str(mid), q.class_iri) not in allowed
                                   for mid in q.mapping_ids) for q in request.queries):
        raise ValueError("lookup_mapping_outside_capabilities")
    try:
        return {**service.query(request), "issues": []}
    except HTTPException:
        return {"results": [], "issues": ["lookup_query_no_longer_valid"]}
