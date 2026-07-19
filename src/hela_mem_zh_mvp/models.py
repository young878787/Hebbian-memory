"""SQLAlchemy mappings for the four MVP tables only."""

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


class Memory(Base):
    __tablename__ = "memories"
    __table_args__ = (
        CheckConstraint("status IN ('active','superseded','uncertain','archived')", name="ck_memories_status"),
        CheckConstraint("memory_type IN ('character_fact','event','preference','decision')", name="ck_memories_type"),
        CheckConstraint("importance BETWEEN 0.0 AND 1.0", name="ck_memories_importance"),
        CheckConstraint("confidence BETWEEN 0.0 AND 1.0", name="ck_memories_confidence"),
        Index("ix_memories_status_occurred_at", "status", "occurred_at"),
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    external_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    memory_type: Mapped[str] = mapped_column(String(32), nullable=False)
    topic: Mapped[str | None] = mapped_column(String(100))
    occurred_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active")
    importance: Mapped[float] = mapped_column(REAL, nullable=False, default=0.5)
    confidence: Mapped[float] = mapped_column(REAL, nullable=False, default=1.0)
    source_session_id: Mapped[str | None] = mapped_column(String(100))
    source_message_ids: Mapped[list[str] | None] = mapped_column(JSONB)
    metadata_: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, nullable=False, default=dict)
    embedding: Mapped[list[float]] = mapped_column(VECTOR(2560), nullable=False)


class MemoryEdge(Base):
    __tablename__ = "memory_edges"
    __table_args__ = (
        CheckConstraint("edge_type IN ('temporal','semantic','co_retrieval','supersedes','contradicts','supports')", name="ck_memory_edges_type"),
        CheckConstraint("weight BETWEEN 0.0 AND 1.0", name="ck_memory_edges_weight"),
        CheckConstraint("activation_count >= 0", name="ck_memory_edges_activation_count"),
        CheckConstraint("source_id <> target_id", name="ck_memory_edges_no_self_loop"),
        Index("ix_memory_edges_target", "target_id", "edge_type"),
    )
    source_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("memories.id", ondelete="CASCADE"), primary_key=True)
    target_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("memories.id", ondelete="CASCADE"), primary_key=True)
    edge_type: Mapped[str] = mapped_column(String(32), primary_key=True)
    weight: Mapped[float] = mapped_column(REAL, nullable=False, default=0.0)
    activation_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    metadata_: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, nullable=False, default=dict)


class RetrievalRun(Base):
    __tablename__ = "retrieval_runs"
    __table_args__ = (
        CheckConstraint("retrieval_mode IN ('embedding_only','hebbian')", name="ck_retrieval_runs_mode"),
        CheckConstraint("run_mode IN ('evaluation','learning')", name="ck_retrieval_runs_run_mode"),
        CheckConstraint("query_scope IN ('historical','current','general')", name="ck_retrieval_runs_scope"),
        CheckConstraint("total_latency_ms IS NULL OR total_latency_ms >= 0", name="ck_retrieval_runs_latency"),
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    query: Mapped[str] = mapped_column(Text, nullable=False)
    retrieval_mode: Mapped[str] = mapped_column(String(32), nullable=False)
    run_mode: Mapped[str] = mapped_column(String(16), nullable=False)
    query_scope: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    total_latency_ms: Mapped[float | None] = mapped_column(REAL)
    metadata_: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, nullable=False, default=dict)


class RetrievalItem(Base):
    __tablename__ = "retrieval_items"
    __table_args__ = (
        UniqueConstraint("run_id", "candidate_rank"),
        UniqueConstraint("run_id", "final_rank"),
        CheckConstraint("retrieval_source IN ('seed','hebbian')", name="ck_retrieval_items_source"),
        CheckConstraint("semantic_score BETWEEN 0.0 AND 1.0", name="ck_retrieval_items_semantic_score"),
        CheckConstraint("hebbian_score >= 0.0", name="ck_retrieval_items_hebbian_score"),
        CheckConstraint("candidate_rank >= 1 AND (final_rank IS NULL OR final_rank >= 1) AND ((selected = TRUE AND final_rank IS NOT NULL) OR (selected = FALSE AND final_rank IS NULL))", name="ck_retrieval_items_ranks"),
        Index("ix_retrieval_items_run_selected", "run_id", "selected", "final_rank"),
    )
    run_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("retrieval_runs.id", ondelete="CASCADE"), primary_key=True)
    memory_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("memories.id", ondelete="CASCADE"), primary_key=True)
    candidate_rank: Mapped[int] = mapped_column(Integer, nullable=False)
    final_rank: Mapped[int | None] = mapped_column(Integer)
    selected: Mapped[bool] = mapped_column(nullable=False, default=False)
    semantic_score: Mapped[float] = mapped_column(REAL, nullable=False, default=0.0)
    hebbian_score: Mapped[float] = mapped_column(REAL, nullable=False, default=0.0)
    status_adjustment: Mapped[float] = mapped_column(REAL, nullable=False, default=0.0)
    final_score: Mapped[float] = mapped_column(REAL, nullable=False)
    retrieval_source: Mapped[str] = mapped_column(String(32), nullable=False)
    activation_path: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
