"""Ingestion use case; provider calls stay outside the caller-owned transaction."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from ..config import load_config
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


def _write_artifact(name: str, payload: object) -> None:
    RESULTS_DIRECTORY.mkdir(parents=True, exist_ok=True)
    (RESULTS_DIRECTORY / name).write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )


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
                    schema_version="message-extraction-outcome-v1",
                    source_message_id=message.message_id,
                    status=ExtractionOutcomeStatus.FAILED,
                    provider_attempts=1,
                    error=f"{type(exc).__name__}: {exc}"[:500],
                )
            )
    with session.begin():
        coverage = record_extraction_outcomes(session, run, outcomes)
    _write_artifact("extraction.json", [outcome.model_dump(mode="json") for outcome in outcomes])
    _write_artifact("extraction_coverage.json", coverage)
    resolution_config = config.resolution
    counts = {
        "created": 0,
        "merged": 0,
        "superseded": 0,
        "contradicted": 0,
        "ignored": 0,
        "deferred": 0,
    }
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
    return {
        "status": "PARTIAL" if coverage["failed_count"] else "COMPLETED",
        "coverage_pass": not coverage["failed_count"],
        "messages": len(messages),
        **counts,
        "coverage": coverage,
    }
