"""Latest-state resolution report for unresolved operational candidates."""

from __future__ import annotations

from collections import Counter

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import MemoryCandidate
from .namespaces import get_namespace


def resolution_report(session: Session, namespace_key: str) -> dict[str, object]:
    namespace = get_namespace(session, namespace_key, create=False)
    candidates = session.scalars(
        select(MemoryCandidate).where(MemoryCandidate.namespace_id == namespace.id)
    ).all()
    return {
        "namespace": namespace_key,
        "mode": "latest_state",
        "candidate_count": len(candidates),
        "candidate_statuses": dict(Counter(item.status for item in candidates)),
        "decision_actions": dict(
            Counter(
                item.latest_decision.get("action")
                for item in candidates
                if item.latest_decision.get("action")
            )
        ),
        "candidates": [
            {
                "candidate_id": str(item.id),
                "status": item.status,
                "attempt_count": item.attempt_count,
                "latest_decision": item.latest_decision,
            }
            for item in candidates
        ],
    }


__all__ = ["resolution_report"]
