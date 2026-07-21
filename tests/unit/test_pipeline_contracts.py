from pathlib import Path

import pytest

from hela_mem_zh_mvp.ingestion.contracts import (
    ExtractionOutcomeStatus,
    MessageExtractionOutcome,
    NoMemoryReason,
    SourceMessage,
    extraction_coverage,
)
from hela_mem_zh_mvp.ingestion.extractor import extract_messages
from hela_mem_zh_mvp.ingestion.input import IngestionError, load_input_messages
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
                    "memory_type": "preference",
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
