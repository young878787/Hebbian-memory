"""Evaluation runner that keeps oracle data outside the retriever/provider boundary."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from ..config import AppConfig
from ..persistence.namespaces import get_namespace
from ..providers.embedding import EmbeddingClient
from ..retrieval.contracts import RetrievalMode, RunMode
from ..retrieval.service import Retriever
from .fixtures import FixtureBundle


@dataclass(frozen=True)
class EvaluationReport:
    embedding_only: list[dict[str, Any]]
    hebbian: list[dict[str, Any]]
    summary: dict[str, Any]


def _evaluate_mode(
    session: Session,
    config: AppConfig,
    client: EmbeddingClient,
    bundle: FixtureBundle,
    mode: RetrievalMode,
    run_mode: RunMode,
) -> list[dict[str, Any]]:
    retriever = Retriever(session, config, client)
    namespace = get_namespace(session, "legacy-mvp-v0.4", create=False)
    records: list[dict[str, Any]] = []
    for fixture_query in bundle.queries:
        result = retriever.retrieve(
            namespace.id, fixture_query.query, mode, fixture_query.scope, run_mode
        )
        selected = [item.external_id for item in result.items if item.selected]
        records.append(
            {
                "query_id": fixture_query.query_id,
                "query": fixture_query.query,
                "scope": fixture_query.scope.value,
                "selected_external_ids": selected,
                "must_include": fixture_query.must_include,
                "nice_to_have": fixture_query.nice_to_have,
                "must_not_primary": fixture_query.must_not_primary,
                "expect_answerable": fixture_query.expect_answerable,
                "category": fixture_query.category,
                "suite": fixture_query.suite,
                "complexity": fixture_query.complexity,
                "architecture_targets": fixture_query.architecture_targets,
                "required_hops": fixture_query.required_hops,
                "must_include_hit": set(fixture_query.must_include) <= set(selected),
                "result": result.as_dict(),
                "manual_scores": {
                    "association_completeness": None,
                    "contradiction_correctness": None,
                    "no_answer_safety": None,
                },
            }
        )
    return records


def evaluate(
    session: Session,
    config: AppConfig,
    client: EmbeddingClient,
    bundle: FixtureBundle,
    run_mode: RunMode = RunMode.EVALUATION,
) -> EvaluationReport:
    embedding_only = _evaluate_mode(
        session, config, client, bundle, RetrievalMode.EMBEDDING_ONLY, run_mode
    )
    hebbian = _evaluate_mode(session, config, client, bundle, RetrievalMode.HEBBIAN, run_mode)
    summary = {
        "contract_gate": {
            "status": "pending_live_verification",
            "checks": [
                "fixture/config validation",
                "database target and pgvector",
                "embedding dimension",
                "20 retrieval runs",
                "retrieval item trace",
                "evaluation edge immutability",
                "repeatability",
            ],
        },
        "quality_gate": {"status": "pending_manual_scoring"},
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
                    {target for query in bundle.queries for target in query.architecture_targets}
                )
            },
            "multi_hop_queries": sum(query.required_hops >= 2 for query in bundle.queries),
        },
        "embedding_only_must_include_hits": sum(
            record["must_include_hit"] for record in embedding_only
        ),
        "hebbian_must_include_hits": sum(record["must_include_hit"] for record in hebbian),
        "must_include_hits_by_complexity": {
            complexity: {
                "embedding_only": sum(
                    record["must_include_hit"]
                    for record in embedding_only
                    if record["complexity"] == complexity
                ),
                "hebbian": sum(
                    record["must_include_hit"]
                    for record in hebbian
                    if record["complexity"] == complexity
                ),
            }
            for complexity in ("basic", "intermediate", "advanced", "adversarial")
        },
    }
    return EvaluationReport(embedding_only, hebbian, summary)


def write_report(report: EvaluationReport, output: str | Path) -> None:
    directory = Path(output)
    directory.mkdir(parents=True, exist_ok=True)
    for filename, payload in (
        ("embedding_only.json", report.embedding_only),
        ("hebbian.json", report.hebbian),
        ("summary.json", report.summary),
    ):
        (directory / filename).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
