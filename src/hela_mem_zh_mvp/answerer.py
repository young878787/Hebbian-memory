"""Grounded answer generation from selected retrieval evidence only."""

from __future__ import annotations

import json

from .provider import StructuredProvider
from .retriever import RetrievalResult
from .schemas import AnswerResult


class AnswerError(ValueError):
    pass


def answer_query(provider: StructuredProvider, query: str, result: RetrievalResult) -> AnswerResult:
    selected = [item for item in result.items if item.selected]
    evidence = [
        {
            "external_id": item.external_id,
            "content": item.memory.content,
            "status": item.memory.status,
            "occurred_at": item.memory.occurred_at.isoformat() if item.memory.occurred_at else None,
            "activation_path": item.activation_path,
        }
        for item in selected
    ]
    prompt = (
        "根據提供的 selected memories 回答問題。只回傳 memory-answer-v1 JSON。"
        "不得使用未提供的事實；answerable=false 時說明無法由現有記憶確定。\n"
        f"query={query}\nselected memories={json.dumps(evidence, ensure_ascii=False)}"
    )
    answer = provider.generate_structured(prompt, AnswerResult)
    allowed = {item.external_id for item in selected}
    if not set(answer.citations) <= allowed:
        raise AnswerError("answer cites a memory that was not selected")
    return answer
