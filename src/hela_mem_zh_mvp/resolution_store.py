"""Atomic application of already validated resolution decisions.

This module deliberately accepts database objects and a validated decision;
the provider is never given a session or called while this transaction is open.
"""

from __future__ import annotations

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from .edges import upsert_edge
from .models import Memory
from .schemas import EdgeType, EffectiveOrder, ResolutionAction, ResolutionDecision


class ResolutionApplyError(ValueError):
    pass


def _lock_state_slot(session: Session, namespace_id: object, state_key: str) -> None:
    session.execute(
        text("SELECT pg_advisory_xact_lock(hashtext(:lock_key))"),
        {"lock_key": f"{namespace_id}:{state_key}"},
    )


def _would_cycle(session: Session, namespace_id: object, source_id: object, target_id: object) -> bool:
    """Adding source -> target cycles iff target already reaches source."""
    return bool(
        session.execute(
            text(
                """
                WITH RECURSIVE path(id) AS (
                  SELECT target_id FROM memory_edges
                  WHERE namespace_id = :namespace_id AND source_id = :target_id
                    AND edge_type = 'supersedes'
                  UNION
                  SELECT edge.target_id FROM memory_edges edge
                  JOIN path ON edge.source_id = path.id
                  WHERE edge.namespace_id = :namespace_id AND edge.edge_type = 'supersedes'
                ) SELECT EXISTS (SELECT 1 FROM path WHERE id = :source_id)
                """
            ),
            {"namespace_id": namespace_id, "source_id": source_id, "target_id": target_id},
        ).scalar_one()
    )


def apply_resolution(
    session: Session,
    namespace_id: object,
    incoming: Memory,
    decision: ResolutionDecision,
    targets: list[Memory],
) -> dict[str, object]:
    """Apply state/edge changes atomically inside a caller-owned transaction."""
    if decision.action not in {ResolutionAction.SUPERSEDE, ResolutionAction.CONTRADICT}:
        raise ResolutionApplyError("only state-changing decisions are accepted")
    if incoming.namespace_id != namespace_id or any(item.namespace_id != namespace_id for item in targets):
        raise ResolutionApplyError("cross-namespace resolution target")
    if incoming.id in {item.id for item in targets}:
        raise ResolutionApplyError("resolution target cannot equal incoming memory")
    if not incoming.state_key or any(item.state_key != incoming.state_key for item in targets):
        raise ResolutionApplyError("resolution requires one explicit, matching state slot")
    _lock_state_slot(session, namespace_id, incoming.state_key)

    if decision.action is ResolutionAction.SUPERSEDE:
        if decision.effective_order is EffectiveOrder.UNKNOWN:
            raise ResolutionApplyError("supersede order is unknown")
        if decision.effective_order is EffectiveOrder.CANDIDATE_AFTER_TARGET:
            for target in targets:
                if _would_cycle(session, namespace_id, incoming.id, target.id):
                    raise ResolutionApplyError("supersedes cycle")
                upsert_edge(
                    session, namespace_id, incoming.id, target.id, EdgeType.SUPERSEDES, 1.0,
                    {"origin": "resolution", "reason": decision.reason},
                )
                target.status = "superseded"
            incoming.status = "active"
        elif decision.effective_order is EffectiveOrder.CANDIDATE_BEFORE_TARGET:
            for target in targets:
                if _would_cycle(session, namespace_id, target.id, incoming.id):
                    raise ResolutionApplyError("supersedes cycle")
                upsert_edge(
                    session, namespace_id, target.id, incoming.id, EdgeType.SUPERSEDES, 1.0,
                    {"origin": "resolution", "reason": "late arriving evidence"},
                )
            incoming.status = "superseded"
        else:
            raise ResolutionApplyError("same-time memories cannot supersede")
    else:
        for target in targets:
            upsert_edge(
                session, namespace_id, incoming.id, target.id, EdgeType.CONTRADICTS, 1.0,
                {"origin": "resolution", "reason": decision.reason},
            )
        # A known, separate active state remains current; otherwise neither
        # side is presented as current truth.
        resolved_active = session.scalar(
            select(Memory.id).where(
                Memory.namespace_id == namespace_id,
                Memory.state_key == incoming.state_key,
                Memory.status == "active",
                Memory.id.not_in([incoming.id, *(item.id for item in targets)]),
            )
        )
        if resolved_active is None:
            incoming.status = "uncertain"
            for target in targets:
                target.status = "uncertain"
    return {
        "incoming_status": incoming.status,
        "target_statuses": {str(item.id): item.status for item in targets},
        "action": decision.action.value,
    }
