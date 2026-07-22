"""Namespace-scoped semantic and deterministic lexical candidate retrieval."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import case, literal, select
from sqlalchemy.orm import Session

from ..config import RetrievalConfig
from ..persistence.models import Entity, EntityAlias, Memory, MemoryEntity
from ..persistence.normalization import normalize_lookup
from .contracts import RankedMemory


def _character_ngrams(value: str, size: int = 2) -> set[str]:
    normalized = normalize_lookup(value)
    if len(normalized) <= size:
        return {normalized}
    return {normalized[index : index + size] for index in range(len(normalized) - size + 1)}


def apply_content_rerank(
    items: list[RankedMemory], config: RetrievalConfig, query: str
) -> None:
    """Add a bounded deterministic overlap score without query-time model calls."""
    query_ngrams = _character_ngrams(query)
    for item in items:
        memory_ngrams = _character_ngrams(item.memory.content)
        overlap = len(query_ngrams & memory_ngrams) / max(1, len(memory_ngrams))
        if overlap >= config.content_rerank_min_overlap:
            item.rerank_score = overlap * config.content_rerank_bonus


def semantic_candidates(
    session: Session,
    config: RetrievalConfig,
    namespace_id: UUID,
    query_embedding: list[float],
) -> list[RankedMemory]:
    distance = Memory.embedding.cosine_distance(query_embedding)
    rows = session.execute(
        select(Memory, (1 - distance).label("semantic_score"))
        .where(Memory.namespace_id == namespace_id)
        .order_by(distance)
        .limit(config.candidate_limit)
    ).all()
    return [
        RankedMemory(
            memory=row.Memory,
            semantic_score=max(0.0, min(1.0, float(row.semantic_score))),
        )
        for row in rows
    ]


def lexical_candidates(
    session: Session, config: RetrievalConfig, namespace_id: UUID, query: str
) -> list[RankedMemory]:
    """Return bounded namespace-local exact alias/entity/topic matches."""
    normalized_query = normalize_lookup(query)
    minimum = config.min_lexical_term_length
    alias_rows = session.execute(
        select(
            Memory,
            case(
                (EntityAlias.alias_raw == Entity.canonical_name, "entity"),
                else_="alias",
            ).label("lexical_source"),
        )
        .join(MemoryEntity, MemoryEntity.memory_id == Memory.id)
        .join(
            Entity,
            (Entity.namespace_id == MemoryEntity.namespace_id)
            & (Entity.id == MemoryEntity.entity_id),
        )
        .join(
            EntityAlias,
            (EntityAlias.namespace_id == MemoryEntity.namespace_id)
            & (EntityAlias.entity_id == MemoryEntity.entity_id),
        )
        .where(
            Memory.namespace_id == namespace_id,
            EntityAlias.namespace_id == namespace_id,
            EntityAlias.status == "active",
            literal(normalized_query).contains(EntityAlias.alias_normalized),
            EntityAlias.alias_normalized.op("~")(rf"^.{{{minimum},}}$"),
        )
        .limit(config.lexical_candidate_limit * 4)
    ).all()
    topic_rows = session.scalars(
        select(Memory)
        .where(
            Memory.namespace_id == namespace_id,
            Memory.topic_key.is_not(None),
            literal(normalized_query).contains(Memory.topic_key),
            Memory.topic_key.op("~")(rf"^.{{{minimum},}}$"),
        )
        .limit(config.lexical_candidate_limit)
    ).all()
    by_id: dict[UUID, RankedMemory] = {}
    for row in alias_rows:
        item = by_id.setdefault(
            row.Memory.id,
            RankedMemory(row.Memory, 0.0, lexical_score=config.lexical_bonus),
        )
        if row.lexical_source not in item.lexical_sources:
            item.lexical_sources.append(row.lexical_source)
    for memory in topic_rows:
        item = by_id.setdefault(
            memory.id,
            RankedMemory(memory, 0.0, lexical_score=config.lexical_bonus),
        )
        if "topic" not in item.lexical_sources:
            item.lexical_sources.append("topic")
    return list(by_id.values())[: config.lexical_candidate_limit]


def fuse_candidates(
    semantic: list[RankedMemory], lexical: list[RankedMemory]
) -> dict[UUID, RankedMemory]:
    candidates = {item.memory.id: item for item in semantic}
    for lexical_item in lexical:
        existing = candidates.get(lexical_item.memory.id)
        if existing is None:
            lexical_item.source = "+".join(lexical_item.lexical_sources)
            candidates[lexical_item.memory.id] = lexical_item
            continue
        existing.lexical_score = max(existing.lexical_score, lexical_item.lexical_score)
        existing.rerank_score = max(existing.rerank_score, lexical_item.rerank_score)
        existing.lexical_sources = sorted(
            set(existing.lexical_sources) | set(lexical_item.lexical_sources)
        )
        existing.source = "+".join(["semantic", *existing.lexical_sources])
    return candidates


def select_seeds(
    config: RetrievalConfig,
    semantic: list[RankedMemory],
    lexical: list[RankedMemory],
) -> list[RankedMemory]:
    semantic = sorted(
        semantic,
        key=lambda item: -(
            item.semantic_score
            + item.lexical_score
            + item.rerank_score
            + item.model_rerank_score
        ),
    )
    semantic_ids = {item.memory.id for item in semantic}
    lexical_only = [item for item in lexical if item.memory.id not in semantic_ids]
    reserved = min(config.lexical_seed_limit, len(lexical_only))
    semantic_count = config.seed_top_k - reserved
    return [*semantic[:semantic_count], *lexical_only[:reserved]]


__all__ = [
    "apply_content_rerank",
    "fuse_candidates",
    "lexical_candidates",
    "select_seeds",
    "semantic_candidates",
]
