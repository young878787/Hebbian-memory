"""Edge direction and transactional symmetry contracts."""

from __future__ import annotations

from datetime import UTC, datetime
from itertools import combinations
from uuid import UUID

from sqlalchemy.orm import Session

from ..config import LearningConfig
from ..ingestion.contracts import EdgeType
from .models import MemoryEdge

SYMMETRIC_EDGE_TYPES = {EdgeType.SEMANTIC, EdgeType.CO_RETRIEVAL, EdgeType.CONTRADICTS}
DEFAULT_LOGICAL_WEIGHT = {
    EdgeType.SUPERSEDES: 1.0,
    EdgeType.CONTRADICTS: 1.0,
    EdgeType.SUPPORTS: 1.0,
}


def edge_weight(edge_type: EdgeType, supplied: float | None) -> float:
    if supplied is not None:
        return supplied
    return DEFAULT_LOGICAL_WEIGHT.get(edge_type, 0.25)


def upsert_edge(
    session: Session,
    namespace_id: UUID,
    source_id: UUID,
    target_id: UUID,
    edge_type: EdgeType,
    weight: float,
    metadata: dict | None = None,
    *,
    activation_count: int = 0,
    last_activated_at: datetime | None = None,
) -> None:
    pairs = [(source_id, target_id)]
    if edge_type in SYMMETRIC_EDGE_TYPES:
        pairs.append((target_id, source_id))
    for source, target in pairs:
        edge = session.get(MemoryEdge, (namespace_id, source, target, edge_type.value))
        if edge is None:
            session.add(
                MemoryEdge(
                    namespace_id=namespace_id,
                    source_id=source,
                    target_id=target,
                    edge_type=edge_type.value,
                    weight=weight,
                    metadata_=metadata or {},
                    activation_count=activation_count,
                    last_activated_at=last_activated_at,
                )
            )
        else:
            edge.weight = weight
            edge.metadata_ = metadata or {}
            edge.activation_count = activation_count
            edge.last_activated_at = last_activated_at


def reinforce_co_retrieval(
    session: Session,
    namespace_id: UUID,
    memory_ids: list[UUID],
    learning: LearningConfig,
    *,
    metadata: dict | None = None,
) -> None:
    """Must be invoked only after a retrieval run and trace are committed."""
    now = datetime.now(UTC)
    for source_id, target_id in combinations(sorted(memory_ids), 2):
        edge = session.get(
            MemoryEdge, (namespace_id, source_id, target_id, EdgeType.CO_RETRIEVAL.value)
        )
        current_weight = edge.weight if edge else 0.0
        current_count = edge.activation_count if edge else 0
        new_weight = min(learning.max_edge_weight, current_weight + learning.co_retrieval_increment)
        upsert_edge(
            session,
            namespace_id,
            source_id,
            target_id,
            EdgeType.CO_RETRIEVAL,
            new_weight,
            metadata={
                **(metadata or {}),
                "old_weight": current_weight,
                "new_weight": new_weight,
                "activation_count": current_count + 1,
                "timestamp": now.isoformat(),
            },
            activation_count=current_count + 1,
            last_activated_at=now,
        )
