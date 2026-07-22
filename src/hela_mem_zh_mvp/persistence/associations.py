"""Atomic current-state learning aggregates without an event-history table."""

from __future__ import annotations

from datetime import UTC, datetime
from itertools import combinations
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import MemoryAssociation

POLICY_VERSION = "association-v2"


def reinforce_associations(
    session: Session,
    namespace_id: UUID,
    memory_ids: list[UUID],
    *,
    learning_token: str,
    increment: float,
    citations: list[str],
) -> int:
    updated = 0
    now = datetime.now(UTC)
    for left, right in combinations(sorted(set(memory_ids), key=str), 2):
        source_id, target_id = sorted((left, right), key=str)
        association = session.scalar(
            select(MemoryAssociation)
            .where(
                MemoryAssociation.namespace_id == namespace_id,
                MemoryAssociation.source_memory_id == source_id,
                MemoryAssociation.target_memory_id == target_id,
            )
            .with_for_update()
        )
        if association is not None and association.last_learning_token == learning_token:
            continue
        if association is None:
            association = MemoryAssociation(
                namespace_id=namespace_id,
                source_memory_id=source_id,
                target_memory_id=target_id,
                policy_version=POLICY_VERSION,
            )
            session.add(association)
        association.positive_strength += increment
        association.activation_count += 1
        association.success_count += 1
        association.last_activated_at = now
        association.last_learning_token = learning_token
        association.evidence_refs = list(
            dict.fromkeys([*(association.evidence_refs or []), *citations])
        )
        association.effective_weight = max(
            0.0,
            min(1.0, association.positive_strength - association.negative_strength),
        )
        updated += 1
    return updated


__all__ = ["POLICY_VERSION", "reinforce_associations"]
