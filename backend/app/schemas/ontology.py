from __future__ import annotations

from pydantic import BaseModel


class ModuleResponse(BaseModel):
    key: str
    iri: str
    label: str | None = None
    class_count: int = 0
    individual_count: int = 0


class TreeNodeResponse(BaseModel):
    iri: str
    name: str
    label: str | None = None
    individual_count: int = 0
    children: list[TreeNodeResponse] = []


class PropertyInfo(BaseModel):
    iri: str
    name: str
    label: str | None = None
    range: list[str] = []


class RestrictionInfo(BaseModel):
    property: str
    type: str
    value: str | None = None
    cardinality: int | None = None


class ClassDetailResponse(BaseModel):
    iri: str
    name: str
    label_zh: str | None = None
    label_en: str | None = None
    comment: str | None = None
    module: str | None = None
    parent_iris: list[str] = []
    children_iris: list[str] = []
    individual_count: int = 0
    object_properties: list[PropertyInfo] = []
    data_properties: list[PropertyInfo] = []
    restrictions: list[RestrictionInfo] = []
