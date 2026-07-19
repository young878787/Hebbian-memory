"""Deterministic, namespace-local memory resolution decisions."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import Memory
from .schemas import ExtractedMemory, ResolutionAction


def canonical_key(content: str, memory_type: str) -> str:
    normalized = re.sub(r"\s+", "", content).casefold()
    return hashlib.sha256(f"{memory_type}:{normalized}".encode()).hexdigest()[:32]


@dataclass(frozen=True)
class Resolution:
    action: ResolutionAction
    existing: Memory | None = None


def resolve_memory(
    session: Session, namespace_id: object, candidate: ExtractedMemory
) -> Resolution:
    key = canonical_key(candidate.content, candidate.memory_type.value)
    existing = session.scalar(
        select(Memory).where(Memory.namespace_id == namespace_id, Memory.canonical_key == key)
    )
    return (
        Resolution(ResolutionAction.MERGE_PROVENANCE, existing)
        if existing
        else Resolution(ResolutionAction.CREATE)
    )
