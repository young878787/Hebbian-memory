"""Build a disposable graph only from evidenced canonical records."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..persistence.models import (
    ClaimEvidence,
    GraphProjectionEdge,
    GraphProjectionNode,
    GraphProjectionRun,
    MemoryClaim,
    RelationEvidence,
)

PROJECTION_VERSION = "claim-graph-v1"


def rebuild_projection(session: Session, namespace_id: object, config_snapshot: dict[str, object]) -> dict[str, object]:
    claims = session.scalars(
        select(MemoryClaim).where(MemoryClaim.namespace_id == namespace_id)
    ).all()
    evidence_claim_ids = set(
        session.scalars(
            select(ClaimEvidence.claim_id).where(ClaimEvidence.namespace_id == namespace_id)
        ).all()
    )
    included = [claim for claim in claims if claim.id in evidence_claim_ids and claim.status != "archived"]
    blockers = {"missing_evidence": len([claim for claim in claims if claim.id not in evidence_claim_ids]), "archived": len([claim for claim in claims if claim.status == "archived"])}
    snapshot = hashlib.sha256(
        json.dumps(
            [(str(claim.id), claim.status, claim.object_value) for claim in sorted(included, key=lambda item: str(item.id))],
            ensure_ascii=False,
        ).encode()
    ).hexdigest()
    run = GraphProjectionRun(
        namespace_id=namespace_id,
        projection_version=PROJECTION_VERSION,
        source_snapshot_hash=snapshot,
        config_snapshot=config_snapshot,
        included_claim_count=len(included),
        excluded_claim_count=len(claims) - len(included),
        blocker_counts=blockers,
        status="running",
    )
    session.add(run)
    session.flush()
    included_ids = {claim.id for claim in included}
    for claim in included:
        session.add(
            GraphProjectionNode(
                projection_run_id=run.id,
                node_key=str(claim.id),
                node_type=claim.claim_type,
                claim_ids=[str(claim.id)],
            )
        )
    relations = session.scalars(
        select(RelationEvidence).where(
            RelationEvidence.namespace_id == namespace_id,
            RelationEvidence.source_claim_id.in_(included_ids),
            RelationEvidence.target_claim_id.in_(included_ids),
            RelationEvidence.resolution_status == "active",
        )
    ).all()
    for relation in relations:
        session.add(
            GraphProjectionEdge(
                projection_run_id=run.id,
                source_key=str(relation.source_claim_id),
                target_key=str(relation.target_claim_id),
                edge_type=relation.relation_type,
                claim_ids=[str(relation.source_claim_id), str(relation.target_claim_id)],
                relation_evidence_ids=[str(relation.id)],
            )
        )
    run.node_count = len(included)
    run.edge_count = len(relations)
    run.status = "completed"
    run.completed_at = datetime.now(UTC)
    return {"projection_run_id": str(run.id), "snapshot_hash": snapshot, "nodes": run.node_count, "edges": run.edge_count, "blockers": blockers}


__all__ = ["PROJECTION_VERSION", "rebuild_projection"]
