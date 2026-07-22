"""Citation-gated association learning; factual edges are never mutated."""

from uuid import UUID

from sqlalchemy.orm import Session

from ..config import LearningConfig
from ..persistence.associations import reinforce_associations


def reinforce_co_retrieval(
    session: Session,
    namespace_id: UUID,
    memory_ids: list[UUID],
    learning: LearningConfig,
    *,
    learning_token: str,
    citations: list[str],
) -> int:
    """Append idempotent events after the answer/citation contract has passed."""
    return reinforce_associations(
        session,
        namespace_id,
        memory_ids,
        learning_token=learning_token,
        increment=learning.co_retrieval_increment,
        citations=citations,
    )


__all__ = ["reinforce_co_retrieval"]
