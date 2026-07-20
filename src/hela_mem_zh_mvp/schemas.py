"""Strict contracts for fixtures and public retrieval values."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

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


class EntityType(StrEnum):
    PERSON = "person"
    CONCEPT = "concept"


class ResolutionAction(StrEnum):
    CREATE = "CREATE"
    MERGE_PROVENANCE = "MERGE_PROVENANCE"
    SUPERSEDE = "SUPERSEDE"
    CONTRADICT = "CONTRADICT"
    IGNORE = "IGNORE"
    DEFER = "DEFER"


class EffectiveOrder(StrEnum):
    CANDIDATE_AFTER_TARGET = "candidate_after_target"
    CANDIDATE_BEFORE_TARGET = "candidate_before_target"
    SAME_TIME = "same_time"
    UNKNOWN = "unknown"


class SourceMessage(BaseModel):
    """The only accepted on-disk ingestion input record."""

    model_config = ConfigDict(extra="forbid")
    message_id: str = Field(min_length=1, max_length=128)
    session_id: str = Field(min_length=1, max_length=100)
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1)
    occurred_at: datetime
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("occurred_at")
    @classmethod
    def requires_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("occurred_at must include a timezone")
        return value


class ExtractedEntity(BaseModel):
    model_config = ConfigDict(extra="forbid")
    candidate_id: str = Field(min_length=1)
    canonical_name: str = Field(min_length=1)
    entity_type: EntityType
    aliases_seen: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0, le=1)
    evidence: str = Field(min_length=1)


class ExtractedMemory(BaseModel):
    model_config = ConfigDict(extra="forbid")
    candidate_id: str = Field(min_length=1)
    content: str = Field(min_length=1)
    memory_type: MemoryType
    entity_candidate_ids: list[str] = Field(default_factory=list)
    concepts: list[str] = Field(default_factory=list)
    attribute_key: str | None = Field(default=None, min_length=1, max_length=100)
    primary_entity_candidate_id: str | None = None
    occurred_at: datetime
    importance: float = Field(ge=0, le=1)
    confidence: float = Field(ge=0, le=1)
    evidence_message_ids: list[str] = Field(min_length=1)
    evidence: str = Field(min_length=1)

    @field_validator("occurred_at")
    @classmethod
    def requires_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("occurred_at must include a timezone")
        return value


class ExtractedRelation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_candidate_id: str = Field(min_length=1)
    target_candidate_id: str = Field(min_length=1)
    edge_type: EdgeType
    evidence: str = Field(min_length=1)


class ExtractionResult(BaseModel):
    """Provider output. It deliberately has no database identifiers or namespace."""

    model_config = ConfigDict(extra="forbid")
    schema_version: Literal["memory-extraction-v1"]
    source_message_ids: list[str] = Field(min_length=1)
    entities: list[ExtractedEntity] = Field(default_factory=list)
    memories: list[ExtractedMemory] = Field(default_factory=list)
    relations: list[ExtractedRelation] = Field(default_factory=list)

    @model_validator(mode="after")
    def validates_internal_references(self) -> ExtractionResult:
        message_ids = set(self.source_message_ids)
        if len(message_ids) != len(self.source_message_ids):
            raise ValueError("source_message_ids must be unique")
        entity_ids = [entity.candidate_id for entity in self.entities]
        memory_ids = [memory.candidate_id for memory in self.memories]
        if len(entity_ids) != len(set(entity_ids)) or len(memory_ids) != len(set(memory_ids)):
            raise ValueError("candidate_id values must be unique per type")
        if not all(set(memory.evidence_message_ids) <= message_ids for memory in self.memories):
            raise ValueError("evidence_message_ids must be supplied source_message_ids")
        if not all(set(memory.entity_candidate_ids) <= set(entity_ids) for memory in self.memories):
            raise ValueError("memory references an unknown entity candidate")
        if not all(
            memory.primary_entity_candidate_id is None
            or memory.primary_entity_candidate_id in memory.entity_candidate_ids
            for memory in self.memories
        ):
            raise ValueError("primary_entity_candidate_id must be an attached entity")
        if not all(
            {relation.source_candidate_id, relation.target_candidate_id} <= set(memory_ids)
            for relation in self.relations
        ):
            raise ValueError("relation references an unknown memory candidate")
        return self


class ResolutionDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    candidate_id: str = Field(min_length=1)
    action: ResolutionAction
    target_refs: list[str] = Field(default_factory=list)
    relationship: str = Field(default="unknown", min_length=1, max_length=64)
    effective_order: EffectiveOrder = EffectiveOrder.UNKNOWN
    confidence: float = Field(ge=0, le=1)
    reason: str = Field(min_length=1)
    evidence_quotes: list[str] = Field(default_factory=list)
    needs_review: bool = False

    @model_validator(mode="after")
    def validates_action_shape(self) -> ResolutionDecision:
        if self.action in {ResolutionAction.SUPERSEDE, ResolutionAction.CONTRADICT} and not self.target_refs:
            raise ValueError("state-changing resolution requires target_refs")
        if self.action is ResolutionAction.SUPERSEDE and self.effective_order is EffectiveOrder.UNKNOWN:
            raise ValueError("SUPERSEDE requires a known effective_order")
        if self.action is ResolutionAction.MERGE_PROVENANCE and len(self.target_refs) > 1:
            raise ValueError("MERGE_PROVENANCE accepts at most one target")
        return self


class MemoryResolutionResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal["memory-resolution-v2"]
    decisions: list[ResolutionDecision]


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


class AIJudgeCase(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query_id: str
    verdict: Literal["PASS", "FAIL", "UNSURE"]
    correctness: int = Field(ge=0, le=2)
    groundedness: int = Field(ge=0, le=2)
    association_completeness: int = Field(ge=0, le=2)
    contradiction_correctness: int = Field(ge=0, le=2)
    no_answer_safety: int | None = Field(default=None, ge=0, le=2)
    unsupported_claims: list[str] = Field(default_factory=list)
    reason: str = Field(min_length=1, max_length=500)


class AIJudgeSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal["answer-judge-summary-v1"]
    cases: list[AIJudgeCase]
    summary: dict[str, Any]


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
