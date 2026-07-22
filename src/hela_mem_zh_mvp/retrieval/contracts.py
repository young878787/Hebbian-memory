"""Retrieval result data transfer objects."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ..persistence.models import Memory


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


class AnswerResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal["memory-answer-v1"]
    answerable: bool
    answer: str = Field(min_length=1)
    citations: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validates_citation_shape(self) -> AnswerResult:
        if self.answerable and not self.citations:
            raise ValueError("answerable answers require at least one citation")
        if len(self.citations) != len(set(self.citations)):
            raise ValueError("citations must be unique")
        return self


class RerankResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal["memory-rerank-v1"]
    ranked_external_ids: list[str] = Field(default_factory=list, max_length=5)

    @model_validator(mode="after")
    def validates_unique_ids(self) -> RerankResult:
        if len(self.ranked_external_ids) != len(set(self.ranked_external_ids)):
            raise ValueError("ranked_external_ids must be unique")
        return self


@dataclass
class RankedMemory:
    memory: Memory
    semantic_score: float
    candidate_rank: int = 0
    lexical_score: float = 0.0
    lexical_sources: list[str] = field(default_factory=list)
    rerank_score: float = 0.0
    model_rerank_score: float = 0.0
    time_decay_factor: float = 1.0
    hebbian_score: float = 0.0
    status_adjustment: float = 0.0
    final_score: float = 0.0
    source: str = "seed"
    activation_path: list[dict[str, Any]] = field(default_factory=list)
    selected: bool = False
    final_rank: int | None = None

    @property
    def external_id(self) -> str:
        return self.memory.external_id


@dataclass(frozen=True)
class RetrievalResult:
    run_id: uuid.UUID
    mode: RetrievalMode
    scope: QueryScope
    latency_ms: float
    items: list[RankedMemory]

    def as_dict(self) -> dict[str, Any]:
        return {
            "run_id": str(self.run_id),
            "mode": self.mode.value,
            "scope": self.scope.value,
            "latency_ms": self.latency_ms,
            "items": [
                {
                    "external_id": item.external_id,
                    "content": item.memory.content,
                    "status": item.memory.status,
                    "semantic_score": item.semantic_score,
                    "lexical_score": item.lexical_score,
                    "lexical_sources": item.lexical_sources,
                    "rerank_score": item.rerank_score,
                    "model_rerank_score": item.model_rerank_score,
                    "time_decay_factor": item.time_decay_factor,
                    "hebbian_score": item.hebbian_score,
                    "status_adjustment": item.status_adjustment,
                    "final_score": item.final_score,
                    "source": item.source,
                    "selected": item.selected,
                    "final_rank": item.final_rank,
                    "activation_path": item.activation_path,
                }
                for item in self.items
            ],
        }


__all__ = [
    "AnswerResult",
    "QueryScope",
    "RankedMemory",
    "RetrievalMode",
    "RetrievalResult",
    "RunMode",
    "RerankResult",
]
