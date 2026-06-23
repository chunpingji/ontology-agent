"""Ontology T-Box API (能力一 — 知识模型维护工作台).

只读浏览端点（modules / hierarchy / class detail）基于 Owlready2 引擎；
可编辑工作台端点（CRUD / 约束 / 映射 / 校验 / 导入导出 / 批次发布 / 审计）
基于 OntologyMetaStore（元数据草稿为真源 + 乐观并发 + 双存储同步）。

路由顺序要点：贪婪的 ``GET /classes/{class_iri:path}`` 必须最后声明，
否则会吞掉 ``/classes/{iri}/mappings`` 等更具体的子资源路由。
"""

from fastapi import APIRouter, Body, Depends, HTTPException, Response

from app.dependencies import (
    ROLE_SENIOR_ANALYST,
    Identity,
    get_ontology_engine,
    get_ontology_meta_store,
    require_role,
)
from app.schemas.ontology import (
    ActionCreate,
    ActionDetail,
    ActionUpdate,
    ClassCreate,
    ClassDetail,
    ClassDetailResponse,
    ClassUpdate,
    DataPropertyCreate,
    DataPropertyDetail,
    DataPropertyUpdate,
    DiffResult,
    ImportResult,
    LinkTypeCreate,
    LinkTypeDetail,
    LinkTypeUpdate,
    Mapping,
    MappingCreate,
    MappingHealth,
    MappingUpdate,
    ModuleResponse,
    ReleaseDetail,
    ReleaseSummary,
    RestrictionCreate,
    RestrictionSummary,
    RestrictionUpdate,
    RiskDataPropertyCreate,
    RiskVocabulary,
    TreeNodeResponse,
    ValidationReport,
)
from app.services.ontology_engine import OntologyEngine
from app.services.ontology_meta_store import OntologyMetaStore

router = APIRouter()

# Writes/publish require the senior_analyst role (R7, FR-033).
_writer = require_role(ROLE_SENIOR_ANALYST)


# ===========================================================================
# 只读浏览（Owlready2 引擎）—— 顶层只读端点
# ===========================================================================
@router.get("/modules", response_model=list[ModuleResponse])
def list_modules(engine: OntologyEngine = Depends(get_ontology_engine)):
    modules = engine.get_modules()
    return [ModuleResponse(**m.__dict__) for m in modules]


# ===========================================================================
# T023 风险受控词表（须在贪婪类路由之前）
# ===========================================================================
@router.get("/risk-vocabularies", response_model=list[RiskVocabulary])
def list_risk_vocabularies(store: OntologyMetaStore = Depends(get_ontology_meta_store)):
    return store.risk_vocabularies()


# ===========================================================================
# T026 映射健康度（须在贪婪类路由之前）
# ===========================================================================
@router.get("/mappings/health", response_model=MappingHealth)
def mappings_health(store: OntologyMetaStore = Depends(get_ontology_meta_store)):
    return store.mappings_health()


# ===========================================================================
# T021 Class CRUD + disable/review
# ===========================================================================
@router.post("/classes", response_model=ClassDetail, status_code=201)
def create_class(
    payload: ClassCreate,
    identity: Identity = Depends(_writer),
    store: OntologyMetaStore = Depends(get_ontology_meta_store),
):
    return store.create_class(payload, identity.username)


@router.post("/classes/{iri:path}/disable", response_model=ClassDetail)
def disable_class(
    iri: str,
    body: dict = Body(...),
    identity: Identity = Depends(_writer),
    store: OntologyMetaStore = Depends(get_ontology_meta_store),
):
    return store.set_class_flag(
        iri, _expected_version(body), identity.username, is_disabled=True
    )


@router.post("/classes/{iri:path}/review", response_model=ClassDetail)
def review_class(
    iri: str,
    body: dict = Body(...),
    identity: Identity = Depends(_writer),
    store: OntologyMetaStore = Depends(get_ontology_meta_store),
):
    return store.set_class_flag(
        iri, _expected_version(body), identity.username, is_reviewed=True
    )


