"""Deterministic source-to-memory gates for live extraction outputs."""

from __future__ import annotations

from ..ingestion.contracts import MessageExtractionOutcome, SourceMessage
from ..ingestion.state import derive_initial_state
from .contracts import LiveExtractionExpectation


def evaluate_extraction_semantics(
    outcomes: list[MessageExtractionOutcome],
    messages: list[SourceMessage],
    expectations: list[LiveExtractionExpectation],
) -> dict[str, object]:
    """Match source assertions, never runtime IDs or provider wording alone."""
    outcome_by_id = {item.source_message_id: item for item in outcomes}
    message_by_id = {item.message_id: item for item in messages}
    failures: list[dict[str, object]] = []
    for expectation in expectations:
        outcome = outcome_by_id.get(expectation.source_message_id)
        message = message_by_id.get(expectation.source_message_id)
        if outcome is None or message is None:
            failures.append({"source_message_id": expectation.source_message_id, "reason": "missing_outcome"})
            continue
        if outcome.status is not expectation.expected_outcome:
            failures.append({"source_message_id": expectation.source_message_id, "reason": "outcome_status"})
            continue
        if (
            expectation.expected_atomic_count is not None
            and len(outcome.assertions) < expectation.expected_atomic_count
        ):
            failures.append({"source_message_id": expectation.source_message_id, "reason": "atomic_count"})
        for claim in expectation.expected_claims:
            matches = [
                memory
                for memory in outcome.memories
                if claim.required_evidence in (memory.evidence or "")
                and memory.memory_type is claim.memory_type
                and memory.modality is claim.modality
                and memory.temporal_scope is claim.temporal_scope
            ]
            if not matches:
                failures.append(
                    {
                        "source_message_id": expectation.source_message_id,
                        "reason": "missing_expected_claim",
                        "required_evidence": claim.required_evidence,
                    }
                )
                continue
            if any(memory.evidence not in message.content for memory in matches):
                failures.append({"source_message_id": expectation.source_message_id, "reason": "evidence_span"})
            if any(
                derive_initial_state(
                    memory.modality, memory.temporal_scope, memory.memory_type
                ).status is not claim.expected_status
                for memory in matches
            ):
                failures.append({"source_message_id": expectation.source_message_id, "reason": "state_mapping"})
            if any(term in memory.content for term in claim.forbidden_assertions for memory in matches):
                failures.append({"source_message_id": expectation.source_message_id, "reason": "forbidden_assertion"})
    return {
        "status": "PASS" if not failures else "FAIL",
        "checked_message_count": len(expectations),
        "failures": failures,
    }
