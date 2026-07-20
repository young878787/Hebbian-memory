"""Structured extraction boundary; providers never receive database capabilities."""

from __future__ import annotations

import json

from ..providers.base import StructuredProvider
from .contracts import ExtractionResult, SourceMessage


class ExtractionError(ValueError):
    pass


def extract_messages(
    provider: StructuredProvider, messages: list[SourceMessage]
) -> ExtractionResult:
    payload = [message.model_dump(mode="json") for message in messages]
    prompt = (
        "將以下對話轉為 atomic memories。只回傳 memory-extraction-v1 JSON。"
        "不得編造證據。每個 entity.evidence 與 memory.evidence 必須從對應來源 content 直接複製一段連續、逐字完全相同的原文；"
        "不可改寫、翻譯、摘要、補上標點或合併不同句子。若無法提供這種逐字證據，請略過該 entity 或 memory。"
        "memory 可選填 attribute_key（可更新的狀態欄位）與 primary_entity_candidate_id；不確定時省略 attribute_key，"
        "不要猜測人物別名或跨語言同一性。"
        "若同一批 memories 明確表達前後狀態改變或無法判定順序的互斥內容，必須填相同的 attribute_key，"
        "並建立 supersedes 或 contradicts relation；relation.evidence 必須是來源中的逐字證據。"
        "不得輸出資料庫 ID、namespace、SQL 或 edge weight。\n"
        f"來源訊息：{json.dumps(payload, ensure_ascii=False)}"
    )
    result = provider.generate_structured(prompt, ExtractionResult)
    supplied = {message.message_id: message.content for message in messages}
    if set(result.source_message_ids) != set(supplied):
        raise ExtractionError("extraction source_message_ids must exactly match supplied messages")
    for entity in result.entities:
        if not any(entity.evidence in content for content in supplied.values()):
            raise ExtractionError(
                f"entity {entity.candidate_id} evidence is not present in source text"
            )
    for memory in result.memories:
        if not any(
            memory.evidence in supplied[message_id] for message_id in memory.evidence_message_ids
        ):
            raise ExtractionError(
                f"memory {memory.candidate_id} evidence is not present in its source message: "
                f"{memory.evidence!r}"
            )
    return result
