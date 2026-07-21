"""Report-only lifecycle proposals; apply is deliberately absent from the CLI."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..persistence.models import AssociationStat, MemoryClaim
from .policy import effective_relevance, may_archive


def lifecycle_report(session: Session, namespace_id: object, *, now: datetime, threshold: float = 0.08) -> dict[str, object]:
    claims = session.scalars(select(MemoryClaim).where(MemoryClaim.namespace_id == namespace_id)).all()
    proposals = []
    for claim in claims:
        stats = session.scalars(
            select(AssociationStat).where(
                AssociationStat.namespace_id == namespace_id,
                (AssociationStat.source_memory_id == claim.legacy_memory_id) | (AssociationStat.target_memory_id == claim.legacy_memory_id),
            )
        ).all()
        positive = sum(stat.positive_strength for stat in stats)
        negative = sum(stat.negative_strength for stat in stats)
        last = max((stat.last_activated_at for stat in stats if stat.last_activated_at), default=None)
        relevance = effective_relevance(importance=claim.importance, confidence=claim.confidence, last_activated_at=last, now=now, positive_strength=positive, negative_strength=negative, half_life_days=30)
        if may_archive(status=claim.status, importance=claim.importance, recently_cited=last is not None and (now - last).days <= 30, relevance=relevance, threshold=threshold):
            proposals.append({"claim_id": str(claim.id), "action": "archive_proposal", "effective_relevance": relevance, "rollback": {"status": claim.status}})
    return {"mode": "report_only", "proposal_count": len(proposals), "proposals": proposals}
