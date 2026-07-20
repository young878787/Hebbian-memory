"""Repeatable live extraction and resolver gate for the dedicated test namespaces."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..ingestion.workflow import ingest
from ..persistence.models import Memory, MemoryCandidate, MemoryEdge, MemoryResolutionDecision
from ..persistence.namespaces import get_namespace, reset_namespace
from ..providers.base import StructuredProvider
from ..providers.embedding import EmbeddingClient

RESULTS_DIRECTORY = Path("results/live_resolver")
SUPERSEDE_NAMESPACE = "live-supersede-v1"
CONTRADICT_NAMESPACE = "live-contradict-v1"
DEFER_NAMESPACE = "live-defer-v1"


def _write_artifact(payload: dict[str, Any]) -> None:
    RESULTS_DIRECTORY.mkdir(parents=True, exist_ok=True)
    (RESULTS_DIRECTORY / "summary.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )


def _reset(session: Session, namespace_key: str) -> None:
    with session.begin():
        try:
            reset_namespace(session, namespace_key)
        except ValueError:
            # The first run has no namespace to clear.
            pass


def _namespace_state(session: Session, namespace_key: str) -> dict[str, Any]:
    namespace = get_namespace(session, namespace_key, create=False)
    memories = session.scalars(select(Memory).where(Memory.namespace_id == namespace.id)).all()
    decisions = session.scalars(
        select(MemoryResolutionDecision).where(
            MemoryResolutionDecision.namespace_id == namespace.id
        )
    ).all()
    candidates = session.scalars(
        select(MemoryCandidate).where(MemoryCandidate.namespace_id == namespace.id)
    ).all()
    contradict_edges = session.scalars(
        select(MemoryEdge).where(
            MemoryEdge.namespace_id == namespace.id,
            MemoryEdge.edge_type == "contradicts",
        )
    ).all()
    state = {
        "memory_statuses": Counter(memory.status for memory in memories),
        "decision_actions": Counter(decision.action for decision in decisions),
        "candidate_statuses": Counter(candidate.status for candidate in candidates),
        "contradict_edge_count": len(contradict_edges),
    }
    session.rollback()
    return state


def run_live_resolver_evaluation(
    session: Session,
    provider: StructuredProvider,
    embeddings: EmbeddingClient,
    *,
    extractor_model: str,
) -> dict[str, Any]:
    """Run live AI extraction through CREATE/SUPERSEDE/CONTRADICT/DEFER gates."""
    _reset(session, SUPERSEDE_NAMESPACE)
    supersede_ingestion = ingest(
        session,
        SUPERSEDE_NAMESPACE,
        provider,
        embeddings,
        input_path=Path("data/input/conversations.jsonl"),
        extractor_model=extractor_model,
    )
    supersede = _namespace_state(session, SUPERSEDE_NAMESPACE)

    _reset(session, CONTRADICT_NAMESPACE)
    contradict_ingestion = ingest(
        session,
        CONTRADICT_NAMESPACE,
        provider,
        embeddings,
        input_path=Path("data/fixtures/live_contradict_input.jsonl"),
        extractor_model=extractor_model,
    )
    contradict = _namespace_state(session, CONTRADICT_NAMESPACE)

    _reset(session, DEFER_NAMESPACE)
    defer_seed_ingestion = ingest(
        session,
        DEFER_NAMESPACE,
        provider,
        embeddings,
        input_path=Path("data/fixtures/live_defer_seed.jsonl"),
        extractor_model=extractor_model,
    )
    defer_candidate_ingestion = ingest(
        session,
        DEFER_NAMESPACE,
        provider,
        embeddings,
        input_path=Path("data/fixtures/live_defer_candidate.jsonl"),
        extractor_model=extractor_model,
    )
    defer = _namespace_state(session, DEFER_NAMESPACE)

    checks = {
        "create": supersede_ingestion["created"] >= 1,
        "supersede": (
            supersede_ingestion["superseded"] >= 1
            and supersede["decision_actions"]["SUPERSEDE"] >= 1
            and supersede["memory_statuses"]["superseded"] >= 1
        ),
        "contradict": (
            contradict_ingestion["contradicted"] >= 1
            and contradict["decision_actions"]["CONTRADICT"] >= 1
            and contradict["contradict_edge_count"] == 2
            and contradict["memory_statuses"]["uncertain"] == 2
        ),
        "defer": (
            defer_candidate_ingestion["deferred"] >= 1
            and defer["decision_actions"]["DEFER"] >= 1
            and defer["candidate_statuses"]["deferred"] >= 1
        ),
    }
    payload = {
        "status": "PASS" if all(checks.values()) else "FAIL",
        "checks": checks,
        "ingestion": {
            "supersede": supersede_ingestion,
            "contradict": contradict_ingestion,
            "defer_seed": defer_seed_ingestion,
            "defer_candidate": defer_candidate_ingestion,
        },
        "states": {
            "supersede": supersede,
            "contradict": contradict,
            "defer": defer,
        },
    }
    _write_artifact(payload)
    return payload
