"""Application workflows for ingest, ask, and the fixed evaluation artifacts."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from .answerer import answer_query
from .config import AppConfig
from .edges import reinforce_co_retrieval
from .embedding import EmbeddingClient
from .extractor import extract_messages
from .fixtures import load_fixture_bundle
from .ingestion import INPUT_PATH, load_input_messages
from .judge import judge_answers
from .memory_store import FIXTURE_NAMESPACE, get_namespace, reset_test_namespace, write_ingestion
from .models import MemoryEdge
from .provider import StructuredProvider
from .resolver import canonical_key
from .retriever import Retriever
from .schemas import QueryScope, RetrievalMode, RunMode

RESULTS_DIRECTORY = Path("results/pipeline")


class PipelineError(ValueError):
    pass


def ingest(
    session: Session,
    namespace_key: str,
    provider: StructuredProvider,
    embeddings: EmbeddingClient,
    *,
    input_path: Path = INPUT_PATH,
    extractor_model: str,
) -> dict[str, int]:
    messages = load_input_messages(input_path)
    extraction = extract_messages(provider, messages)  # no DB transaction while calling AI
    with session.begin():
        return write_ingestion(
            session,
            namespace_key,
            messages,
            extraction,
            embeddings,
            extractor_model=extractor_model,
        )


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


def _write_artifact(name: str, payload: Any) -> None:
    RESULTS_DIRECTORY.mkdir(parents=True, exist_ok=True)
    (RESULTS_DIRECTORY / name).write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )


def _edge_checksum(
    session: Session, namespace_id: object
) -> list[tuple[str, str, str, float, int, str]]:
    rows = session.scalars(select(MemoryEdge).where(MemoryEdge.namespace_id == namespace_id)).all()
    return sorted(
        (
            str(row.source_id),
            str(row.target_id),
            row.edge_type,
            row.weight,
            row.activation_count,
            json.dumps(row.metadata_, sort_keys=True),
        )
        for row in rows
    )


def evaluate(
    session: Session,
    provider: StructuredProvider,
    embeddings: EmbeddingClient,
    config: AppConfig,
    *,
    extractor_model: str,
) -> dict[str, Any]:
    """Rebuild only the reserved test namespace and write the five fixed artifacts."""
    messages = load_input_messages()
    bundle = load_fixture_bundle("data/fixtures")
    # Input and fixture schemas are preflighted before the one permitted reset.
    with session.begin():
        get_namespace(session, FIXTURE_NAMESPACE)
    with session.begin():
        reset_test_namespace(session)
    try:
        extraction = extract_messages(provider, messages)
        with session.begin():
            oracle_external_ids = {
                canonical_key(memory.content, memory.memory_type.value): memory.external_id
                for memory in bundle.memories
            }
            ingestion = write_ingestion(
                session,
                FIXTURE_NAMESPACE,
                messages,
                extraction,
                embeddings,
                extractor_model=extractor_model,
                external_id_by_canonical_key=oracle_external_ids,
            )
        namespace = get_namespace(session, FIXTURE_NAMESPACE, create=False)
        before = _edge_checksum(session, namespace.id)
        records: list[dict[str, Any]] = []
        answers: list[dict[str, Any]] = []
        retriever = Retriever(session, config, embeddings)
        for fixture_query in bundle.queries:
            result = retriever.retrieve(
                namespace.id,
                fixture_query.query,
                RetrievalMode.HEBBIAN,
                fixture_query.scope,
                RunMode.EVALUATION,
            )
            answer = answer_query(provider, fixture_query.query, result)
            selected = [item.external_id for item in result.items if item.selected]
            deterministic = {
                "must_include": set(fixture_query.must_include) <= set(selected),
                "must_not_primary": not selected
                or selected[0] not in fixture_query.must_not_primary,
                "citation_membership": set(answer.citations) <= set(selected),
            }
            records.append(
                {
                    "query_id": fixture_query.query_id,
                    "query": fixture_query.query,
                    "expect_answerable": fixture_query.expect_answerable,
                    "must_include": fixture_query.must_include,
                    "must_not_primary": fixture_query.must_not_primary,
                    "retrieved_memories": result.as_dict(),
                    "answer": answer.model_dump(),
                    "deterministic_checks": deterministic,
                }
            )
            answers.append({"query_id": fixture_query.query_id, **answer.model_dump()})
        after = _edge_checksum(session, namespace.id)
        _write_artifact("ingestion.json", ingestion)
        _write_artifact("retrieval.json", records)
        _write_artifact("answers.json", answers)
        judge_input = {"schema_version": "answer-judge-input-v1", "cases": records}
        _write_artifact("judge_input.json", judge_input)
        try:
            ai_judge = judge_answers(provider, records)
        except Exception as exc:
            ai_judge = {"status": "ERROR", "error": f"{type(exc).__name__}: {exc}"}
        checks = [check for record in records for check in record["deterministic_checks"].values()]
        summary = {
            "status": "PASS"
            if all(checks) and ai_judge["status"] == "PASS" and before == after
            else "FAIL",
            "ingestion": ingestion,
            "retrieval": {
                "query_count": len(records),
                "must_include_pass": sum(
                    record["deterministic_checks"]["must_include"] for record in records
                ),
                "must_not_primary_violations": sum(
                    not record["deterministic_checks"]["must_not_primary"] for record in records
                ),
                "edge_immutability": "PASS" if before == after else "FAIL",
            },
            "answers": {
                "citation_contract_pass": sum(
                    record["deterministic_checks"]["citation_membership"] for record in records
                )
            },
            "ai_judge": ai_judge,
        }
    except Exception as exc:
        summary = {"status": "FAIL", "failure_stage": type(exc).__name__, "error": str(exc)}
    _write_artifact("summary.json", summary)
    return summary
