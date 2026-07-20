"""Persistence primitives for retrieval run traces."""

from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from ..retrieval.contracts import QueryScope, RankedMemory, RetrievalMode, RunMode
from .models import RetrievalItem, RetrievalRun


def create_retrieval_run(
    session: Session,
    namespace_id: uuid.UUID,
    query: str,
    mode: RetrievalMode,
    scope: QueryScope,
    run_mode: RunMode,
    latency_ms: float,
    config_snapshot: dict[str, object],
) -> RetrievalRun:
    """Store a retrieval trace without committing the caller transaction."""
    run = RetrievalRun(
        namespace_id=namespace_id,
        query=query,
        retrieval_mode=mode.value,
        run_mode=run_mode.value,
        query_scope=scope.value,
        total_latency_ms=latency_ms,
        metadata_={"config": config_snapshot},
    )
    session.add(run)
    session.flush()
    return run


def create_retrieval_items(
    session: Session,
    namespace_id: uuid.UUID,
    run_id: uuid.UUID,
    items: list[RankedMemory],
) -> None:
    """Stage one persisted trace item per ranked memory without committing."""
    for item in items:
        session.add(
            RetrievalItem(
                namespace_id=namespace_id,
                run_id=run_id,
                memory_id=item.memory.id,
                candidate_rank=item.candidate_rank,
                final_rank=item.final_rank,
                selected=item.selected,
                semantic_score=item.semantic_score,
                hebbian_score=item.hebbian_score,
                status_adjustment=item.status_adjustment,
                final_score=item.final_score,
                retrieval_source=item.source,
                activation_path=item.activation_path,
            )
        )


__all__ = ["create_retrieval_items", "create_retrieval_run"]
