"""Report-only lifecycle proposals; apply is deliberately absent from the CLI."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import LifecycleConfig
from ..persistence.models import Memory, MemoryAssociation
from .policy import effective_relevance, is_forgetting_candidate


def lifecycle_report(
    session: Session, namespace_id: object, *, now: datetime, config: LifecycleConfig
) -> dict[str, object]:
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
        relevance = effective_relevance(importance=memory.importance, confidence=memory.confidence, last_activated_at=last, now=now, positive_strength=positive, negative_strength=negative, half_life_days=config.half_life_days)
        recently_cited = last is not None and (now - last).days <= config.recently_cited_days
        last_activity = last or memory.updated_at or memory.occurred_at
        idle_days = (now - last_activity).days if last_activity is not None else None
        max_edge_weight = max((stat.effective_weight for stat in stats), default=0.0)
        activation_count = sum(stat.activation_count for stat in stats)
        if is_forgetting_candidate(
            status=memory.status,
            importance=memory.importance,
            recently_cited=recently_cited,
            relevance=relevance,
            relevance_threshold=config.relevance_threshold,
            idle_days=idle_days,
            min_idle_days=config.min_idle_days,
            max_edge_weight=max_edge_weight,
            edge_weight_limit=config.max_edge_weight,
            activation_count=activation_count,
            activation_count_limit=config.max_activation_count,
        ):
            proposals.append({
                "memory_id": str(memory.id),
                "action": "archive_proposal",
                "effective_relevance": relevance,
                "reasons": {
                    "idle_days": idle_days,
                    "max_edge_weight": max_edge_weight,
                    "activation_count": activation_count,
                    "recently_cited": recently_cited,
                },
                "rollback": {"status": memory.status},
            })
    return {
        "mode": "report_only",
        "config": config.model_dump(mode="json"),
        "proposal_count": len(proposals),
        "proposals": proposals,
    }
