"""Structured extraction boundary; providers never receive database capabilities."""

from __future__ import annotations

import json

from ..providers.base import StructuredProvider
from .contracts import MessageExtractionOutcome, SourceMessage


class ExtractionError(ValueError):
    pass


def _attach_source_evidence(
    result: MessageExtractionOutcome, message: SourceMessage
) -> MessageExtractionOutcome:
    """Never accept model-authored evidence; persist the primary source verbatim."""
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
    for memory in result.memories:
        if memory.evidence != message.content:
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
        "message-extraction-outcome-v1 JSON。"
        "這不是對話摘要：不得假設、引用或推論未提供的其他訊息、既有記憶或 fixture。"
        "不要輸出 evidence；伺服器會把唯一來源訊息全文作為不可變 provenance evidence。"
        "preference 必須有 attribute_key 與 primary_entity_candidate_id；character_fact 必須有 "
        "primary_entity_candidate_id。primary_entity_candidate_id 必須逐字等於 entities 內某一個 candidate_id，"
        "且該 ID 必須同時放入 memory.entity_candidate_ids；它表示主詞（例如使用者的『我』應建立 canonical_name=user 的 person entity），"
        "不能把茶、書、香菜等受詞物件當成主詞。若只有朋友、論壇或其他外部人物的偏好，或使用者尚未決定，"
        "不可升格為使用者記憶，回傳 NO_MEMORY 和受限的 reason_code。"
        "attribute_key 僅能使用可比較的穩定 key：飲料甜度=beverage_sweetness、閱讀音訊=reading_audio、"
        "閱讀媒介=reading_medium、香菜=food_cilantro、早餐口味=breakfast_flavor；其他確定偏好請選最貼近的 "
        "snake_case state key，不能為同一狀態隨意改名。"
        "不得輸出 relation、資料庫 ID、namespace、SQL、fixture ID 或 edge weight。\n"
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
