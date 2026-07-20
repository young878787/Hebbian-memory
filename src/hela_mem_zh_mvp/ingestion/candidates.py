"""Database-side, namespace-scoped vector candidate selection."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..persistence.models import Memory, MemoryEntity


@dataclass(frozen=True)
class ScopedCandidate:
    memory: Memory
    similarity: float


def find_scoped_candidates(
    session: Session,
    namespace_id: UUID,
    *,
    embedding: list[float],
    memory_type: str,
    topic_key: str | None,
    entity_ids: list[UUID],
    limit: int,
) -> list[ScopedCandidate]:
    """Use pgvector ranking only after namespace/type/entity/topic filtering.

    No entity or topic means there is no safe global state-update scope.  The
    caller may still create a new memory but must not ask this function to
    choose an update target.
    """
    if not entity_ids or topic_key is None:
        return []
    distance = Memory.embedding.cosine_distance(embedding)
    rows = session.execute(
        select(Memory, (1 - distance).label("similarity"))
        .join(
            MemoryEntity,
            (MemoryEntity.namespace_id == Memory.namespace_id)
            & (MemoryEntity.memory_id == Memory.id),
        )
        .where(
            Memory.namespace_id == namespace_id,
            Memory.memory_type == memory_type,
            Memory.topic_key == topic_key,
            Memory.status.in_(("active", "uncertain", "superseded")),
            MemoryEntity.entity_id.in_(entity_ids),
        )
        .order_by(distance, Memory.occurred_at.desc().nullslast(), Memory.external_id.asc())
        .limit(limit)
    ).all()
    return [ScopedCandidate(memory=row[0], similarity=float(row[1])) for row in rows]
