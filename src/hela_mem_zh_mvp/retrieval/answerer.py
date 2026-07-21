"""Grounded answer generation from selected retrieval evidence only."""

from __future__ import annotations

import json

from ..providers.base import StructuredProvider
from .contracts import AnswerResult, RetrievalResult


class AnswerError(ValueError):
    pass


def answer_query(provider: StructuredProvider, query: str, result: RetrievalResult) -> AnswerResult:
    selected = [item for item in result.items if item.selected]
    evidence = [
        {
            "external_id": item.external_id,
            "content": item.memory.content,
            "status": item.memory.status,
            "modality": item.memory.modality,
            "temporal_scope": item.memory.temporal_scope,
            "occurred_at": item.memory.occurred_at.isoformat() if item.memory.occurred_at else None,
            "activation_path": item.activation_path,
        }
        for item in selected
    ]
    prompt = (
        "根據提供的 selected memories 回答問題。只回傳 memory-answer-v1 JSON。"
        "不得使用未提供的事實；answerable=false 時說明無法由現有記憶確定。"
        "modality=question 只能說使用者正在研究問題，不可把問題答案當事實；"
        "modality=uncertain 必須保留尚未確認或尚未決定；archived/historical 不得描述為目前計畫。"
        "answerable=true 時 citations 必須至少包含一個 selected memory 的 external_id；"
        "answerable=false 時 citations 必須是空陣列。\n"
        f"query={query}\nselected memories={json.dumps(evidence, ensure_ascii=False)}"
    )
    answer = provider.generate_structured(prompt, AnswerResult)
    allowed = {item.external_id for item in selected}
    if not set(answer.citations) <= allowed:
        raise AnswerError("answer cites a memory that was not selected")
    return answer


__all__ = ["AnswerError", "answer_query"]
