"""Read-only mapped source query contracts, independent of extraction protocols."""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictFloat,
    StrictInt,
    StrictStr,
    model_validator,
)

Scalar = StrictStr | StrictInt | StrictFloat | StrictBool


class QueryModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class LookupKeyGroup(QueryModel):
    property_iris: list[str] = Field(min_length=1)
    scope_property_iris: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def distinct_components(self):
        components = self.property_iris + self.scope_property_iris
        if len(set(components)) != len(components) or any(not p.strip() for p in components):
            raise ValueError("键组组件须非空且互不重复")
        return self


class MockQueryConfig(QueryModel):
    # An incomplete config can be saved as a draft; filled fields are strictly validated.
    label_path: str | None = None
    entity_iri_path: str | None = None
    class_path: str | None = None
    identifier_namespace: str | None = None
    lookup_key_groups: list[LookupKeyGroup] = Field(default_factory=list)


class NameFilter(QueryModel):
    value: str = Field(min_length=1, max_length=1000)
    match: Literal["exact", "contains"] = "exact"


class PropertyFilter(QueryModel):
    property_iri: str = Field(min_length=1, max_length=500)
    value: Scalar
    datatype_iri: str = Field(min_length=1, max_length=500)


class EntityQuery(QueryModel):
    query_id: str = Field(min_length=1, max_length=100)
    class_iri: str = Field(min_length=1, max_length=500)
    include_subclasses: bool = False
    mapping_ids: list[UUID] = Field(default_factory=list, max_length=100)
    name: NameFilter | None = None
    property_filters: list[PropertyFilter] = Field(default_factory=list, max_length=32)
    limit: int = Field(default=20, ge=1, le=50)
    offset: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def criteria(self):
        if self.name is None and not self.property_filters and len(self.mapping_ids) != 1:
            raise ValueError("无筛选条件浏览须指定一个映射，否则至少提供名称或一个属性条件")
        iris = [f.property_iri for f in self.property_filters]
        if len(set(iris)) != len(iris) or len(set(self.mapping_ids)) != len(self.mapping_ids):
            raise ValueError("属性条件和映射 ID 不可重复")
        return self


class EntityQueryRequest(QueryModel):
    queries: list[EntityQuery] = Field(min_length=1, max_length=32)

    @model_validator(mode="after")
    def unique_ids(self):
        if len({q.query_id for q in self.queries}) != len(self.queries):
            raise ValueError("query_id 批内必须唯一")
        return self


class QueryIssue(BaseModel):
    code: str
    message: str
    property_iri: str | None = None
    record_id: str | None = None


class QueryValue(BaseModel):
    value: Scalar
    datatype_iri: str
    raw_value: Scalar
    source_path: str
    source_index: int | None = None


class QueryProperty(BaseModel):
    property_iri: str
    values: list[QueryValue]


class RecordRef(BaseModel):
    source_system: str
    dataset: str
    record_id: str


class QueryMatch(BaseModel):
    kind: Literal["name_exact", "name_contains", "property_exact"]
    property_iri: str | None = None


class EntityQueryCandidate(BaseModel):
    record_ref: RecordRef
    record_version: str
    mapping_id: str
    mapping_revision: str
    source_kind: Literal["mock"] = "mock"
    source_entity_iri: str | None
    class_iri: str
    label: str
    properties: list[QueryProperty]
    matches: list[QueryMatch]
    matched_lookup_groups: list[int]
    identifier_namespace: str | None
    business_scope_status: Literal["provided", "unspecified"]
    identity_status: Literal["not_checked"] = "not_checked"
    issues: list[QueryIssue]


class QuerySourceResult(BaseModel):
    mapping_id: str
    status: Literal[
        "complete", "incomplete", "invalid_mapping", "unsupported_filter", "source_unavailable"
    ]
    issues: list[QueryIssue]


class EntityQueryResult(BaseModel):
    query_id: str
    outcome: Literal["matches", "no_match", "unresolved"]
    complete: bool
    truncated: bool
    total: int | None
    next_offset: int | None
    sources: list[QuerySourceResult]
    candidates: list[EntityQueryCandidate]
    issues: list[QueryIssue]


class EntityQueryResponse(BaseModel):
    results: list[EntityQueryResult]
