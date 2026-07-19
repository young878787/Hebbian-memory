"""Single-call, batch answer judge with application-owned aggregate validation."""

from __future__ import annotations

import json
from typing import Any

from .provider import StructuredProvider
from .schemas import AIJudgeSummary


class JudgeError(ValueError):
    pass


def judge_answers(provider: StructuredProvider, cases: list[dict[str, Any]]) -> dict[str, Any]:
    prompt = (
        "評估以下記憶系統回答。只回傳 answer-judge-summary-v1 JSON，"
        "每個 case 剛好一次，reason 短且不可輸出推理過程。\n"
        f"{json.dumps({'schema_version': 'answer-judge-input-v1', 'cases': cases}, ensure_ascii=False)}"
    )
    result = provider.generate_structured(prompt, AIJudgeSummary)
    expected = [case["query_id"] for case in cases]
    actual = [case.query_id for case in result.cases]
    if len(actual) != len(expected) or set(actual) != set(expected):
        raise JudgeError("judge must return every case exactly once")
    counts = {
        verdict.lower() + "_count": sum(case.verdict == verdict for case in result.cases)
        for verdict in ("PASS", "FAIL", "UNSURE")
    }
    return {
        "status": "PASS",
        **counts,
        "cases": [case.model_dump() for case in result.cases],
        "summary": result.summary,
    }
