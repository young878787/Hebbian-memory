"""Strict contracts for fixtures and public retrieval values."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class MemoryStatus(StrEnum):
    ACTIVE = "active"
    SUPERSEDED = "superseded"
    UNCERTAIN = "uncertain"
    ARCHIVED = "archived"


class MemoryType(StrEnum):
    CHARACTER_FACT = "character_fact"
    EVENT = "event"
    PREFERENCE = "preference"
    DECISION = "decision"


class EdgeType(StrEnum):
    TEMPORAL = "temporal"
    SEMANTIC = "semantic"
    CO_RETRIEVAL = "co_retrieval"
    SUPERSEDES = "supersedes"
    CONTRADICTS = "contradicts"
    SUPPORTS = "supports"


class QueryScope(StrEnum):
    HISTORICAL = "historical"
    CURRENT = "current"
    GENERAL = "general"


class RetrievalMode(StrEnum):
    EMBEDDING_ONLY = "embedding_only"
    HEBBIAN = "hebbian"


class RunMode(StrEnum):
    EVALUATION = "evaluation"
    LEARNING = "learning"


class FixtureMemory(BaseModel):
    model_config = ConfigDict(extra="forbid")
    external_id: str = Field(min_length=1, max_length=64)
    content: str = Field(min_length=1)
    memory_type: MemoryType
    topic: str | None = Field(default=None, max_length=100)
    occurred_at: datetime | None = None
    status: MemoryStatus = MemoryStatus.ACTIVE
    importance: float = Field(default=0.5, ge=0, le=1)
    confidence: float = Field(default=1.0, ge=0, le=1)
    source_session_id: str | None = Field(default=None, max_length=100)
    source_message_ids: list[str] | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class FixtureEdge(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_external_id: str = Field(min_length=1, max_length=64)
    target_external_id: str = Field(min_length=1, max_length=64)
    edge_type: EdgeType
    weight: float | None = Field(default=None, ge=0, le=1)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def no_self_loop(self) -> FixtureEdge:
        if self.source_external_id == self.target_external_id:
            raise ValueError("memory edges cannot have self loops")
        return self


class FixtureQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query_id: str = Field(min_length=1)
    query: str = Field(min_length=1)
    scope: QueryScope
    must_include: list[str] = Field(default_factory=list)
    nice_to_have: list[str] = Field(default_factory=list)
    must_not_primary: list[str] = Field(default_factory=list)
    expect_answerable: bool
    category: str = Field(min_length=1)

    @field_validator("must_include", "nice_to_have", "must_not_primary")
    @classmethod
    def unique_ids(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("oracle IDs must be unique")
        return value
