"""Structured extraction boundary; providers never receive database capabilities."""

from __future__ import annotations

import json

from ..providers.base import StructuredProvider
from .contracts import (
    AssertionDisposition,
    AtomicAssertion,
    MessageExtractionOutcome,
    SourceMessage,
)


class ExtractionError(ValueError):
    pass


def _attach_source_evidence(
    result: MessageExtractionOutcome, message: SourceMessage
) -> MessageExtractionOutcome:
    """Validate v2 source spans while preserving v1 artifact compatibility."""
    if result.schema_version == "message-extraction-outcome-v2":
        assertions = result.assertions
        if result.status.value == "NO_MEMORY" and not assertions and result.reason_code is not None:
            assertions = [
                AtomicAssertion(
                    assertion_id="provider-no-memory-1",
                    evidence_quote=message.content,
                    disposition=AssertionDisposition.NO_MEMORY,
                    reason_code=result.reason_code,
                )
            ]
        memories = []
        for memory in result.memories:
            quote = memory.evidence_quote
            if quote is None:
                raise ExtractionError(f"memory {memory.candidate_id} has no evidence_quote")
            start = message.content.find(quote)
            if start < 0:
                raise ExtractionError(
                    f"memory {memory.candidate_id} evidence_quote is not in the primary source"
                )
            memories.append(
                memory.model_copy(
                    update={
                        "evidence": quote,
                        "evidence_start": start,
                        "evidence_end": start + len(quote),
                    }
                )
            )
        return result.model_copy(
            update={
                "entities": [
                    entity.model_copy(update={"evidence": message.content})
                    for entity in result.entities
                ],
                "memories": memories,
                "assertions": assertions,
                "reason_code": None,
            }
        )
    """v1 has no span contract; keep its full-message provenance readable."""
    return result.model_copy(
        update={
            "entities": [
                entity.model_copy(update={"evidence": message.content}) for entity in result.entities
            ],
            "memories": [
                memory.model_copy(update={"evidence": message.content}) for memory in result.memories
            ],
        }
    )


def _validate_evidence(result: MessageExtractionOutcome, message: SourceMessage) -> None:
    if result.source_message_id != message.message_id:
        raise ExtractionError("outcome source_message_id must equal the primary message")
    for entity in result.entities:
        if entity.evidence != message.content:
            raise ExtractionError(
                f"entity {entity.candidate_id} evidence is not the primary source text"
            )
    if result.schema_version == "message-extraction-outcome-v2":
        for assertion in result.assertions:
            if assertion.evidence_quote not in message.content:
                raise ExtractionError(
                    f"assertion {assertion.assertion_id} evidence_quote is not in the primary source"
                )
    for memory in result.memories:
        if result.schema_version == "message-extraction-outcome-v2":
            if (
                memory.evidence is None
                or memory.evidence_start is None
                or memory.evidence_end is None
                or message.content[memory.evidence_start : memory.evidence_end] != memory.evidence
            ):
                raise ExtractionError(
                    f"memory {memory.candidate_id} evidence is not an exact primary-source span"
                )
        elif memory.evidence != message.content:
            raise ExtractionError(
                f"memory {memory.candidate_id} evidence is not the primary source text: "
                f"{memory.evidence!r}"
            )


def extract_message(
    provider: StructuredProvider, message: SourceMessage
) -> MessageExtractionOutcome:
    """Extract one source message without conversation or database context."""
    payload = message.model_dump(mode="json")
    prompt = (
        "將唯一提供的來源訊息轉為 atomic memories。只回傳 "
        "message-extraction-outcome-v2 JSON。"
        "這不是對話摘要：不得假設、引用或推論未提供的其他訊息、既有記憶或 fixture。"
        "先列出來源內所有 atomic assertions，再逐筆指定 EXTRACTED 或 NO_MEMORY。"
        "每個 memory 必須由且只由一個 EXTRACTED assertion 引用，並提供來源中的連續逐字 evidence_quote；"
        "伺服器會驗證並計算 offset。"
        "preference 必須有 attribute_key 與 primary_entity_candidate_id；character_fact 必須有 "
        "primary_entity_candidate_id。primary_entity_candidate_id 必須逐字等於 entities 內某一個 candidate_id，"
        "且該 ID 必須同時放入 memory.entity_candidate_ids；它表示主詞（例如使用者的『我』應建立 canonical_name=user 的 person entity），"
        "不能把茶、書、香菜等受詞物件當成主詞。若只有朋友、論壇或其他外部人物的偏好，"
        "才可回傳 NO_MEMORY。使用者尚未決定、尚未確認、正在研究、想知道、曾考慮或規劃，都是可保存語義："
        "請以 modality=uncertain/question/considered 與 temporal_scope 表示，不得丟棄。question 的 content 必須描述使用者正在研究的問題，"
        "不可把問題答案寫成事實。"
        "分句規則是硬性契約：連接詞兩側若各有獨立事件、狀態、偏好或問題，必須列為不同 assertion，"
        "不得以一筆廣泛 memory 合併。『開始研究 X，還不確定 Y』必須輸出 asserted/current event（研究 X）"
        "與 uncertain/current event（Y 尚未確認）兩筆；『想知道 X 能否 Y』必須是 question/current event，不是 preference；"
        "『規劃時會採用方法 X』必須是 asserted/current preference；『曾考慮 X』必須是 considered/historical event；"
        "『尚未決定 A 或 B』必須是 uncertain/current decision。considered 只可用於明示曾考慮，不能取代 uncertain。"
        "每一筆 memory 的 memory_type/modality/temporal_scope 必須符合上述語義，不能只因主詞是 user 就預設 preference。"
        "attribute_key 僅能使用可比較的穩定 key：飲料甜度=beverage_sweetness、閱讀音訊=reading_audio、"
        "閱讀媒介=reading_medium、香菜=food_cilantro、早餐口味=breakfast_flavor；其他確定偏好請選最貼近的 "
        "snake_case state key，不能為同一狀態隨意改名。"
        "v2 NO_MEMORY reason_code 僅能用 non_durable_chitchat、external_noise、unsupported_role_content、"
        "non_propositional_fragment；不得輸出 relation、資料庫 ID、namespace、SQL、fixture ID 或 edge weight。\n"
        f"唯一來源訊息：{json.dumps(payload, ensure_ascii=False)}"
    )
    result = _attach_source_evidence(
        provider.generate_structured(prompt, MessageExtractionOutcome), message
    )
    _validate_evidence(result, message)
    return result.model_copy(update={"provider_attempts": 1})


def extract_messages(
    provider: StructuredProvider, messages: list[SourceMessage]
) -> list[MessageExtractionOutcome]:
    """Compatibility helper that still invokes the provider once per message."""
    return [extract_message(provider, message) for message in messages]
