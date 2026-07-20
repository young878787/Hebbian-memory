"""One-case live path test joining ingestion and answer evaluation."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import AppConfig
from .embedding import EmbeddingClient
from .judge import judge_answers
from .memory_store import get_namespace, reset_namespace
from .models import MemoryCandidate, MemoryResolutionDecision
from .pipeline import ask, ingest
from .provider import StructuredProvider

RESULTS_DIRECTORY = Path("results/single_e2e")
DEFAULT_NAMESPACE = "single-e2e-v1"
DEFAULT_INPUT_PATH = Path("data/input/conversations.jsonl")
DEFAULT_QUERY = "使用者最後對 RTX 3090 的決定是什麼？"
DEFAULT_QUERY_ID = "single-e2e-001"


def _write_artifact(payload: dict[str, Any]) -> None:
    RESULTS_DIRECTORY.mkdir(parents=True, exist_ok=True)
    (RESULTS_DIRECTORY / "summary.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )


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


def _reset_single_e2e_namespace(session: Session) -> None:
    """Reset only the reserved one-case test namespace before each live run."""
    with session.begin():
        try:
            reset_namespace(session, DEFAULT_NAMESPACE)
        except ValueError:
            # The first run has no namespace to clear.
            pass


def run_single_e2e_evaluation(
    session: Session,
    provider: StructuredProvider,
    embeddings: EmbeddingClient,
    config: AppConfig,
    *,
    extractor_model: str,
    input_path: Path = DEFAULT_INPUT_PATH,
    query: str = DEFAULT_QUERY,
    query_id: str = DEFAULT_QUERY_ID,
) -> dict[str, Any]:
    """Reset and run one message batch plus one query in the test namespace.

    This is intentionally a plumbing test, not the fixed-corpus quality
    evaluation. It exercises the live provider exactly for extraction, answer,
    and judge calls while retaining a complete, inspectable stage record.
    """
    payload: dict[str, Any] = {
        "status": "FAIL",
        "test": {
            "namespace": DEFAULT_NAMESPACE,
            "input_path": str(input_path),
            "query_id": query_id,
            "query": query,
            "run_mode": "evaluation",
        },
    }
    try:
        _reset_single_e2e_namespace(session)
        ingestion = ingest(
            session,
            DEFAULT_NAMESPACE,
            provider,
            embeddings,
            input_path=input_path,
            extractor_model=extractor_model,
        )
        payload["ingestion"] = ingestion
        resolver = _resolver_state(session, DEFAULT_NAMESPACE)
        payload["resolver"] = resolver
        answer_result = ask(
            session,
            DEFAULT_NAMESPACE,
            query,
            provider,
            embeddings,
            config,
            learn=False,
        )
        retrieval = answer_result["retrieval"]
        answer = answer_result["answer"]
        payload["retrieval"] = retrieval
        payload["answer"] = answer
        selected = {item["external_id"] for item in retrieval["items"] if item["selected"]}
        checks = {
            "resolver_decision_persisted": bool(resolver["decision_actions"]),
            "retrieval_selected_memory": bool(selected),
            "answer_citations_selected": set(answer["citations"]) <= selected,
        }
        record = {
            "query_id": query_id,
            "query": query,
            "retrieved_memories": retrieval,
            "answer": answer,
            "deterministic_checks": checks,
        }
        try:
            ai_judge = judge_answers(provider, [record])
        except Exception as exc:
            ai_judge = {"status": "ERROR", "error": f"{type(exc).__name__}: {exc}"}
        payload["ai_judge"] = ai_judge
        checks["ai_judge_contract"] = ai_judge["status"] == "PASS"
        payload.update(
            {
                "status": "PASS" if all(checks.values()) else "FAIL",
                "checks": checks,
            }
        )
    except Exception as exc:
        payload.update(
            {
                "failure_stage": _failure_stage(payload),
                "error": f"{type(exc).__name__}: {exc}",
            }
        )
    _write_artifact(payload)
    return payload


def _failure_stage(payload: dict[str, Any]) -> str:
    if "ingestion" not in payload:
        return "ingestion"
    if "retrieval" not in payload:
        return "retrieval_or_answer"
    return "ai_judge"
