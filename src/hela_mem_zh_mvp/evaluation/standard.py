"""Standard fixture evaluation workflow with stable artifact contracts."""

from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import AppConfig
from ..ingestion.input import load_input_messages
from ..persistence.models import MemoryRelation
from ..persistence.namespaces import FIXTURE_NAMESPACE, get_namespace, reset_test_namespace
from ..providers.base import StructuredProvider
from ..providers.embedding import EmbeddingClient
from ..retrieval.answerer import AnswerError, answer_query
from ..retrieval.contracts import AnswerResult, RetrievalMode, RunMode
from ..retrieval.service import Retriever
from .fixtures import (
    load_fixture_bundle,
    query_reference_answer,
    query_reference_conversations,
    seed_fixtures,
    validate_query_answer_oracles,
)
from .judge import judge_answers
from .reporting import SUMMARY_PATH as DEFAULT_SUMMARY_PATH
from .reporting import build_qa_section, write_root_summary

RESULTS_DIRECTORY = Path("results/pipeline")
SUMMARY_PATH = DEFAULT_SUMMARY_PATH


def _write_artifact(name: str, payload: Any) -> None:
    RESULTS_DIRECTORY.mkdir(parents=True, exist_ok=True)
    artifact_path = RESULTS_DIRECTORY / name
    temporary_path = artifact_path.with_name(f".{artifact_path.name}.{uuid.uuid4().hex}.tmp")
    temporary_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
    temporary_path.replace(artifact_path)


def _write_summary(payload: dict[str, Any]) -> None:
    write_root_summary(
        payload,
        results_directory=RESULTS_DIRECTORY,
        summary_path=SUMMARY_PATH,
    )


