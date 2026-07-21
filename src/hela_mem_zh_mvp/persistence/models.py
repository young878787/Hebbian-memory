"""Namespace-scoped SQLAlchemy mappings for the v0.5 pipeline."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pgvector.sqlalchemy import VECTOR
from sqlalchemy import (
    REAL,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.sql import func


class Base(DeclarativeBase):
    pass


class Timestamped:
    """Database-backed audit timestamps for every persisted pipeline record."""

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class MemoryNamespace(Timestamped, Base):
    __tablename__ = "memory_namespaces"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    namespace_key: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    display_name: Mapped[str | None] = mapped_column(String(255))
    metadata_: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSONB, nullable=False, default=dict
    )


class SourceMessage(Timestamped, Base):
    __tablename__ = "source_messages"
    namespace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("memory_namespaces.id", ondelete="RESTRICT"),
        primary_key=True,
    )
    message_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    session_id: Mapped[str] = mapped_column(String(100), nullable=False)
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    metadata_: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSONB, nullable=False, default=dict
    )


class IngestionRun(Timestamped, Base):
    __tablename__ = "ingestion_runs"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    namespace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("memory_namespaces.id", ondelete="RESTRICT"), nullable=False
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    extractor_model: Mapped[str] = mapped_column(String(255), nullable=False)
    extractor_schema_version: Mapped[str] = mapped_column(String(64), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    message_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    merged_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    superseded_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    contradicted_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    ignored_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    metadata_: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSONB, nullable=False, default=dict
    )


class MessageExtractionOutcome(Timestamped, Base):
    __tablename__ = "message_extraction_outcomes"
    __table_args__ = (
        ForeignKeyConstraint(
            ["namespace_id", "source_message_id"],
            ["source_messages.namespace_id", "source_messages.message_id"],
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "ingestion_run_id", "source_message_id", name="uq_message_outcomes_run_message"
        ),
        CheckConstraint(
            "status IN ('EXTRACTED','NO_MEMORY','FAILED')", name="ck_message_outcomes_status"
        ),
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    namespace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("memory_namespaces.id", ondelete="RESTRICT"), nullable=False
    )
    ingestion_run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("ingestion_runs.id", ondelete="RESTRICT"), nullable=False
    )
    source_message_id: Mapped[str] = mapped_column(String(128), nullable=False)
    schema_version: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    reason_code: Mapped[str | None] = mapped_column(String(64))
    provider_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error: Mapped[str | None] = mapped_column(Text)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)


class Entity(Timestamped, Base):
    __tablename__ = "entities"
    __table_args__ = (
        UniqueConstraint(
            "namespace_id", "entity_type", "canonical_name", name="uq_entities_namespace_type_name"
        ),
        UniqueConstraint("namespace_id", "id", name="uq_entities_namespace_id"),
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    namespace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("memory_namespaces.id", ondelete="RESTRICT"), nullable=False
    )
    canonical_name: Mapped[str] = mapped_column(String(255), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(32), nullable=False)
    aliases: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    confidence: Mapped[float] = mapped_column(REAL, nullable=False)
    metadata_: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSONB, nullable=False, default=dict
    )


class EntityAlias(Timestamped, Base):
    __tablename__ = "entity_aliases"
    __table_args__ = (
        ForeignKeyConstraint(
            ["namespace_id", "entity_id"],
            ["entities.namespace_id", "entities.id"],
            ondelete="CASCADE",
        ),
        UniqueConstraint(
            "namespace_id", "entity_type", "alias_normalized", name="uq_entity_aliases_scope_key"
        ),
        CheckConstraint("confidence BETWEEN 0.0 AND 1.0", name="ck_entity_aliases_confidence"),
        CheckConstraint(
            "status IN ('active','pending','rejected')", name="ck_entity_aliases_status"
        ),
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    namespace_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    entity_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(32), nullable=False)
    alias_raw: Mapped[str] = mapped_column(String(255), nullable=False)
    alias_normalized: Mapped[str] = mapped_column(String(255), nullable=False)
    source_type: Mapped[str] = mapped_column(String(32), nullable=False)
    source_ref: Mapped[str | None] = mapped_column(Text)
    confidence: Mapped[float] = mapped_column(REAL, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    normalizer_version: Mapped[str] = mapped_column(String(32), nullable=False)


class Memory(Timestamped, Base):
    __tablename__ = "memories"
    __table_args__ = (
        UniqueConstraint("namespace_id", "external_id", name="uq_memories_namespace_external_id"),
        UniqueConstraint("namespace_id", "id", name="uq_memories_namespace_id"),
        CheckConstraint(
            "status IN ('active','superseded','uncertain','archived')", name="ck_memories_status"
        ),
        CheckConstraint(
            "memory_type IN ('character_fact','event','preference','decision')",
            name="ck_memories_type",
        ),
        CheckConstraint(
            "modality IN ('asserted','question','uncertain','considered')",
            name="ck_memories_modality",
        ),
        CheckConstraint(
            "temporal_scope IN ('current','historical','unknown')",
            name="ck_memories_temporal_scope",
        ),
        CheckConstraint("importance BETWEEN 0.0 AND 1.0", name="ck_memories_importance"),
        CheckConstraint("confidence BETWEEN 0.0 AND 1.0", name="ck_memories_confidence"),
        Index("ix_memories_namespace_status_occurred_at", "namespace_id", "status", "occurred_at"),
        Index(
            "ix_memories_namespace_temporal_status_occurred_at",
            "namespace_id",
            "temporal_scope",
            "status",
            "occurred_at",
        ),
        Index(
            "ix_memories_resolution_scope",
            "namespace_id",
            "memory_type",
            "topic_key",
            "status",
        ),
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    namespace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("memory_namespaces.id", ondelete="RESTRICT"), nullable=False
    )
    external_id: Mapped[str] = mapped_column(String(64), nullable=False)
    canonical_key: Mapped[str] = mapped_column(String(128), nullable=False)
    extraction_schema_version: Mapped[str] = mapped_column(String(64), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    memory_type: Mapped[str] = mapped_column(String(32), nullable=False)
    topic: Mapped[str | None] = mapped_column(String(100))
    topic_raw: Mapped[str | None] = mapped_column(String(100))
    topic_key: Mapped[str | None] = mapped_column(String(100))
    topic_version: Mapped[str | None] = mapped_column(String(32))
    attribute_key: Mapped[str | None] = mapped_column(String(100))
    state_key: Mapped[str | None] = mapped_column(String(64))
    occurred_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active")
    modality: Mapped[str] = mapped_column(String(16), nullable=False, default="asserted")
    temporal_scope: Mapped[str] = mapped_column(String(16), nullable=False, default="unknown")
    importance: Mapped[float] = mapped_column(REAL, nullable=False, default=0.5)
    confidence: Mapped[float] = mapped_column(REAL, nullable=False, default=1.0)
    source_session_id: Mapped[str | None] = mapped_column(String(100))
    source_message_ids: Mapped[list[str] | None] = mapped_column(JSONB)
    metadata_: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSONB, nullable=False, default=dict
    )
    embedding: Mapped[list[float]] = mapped_column(VECTOR(2560), nullable=False)


class MemoryEntity(Timestamped, Base):
    __tablename__ = "memory_entities"
    __table_args__ = (
        ForeignKeyConstraint(
            ["namespace_id", "memory_id"],
            ["memories.namespace_id", "memories.id"],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["namespace_id", "entity_id"],
            ["entities.namespace_id", "entities.id"],
            ondelete="CASCADE",
        ),
    )
    namespace_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    memory_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    entity_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    mention_role: Mapped[str | None] = mapped_column(String(32))
    confidence: Mapped[float] = mapped_column(REAL, nullable=False)


class MemoryCandidate(Timestamped, Base):
    __tablename__ = "memory_candidates"
    __table_args__ = (
        UniqueConstraint("namespace_id", "candidate_key", name="uq_memory_candidates_scope_key"),
        CheckConstraint(
            "status IN ('pending','resolving','resolved','deferred','failed')",
            name="ck_memory_candidates_status",
        ),
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    namespace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("memory_namespaces.id", ondelete="RESTRICT"), nullable=False
    )
    ingestion_run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("ingestion_runs.id", ondelete="RESTRICT"), nullable=False
    )
    candidate_key: Mapped[str] = mapped_column(String(64), nullable=False)
    extraction_payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    normalized_payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    embedding: Mapped[list[float]] = mapped_column(VECTOR(2560), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class MemoryResolutionDecision(Timestamped, Base):
    __tablename__ = "memory_resolution_decisions"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    namespace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("memory_namespaces.id", ondelete="RESTRICT"), nullable=False
    )
    candidate_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("memory_candidates.id", ondelete="RESTRICT"), nullable=False
    )
    resolver_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    resolver_model: Mapped[str | None] = mapped_column(String(255))
    resolver_schema_version: Mapped[str] = mapped_column(String(64), nullable=False)
    prompt_version: Mapped[str | None] = mapped_column(String(64))
    candidate_snapshot_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    action: Mapped[str] = mapped_column(String(32), nullable=False)
    target_memory_ids: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    confidence: Mapped[float] = mapped_column(REAL, nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    evidence_quotes: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    validation_status: Mapped[str] = mapped_column(String(32), nullable=False)
    before_state: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    after_state: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)


class MemoryEdge(Timestamped, Base):
    __tablename__ = "memory_edges"
    __table_args__ = (
        ForeignKeyConstraint(
            ["namespace_id", "source_id"],
            ["memories.namespace_id", "memories.id"],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["namespace_id", "target_id"],
            ["memories.namespace_id", "memories.id"],
            ondelete="CASCADE",
        ),
        CheckConstraint(
            "edge_type IN ('temporal','semantic','co_retrieval','supersedes','contradicts','supports')",
            name="ck_memory_edges_type",
        ),
        CheckConstraint("weight BETWEEN 0.0 AND 1.0", name="ck_memory_edges_weight"),
        CheckConstraint("activation_count >= 0", name="ck_memory_edges_activation_count"),
        CheckConstraint("source_id <> target_id", name="ck_memory_edges_no_self_loop"),
        Index("ix_memory_edges_namespace_target", "namespace_id", "target_id", "edge_type"),
    )
    namespace_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    source_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    target_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    edge_type: Mapped[str] = mapped_column(String(32), primary_key=True)
    weight: Mapped[float] = mapped_column(REAL, nullable=False, default=0.0)
    activation_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    metadata_: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSONB, nullable=False, default=dict
    )


# v0.6 canonical ledger.  `Memory` remains the compatibility/read model while
# these tables make evidence, factual relations, learned associations, and
# rebuildable projections separate durable concerns.
class MemoryClaim(Timestamped, Base):
    __tablename__ = "memory_claims"
    __table_args__ = (
        ForeignKeyConstraint(
            ["namespace_id", "legacy_memory_id"],
            ["memories.namespace_id", "memories.id"],
            ondelete="RESTRICT",
        ),
        UniqueConstraint("namespace_id", "id", name="uq_claims_namespace_id"),
        UniqueConstraint("namespace_id", "legacy_memory_id", name="uq_claims_namespace_memory"),
        CheckConstraint("status IN ('active','superseded','uncertain','archived')", name="ck_claims_status"),
        CheckConstraint(
            "modality IN ('asserted','question','uncertain','considered')",
            name="ck_claims_modality",
        ),
        CheckConstraint(
            "temporal_scope IN ('current','historical','unknown')",
            name="ck_claims_temporal_scope",
        ),
        CheckConstraint("confidence BETWEEN 0.0 AND 1.0", name="ck_claims_confidence"),
        CheckConstraint("importance BETWEEN 0.0 AND 1.0", name="ck_claims_importance"),
        Index(
            "uq_claims_one_active_state",
            "namespace_id",
            "state_key",
            unique=True,
            postgresql_where=text("status = 'active' AND state_key IS NOT NULL"),
        ),
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    namespace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("memory_namespaces.id", ondelete="RESTRICT"), nullable=False
    )
    legacy_memory_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    claim_type: Mapped[str] = mapped_column(String(32), nullable=False)
    subject_entity_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    predicate_key: Mapped[str | None] = mapped_column(String(100))
    object_value: Mapped[str] = mapped_column(Text, nullable=False)
    object_entity_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    event_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    valid_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    valid_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    polarity: Mapped[str] = mapped_column(String(16), nullable=False, default="affirmed")
    modality: Mapped[str] = mapped_column(String(16), nullable=False, default="asserted")
    temporal_scope: Mapped[str] = mapped_column(String(16), nullable=False, default="unknown")
    confidence: Mapped[float] = mapped_column(REAL, nullable=False)
    importance: Mapped[float] = mapped_column(REAL, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active")
    state_key: Mapped[str | None] = mapped_column(String(64))
    schema_version: Mapped[str] = mapped_column(String(64), nullable=False)


class ClaimEvidence(Timestamped, Base):
    __tablename__ = "claim_evidence"
    __table_args__ = (
        ForeignKeyConstraint(["namespace_id", "claim_id"], ["memory_claims.namespace_id", "memory_claims.id"], ondelete="RESTRICT"),
        ForeignKeyConstraint(["namespace_id", "source_message_id"], ["source_messages.namespace_id", "source_messages.message_id"], ondelete="RESTRICT"),
        UniqueConstraint("claim_id", "source_message_id", "evidence_text", name="uq_claim_evidence_source_text"),
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    namespace_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    claim_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    source_message_id: Mapped[str] = mapped_column(String(128), nullable=False)
    evidence_text: Mapped[str] = mapped_column(Text, nullable=False)
    evidence_start: Mapped[int | None] = mapped_column(Integer)
    evidence_end: Mapped[int | None] = mapped_column(Integer)
    extractor_model: Mapped[str] = mapped_column(String(255), nullable=False)
    extraction_schema_version: Mapped[str] = mapped_column(String(64), nullable=False)


class RelationEvidence(Timestamped, Base):
    __tablename__ = "relation_evidence"
    __table_args__ = (
        ForeignKeyConstraint(["namespace_id", "source_claim_id"], ["memory_claims.namespace_id", "memory_claims.id"], ondelete="RESTRICT"),
        ForeignKeyConstraint(["namespace_id", "target_claim_id"], ["memory_claims.namespace_id", "memory_claims.id"], ondelete="RESTRICT"),
        CheckConstraint("relation_type IN ('supports','contradicts','supersedes','temporal')", name="ck_relation_evidence_type"),
        CheckConstraint("source_claim_id <> target_claim_id", name="ck_relation_evidence_no_self"),
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    namespace_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("memory_namespaces.id", ondelete="RESTRICT"), nullable=False)
    source_claim_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    target_claim_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    relation_type: Mapped[str] = mapped_column(String(16), nullable=False)
    direction: Mapped[str] = mapped_column(String(16), nullable=False, default="directed")
    origin: Mapped[str] = mapped_column(String(32), nullable=False)
    evidence_refs: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    confidence: Mapped[float] = mapped_column(REAL, nullable=False)
    valid_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    valid_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolution_status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")


class AssociationEvent(Timestamped, Base):
    __tablename__ = "association_events"
    __table_args__ = (
        ForeignKeyConstraint(["namespace_id", "source_memory_id"], ["memories.namespace_id", "memories.id"], ondelete="RESTRICT"),
        ForeignKeyConstraint(["namespace_id", "target_memory_id"], ["memories.namespace_id", "memories.id"], ondelete="RESTRICT"),
        UniqueConstraint("idempotency_key", name="uq_association_events_idempotency"),
        CheckConstraint("event_type IN ('cited_together','explicit_positive_feedback','explicit_negative_feedback','answer_rejected','manual_link','decay_checkpoint')", name="ck_association_events_type"),
        CheckConstraint("source_memory_id < target_memory_id", name="ck_association_events_order"),
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    namespace_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("memory_namespaces.id", ondelete="RESTRICT"), nullable=False)
    source_memory_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    target_memory_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    event_type: Mapped[str] = mapped_column(String(32), nullable=False)
    delta: Mapped[float] = mapped_column(REAL, nullable=False)
    retrieval_run_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("retrieval_runs.id", ondelete="RESTRICT"))
    evidence_refs: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    policy_version: Mapped[str] = mapped_column(String(64), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)


class AssociationStat(Timestamped, Base):
    __tablename__ = "association_stats"
    __table_args__ = (
        ForeignKeyConstraint(["namespace_id", "source_memory_id"], ["memories.namespace_id", "memories.id"], ondelete="RESTRICT"),
        ForeignKeyConstraint(["namespace_id", "target_memory_id"], ["memories.namespace_id", "memories.id"], ondelete="RESTRICT"),
        CheckConstraint("source_memory_id < target_memory_id", name="ck_association_stats_order"),
    )
    namespace_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    source_memory_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    target_memory_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    positive_strength: Mapped[float] = mapped_column(REAL, nullable=False, default=0.0)
    negative_strength: Mapped[float] = mapped_column(REAL, nullable=False, default=0.0)
    activation_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    success_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    rejection_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_reinforced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    effective_weight: Mapped[float] = mapped_column(REAL, nullable=False, default=0.0)
    decay_policy_version: Mapped[str] = mapped_column(String(64), nullable=False)


class LifecycleDecision(Timestamped, Base):
    __tablename__ = "lifecycle_decisions"
    __table_args__ = (ForeignKeyConstraint(["namespace_id", "claim_id"], ["memory_claims.namespace_id", "memory_claims.id"], ondelete="RESTRICT"),)
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    namespace_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("memory_namespaces.id", ondelete="RESTRICT"), nullable=False)
    claim_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    action: Mapped[str] = mapped_column(String(32), nullable=False)
    policy_version: Mapped[str] = mapped_column(String(64), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    before_state: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    after_state: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    rollback_payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)


class GraphProjectionRun(Timestamped, Base):
    __tablename__ = "graph_projection_runs"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    namespace_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("memory_namespaces.id", ondelete="RESTRICT"), nullable=False)
    projection_version: Mapped[str] = mapped_column(String(64), nullable=False)
    source_snapshot_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    config_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    included_claim_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    excluded_claim_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    node_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    edge_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    blocker_counts: Mapped[dict[str, int]] = mapped_column(JSONB, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class GraphProjectionNode(Timestamped, Base):
    __tablename__ = "graph_projection_nodes"
    __table_args__ = (ForeignKeyConstraint(["projection_run_id"], ["graph_projection_runs.id"], ondelete="CASCADE"), CheckConstraint("jsonb_array_length(claim_ids) > 0", name="ck_projection_node_provenance"))
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    projection_run_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    node_key: Mapped[str] = mapped_column(String(255), nullable=False)
    node_type: Mapped[str] = mapped_column(String(32), nullable=False)
    claim_ids: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)


class GraphProjectionEdge(Timestamped, Base):
    __tablename__ = "graph_projection_edges"
    __table_args__ = (ForeignKeyConstraint(["projection_run_id"], ["graph_projection_runs.id"], ondelete="CASCADE"), CheckConstraint("jsonb_array_length(claim_ids) > 0 OR jsonb_array_length(relation_evidence_ids) > 0", name="ck_projection_edge_provenance"))
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    projection_run_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    source_key: Mapped[str] = mapped_column(String(255), nullable=False)
    target_key: Mapped[str] = mapped_column(String(255), nullable=False)
    edge_type: Mapped[str] = mapped_column(String(32), nullable=False)
    claim_ids: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    relation_evidence_ids: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)


class RetrievalRun(Timestamped, Base):
    __tablename__ = "retrieval_runs"
    __table_args__ = (
        UniqueConstraint("namespace_id", "id", name="uq_retrieval_runs_namespace_id"),
        CheckConstraint(
            "retrieval_mode IN ('embedding_only','hebbian')", name="ck_retrieval_runs_mode"
        ),
        CheckConstraint("run_mode IN ('evaluation','learning')", name="ck_retrieval_runs_run_mode"),
        CheckConstraint(
            "query_scope IN ('historical','current','general')", name="ck_retrieval_runs_scope"
        ),
        CheckConstraint(
            "total_latency_ms IS NULL OR total_latency_ms >= 0", name="ck_retrieval_runs_latency"
        ),
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    namespace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("memory_namespaces.id", ondelete="RESTRICT"), nullable=False
    )
    query: Mapped[str] = mapped_column(Text, nullable=False)
    retrieval_mode: Mapped[str] = mapped_column(String(32), nullable=False)
    run_mode: Mapped[str] = mapped_column(String(16), nullable=False)
    query_scope: Mapped[str] = mapped_column(String(16), nullable=False)
    total_latency_ms: Mapped[float | None] = mapped_column(REAL)
    metadata_: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSONB, nullable=False, default=dict
    )


class RetrievalItem(Timestamped, Base):
    __tablename__ = "retrieval_items"
    __table_args__ = (
        ForeignKeyConstraint(
            ["namespace_id", "run_id"],
            ["retrieval_runs.namespace_id", "retrieval_runs.id"],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["namespace_id", "memory_id"],
            ["memories.namespace_id", "memories.id"],
            ondelete="CASCADE",
        ),
        UniqueConstraint("run_id", "candidate_rank"),
        UniqueConstraint("run_id", "final_rank"),
        CheckConstraint("retrieval_source IN ('seed','hebbian')", name="ck_retrieval_items_source"),
        CheckConstraint(
            "semantic_score BETWEEN 0.0 AND 1.0", name="ck_retrieval_items_semantic_score"
        ),
        CheckConstraint("hebbian_score >= 0.0", name="ck_retrieval_items_hebbian_score"),
        CheckConstraint(
            "candidate_rank >= 1 AND (final_rank IS NULL OR final_rank >= 1) AND ((selected = TRUE AND final_rank IS NOT NULL) OR (selected = FALSE AND final_rank IS NULL))",
            name="ck_retrieval_items_ranks",
        ),
        Index("ix_retrieval_items_run_selected", "run_id", "selected", "final_rank"),
    )
    namespace_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    run_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    memory_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    candidate_rank: Mapped[int] = mapped_column(Integer, nullable=False)
    final_rank: Mapped[int | None] = mapped_column(Integer)
    selected: Mapped[bool] = mapped_column(nullable=False, default=False)
    semantic_score: Mapped[float] = mapped_column(REAL, nullable=False, default=0.0)
    hebbian_score: Mapped[float] = mapped_column(REAL, nullable=False, default=0.0)
    status_adjustment: Mapped[float] = mapped_column(REAL, nullable=False, default=0.0)
    final_score: Mapped[float] = mapped_column(REAL, nullable=False)
    retrieval_source: Mapped[str] = mapped_column(String(32), nullable=False)
    activation_path: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list
    )
