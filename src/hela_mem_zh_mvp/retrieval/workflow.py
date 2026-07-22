"""Ask use case and the explicit co-retrieval learning gate."""

from __future__ import annotations

import json
import uuid
from pathlib import Path
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

RESULTS_DIRECTORY = Path("results/ask")


def _write_retrieval_artifact(payload: dict[str, Any]) -> None:
    RESULTS_DIRECTORY.mkdir(parents=True, exist_ok=True)
    path = RESULTS_DIRECTORY / "retrieval.json"
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
    temporary.replace(path)


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
    result = Retriever(session, config, embeddings, provider).retrieve(
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
            learning_token=str(result.run_id),
            citations=answer.citations,
        )
        session.commit()
    payload = {"retrieval": result.as_dict(), "answer": answer.model_dump()}
    _write_retrieval_artifact(payload)
    return payload