# T025 约束
@router.post(
    "/classes/{iri:path}/restrictions",
    response_model=RestrictionSummary,
    status_code=201,
)
def create_restriction(
    iri: str,
    payload: RestrictionCreate,
    identity: Identity = Depends(_writer),
    store: OntologyMetaStore = Depends(get_ontology_meta_store),
):
    return store.create_restriction(iri, payload, identity.username)


# T026 映射（按类）
@router.get("/classes/{iri:path}/mappings", response_model=list[Mapping])
def list_mappings(
    iri: str, store: OntologyMetaStore = Depends(get_ontology_meta_store)
):
    return store.list_mappings(iri)


@router.post(
    "/classes/{iri:path}/mappings", response_model=Mapping, status_code=201
)
def create_mapping(
    iri: str,
    payload: MappingCreate,
    identity: Identity = Depends(_writer),
    store: OntologyMetaStore = Depends(get_ontology_meta_store),
):
    return store.create_mapping(iri, payload, identity.username)


@router.put("/classes/{iri:path}", response_model=ClassDetail)
def update_class(
    iri: str,
    payload: ClassUpdate,
    identity: Identity = Depends(_writer),
    store: OntologyMetaStore = Depends(get_ontology_meta_store),
):
    return store.update_class(iri, payload, identity.username)


@router.delete("/classes/{iri:path}", status_code=204)
def delete_class(
    iri: str,
    expected_version: int,
    identity: Identity = Depends(_writer),
    store: OntologyMetaStore = Depends(get_ontology_meta_store),
):
    store.delete_class(iri, expected_version, identity.username)
    return Response(status_code=204)


# ===========================================================================
# T022 对象属性 / 关系
# ===========================================================================
@router.get("/link-types", response_model=list[LinkTypeDetail])
def list_link_types(
    domain_iri: str | None = None,
    include_inherited: bool = False,
    store: OntologyMetaStore = Depends(get_ontology_meta_store),
):
    """列出对象属性 / 关系；可选 domain_iri 仅返回 domain 挂接该类的关系，
    include_inherited=true 时并入继承自祖先类的关系。"""
    return store.list_link_types(domain_iri, include_inherited)


@router.post("/link-types", response_model=LinkTypeDetail, status_code=201)
def create_link_type(
    payload: LinkTypeCreate,
    identity: Identity = Depends(_writer),
    store: OntologyMetaStore = Depends(get_ontology_meta_store),
):
    return store.create_link_type(payload, identity.username)


@router.put("/link-types/{iri:path}", response_model=LinkTypeDetail)
def update_link_type(
    iri: str,
    payload: LinkTypeUpdate,
    identity: Identity = Depends(_writer),
    store: OntologyMetaStore = Depends(get_ontology_meta_store),
):
    return store.update_link_type(iri, payload, identity.username)


@router.delete("/link-types/{iri:path}", status_code=204)
def delete_link_type(
    iri: str,
    expected_version: int,
    identity: Identity = Depends(_writer),
    store: OntologyMetaStore = Depends(get_ontology_meta_store),
):
    store.delete_link_type(iri, expected_version, identity.username)
    return Response(status_code=204)


# ===========================================================================
# T023 数据属性 + 风险向导
# ===========================================================================
@router.get("/data-properties", response_model=list[DataPropertyDetail])
def list_data_properties(
    domain_iri: str | None = None,
    include_inherited: bool = False,
    store: OntologyMetaStore = Depends(get_ontology_meta_store),
):
    """列出数据属性；可选 domain_iri 仅返回挂接该类的属性，
    include_inherited=true 时并入继承自祖先类的属性。"""
    return store.list_data_properties(domain_iri, include_inherited)


@router.post("/data-properties/risk", response_model=DataPropertyDetail, status_code=201)
def create_risk_data_property(
    payload: RiskDataPropertyCreate,
    identity: Identity = Depends(_writer),
    store: OntologyMetaStore = Depends(get_ontology_meta_store),
):
    return store.create_risk_data_property(payload, identity.username)


