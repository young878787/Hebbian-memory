"""Fail-closed, deterministic resolution primitives and AI post-validation."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import ResolutionConfig
from ..persistence.models import Memory
from .candidates import ScopedCandidate
from .contracts import EffectiveOrder, ExtractedMemory, ResolutionAction, ResolutionDecision
from .normalization import normalize_lookup


def canonical_key(content: str, memory_type: str) -> str:
    normalized = normalize_lookup(content)
    return hashlib.sha256(f"{memory_type}:{normalized}".encode()).hexdigest()[:32]


def candidate_key(
    namespace_id: object,
    candidate: ExtractedMemory,
    *,
    entity_ids: list[UUID],
    topic_key: str | None,
) -> str:
    payload = {
        "namespace_id": str(namespace_id),
        "schema": "memory-extraction-v1",
        "evidence": sorted(candidate.evidence_message_ids),
        "content": normalize_lookup(candidate.content),
        "memory_type": candidate.memory_type.value,
        "entities": sorted(map(str, entity_ids)),
        "topic_key": topic_key,
        "attribute_key": candidate.attribute_key or "unknown",
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def snapshot_hash(candidates: list[ScopedCandidate]) -> str:
    payload = [
        {
            "id": str(item.memory.id),
            "status": item.memory.status,
            "occurred_at": item.memory.occurred_at.isoformat() if item.memory.occurred_at else None,
            "similarity": round(item.similarity, 8),
        }
        for item in candidates
    ]
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


@dataclass(frozen=True)
class Resolution:
    action: ResolutionAction
    existing: Memory | None = None
    candidates: tuple[ScopedCandidate, ...] = ()
    reason: str = ""


def resolve_memory(
    session: Session,
    namespace_id: object,
    candidate: ExtractedMemory,
    *,
    candidates: list[ScopedCandidate] | None = None,
) -> Resolution:
    """Only make decisions whose correctness does not depend on language inference."""
    key = canonical_key(candidate.content, candidate.memory_type.value)
    existing = session.scalar(
        select(Memory).where(Memory.namespace_id == namespace_id, Memory.canonical_key == key)
    )
    if existing:
        return Resolution(
            ResolutionAction.MERGE_PROVENANCE, existing, reason="canonical key exact match"
        )
    scoped = tuple(candidates or ())
    if not scoped:
        return Resolution(ResolutionAction.CREATE, reason="no safe scoped candidates")
    # Semantic similarity alone never selects a state transition.  It only
    # marks the candidate for an optional resolver pass.
    return Resolution(
        ResolutionAction.DEFER, candidates=scoped, reason="requires semantic comparison"
    )


def resolution_prompt(
    candidate_ref: str,
    candidate: dict[str, object],
    existing: list[dict[str, object]],
) -> str:
    return (
        "你是記憶決策器。僅可在提供的 opaque refs 中選擇 target；不可產生 SQL、UUID、namespace 或新證據。"
        "證據不足時回傳 DEFER。SUPERSEDE 必須有可比較順序和互斥證據；CONTRADICT 必須是同一狀態欄位且不可同時成立。"
        f"\n新候選({candidate_ref})：{json.dumps(candidate, ensure_ascii=False)}"
        f"\n既有候選：{json.dumps(existing, ensure_ascii=False)}"
    )


def validate_ai_decision(
    decision: ResolutionDecision,
    *,
    candidate_ref: str,
    target_by_ref: dict[str, Memory],
    candidate_evidence: str,
    config: ResolutionConfig,
) -> str | None:
    """Return a durable rejection reason, never a best-effort correction."""
    if decision.candidate_id != candidate_ref:
        return "candidate_ref mismatch"
    if any(ref not in target_by_ref for ref in decision.target_refs):
        return "target outside supplied snapshot"
    if decision.confidence < config.ai_decision_confidence_min:
        return "confidence below threshold"
    if any(
        quote not in candidate_evidence
        and not any(quote in target.content for target in target_by_ref.values())
        for quote in decision.evidence_quotes
    ):
        return "evidence quote absent from supplied evidence"
    if decision.action is ResolutionAction.SUPERSEDE:
        if decision.effective_order is EffectiveOrder.UNKNOWN:
            return "supersede order unknown"
        if decision.confidence < config.supersede_confidence_min:
            return "supersede confidence below threshold"
    if decision.action is ResolutionAction.CONTRADICT and not decision.evidence_quotes:
        return "contradiction lacks evidence quote"
    return None


def reliable_order(
    candidate_at: datetime | None,
    target_at: datetime | None,
    tolerance_seconds: int,
) -> EffectiveOrder:
    if candidate_at is None or target_at is None:
        return EffectiveOrder.UNKNOWN
    delta = (candidate_at - target_at).total_seconds()
    if abs(delta) <= tolerance_seconds:
        return EffectiveOrder.SAME_TIME
    return (
        EffectiveOrder.CANDIDATE_AFTER_TARGET
        if delta > 0
        else EffectiveOrder.CANDIDATE_BEFORE_TARGET
    )
