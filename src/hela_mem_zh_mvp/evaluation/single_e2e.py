"""Live end-to-end evaluation joining ingestion and a bounded answer batch."""

from __future__ import annotations

import json
import time
import uuid
from collections import Counter
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import AppConfig
from ..ingestion.input import load_input_messages
from ..ingestion.workflow import ingest
from ..persistence.models import (
    Memory,
    MemoryCandidate,
    MemoryEdge,
    MemoryNamespace,
    MemoryResolutionDecision,
)
from ..persistence.namespaces import get_namespace, purge_namespace
from ..providers.base import StructuredProvider
from ..providers.embedding import EmbeddingClient
from ..retrieval.answerer import answer_query
from ..retrieval.contracts import QueryScope, RetrievalMode, RunMode
from ..retrieval.service import Retriever
from .contracts import FixtureQuery
from .fixtures import load_fixture_bundle
from .judge import judge_answers

RESULTS_DIRECTORY = Path("results/pipeline")
SUMMARY_PATH = Path("results/summary.json")
LEGACY_NAMESPACE = "single-e2e-v1"
DEFAULT_INPUT_PATH = Path("data/input/conversations.jsonl")
DEFAULT_QUERY = "使用者最後對 RTX 3090 的決定是什麼？"
DEFAULT_QUERY_ID = "single-e2e-001"
DEFAULT_QUERY_LIMIT = 60


def _write_artifact(name: str, payload: Any) -> None:
    RESULTS_DIRECTORY.mkdir(parents=True, exist_ok=True)
    (RESULTS_DIRECTORY / name).write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )


def _write_summary(payload: dict[str, Any]) -> None:
    _write_artifact("summary.json", payload)
    SUMMARY_PATH.parent.mkdir(parents=True, exist_ok=True)
    SUMMARY_PATH.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )


def _namespace_counts(session: Session, namespace_key: str) -> dict[str, int]:
    namespace = session.scalar(
        select(MemoryNamespace).where(MemoryNamespace.namespace_key == namespace_key)
    )
    if namespace is None:
        return {"memories": 0, "edges": 0}
    return {
        "memories": session.scalar(
            select(func.count()).select_from(Memory).where(Memory.namespace_id == namespace.id)
        )
        or 0,
        "edges": session.scalar(
            select(func.count())
            .select_from(MemoryEdge)
            .where(MemoryEdge.namespace_id == namespace.id)
        )
        or 0,
    }


def _resolver_state(session: Session, namespace_key: str) -> dict[str, Any]:
    namespace = get_namespace(session, namespace_key, create=False)
    decisions = session.scalars(
        select(MemoryResolutionDecision).where(
            MemoryResolutionDecision.namespace_id == namespace.id
        )
    ).all()
    candidates = session.scalars(
        select(MemoryCandidate).where(MemoryCandidate.namespace_id == namespace.id)
    ).all()
    return {
        "decision_actions": dict(Counter(decision.action for decision in decisions)),
        "candidate_statuses": dict(Counter(candidate.status for candidate in candidates)),
    }


def _purge_namespace(session: Session, namespace_key: str) -> dict[str, Any]:
    """Remove one temporary namespace and confirm its namespace row is gone."""
    if session.in_transaction():
        session.rollback()
    with session.begin():
        before = _namespace_counts(session, namespace_key)
        removed = purge_namespace(session, namespace_key)
        namespace_exists_after = (
            session.scalar(
                select(MemoryNamespace.id).where(MemoryNamespace.namespace_key == namespace_key)
            )
            is not None
        )
    return {
        "before": before,
        "removed": removed,
        "namespace_exists_after": namespace_exists_after,
    }


def _evaluation_queries(query: str | None, query_id: str, query_limit: int) -> list[FixtureQuery]:
    if query:
        return [
            FixtureQuery(
                query_id=query_id,
                query=query,
                scope=QueryScope.GENERAL,
                expect_answerable=True,
                category="custom",
            )
        ]
    queries = load_fixture_bundle("data/fixtures").queries
    if query_limit < 1 or query_limit > len(queries):
        raise ValueError(f"query_limit must be between 1 and {len(queries)}")
    return queries[:query_limit]


