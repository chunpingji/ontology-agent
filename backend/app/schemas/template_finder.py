"""Finder display contracts; no reporting or fact submission input."""

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class StartFinderRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_key: str = Field(min_length=1, max_length=200, pattern=r"\S")
    expected_execution_id: UUID | None = None


class RecognitionEngineUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    recognition_mode: Literal["ontology_guided", "finder_legacy"]
    finder_profile_id: str | None = Field(max_length=100)
    expected_recognition_mode: Literal["ontology_guided", "finder_legacy"]
    expected_finder_profile_id: str | None = Field(max_length=100)


class FinderPdeDecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    chosen: Literal["derived", "asserted", "pending"]
    note: str = Field(default="", max_length=4000)
    expected_version: int = Field(default=0, ge=0)
