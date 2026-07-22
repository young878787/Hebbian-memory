"""Exact source evidence attached directly to canonical memories."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import Memory, MemoryEvidence


def ensure_memory_evidence(
    session: Session,
    memory: Memory,
    *,
    evidence_by_message: dict[str, tuple[str, int, int]],
    extractor_model: str,
    schema_version: str,
) -> None:
    known = {
        (row.source_message_id, row.evidence_text)
        for row in session.scalars(
            select(MemoryEvidence).where(MemoryEvidence.memory_id == memory.id)
        ).all()
    }
    for message_id, (evidence, evidence_start, evidence_end) in evidence_by_message.items():
        if (message_id, evidence) in known:
            continue
        session.add(
            MemoryEvidence(
                namespace_id=memory.namespace_id,
                memory_id=memory.id,
                source_message_id=message_id,
                evidence_text=evidence,
                evidence_start=evidence_start,
                evidence_end=evidence_end,
                extractor_model=extractor_model,
                extraction_schema_version=schema_version,
            )
        )


__all__ = ["ensure_memory_evidence"]
