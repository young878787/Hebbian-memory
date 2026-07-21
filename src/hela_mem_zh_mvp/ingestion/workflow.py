"""Ingestion use case; provider calls stay outside the caller-owned transaction."""

from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from ..config import load_config
from ..evaluation.fixtures import load_key_extraction_expectations
from ..evaluation.semantic_gate import evaluate_extraction_semantics
from ..providers.base import StructuredProvider
from ..providers.embedding import EmbeddingClient
from .contracts import ExtractionOutcomeStatus, MessageExtractionOutcome
from .extractor import extract_message
from .input import INPUT_PATH, load_input_messages
from .resolution_workflow import resolve_staged_candidates
from .service import (
    finish_ingestion_run,
    record_extraction_outcomes,
    start_ingestion_run,
    write_ingestion,
)

RESULTS_DIRECTORY = Path("results/pipeline")
SEMANTIC_CANARY_PATH = Path("data/fixtures/live_extraction_expectations.jsonl")


def _write_artifact(name: str, payload: object) -> None:
    RESULTS_DIRECTORY.mkdir(parents=True, exist_ok=True)
    artifact_path = RESULTS_DIRECTORY / name
    temporary_path = artifact_path.with_name(f".{artifact_path.name}.{uuid.uuid4().hex}.tmp")
    temporary_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
    temporary_path.replace(artifact_path)


def ingest(
    session: Session,
    namespace_key: str,
    provider: StructuredProvider,
    embeddings: EmbeddingClient,
    *,
    input_path: Path = INPUT_PATH,
    extractor_model: str,
) -> dict[str, Any]:
    messages = load_input_messages(input_path)
    with session.begin():
        run = start_ingestion_run(
            session, namespace_key, messages, extractor_model=extractor_model
        )
    config = load_config()
    outcomes: list[MessageExtractionOutcome] = []
    for index, message in enumerate(messages):
        if index:
            time.sleep(config.evaluation.structured_request_interval_seconds)
        try:
            outcomes.append(extract_message(provider, message))
        except Exception as exc:
            outcomes.append(
                MessageExtractionOutcome(
                    schema_version="message-extraction-outcome-v2",
                    source_message_id=message.message_id,
                    status=ExtractionOutcomeStatus.FAILED,
                    provider_attempts=1,
                    error=f"{type(exc).__name__}: {exc}"[:500],
                )
            )
    with session.begin():
        coverage = record_extraction_outcomes(session, run, outcomes)
    canary_expectations = load_key_extraction_expectations(SEMANTIC_CANARY_PATH)
    applicable_expectations = [
        item for item in canary_expectations if item.source_message_id in {message.message_id for message in messages}
    ]
    semantic_gate = (
        evaluate_extraction_semantics(outcomes, messages, applicable_expectations)
        if applicable_expectations
        else {"status": "SKIPPED", "checked_message_count": 0, "failures": []}
    )
    _write_artifact("extraction.json", [outcome.model_dump(mode="json") for outcome in outcomes])
    _write_artifact("extraction_coverage.json", coverage)
    _write_artifact("extraction_semantic_gate.json", semantic_gate)
    counts = {
        "created": 0,
        "merged": 0,
        "superseded": 0,
        "contradicted": 0,
        "ignored": 0,
        "deferred": 0,
    }
    if semantic_gate["status"] == "FAIL":
        # Source-to-memory failures are fail-closed: outcome audit remains
        # durable, but no invalid candidate reaches staging or canonical rows.
        with session.begin():
            finish_ingestion_run(session, run, counts, status="failed_semantic")
        _write_artifact("resolution.json", [])
        return {
            "status": "FAILED_SEMANTIC",
            "coverage_pass": False,
            "messages": len(messages),
            **counts,
            "coverage": coverage,
            "semantic_gate": semantic_gate,
        }
    resolution_config = config.resolution
    for message, outcome in zip(messages, outcomes, strict=True):
        if outcome.status is not ExtractionOutcomeStatus.EXTRACTED:
            continue
        with session.begin():
            result = write_ingestion(
                session,
                namespace_key,
                [message],
                outcome.as_extraction_result(),
                embeddings,
                extractor_model=extractor_model,
                resolution=resolution_config,
                run=run,
                complete_run=False,
            )
            for key in counts:
                counts[key] += result[key]
    resolution_counts, resolution_records = resolve_staged_candidates(
        session,
        run,
        provider,
        resolution_config,
        resolver_model=extractor_model,
        request_interval_seconds=config.evaluation.structured_request_interval_seconds,
    )
    for key in counts:
        counts[key] += resolution_counts[key]
    with session.begin():
        finish_ingestion_run(
            session,
            run,
            counts,
            status="partial" if coverage["failed_count"] else "completed",
        )
    _write_artifact("resolution.json", resolution_records)
    status = (
        "PARTIAL"
        if coverage["failed_count"]
        else "FAILED_SEMANTIC"
        if semantic_gate["status"] == "FAIL"
        else "COMPLETED"
    )
    return {
        "status": status,
        "coverage_pass": not coverage["failed_count"] and semantic_gate["status"] != "FAIL",
        "messages": len(messages),
        **counts,
        "coverage": coverage,
        "semantic_gate": semantic_gate,
    }
