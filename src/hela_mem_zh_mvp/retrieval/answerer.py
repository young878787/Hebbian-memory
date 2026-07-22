"""Grounded answer generation from selected retrieval evidence only."""

from __future__ import annotations

import json

from ..providers.base import StructuredProvider
from .contracts import AnswerResult, RetrievalResult


class AnswerError(ValueError):
    """Answer generation produced a result that cannot pass the grounding gate.

    The provider result is retained so evaluators can report and judge the
    answer without treating an application-side citation violation as a
    missing provider response.  Callers that perform learning still receive
    the exception and therefore cannot reinforce an ungrounded answer.
    """

    def __init__(
        self,
        message: str,
        *,
        answer: AnswerResult | None = None,
        invalid_citations: list[str] | None = None,
    ) -> None:
        super().__init__(message)
        self.answer = answer
        self.invalid_citations = invalid_citations or []


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
        "作答規則：\n"
        "1. 每個具體事實都必須能對應到一筆 selected memory；沒有對應時寧可省略或留白，也不要編造或推測。\n"
        "2. answerable 的判斷以『問題需要的事實是否有任何 selected memory 支撐』為準；"
        "selected 集合中其他主題或來源的 evidence(例如 archived、uncertain、他人討論、別的 session 內容)"
        "屬於輔助上下文,不是拒答的理由。請只把題目主體所需的事實納入判斷。\n"
        "3. 當題目問『後來/目前/現在』時,superseded 與 archived 的事實不應描述為目前計畫,"
        "但這不影響 answerable=true:只要目前狀態有對應事實或能由時序推得,即可回答並在 citations 標示目前事實。\n"
        "4. modality=question 只能說使用者正在研究問題,不可把問題答案當事實；"
        "modality=uncertain 必須保留『尚未確認/尚未決定』；他人/群組/論壇的 evidence 屬於對照脈絡,"
        "可作為對比引用,不算主體事實。\n"
        "5. answerable=true 時 citations 必須至少包含一個支撐主體事實的 selected memory external_id；"
        "answerable=false 時 citations 必須是空陣列,且 reason 需說明缺少哪一條事實。\n"
        f"query={query}\nselected memories={json.dumps(evidence, ensure_ascii=False)}"
    )
    answer = provider.generate_structured(prompt, AnswerResult)
    allowed = {item.external_id for item in selected}
    invalid_citations = sorted(set(answer.citations) - allowed)
    if invalid_citations:
        raise AnswerError(
            "answer cites a memory that was not selected",
            answer=answer,
            invalid_citations=invalid_citations,
        )
    return answer


__all__ = ["AnswerError", "answer_query"]
