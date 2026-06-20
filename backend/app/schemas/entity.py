from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class IndividualResponse(BaseModel):
    iri: str
    name: str
    class_iris: list[str] = []
    label_zh: str | None = None
    label_en: str | None = None
    properties: dict[str, Any] = {}


class CreateIndividualRequest(BaseModel):
    class_iri: str
    name: str
    properties: dict[str, Any] = {}


class UpdateIndividualRequest(BaseModel):
    properties: dict[str, Any] = {}


class EntityShadowResponse(BaseModel):
    iri: str
    class_iri: str
    label_zh: str | None = None
    label_en: str | None = None
    module: str
    properties_json: dict | None = None

    class Config:
        from_attributes = True


class EntitySearchResponse(BaseModel):
    items: list[EntityShadowResponse]
    total: int
    page: int
    page_size: int
