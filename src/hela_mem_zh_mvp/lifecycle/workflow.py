"""Report-only lifecycle proposals; apply is deliberately absent from the CLI."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..persistence.models import Memory, MemoryAssociation
from .policy import effective_relevance, may_archive


def lifecycle_report(session: Session, namespace_id: object, *, now: datetime, threshold: float = 0.08) -> dict[str, object]:
    memories = session.scalars(select(Memory).where(Memory.namespace_id == namespace_id)).all()
    proposals = []
    for memory in memories:
        stats = session.scalars(
            select(MemoryAssociation).where(
                MemoryAssociation.namespace_id == namespace_id,
                (MemoryAssociation.source_memory_id == memory.id) | (MemoryAssociation.target_memory_id == memory.id),
            )
        ).all()
        positive = sum(stat.positive_strength for stat in stats)
        negative = sum(stat.negative_strength for stat in stats)
        last = max((stat.last_activated_at for stat in stats if stat.last_activated_at), default=None)
        relevance = effective_relevance(importance=memory.importance, confidence=memory.confidence, last_activated_at=last, now=now, positive_strength=positive, negative_strength=negative, half_life_days=30)
        if may_archive(status=memory.status, importance=memory.importance, recently_cited=last is not None and (now - last).days <= 30, relevance=relevance, threshold=threshold):
            proposals.append({"memory_id": str(memory.id), "action": "archive_proposal", "effective_relevance": relevance, "rollback": {"status": memory.status}})
    return {"mode": "report_only", "proposal_count": len(proposals), "proposals": proposals}
