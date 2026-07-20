"""Ask use case and the explicit co-retrieval learning gate."""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from ..config import AppConfig
from ..persistence.namespaces import get_namespace
from ..providers.base import StructuredProvider
from ..providers.embedding import EmbeddingClient
from .answerer import answer_query
from .contracts import QueryScope, RetrievalMode, RunMode
from .learning import reinforce_co_retrieval
from .service import Retriever


def ask(
    session: Session,
    namespace_key: str,
    query: str,
    provider: StructuredProvider,
    embeddings: EmbeddingClient,
    config: AppConfig,
    *,
    learn: bool = False,
) -> dict[str, Any]:
    namespace = get_namespace(session, namespace_key, create=False)
    result = Retriever(session, config, embeddings).retrieve(
        namespace.id,
        query,
        RetrievalMode.HEBBIAN,
        QueryScope.GENERAL,
        RunMode.LEARNING if learn else RunMode.EVALUATION,
    )
    answer = answer_query(provider, query, result)
    if learn and answer.answerable:
        selected = {item.external_id: item.memory.id for item in result.items if item.selected}
        cited_ids = [selected[citation] for citation in answer.citations]
        reinforce_co_retrieval(
            session,
            namespace.id,
            cited_ids,
            config.learning,
            metadata={
                "origin": "co_retrieval",
                "retrieval_run_id": str(result.run_id),
                "answer_citations": answer.citations,
                "learning_gate_reason": "explicit ask --learn and citation contract passed",
            },
        )
        session.commit()
    return {"retrieval": result.as_dict(), "answer": answer.model_dump()}