@router.post("/data-properties", response_model=DataPropertyDetail, status_code=201)
def create_data_property(
    payload: DataPropertyCreate,
    identity: Identity = Depends(_writer),
    store: OntologyMetaStore = Depends(get_ontology_meta_store),
):
    return store.create_data_property(payload, identity.username)


@router.put("/data-properties/{iri:path}", response_model=DataPropertyDetail)
def update_data_property(
    iri: str,
    payload: DataPropertyUpdate,
    identity: Identity = Depends(_writer),
    store: OntologyMetaStore = Depends(get_ontology_meta_store),
):
    return store.update_data_property(iri, payload, identity.username)


@router.delete("/data-properties/{iri:path}", status_code=204)
def delete_data_property(
    iri: str,
    expected_version: int,
    identity: Identity = Depends(_writer),
    store: OntologyMetaStore = Depends(get_ontology_meta_store),
):
    store.delete_data_property(iri, expected_version, identity.username)
    return Response(status_code=204)


# ===========================================================================
# T024 Action（仅定义，不触发推理，R10）
# ===========================================================================
@router.get("/actions", response_model=list[ActionDetail])
def list_actions(store: OntologyMetaStore = Depends(get_ontology_meta_store)):
    return store.list_actions()


@router.post("/actions", response_model=ActionDetail, status_code=201)
def create_action(
    payload: ActionCreate,
    identity: Identity = Depends(_writer),
    store: OntologyMetaStore = Depends(get_ontology_meta_store),
):
    return store.create_action(payload, identity.username)


@router.put("/actions/{iri:path}", response_model=ActionDetail)
def update_action(
    iri: str,
    payload: ActionUpdate,
    identity: Identity = Depends(_writer),
    store: OntologyMetaStore = Depends(get_ontology_meta_store),
):
    return store.update_action(iri, payload, identity.username)


@router.delete("/actions/{iri:path}", status_code=204)
def delete_action(
    iri: str,
    expected_version: int,
    identity: Identity = Depends(_writer),
    store: OntologyMetaStore = Depends(get_ontology_meta_store),
):
    store.delete_action(iri, expected_version, identity.username)
    return Response(status_code=204)


# ===========================================================================
# T025 约束 update/delete（按 id）
# ===========================================================================
@router.put("/restrictions/{rid}", response_model=RestrictionSummary)
def update_restriction(
    rid: str,
    payload: RestrictionUpdate,
    identity: Identity = Depends(_writer),
    store: OntologyMetaStore = Depends(get_ontology_meta_store),
):
    return store.update_restriction(rid, payload, identity.username)


@router.delete("/restrictions/{rid}", status_code=204)
def delete_restriction(
    rid: str,
    expected_version: int,
    identity: Identity = Depends(_writer),
    store: OntologyMetaStore = Depends(get_ontology_meta_store),
):
    store.delete_restriction(rid, expected_version, identity.username)
    return Response(status_code=204)


# ===========================================================================
# T026 映射 update/delete（按 id）
# ===========================================================================
@router.put("/mappings/{mid}", response_model=Mapping)
def update_mapping(
    mid: str,
    payload: MappingUpdate,
    identity: Identity = Depends(_writer),
    store: OntologyMetaStore = Depends(get_ontology_meta_store),
):
    return store.update_mapping(mid, payload, identity.username)


@router.delete("/mappings/{mid}", status_code=204)
def delete_mapping(
    mid: str,
    expected_version: int,
    identity: Identity = Depends(_writer),
    store: OntologyMetaStore = Depends(get_ontology_meta_store),
):
    store.delete_mapping(mid, expected_version, identity.username)
    return Response(status_code=204)


# ===========================================================================
# T027 校验（规则式 + HermiT 优雅降级）
# ===========================================================================
@router.post("/validate", response_model=ValidationReport)
def validate(store: OntologyMetaStore = Depends(get_ontology_meta_store)):
    return store.validate()


# ===========================================================================
# T028 导入 / 导出 / diff（外科式合并）
# ===========================================================================
@router.post("/import/ttl", response_model=ImportResult)
def import_ttl(
    body: dict = Body(...),
    identity: Identity = Depends(_writer),
    store: OntologyMetaStore = Depends(get_ontology_meta_store),
):
    content = body.get("content", "")
    return store.import_ttl(content, identity.username)


