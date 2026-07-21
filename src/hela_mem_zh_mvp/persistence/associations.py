"""Replayable association event ledger and materialized statistics."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from itertools import combinations
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import AssociationEvent, AssociationStat

POLICY_VERSION = "association-v1"
POSITIVE_EVENTS = {"cited_together", "explicit_positive_feedback", "manual_link"}
NEGATIVE_EVENTS = {"explicit_negative_feedback", "answer_rejected"}


def _pair(left: UUID, right: UUID) -> tuple[UUID, UUID]:
    return tuple(sorted((left, right), key=str))  # type: ignore[return-value]


def _key(run_id: UUID | None, event_type: str, source_id: UUID, target_id: UUID) -> str:
    payload = f"{run_id or 'manual'}:{event_type}:{source_id}:{target_id}"
    return hashlib.sha256(payload.encode()).hexdigest()


def _materialize(session: Session, namespace_id: UUID, source_id: UUID, target_id: UUID) -> AssociationStat:
    events = session.scalars(
        select(AssociationEvent).where(
            AssociationEvent.namespace_id == namespace_id,
            AssociationEvent.source_memory_id == source_id,
            AssociationEvent.target_memory_id == target_id,
        )
    ).all()
    positive = sum(event.delta for event in events if event.event_type in POSITIVE_EVENTS)
    negative = sum(abs(event.delta) for event in events if event.event_type in NEGATIVE_EVENTS)
    stat = session.get(AssociationStat, (namespace_id, source_id, target_id))
    if stat is None:
        stat = AssociationStat(
            namespace_id=namespace_id,
            source_memory_id=source_id,
            target_memory_id=target_id,
            decay_policy_version=POLICY_VERSION,
        )
        session.add(stat)
    stat.positive_strength = positive
    stat.negative_strength = negative
    stat.activation_count = len(events)
    stat.success_count = sum(event.event_type in POSITIVE_EVENTS for event in events)
    stat.rejection_count = sum(event.event_type in NEGATIVE_EVENTS for event in events)
    stat.last_activated_at = max((event.occurred_at for event in events), default=None)
    stat.last_reinforced_at = stat.last_activated_at
    stat.effective_weight = max(0.0, min(1.0, positive - negative))
    return stat


def append_events(
    session: Session,
    namespace_id: UUID,
    memory_ids: list[UUID],
    *,
    event_type: str,
    delta: float,
    retrieval_run_id: UUID | None,
    evidence_refs: list[str],
    occurred_at: datetime | None = None,
) -> int:
    if event_type not in POSITIVE_EVENTS | NEGATIVE_EVENTS | {"decay_checkpoint"}:
        raise ValueError(f"unsupported association event {event_type!r}")
    now = occurred_at or datetime.now(UTC)
    inserted = 0
    for left, right in combinations(sorted(set(memory_ids), key=str), 2):
        source_id, target_id = _pair(left, right)
        key = _key(retrieval_run_id, event_type, source_id, target_id)
        existing = session.scalar(
            select(AssociationEvent.id).where(AssociationEvent.idempotency_key == key)
        )
        if existing is not None:
            continue
        session.add(
            AssociationEvent(
                namespace_id=namespace_id,
                source_memory_id=source_id,
                target_memory_id=target_id,
                event_type=event_type,
                delta=delta,
                retrieval_run_id=retrieval_run_id,
                evidence_refs=evidence_refs,
                occurred_at=now,
                policy_version=POLICY_VERSION,
                idempotency_key=key,
            )
        )
        session.flush()
        _materialize(session, namespace_id, source_id, target_id)
        inserted += 1
    return inserted


def record_cited_together(
    session: Session, namespace_id: UUID, memory_ids: list[UUID], run_id: UUID, increment: float, citations: list[str]
) -> int:
    return append_events(
        session, namespace_id, memory_ids, event_type="cited_together", delta=increment,
        retrieval_run_id=run_id, evidence_refs=citations,
    )


__all__ = ["POLICY_VERSION", "append_events", "record_cited_together"]
