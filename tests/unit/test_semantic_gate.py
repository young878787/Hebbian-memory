from hela_mem_zh_mvp.evaluation.contracts import ExtractionExpectedClaim, LiveExtractionExpectation
from hela_mem_zh_mvp.evaluation.semantic_gate import evaluate_extraction_semantics
from hela_mem_zh_mvp.ingestion.contracts import MessageExtractionOutcome, SourceMessage


def test_semantic_gate_fails_when_required_atomic_claim_is_missing() -> None:
    message = SourceMessage(
        message_id="m-1",
        session_id="s-1",
        role="user",
        content="我尚未決定紙本或電子書。",
        occurred_at="2026-01-01T00:00:00+08:00",
    )
    outcome = MessageExtractionOutcome(
        schema_version="message-extraction-outcome-v1",
        source_message_id="m-1",
        status="NO_MEMORY",
        reason_code="non_durable_chitchat",
    )
    expectation = LiveExtractionExpectation(
        source_message_id="m-1",
        expected_outcome="EXTRACTED",
        expected_atomic_count=1,
        expected_claims=[
            ExtractionExpectedClaim(
                subject="user",
                memory_type="decision",
                required_evidence="尚未決定紙本或電子書",
                modality="uncertain",
                temporal_scope="current",
                expected_status="uncertain",
            )
        ],
    )
    result = evaluate_extraction_semantics([outcome], [message], [expectation])
    assert result["status"] == "FAIL"
    assert result["failures"] == [{"source_message_id": "m-1", "reason": "outcome_status"}]
