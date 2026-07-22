from pathlib import Path
from types import SimpleNamespace

import pytest

from hela_mem_zh_mvp.ingestion.contracts import (
    AssertionDisposition,
    ExtractionOutcomeStatus,
    MemoryModality,
    MemoryStatus,
    MemoryType,
    MessageExtractionOutcome,
    NoMemoryReason,
    SourceMessage,
    TemporalScope,
    extraction_coverage,
)
from hela_mem_zh_mvp.ingestion.extractor import extract_messages
from hela_mem_zh_mvp.ingestion.input import IngestionError, load_input_messages
from hela_mem_zh_mvp.ingestion.state import derive_initial_state
from hela_mem_zh_mvp.retrieval.answerer import AnswerError, answer_query
from hela_mem_zh_mvp.retrieval.contracts import AnswerResult


class StaticProvider:
    def __init__(self, value: object):
        self.value = value

    def generate_structured(self, prompt: str, response_model: type):  # noqa: ARG002
        return response_model.model_validate(self.value)


class RecordingProvider:
    def __init__(self) -> None:
        self.prompts: list[str] = []

    def generate_structured(self, prompt: str, response_model: type):
        self.prompts.append(prompt)
        message_id = "m-1" if "第一筆只在這裡" in prompt else "m-2"
        return response_model.model_validate(
            {
                "schema_version": "message-extraction-outcome-v1",
                "source_message_id": message_id,
                "status": "NO_MEMORY",
                "reason_code": "non_durable_chitchat",
            }
        )


def _messages() -> list[SourceMessage]:
    return [
        SourceMessage(
            message_id="m-1",
            session_id="s-1",
            role="user",
            content="我最後沒有買 RTX 3090。",
            occurred_at="2026-02-01T12:00:00+08:00",
        )
    ]


def test_fixed_input_is_valid_and_has_stable_ids() -> None:
    messages = load_input_messages()
    assert len(messages) == 60
    assert messages[0].message_id == "msg-001"
    assert messages[-1].message_id == "msg-060"


def test_semantic_canary_is_an_exact_subset_of_primary_input() -> None:
    primary = {message.message_id: message for message in load_input_messages()}
    canary = load_input_messages(Path("data/input/semantic_canary.jsonl"))

    assert [message.message_id for message in canary] == [
        "msg-003",
        "msg-004",
        "msg-045",
        "msg-046",
        "msg-050",
    ]
    assert all(message == primary[message.message_id] for message in canary)


def test_input_rejects_duplicate_message_ids(tmp_path: Path) -> None:
    line = '{"message_id":"m","session_id":"s","role":"user","content":"x","occurred_at":"2026-01-01T00:00:00+08:00","metadata":{}}\n'
    path = tmp_path / "conversations.jsonl"
    path.write_text(line + line, encoding="utf-8")
    with pytest.raises(IngestionError, match="duplicate"):
        load_input_messages(path)


def test_extractor_derives_evidence_from_primary_source_not_provider_output() -> None:
    provider = StaticProvider(
        {
            "schema_version": "message-extraction-outcome-v1",
            "source_message_id": "m-1",
            "status": "EXTRACTED",
            "entities": [],
            "memories": [
                {
                    "candidate_id": "memory-1",
                    "content": "使用者沒有購買 RTX 3090。",
                    "memory_type": "decision",
                    "entity_candidate_ids": [],
                    "concepts": [],
                    "occurred_at": "2026-02-01T12:00:00+08:00",
                    "importance": 0.9,
                    "confidence": 0.9,
                    "evidence_message_ids": ["m-1"],
                    "evidence": "不存在的證據",
                }
            ],
        }
    )
    outcome = extract_messages(provider, _messages())[0]
    assert outcome.memories[0].evidence == _messages()[0].content


def test_each_extraction_prompt_contains_only_its_primary_message() -> None:
    messages = [
        SourceMessage(
            message_id="m-1",
            session_id="s-1",
            role="user",
            content="第一筆只在這裡",
            occurred_at="2026-02-01T12:00:00+08:00",
        ),
        SourceMessage(
            message_id="m-2",
            session_id="s-1",
            role="assistant",
            content="第二筆只在這裡",
            occurred_at="2026-02-01T12:01:00+08:00",
        ),
    ]
    provider = RecordingProvider()
    outcomes = extract_messages(provider, messages)
    assert [outcome.source_message_id for outcome in outcomes] == ["m-1", "m-2"]
    assert "第二筆只在這裡" not in provider.prompts[0]
    assert "第一筆只在這裡" not in provider.prompts[1]


