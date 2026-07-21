"""Evidence-backed factual relation ledger; never accepts learning origins."""

from __future__ import annotations

from sqlalchemy.orm import Session

from .models import MemoryClaim, RelationEvidence

FACTUAL_RELATIONS = {"supports", "contradicts", "supersedes", "temporal"}


def append_relation_evidence(
    session: Session,
    *,
    namespace_id: object,
    source_claim: MemoryClaim,
    target_claim: MemoryClaim,
    relation_type: str,
    origin: str,
    evidence_refs: list[str],
    confidence: float = 1.0,
) -> RelationEvidence:
    if relation_type not in FACTUAL_RELATIONS:
        raise ValueError(f"unsupported factual relation {relation_type!r}")
    if origin in {"co_retrieval", "association", "learning"}:
        raise ValueError("association learning cannot create factual relation evidence")
    if source_claim.namespace_id != namespace_id or target_claim.namespace_id != namespace_id:
        raise ValueError("relation evidence cannot cross namespace")
    if source_claim.id == target_claim.id:
        raise ValueError("relation evidence cannot self-link")
    relation = RelationEvidence(
        namespace_id=namespace_id,
        source_claim_id=source_claim.id,
        target_claim_id=target_claim.id,
        relation_type=relation_type,
        origin=origin,
        evidence_refs=evidence_refs,
        confidence=confidence,
    )
    session.add(relation)
    return relation


__all__ = ["FACTUAL_RELATIONS", "append_relation_evidence"]
