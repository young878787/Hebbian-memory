"""Single-writer persistence for factual, temporal, and derived relations."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy.orm import Session

from ..ingestion.contracts import EdgeType
from .models import MemoryRelation


def upsert_memory_relation(
    session: Session,
    namespace_id: UUID,
    source_id: UUID,
    target_id: UUID,
    relation_type: EdgeType | str,
    weight: float,
    *,
    origin: str,
    confidence: float = 1.0,
    evidence_refs: list[str] | None = None,
    metadata: dict[str, object] | None = None,
) -> MemoryRelation:
    relation_value = relation_type.value if isinstance(relation_type, EdgeType) else relation_type
    if relation_value == EdgeType.CO_RETRIEVAL.value:
        raise ValueError("co-retrieval learning belongs in memory_associations")
    if source_id == target_id:
        raise ValueError("memory relation cannot self-link")
    source, target = source_id, target_id
    if relation_value in {EdgeType.SEMANTIC.value, EdgeType.CONTRADICTS.value}:
        source, target = sorted((source_id, target_id), key=str)
    relation = session.get(MemoryRelation, (namespace_id, source, target, relation_value))
    if relation is None:
        relation = MemoryRelation(
            namespace_id=namespace_id,
            source_id=source,
            target_id=target,
            relation_type=relation_value,
            weight=weight,
            origin=origin,
            confidence=confidence,
            evidence_refs=list(dict.fromkeys(evidence_refs or [])),
            metadata_=metadata or {},
        )
        session.add(relation)
    else:
        relation.weight = max(relation.weight, weight)
        relation.confidence = max(relation.confidence, confidence)
        relation.evidence_refs = list(
            dict.fromkeys([*(relation.evidence_refs or []), *(evidence_refs or [])])
        )
        relation.metadata_ = {**(relation.metadata_ or {}), **(metadata or {})}
    return relation


def relation_weight(
    session: Session,
    namespace_id: UUID,
    source_id: UUID,
    target_id: UUID,
    relation_type: EdgeType,
) -> float | None:
    source, target = source_id, target_id
    if relation_type in {EdgeType.SEMANTIC, EdgeType.CONTRADICTS}:
        source, target = sorted((source_id, target_id), key=str)
    relation = session.get(
        MemoryRelation, (namespace_id, source, target, relation_type.value)
    )
    return relation.weight if relation is not None else None


__all__ = ["relation_weight", "upsert_memory_relation"]