def _edge_checksum(
    session: Session, namespace_id: object
) -> list[tuple[str, str, str, float, int, str]]:
    rows = session.scalars(
        select(MemoryRelation).where(MemoryRelation.namespace_id == namespace_id)
    ).all()
    return sorted(
        (
            str(row.source_id),
            str(row.target_id),
            row.relation_type,
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
    """Evaluate the stable fixture corpus in the reserved namespace only.

    Live extraction has its own ingest workflow.  An evaluation oracle must
    seed its declared M1..Mn records rather than depending on a provider to
    reproduce fixture wording and cardinality.
    """
    bundle = load_fixture_bundle("data/fixtures")
    source_messages = load_input_messages()
    validate_query_answer_oracles(bundle.queries, source_messages)
    # Input and fixture schemas are preflighted before the one permitted reset.
    with session.begin():
        get_namespace(session, FIXTURE_NAMESPACE)
    with session.begin():
        reset_test_namespace(session)
    try:
        with session.begin():
            seeded_count = seed_fixtures(
                session, bundle, embeddings, namespace_key=FIXTURE_NAMESPACE
            )
        ingestion = {
            "mode": "fixture_seed",
            "fixture_memory_count": seeded_count,
            "fixture_edge_count": len(bundle.edges),
            "extractor_model": extractor_model,
        }
        namespace = get_namespace(session, FIXTURE_NAMESPACE, create=False)
        before = _edge_checksum(session, namespace.id)
        records: list[dict[str, Any]] = []
        answers: list[dict[str, Any]] = []
        retriever = Retriever(session, config, embeddings)
        for query_index, fixture_query in enumerate(bundle.queries):
            result = retriever.retrieve(
                namespace.id,
                fixture_query.query,
                RetrievalMode.HEBBIAN,
                fixture_query.scope,
                RunMode.EVALUATION,
                activation_depth=(
                    config.retrieval.spread_depth
                    if fixture_query.suite == "architecture_v1"
                    else 1
                ),
            )
            answer_error: str | None = None
            answer_error_kind: str | None = None
            try:
                if query_index:
                    time.sleep(config.evaluation.answer_request_interval_seconds)
                answer = answer_query(provider, fixture_query.query, result)
            except AnswerError as exc:
                answer_error = f"{type(exc).__name__}: {exc}"
                answer_error_kind = "answer_contract"
                answer = exc.answer or AnswerResult(
                    schema_version="memory-answer-v1",
                    answerable=False,
                    answer="回答提供者未產生符合引用契約的結果。",
                    citations=[],
                )
            except Exception as exc:
                answer_error = f"{type(exc).__name__}: {exc}"
                answer_error_kind = "provider"
                answer = AnswerResult(
                    schema_version="memory-answer-v1",
                    answerable=False,
                    answer="回答提供者未產生符合引用契約的結果。",
                    citations=[],
                )
            selected = [item.external_id for item in result.items if item.selected]
            deterministic = {
                "must_include": set(fixture_query.must_include) <= set(selected),
                "must_not_primary": not selected
                or selected[0] not in fixture_query.must_not_primary,
                "citation_membership": set(answer.citations) <= set(selected),
                "answerable_matches_expectation": (
                    answer.answerable == fixture_query.expect_answerable
                ),
            }
            records.append(
                {
                    "query_id": fixture_query.query_id,
                    "query": fixture_query.query,
                    "expect_answerable": fixture_query.expect_answerable,
                    "reference_answer": query_reference_answer(
                        fixture_query, source_messages
                    ),
                    "reference_conversations": query_reference_conversations(
                        fixture_query, source_messages
                    ),
                    "must_include": fixture_query.must_include,
                    "must_not_primary": fixture_query.must_not_primary,
                    "category": fixture_query.category,
                    "suite": fixture_query.suite,
                    "complexity": fixture_query.complexity,
                    "architecture_targets": fixture_query.architecture_targets,
                    "required_hops": fixture_query.required_hops,
                    "retrieved_memories": result.as_dict(),
                    "answer": answer.model_dump(),
                    "answer_error": answer_error,
                    "answer_error_kind": answer_error_kind,
                    "deterministic_checks": deterministic,
                }
            )
            answers.append(
                {
                    "query_id": fixture_query.query_id,
                    **answer.model_dump(),
                    "error": answer_error,
                    "error_kind": answer_error_kind,
                }
            )
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
        contract_status = "PASS" if all(checks) and before == after else "FAIL"
        summary = {
            # The judge is quality review only.  A model verdict must not turn
            # a deterministic contract failure green (or block a green gate).
            "status": contract_status,
            "contract_status": contract_status,
            "judge_status": ai_judge["status"],
            "ingestion": ingestion,
            "coverage": {
                "query_count": len(bundle.queries),
                "by_suite": {
                    suite: sum(query.suite == suite for query in bundle.queries)
                    for suite in ("baseline", "architecture_v1")
                },
                "by_complexity": {
                    complexity: sum(query.complexity == complexity for query in bundle.queries)
                    for complexity in ("basic", "intermediate", "advanced", "adversarial")
                },
                "by_architecture_target": {
                    target: sum(target in query.architecture_targets for query in bundle.queries)
                    for target in sorted(
                        {
                            target
                            for query in bundle.queries
                            for target in query.architecture_targets
                        }
                    )
                },
                "multi_hop_queries": sum(query.required_hops >= 2 for query in bundle.queries),
            },
            "retrieval": {
                "query_count": len(records),
                "must_include_pass": sum(
                    record["deterministic_checks"]["must_include"] for record in records
                ),
                "must_not_primary_violations": sum(
                    not record["deterministic_checks"]["must_not_primary"] for record in records
                ),
                "must_include_by_complexity": {
                    complexity: {
                        "passed": sum(
                            record["deterministic_checks"]["must_include"]
                            for record in records
                            if record["complexity"] == complexity
                        ),
                        "total": sum(record["complexity"] == complexity for record in records),
                    }
                    for complexity in (
                        "basic",
                        "intermediate",
                        "advanced",
                        "adversarial",
                    )
                },
                "edge_immutability": "PASS" if before == after else "FAIL",
                "multi_hop": {
                    "required_queries": sum(record["required_hops"] >= 2 for record in records),
                    "observed_queries": sum(
                        any(
                            path.get("path_depth", 0) >= 2
                            for item in record["retrieved_memories"]["items"]
                            for path in item["activation_path"]
                        )
                        for record in records
                    ),
                    "average_explored_nodes": (
                        sum(
                            path.get("explored_nodes", 0)
                            for record in records
                            for item in record["retrieved_memories"]["items"]
                            for path in item["activation_path"]
                        )
                        / max(1, sum(
                            1
                            for record in records
                            for item in record["retrieved_memories"]["items"]
                            for path in item["activation_path"]
                        ))
                    ),
                    "path_budget_violations": sum(
                        path.get("path_budget_violations", 0)
                        for record in records
                        for item in record["retrieved_memories"]["items"]
                        for path in item["activation_path"]
                    ),
                },
            },
            "answers": {
                "citation_contract_pass": sum(
                    record["deterministic_checks"]["citation_membership"] for record in records
                ),
                "answerable_expectation_pass": sum(
                    record["deterministic_checks"]["answerable_matches_expectation"]
                    for record in records
                ),
                "provider_errors": sum(
                    record["answer_error_kind"] == "provider" for record in records
                ),
                "answer_contract_errors": sum(
                    record["answer_error_kind"] == "answer_contract" for record in records
                ),
            },
            "qa": build_qa_section(records, ai_judge),
            "artifacts": {
                "pipeline_directory": "results/pipeline",
                "summary": "results/summary.json",
                "ingestion": "results/pipeline/ingestion.json",
                "retrieval": "results/pipeline/retrieval.json",
                "answers": "results/pipeline/answers.json",
                "judge_input": "results/pipeline/judge_input.json",
            },
        }
    except Exception as exc:
        summary = {"status": "FAIL", "failure_stage": type(exc).__name__, "error": str(exc)}
    _write_summary(summary)
    return summary
