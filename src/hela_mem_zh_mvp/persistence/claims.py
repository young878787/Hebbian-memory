"""Canonical claim/evidence writes used alongside the legacy ``Memory`` view."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import ClaimEvidence, Memory, MemoryClaim


def ensure_claim(
    session: Session,
    memory: Memory,
    *,
    evidence_by_message: dict[str, tuple[str, int, int]],
    extractor_model: str,
    schema_version: str,
) -> MemoryClaim:
    """Append the canonical record once and add any newly observed evidence.

    The function never rewrites evidence.  The legacy view can still merge
    provenance while the ledger keeps each message/evidence pairing visible.
    """
    claim = session.scalar(
        select(MemoryClaim).where(
            MemoryClaim.namespace_id == memory.namespace_id,
            MemoryClaim.legacy_memory_id == memory.id,
        )
    )
    if claim is None:
        # A batch may stage two candidates for one state slot before the
        # resolver applies their explicit relation.  Do not violate the DB
        # invariant transiently: keep the new ledger record uncertain until
        # the state decision synchronizes its derived view.
        initial_status = memory.status
        if memory.status == "active" and memory.state_key is not None:
            active_exists = session.scalar(
                select(MemoryClaim.id).where(
                    MemoryClaim.namespace_id == memory.namespace_id,
                    MemoryClaim.state_key == memory.state_key,
                    MemoryClaim.status == "active",
                )
            )
            if active_exists is not None:
                initial_status = "uncertain"
                memory.status = initial_status
        claim = MemoryClaim(
            namespace_id=memory.namespace_id,
            legacy_memory_id=memory.id,
            claim_type=memory.memory_type,
            predicate_key=memory.attribute_key,
            object_value=memory.content,
            event_time=memory.occurred_at,
            confidence=memory.confidence,
            importance=memory.importance,
            status=initial_status,
            modality=memory.modality,
            temporal_scope=memory.temporal_scope,
            state_key=memory.state_key,
            schema_version=schema_version,
        )
        session.add(claim)
        session.flush()
    known = {
        (row.source_message_id, row.evidence_text)
        for row in session.scalars(
            select(ClaimEvidence).where(ClaimEvidence.claim_id == claim.id)
        ).all()
    }
    for message_id, (evidence, evidence_start, evidence_end) in evidence_by_message.items():
        if (message_id, evidence) not in known:
            session.add(
                ClaimEvidence(
                    namespace_id=memory.namespace_id,
                    claim_id=claim.id,
                    source_message_id=message_id,
                    evidence_text=evidence,
                    evidence_start=evidence_start,
                    evidence_end=evidence_end,
                    extractor_model=extractor_model,
                    extraction_schema_version=schema_version,
                )
            )
    return claim


def claim_for_memory(session: Session, memory: Memory) -> MemoryClaim | None:
    return session.scalar(
        select(MemoryClaim).where(
            MemoryClaim.namespace_id == memory.namespace_id,
            MemoryClaim.legacy_memory_id == memory.id,
        )
    )


def sync_claim_view(session: Session, memory: Memory) -> None:
    """Refresh only the derived current-state fields; evidence stays append-only."""
    claim = claim_for_memory(session, memory)
    if claim is not None:
        claim.status = memory.status
        claim.modality = memory.modality
        claim.temporal_scope = memory.temporal_scope
        claim.confidence = memory.confidence
        claim.importance = memory.importance


__all__ = ["claim_for_memory", "ensure_claim", "sync_claim_view"]
