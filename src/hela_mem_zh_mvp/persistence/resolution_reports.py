"""Read-only reporting queries for persisted resolution decisions."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import MemoryCandidate, MemoryResolutionDecision
from .namespaces import get_namespace


def resolution_report(session: Session, namespace_key: str) -> dict[str, object]:
    namespace = get_namespace(session, namespace_key, create=False)
    candidates = session.scalars(
        select(MemoryCandidate).where(MemoryCandidate.namespace_id == namespace.id)
    ).all()
    decisions = session.scalars(
        select(MemoryResolutionDecision).where(
            MemoryResolutionDecision.namespace_id == namespace.id
        )
    ).all()
    return {
        "namespace": namespace_key,
        "candidate_statuses": {
            status: sum(item.status == status for item in candidates)
            for status in ("pending", "resolving", "resolved", "deferred", "failed")
        },
        "decisions": [
            {"action": item.action, "validation_status": item.validation_status}
            for item in decisions
        ],
        "apply": False,
    }
