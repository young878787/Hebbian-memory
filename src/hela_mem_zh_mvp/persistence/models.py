"""Minimal namespace-scoped persistence model for the converged pipeline."""

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
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.sql import func


class Base(DeclarativeBase):
    pass


class Timestamped:
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
    metadata_: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, nullable=False, default=dict)


class SourceMessage(Timestamped, Base):
    __tablename__ = "source_messages"
    namespace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("memory_namespaces.id", ondelete="RESTRICT"), primary_key=True
    )
    message_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    session_id: Mapped[str] = mapped_column(String(100), nullable=False)
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    metadata_: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, nullable=False, default=dict)


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
    extraction_outcomes: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list
    )
    metadata_: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, nullable=False, default=dict)


class Entity(Timestamped, Base):
    __tablename__ = "entities"
    __table_args__ = (
        UniqueConstraint("namespace_id", "entity_type", "canonical_name", name="uq_entities_namespace_type_name"),
        UniqueConstraint("namespace_id", "id", name="uq_entities_namespace_id"),
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    namespace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("memory_namespaces.id", ondelete="RESTRICT"), nullable=False
    )
    canonical_name: Mapped[str] = mapped_column(String(255), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(32), nullable=False)
    confidence: Mapped[float] = mapped_column(REAL, nullable=False)
    metadata_: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, nullable=False, default=dict)


class EntityAlias(Timestamped, Base):
    __tablename__ = "entity_aliases"
    __table_args__ = (
        ForeignKeyConstraint(["namespace_id", "entity_id"], ["entities.namespace_id", "entities.id"], ondelete="CASCADE"),
        UniqueConstraint("namespace_id", "entity_type", "alias_normalized", name="uq_entity_aliases_scope_key"),
        CheckConstraint("confidence BETWEEN 0.0 AND 1.0", name="ck_entity_aliases_confidence"),
        CheckConstraint("status IN ('active','pending','rejected')", name="ck_entity_aliases_status"),
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
        CheckConstraint("status IN ('active','superseded','uncertain','archived')", name="ck_memories_status"),
        CheckConstraint("memory_type IN ('character_fact','event','preference','decision')", name="ck_memories_type"),
        CheckConstraint("modality IN ('asserted','question','uncertain','considered')", name="ck_memories_modality"),
        CheckConstraint("temporal_scope IN ('current','historical','unknown')", name="ck_memories_temporal_scope"),
        CheckConstraint("importance BETWEEN 0.0 AND 1.0", name="ck_memories_importance"),
        CheckConstraint("confidence BETWEEN 0.0 AND 1.0", name="ck_memories_confidence"),
        Index("ix_memories_namespace_status_occurred_at", "namespace_id", "status", "occurred_at"),
        Index("ix_memories_namespace_temporal_status_occurred_at", "namespace_id", "temporal_scope", "status", "occurred_at"),
        Index("ix_memories_resolution_scope", "namespace_id", "memory_type", "topic_key", "status"),
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
    metadata_: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, nullable=False, default=dict)
    embedding: Mapped[list[float]] = mapped_column(VECTOR(2560), nullable=False)


class MemoryEvidence(Timestamped, Base):
    __tablename__ = "memory_evidence"
    __table_args__ = (
        ForeignKeyConstraint(["namespace_id", "memory_id"], ["memories.namespace_id", "memories.id"], ondelete="CASCADE"),
        ForeignKeyConstraint(["namespace_id", "source_message_id"], ["source_messages.namespace_id", "source_messages.message_id"], ondelete="RESTRICT"),
        UniqueConstraint("memory_id", "source_message_id", "evidence_text", name="uq_memory_evidence_source_text"),
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    namespace_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    memory_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    source_message_id: Mapped[str] = mapped_column(String(128), nullable=False)
    evidence_text: Mapped[str] = mapped_column(Text, nullable=False)
    evidence_start: Mapped[int | None] = mapped_column(Integer)
    evidence_end: Mapped[int | None] = mapped_column(Integer)
    extractor_model: Mapped[str] = mapped_column(String(255), nullable=False)
    extraction_schema_version: Mapped[str] = mapped_column(String(64), nullable=False)


class MemoryEntity(Timestamped, Base):
    __tablename__ = "memory_entities"
    __table_args__ = (
        ForeignKeyConstraint(["namespace_id", "memory_id"], ["memories.namespace_id", "memories.id"], ondelete="CASCADE"),
        ForeignKeyConstraint(["namespace_id", "entity_id"], ["entities.namespace_id", "entities.id"], ondelete="CASCADE"),
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
        CheckConstraint("status IN ('pending','resolving','resolved','deferred','failed')", name="ck_memory_candidates_status"),
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
    latest_decision: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)


class MemoryRelation(Timestamped, Base):
    __tablename__ = "memory_relations"
    __table_args__ = (
        ForeignKeyConstraint(["namespace_id", "source_id"], ["memories.namespace_id", "memories.id"], ondelete="CASCADE"),
        ForeignKeyConstraint(["namespace_id", "target_id"], ["memories.namespace_id", "memories.id"], ondelete="CASCADE"),
        CheckConstraint("relation_type IN ('temporal','semantic','supersedes','contradicts','supports')", name="ck_memory_relations_type"),
        CheckConstraint("weight BETWEEN 0.0 AND 1.0", name="ck_memory_relations_weight"),
        CheckConstraint("confidence BETWEEN 0.0 AND 1.0", name="ck_memory_relations_confidence"),
        CheckConstraint("activation_count >= 0", name="ck_memory_relations_activation_count"),
        CheckConstraint("source_id <> target_id", name="ck_memory_relations_no_self_loop"),
        Index("ix_memory_relations_namespace_target", "namespace_id", "target_id", "relation_type"),
    )
    namespace_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    source_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    target_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    relation_type: Mapped[str] = mapped_column(String(32), primary_key=True)
    weight: Mapped[float] = mapped_column(REAL, nullable=False, default=0.0)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    origin: Mapped[str] = mapped_column(String(32), nullable=False)
    confidence: Mapped[float] = mapped_column(REAL, nullable=False, default=1.0)
    evidence_refs: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    activation_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    metadata_: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, nullable=False, default=dict)


class MemoryAssociation(Timestamped, Base):
    __tablename__ = "memory_associations"
    __table_args__ = (
        ForeignKeyConstraint(["namespace_id", "source_memory_id"], ["memories.namespace_id", "memories.id"], ondelete="CASCADE"),
        ForeignKeyConstraint(["namespace_id", "target_memory_id"], ["memories.namespace_id", "memories.id"], ondelete="CASCADE"),
        CheckConstraint("source_memory_id < target_memory_id", name="ck_memory_associations_order"),
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
    effective_weight: Mapped[float] = mapped_column(REAL, nullable=False, default=0.0)
    last_learning_token: Mapped[str | None] = mapped_column(String(128))
    policy_version: Mapped[str] = mapped_column(String(64), nullable=False)
    evidence_refs: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
