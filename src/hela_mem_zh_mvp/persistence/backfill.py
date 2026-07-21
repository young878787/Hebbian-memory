"""Safe inspection for the additive canonical-ledger migration."""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .models import ClaimEvidence, Memory, MemoryClaim
from .namespaces import get_namespace


def canonical_backfill_report(session: Session, namespace_key: str) -> dict[str, object]:
    namespace = get_namespace(session, namespace_key, create=False)
    memory_count = session.scalar(select(func.count()).select_from(Memory).where(Memory.namespace_id == namespace.id)) or 0
    claim_count = session.scalar(select(func.count()).select_from(MemoryClaim).where(MemoryClaim.namespace_id == namespace.id)) or 0
    evidence_count = session.scalar(select(func.count()).select_from(ClaimEvidence).where(ClaimEvidence.namespace_id == namespace.id)) or 0
    missing = session.scalar(
        select(func.count()).select_from(Memory).outerjoin(
            MemoryClaim,
            (MemoryClaim.namespace_id == Memory.namespace_id) & (MemoryClaim.legacy_memory_id == Memory.id),
        ).where(Memory.namespace_id == namespace.id, MemoryClaim.id.is_(None))
    ) or 0
    return {
        "mode": "report_only",
        "namespace": namespace_key,
        "memory_count": memory_count,
        "claim_count": claim_count,
        "claim_evidence_count": evidence_count,
        "proposed_claim_backfill": missing,
        "apply_supported": False,
    }