def test_extractor_replaces_all_provider_evidence_with_primary_source() -> None:
    message = SourceMessage(
        message_id="m-5",
        session_id="s-1",
        role="assistant",
        content="角色喜歡安靜、光線柔和的房間，不喜歡吵雜和擁擠的環境。",
        occurred_at="2026-02-01T12:00:00+08:00",
    )
    provider = StaticProvider(
        {
            "schema_version": "message-extraction-outcome-v1",
            "source_message_id": "m-5",
            "status": "EXTRACTED",
            "entities": [
                {
                    "candidate_id": "character-1",
                    "canonical_name": "角色",
                    "entity_type": "person",
                    "aliases_seen": [],
                    "confidence": 0.9,
                    "evidence": "角色",
                }
            ],
            "memories": [
                {
                    "candidate_id": "memory-2",
                    "content": "角色不喜歡吵雜和擁擠的環境。",
                    "memory_type": "event",
                    "entity_candidate_ids": ["character-1"],
                    "concepts": [],
                    "attribute_key": "room_noise",
                    "primary_entity_candidate_id": "character-1",
                    "occurred_at": "2026-02-01T12:00:00+08:00",
                    "importance": 0.9,
                    "confidence": 0.9,
                    "evidence_message_ids": ["m-5"],
                    "evidence": "角色不喜歡吵雜和擁擠的環境",
                }
            ],
        }
    )
    outcome = extract_messages(provider, [message])[0]
    assert outcome.entities[0].evidence == message.content
    assert outcome.memories[0].evidence == message.content


def test_answer_contract_requires_selected_citations() -> None:
    with pytest.raises(ValueError, match="citation"):
        AnswerResult(schema_version="memory-answer-v1", answerable=True, answer="有。")


def test_answer_error_retains_model_answer_for_evaluation() -> None:
    provider = StaticProvider(
        {
            "schema_version": "memory-answer-v1",
            "answerable": True,
            "answer": "使用者喜歡散步。",
            "citations": ["selected-memory", "outside-memory"],
        }
    )
    memory = SimpleNamespace(
        content="使用者喜歡散步。",
        status="active",
        modality="asserted",
        temporal_scope="current",
        occurred_at=None,
    )
    item = SimpleNamespace(
        selected=True,
        external_id="selected-memory",
        memory=memory,
        activation_path=[],
    )
    result = SimpleNamespace(items=[item])

    with pytest.raises(AnswerError) as error:
        answer_query(provider, "我平常喜歡什麼？", result)

    assert error.value.invalid_citations == ["outside-memory"]
    assert error.value.answer is not None
    assert error.value.answer.citations == ["selected-memory", "outside-memory"]


def test_question_memory_cannot_be_promoted_into_supported_fact() -> None:
    provider = StaticProvider(
        {
            "schema_version": "memory-answer-v1",
            "answerable": True,
            "answer": "CMP 170HX 可以安全解鎖。",
            "citations": ["question-memory"],
        }
    )
    item = SimpleNamespace(
        selected=True,
        external_id="question-memory",
        memory=SimpleNamespace(
            content="使用者想知道 CMP 170HX 是否能安全解鎖。",
            status="active",
            modality="question",
            temporal_scope="current",
            occurred_at=None,
        ),
        activation_path=[],
    )

    with pytest.raises(AnswerError, match="question memories"):
        answer_query(provider, "CMP 170HX 能安全解鎖嗎？", SimpleNamespace(items=[item]))


def test_outcome_coverage_fails_closed_for_missing_duplicate_and_failed_messages() -> None:
    outcomes = [
        MessageExtractionOutcome(
            schema_version="message-extraction-outcome-v1",
            source_message_id="m-1",
            status=ExtractionOutcomeStatus.NO_MEMORY,
            reason_code=NoMemoryReason.NON_DURABLE_CHITCHAT,
        ),
        MessageExtractionOutcome(
            schema_version="message-extraction-outcome-v1",
            source_message_id="m-1",
            status=ExtractionOutcomeStatus.FAILED,
            error="ProviderError",
        ),
    ]
    coverage = extraction_coverage(["m-1", "m-2"], outcomes)
    assert coverage["unreported_message_ids"] == ["m-2"]
    assert coverage["duplicate_message_ids"] == ["m-1"]
    assert coverage["failed_message_ids"] == ["m-1"]


