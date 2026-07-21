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


class ExtractionOutcomeStatus(StrEnum):
    EXTRACTED = "EXTRACTED"
    NO_MEMORY = "NO_MEMORY"
    FAILED = "FAILED"


class NoMemoryReason(StrEnum):
    NON_DURABLE_CHITCHAT = "non_durable_chitchat"
    EXTERNAL_NOISE = "external_noise"
    DUPLICATE_SURFACE_FORM = "duplicate_surface_form"
    INSUFFICIENT_ASSERTION = "insufficient_assertion"
    UNSUPPORTED_ROLE_CONTENT = "unsupported_role_content"


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
    evidence: str | None = None


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
    evidence: str | None = None

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


class MessageExtractionOutcome(BaseModel):
    """One auditable extraction result for exactly one source message."""

    model_config = ConfigDict(extra="forbid")
    schema_version: Literal["message-extraction-outcome-v1"]
    source_message_id: str = Field(min_length=1, max_length=128)
    status: ExtractionOutcomeStatus
    entities: list[ExtractedEntity] = Field(default_factory=list)
    memories: list[ExtractedMemory] = Field(default_factory=list)
    reason_code: NoMemoryReason | None = None
    provider_attempts: int = Field(ge=0, default=1)
    error: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def validates_message_outcome(self) -> MessageExtractionOutcome:
        entity_ids = [entity.candidate_id for entity in self.entities]
        memory_ids = [memory.candidate_id for memory in self.memories]
        if len(entity_ids) != len(set(entity_ids)) or len(memory_ids) != len(set(memory_ids)):
            raise ValueError("candidate_id values must be unique per outcome")
        if not all(
            memory.evidence_message_ids == [self.source_message_id]
            for memory in self.memories
        ):
            raise ValueError("each memory evidence_message_ids must equal the primary message")
        if not all(
            set(memory.entity_candidate_ids) <= set(entity_ids) for memory in self.memories
        ):
            raise ValueError("memory references an unknown entity candidate")
        if not all(
            memory.primary_entity_candidate_id in memory.entity_candidate_ids
            for memory in self.memories
            if memory.primary_entity_candidate_id is not None
        ):
            raise ValueError("primary_entity_candidate_id must be an attached entity")
        if any(
            memory.memory_type in {MemoryType.PREFERENCE, MemoryType.CHARACTER_FACT}
            and memory.primary_entity_candidate_id is None
            for memory in self.memories
        ):
            raise ValueError("preference and character_fact require a primary entity")
        if any(
            memory.memory_type is MemoryType.PREFERENCE and memory.attribute_key is None
            for memory in self.memories
        ):
            raise ValueError("preference requires an attribute_key")
        if self.status is ExtractionOutcomeStatus.EXTRACTED:
            if not self.memories or self.reason_code is not None or self.error is not None:
                raise ValueError("EXTRACTED requires memories and no reason/error")
        elif self.status is ExtractionOutcomeStatus.NO_MEMORY:
            if self.memories or self.entities or self.reason_code is None or self.error is not None:
                raise ValueError("NO_MEMORY requires a reason and no extracted values")
        else:
            if self.memories or self.entities or self.error is None:
                raise ValueError("FAILED requires an error and no extracted values")
        return self

    def as_extraction_result(self) -> ExtractionResult:
        """Adapt a successful single-message outcome to the legacy writer contract."""
        if self.status is not ExtractionOutcomeStatus.EXTRACTED:
            raise ValueError("only EXTRACTED outcomes have an extraction result")
        return ExtractionResult(
            schema_version="memory-extraction-v1",
            source_message_ids=[self.source_message_id],
            entities=self.entities,
            memories=self.memories,
            relations=[],
        )


def extraction_coverage(
    input_message_ids: list[str], outcomes: list[MessageExtractionOutcome]
) -> dict[str, object]:
    """Calculate the deterministic per-message coverage gate without I/O."""
    supplied = [outcome.source_message_id for outcome in outcomes]
    expected = set(input_message_ids)
    actual = set(supplied)
    duplicates = sorted({item for item in supplied if supplied.count(item) > 1})
    failed = [
        outcome.source_message_id
        for outcome in outcomes
        if outcome.status is ExtractionOutcomeStatus.FAILED
    ]
    return {
        "input_message_count": len(input_message_ids),
        "outcome_count": len(outcomes),
        "extracted_count": sum(
            outcome.status is ExtractionOutcomeStatus.EXTRACTED for outcome in outcomes
        ),
        "no_memory_count": sum(
            outcome.status is ExtractionOutcomeStatus.NO_MEMORY for outcome in outcomes
        ),
        "failed_count": len(failed),
        "memory_candidate_count": sum(len(outcome.memories) for outcome in outcomes),
        "unreported_message_ids": sorted(expected - actual),
        "unexpected_message_ids": sorted(actual - expected),
        "duplicate_message_ids": duplicates,
        "failed_message_ids": failed,
    }


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
        if (
            self.action in {ResolutionAction.SUPERSEDE, ResolutionAction.CONTRADICT}
            and not self.target_refs
        ):
            raise ValueError("state-changing resolution requires target_refs")
        if (
            self.action is ResolutionAction.SUPERSEDE
            and self.effective_order is EffectiveOrder.UNKNOWN
        ):
            raise ValueError("SUPERSEDE requires a known effective_order")
        if self.action is ResolutionAction.MERGE_PROVENANCE and len(self.target_refs) > 1:
            raise ValueError("MERGE_PROVENANCE accepts at most one target")
        if self.action is ResolutionAction.MERGE_PROVENANCE and len(self.target_refs) != 1:
            raise ValueError("MERGE_PROVENANCE requires exactly one target")
        if self.action in {ResolutionAction.CREATE, ResolutionAction.DEFER, ResolutionAction.IGNORE} and self.target_refs:
            raise ValueError(f"{self.action.value} cannot name targets")
        return self


class MemoryResolutionResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal["memory-resolution-v2"]
    decisions: list[ResolutionDecision]
