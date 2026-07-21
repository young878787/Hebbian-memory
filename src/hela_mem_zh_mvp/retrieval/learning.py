"""Citation-gated association learning; factual edges are never mutated."""

from uuid import UUID

from sqlalchemy.orm import Session

from ..config import LearningConfig
from ..persistence.associations import record_cited_together


def reinforce_co_retrieval(
    session: Session,
    namespace_id: UUID,
    memory_ids: list[UUID],
    learning: LearningConfig,
    *,
    retrieval_run_id: UUID,
    citations: list[str],
) -> int:
    """Append idempotent events after the answer/citation contract has passed."""
    return record_cited_together(
        session,
        namespace_id,
        memory_ids,
        retrieval_run_id,
        learning.co_retrieval_increment,
        citations,
    )


__all__ = ["reinforce_co_retrieval"]