def _judge_record(record: dict[str, Any]) -> dict[str, Any]:
    retrieval = record["retrieved_memories"]
    return {
        "query_id": record["query_id"],
        "query": record["query"],
        "expect_answerable": record["expect_answerable"],
        "selected_memories": [
            {
                "external_id": item["external_id"],
                "content": item["content"],
                "status": item["status"],
            }
            for item in retrieval["items"]
            if item["selected"]
        ],
        "answer": record["answer"],
        "deterministic_checks": record["deterministic_checks"],
    }


def _final_qa(answers: list[dict[str, Any]], ai_judge: dict[str, Any]) -> list[dict[str, Any]]:
    verdicts = {case["query_id"]: case for case in ai_judge.get("cases", [])}
    return [
        {
            **item,
            "judge_verdict": verdicts.get(item["query_id"], {}).get("verdict"),
            "judge_reason": verdicts.get(item["query_id"], {}).get("reason"),
        }
        for item in answers
    ]


def run_single_e2e_evaluation(
    session: Session,
    provider: StructuredProvider,
    embeddings: EmbeddingClient,
    config: AppConfig,
    *,
    extractor_model: str,
    input_path: Path = DEFAULT_INPUT_PATH,
    query: str | None = None,
    query_id: str = DEFAULT_QUERY_ID,
    query_limit: int = DEFAULT_QUERY_LIMIT,
) -> dict[str, Any]:
    """Run one temporary end-to-end batch and remove all temporary database data."""
    namespace_key = f"e2e-run-{uuid.uuid4().hex}"
    payload: dict[str, Any] = {
        "status": "FAIL",
        "test": {
            "temporary_namespace": namespace_key,
            "input_path": str(input_path),
            "run_mode": "evaluation",
        },
    }
    records: list[dict[str, Any]] = []
    answers: list[dict[str, Any]] = []
    try:
        # Validate all local inputs before deleting the obsolete legacy namespace.
        message_count = len(load_input_messages(input_path))
        queries = _evaluation_queries(query, query_id, query_limit)
        payload["test"].update(
            {
                "input_message_count": message_count,
                "query_source": "custom" if query else "data/fixtures/test_queries.jsonl",
                "query_count": len(queries),
            }
        )
        payload["legacy_namespace_cleanup"] = _purge_namespace(session, LEGACY_NAMESPACE)
        ingestion = ingest(
            session,
            namespace_key,
            provider,
            embeddings,
            input_path=input_path,
            extractor_model=extractor_model,
        )
        payload["ingestion"] = ingestion
        resolver = _resolver_state(session, namespace_key)
        payload["resolver"] = resolver
        namespace = get_namespace(session, namespace_key, create=False)
        retriever = Retriever(session, config, embeddings)
        for query_index, fixture_query in enumerate(queries):
            result = retriever.retrieve(
                namespace.id,
                fixture_query.query,
                RetrievalMode.HEBBIAN,
                fixture_query.scope,
                RunMode.EVALUATION,
            )
            answer_error: str | None = None
            try:
                if query_index:
                    time.sleep(config.evaluation.answer_request_interval_seconds)
                answer = answer_query(provider, fixture_query.query, result).model_dump()
            except Exception as exc:
                answer_error = f"{type(exc).__name__}: {exc}"
                answer = None
            retrieval = result.as_dict()
            selected = {item["external_id"] for item in retrieval["items"] if item["selected"]}
            checks = {
                "retrieval_selected_memory": bool(selected),
                "answer_returned": answer is not None,
                "answer_citations_selected": answer is not None
                and set(answer["citations"]) <= selected,
            }
            record = {
                "query_id": fixture_query.query_id,
                "query": fixture_query.query,
                "category": fixture_query.category,
                "suite": fixture_query.suite,
                "complexity": fixture_query.complexity,
                "architecture_targets": fixture_query.architecture_targets,
                "required_hops": fixture_query.required_hops,
                "scope": fixture_query.scope.value,
                "expect_answerable": fixture_query.expect_answerable,
                "retrieved_memories": retrieval,
                "answer": answer,
                "answer_error": answer_error,
                "deterministic_checks": checks,
            }
            records.append(record)
            answers.append(
                {
                    "query_id": fixture_query.query_id,
                    "query": fixture_query.query,
                    "answer": answer,
                    "error": answer_error,
                }
            )
        judge_input = [_judge_record(record) for record in records if record["answer"] is not None]
        try:
            ai_judge = judge_answers(provider, judge_input)
        except Exception as exc:
            ai_judge = {"status": "ERROR", "error": f"{type(exc).__name__}: {exc}"}
        checks = {
            "resolver_decision_persisted": bool(resolver["decision_actions"]),
            "all_queries_completed": len(records) == len(queries),
            "all_answers_returned": all(record["answer"] is not None for record in records),
            "all_citations_selected": all(
                record["deterministic_checks"]["answer_citations_selected"] for record in records
            ),
            "ai_judge_contract": ai_judge["status"] == "PASS",
        }
        verdict_counts = {
            key: ai_judge.get(f"{key.lower()}_count", 0) for key in ("PASS", "FAIL", "UNSURE")
        }
        payload.update(
            {
                "status": "PASS" if all(checks.values()) else "FAIL",
                "quality_status": (
                    "PASS"
                    if verdict_counts["PASS"] == len(queries)
                    else "UNSURE"
                    if verdict_counts["UNSURE"] and not verdict_counts["FAIL"]
                    else "FAIL"
                ),
                "checks": checks,
                "questions": {
                    "requested": len(queries),
                    "completed": len(records),
                    "answers_returned": sum(record["answer"] is not None for record in records),
                    "provider_errors": sum(
                        record["answer_error"] is not None for record in records
                    ),
                },
                "coverage": {
                    "by_suite": {
                        suite: sum(record["suite"] == suite for record in records)
                        for suite in ("baseline", "architecture_v1")
                    },
                    "by_complexity": {
                        complexity: sum(record["complexity"] == complexity for record in records)
                        for complexity in (
                            "basic",
                            "intermediate",
                            "advanced",
                            "adversarial",
                        )
                    },
                    "multi_hop_queries": sum(record["required_hops"] >= 2 for record in records),
                },
                "ai_judge": {**ai_judge, "verdict_counts": verdict_counts},
                "final_qa": _final_qa(answers, ai_judge),
                "artifacts": {
                    "pipeline_directory": "results/pipeline",
                    "summary": "results/summary.json",
                    "retrieval": "results/pipeline/retrieval.json",
                    "answers": "results/pipeline/answers.json",
                    "judge_input": "results/pipeline/judge_input.json",
                },
            }
        )
        _write_artifact("retrieval.json", records)
        _write_artifact("answers.json", answers)
        _write_artifact("judge_input.json", {"cases": judge_input})
    except Exception as exc:
        payload.update(
            {
                "failure_stage": _failure_stage(payload),
                "error": f"{type(exc).__name__}: {exc}",
            }
        )
    finally:
        try:
            cleanup = _purge_namespace(session, namespace_key)
            payload["temporary_namespace_cleanup"] = cleanup
            if cleanup["namespace_exists_after"]:
                payload["status"] = "FAIL"
                payload["error"] = "temporary namespace was not removed"
        except Exception as exc:
            payload["status"] = "FAIL"
            payload["temporary_namespace_cleanup"] = {
                "removed": False,
                "error": f"{type(exc).__name__}: {exc}",
            }
    _write_summary(payload)
    return payload


def _failure_stage(payload: dict[str, Any]) -> str:
    if "legacy_namespace_cleanup" not in payload:
        return "preflight"
    if "ingestion" not in payload:
        return "ingestion"
    if "resolver" not in payload:
        return "resolver"
    return "retrieval_answer_or_judge"
