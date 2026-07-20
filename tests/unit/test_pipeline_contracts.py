from pathlib import Path

import pytest

from hela_mem_zh_mvp.ingestion.contracts import SourceMessage
from hela_mem_zh_mvp.ingestion.extractor import ExtractionError, extract_messages
from hela_mem_zh_mvp.ingestion.input import IngestionError, load_input_messages
from hela_mem_zh_mvp.retrieval.contracts import AnswerResult


class StaticProvider:
    def __init__(self, value: object):
        self.value = value

    def generate_structured(self, prompt: str, response_model: type):  # noqa: ARG002
        return response_model.model_validate(self.value)


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
    assert len(messages) == 12
    assert messages[0].message_id == "msg-001"


def test_input_rejects_duplicate_message_ids(tmp_path: Path) -> None:
    line = '{"message_id":"m","session_id":"s","role":"user","content":"x","occurred_at":"2026-01-01T00:00:00+08:00","metadata":{}}\n'
    path = tmp_path / "conversations.jsonl"
    path.write_text(line + line, encoding="utf-8")
    with pytest.raises(IngestionError, match="duplicate"):
        load_input_messages(path)


def test_extractor_rejects_evidence_not_in_its_message() -> None:
    provider = StaticProvider(
        {
            "schema_version": "memory-extraction-v1",
            "source_message_ids": ["m-1"],
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
            "relations": [],
        }
    )
    with pytest.raises(ExtractionError, match="evidence"):
        extract_messages(provider, _messages())


def test_answer_contract_requires_selected_citations() -> None:
    with pytest.raises(ValueError, match="citation"):
        AnswerResult(schema_version="memory-answer-v1", answerable=True, answer="有。")