@router.get("/export/ttl")
def export_ttl(store: OntologyMetaStore = Depends(get_ontology_meta_store)):
    return Response(content=store.export_ttl(), media_type="text/turtle")


@router.get("/export/diff", response_model=DiffResult)
def export_diff(store: OntologyMetaStore = Depends(get_ontology_meta_store)):
    return store.export_diff()


# ===========================================================================
# T029 批次发布生命周期
# ===========================================================================
@router.get("/releases", response_model=list[ReleaseSummary])
def list_releases(store: OntologyMetaStore = Depends(get_ontology_meta_store)):
    return store.list_releases()


@router.post("/releases", response_model=ReleaseDetail, status_code=201)
def create_release(
    payload: dict = Body(...),
    identity: Identity = Depends(_writer),
    store: OntologyMetaStore = Depends(get_ontology_meta_store),
):
    title = payload.get("title")
    if not title:
        raise HTTPException(status_code=400, detail="缺少 title")
    return store.create_release(title, identity.username)


@router.get("/releases/{rid}", response_model=ReleaseDetail)
def release_detail(
    rid: str, store: OntologyMetaStore = Depends(get_ontology_meta_store)
):
    return store.release_detail(rid)


@router.post("/releases/{rid}/submit", response_model=ReleaseDetail)
def submit_release(
    rid: str,
    identity: Identity = Depends(_writer),
    store: OntologyMetaStore = Depends(get_ontology_meta_store),
):
    return store.submit_release(rid, identity.username)


@router.post("/releases/{rid}/publish", response_model=ReleaseDetail)
def publish_release(
    rid: str,
    identity: Identity = Depends(_writer),
    store: OntologyMetaStore = Depends(get_ontology_meta_store),
):
    return store.publish_release(rid, identity.username)


@router.post("/releases/{rid}/rollback", response_model=ReleaseDetail)
def rollback_release(
    rid: str,
    identity: Identity = Depends(_writer),
    store: OntologyMetaStore = Depends(get_ontology_meta_store),
):
    return store.rollback_release(rid, identity.username)


# ===========================================================================
# T030 审计
# ===========================================================================
@router.get("/audit")
def list_audit(
    entity_iri: str | None = None,
    release_id: str | None = None,
    actor: str | None = None,
    store: OntologyMetaStore = Depends(get_ontology_meta_store),
):
    return store.list_audit(entity_iri=entity_iri, release_id=release_id, actor=actor)


# ===========================================================================
# 只读浏览（Owlready2 引擎）—— 贪婪类路由：必须最后声明
# ===========================================================================
@router.get("/{module}/classes", response_model=list[TreeNodeResponse])
def get_class_hierarchy(module: str, engine: OntologyEngine = Depends(get_ontology_engine)):
    tree = engine.get_class_hierarchy(module)
    if not tree:
        raise HTTPException(404, f"Module not found or empty: {module}")
    return [_tree_to_response(n) for n in tree]


@router.get("/classes/{class_iri:path}")
def get_class_detail(
    class_iri: str,
    store: OntologyMetaStore = Depends(get_ontology_meta_store),
    engine: OntologyEngine = Depends(get_ontology_engine),
):
    """类详情：优先返回元数据草稿（含约束/映射），回退到引擎只读视图。"""
    c = store._class_by_iri(class_iri)
    if c is not None:
        return store.class_detail(c)
    detail = engine.get_class_detail(class_iri)
    if detail is None:
        raise HTTPException(404, f"Class not found: {class_iri}")
    return ClassDetailResponse(**detail.__dict__).model_dump()


def _expected_version(body: dict) -> int:
    if "expected_version" not in body:
        raise HTTPException(status_code=422, detail="缺少 expected_version")
    return body["expected_version"]


def _tree_to_response(node) -> TreeNodeResponse:
    return TreeNodeResponse(
        iri=node.iri,
        name=node.name,
        label=node.label,
        individual_count=node.individual_count,
        children=[_tree_to_response(c) for c in node.children],
    )