def test_v2_outcome_preserves_mixed_atomic_assertions_and_exact_evidence() -> None:
    message = SourceMessage(
        message_id="m-v2",
        session_id="s-1",
        role="user",
        content="我需要大顯存，也想知道能否解鎖。謝謝。",
        occurred_at="2026-02-01T12:00:00+08:00",
    )
    provider = StaticProvider(
        {
            "schema_version": "message-extraction-outcome-v2",
            "source_message_id": "m-v2",
            "status": "EXTRACTED",
            "assertions": [
                {
                    "assertion_id": "a-1",
                    "evidence_quote": "我需要大顯存",
                    "disposition": "EXTRACTED",
                    "memory_candidate_ids": ["memory-1"],
                },
                {
                    "assertion_id": "a-2",
                    "evidence_quote": "也想知道能否解鎖",
                    "disposition": "EXTRACTED",
                    "memory_candidate_ids": ["memory-2"],
                },
                {
                    "assertion_id": "a-3",
                    "evidence_quote": "謝謝",
                    "disposition": "NO_MEMORY",
                    "reason_code": "non_durable_chitchat",
                },
            ],
            "memories": [
                {
                    "candidate_id": "memory-1",
                    "content": "使用者需要大顯存。",
                    "memory_type": "event",
                    "entity_candidate_ids": [],
                    "concepts": [],
                    "occurred_at": "2026-02-01T12:00:00+08:00",
                    "importance": 0.8,
                    "confidence": 0.9,
                    "evidence_message_ids": ["m-v2"],
                    "evidence_quote": "我需要大顯存",
                    "modality": "asserted",
                    "temporal_scope": "current",
                },
                {
                    "candidate_id": "memory-2",
                    "content": "使用者正在研究是否能解鎖。",
                    "memory_type": "event",
                    "entity_candidate_ids": [],
                    "concepts": [],
                    "occurred_at": "2026-02-01T12:00:00+08:00",
                    "importance": 0.8,
                    "confidence": 0.8,
                    "evidence_message_ids": ["m-v2"],
                    "evidence_quote": "也想知道能否解鎖",
                    "modality": "question",
                    "temporal_scope": "current",
                },
            ],
        }
    )
    outcome = extract_messages(provider, [message])[0]
    assert [item.disposition for item in outcome.assertions] == [
        AssertionDisposition.EXTRACTED,
        AssertionDisposition.EXTRACTED,
        AssertionDisposition.NO_MEMORY,
    ]
    assert [(item.evidence_start, item.evidence_end) for item in outcome.memories] == [
        (0, 6),
        (7, 15),
    ]


def test_v2_rejects_legacy_no_memory_reasons_and_unreferenced_candidates() -> None:
    with pytest.raises(ValueError, match="legacy NO_MEMORY"):
        MessageExtractionOutcome(
            schema_version="message-extraction-outcome-v2",
            source_message_id="m-1",
            status="NO_MEMORY",
            assertions=[
                {
                    "assertion_id": "a-1",
                    "evidence_quote": "片段",
                    "disposition": "NO_MEMORY",
                    "reason_code": "insufficient_assertion",
                }
            ],
        )


def test_v2_failed_outcome_remains_valid_when_provider_output_is_rejected() -> None:
    outcome = MessageExtractionOutcome(
        schema_version="message-extraction-outcome-v2",
        source_message_id="m-1",
        status="FAILED",
        error="ValidationError: malformed provider inventory",
    )
    assert outcome.assertions == []


def test_v2_normalizes_provider_no_memory_without_creating_a_memory() -> None:
    message = SourceMessage(
        message_id="m-noise",
        session_id="s-1",
        role="assistant",
        content="論壇文章討論二手硬體的交易風險。",
        occurred_at="2026-02-01T12:00:00+08:00",
    )
    outcome = extract_messages(
        StaticProvider(
            {
                "schema_version": "message-extraction-outcome-v2",
                "source_message_id": "m-noise",
                "status": "EXTRACTED",
                "reason_code": "unsupported_role_content",
            }
        ),
        [message],
    )[0]
    assert outcome.status is ExtractionOutcomeStatus.NO_MEMORY
    assert outcome.reason_code is None
    assert outcome.memories == []
    assert outcome.assertions[0].reason_code is NoMemoryReason.UNSUPPORTED_ROLE_CONTENT


def test_initial_state_policy_separates_history_from_lifecycle() -> None:
    assert derive_initial_state(
        MemoryModality.ASSERTED, TemporalScope.HISTORICAL, MemoryType.EVENT
    ).status is MemoryStatus.ACTIVE
    assert derive_initial_state(
        MemoryModality.CONSIDERED, TemporalScope.HISTORICAL, MemoryType.EVENT
    ).status is MemoryStatus.ARCHIVED
    assert derive_initial_state(
        MemoryModality.UNCERTAIN, TemporalScope.CURRENT, MemoryType.DECISION
    ).status is MemoryStatus.UNCERTAIN
