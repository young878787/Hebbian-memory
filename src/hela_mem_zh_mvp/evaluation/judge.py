"""Batched answer judge with application-owned aggregate validation."""

from __future__ import annotations

import json
from typing import Any

from ..providers.base import StructuredProvider
from .contracts import AIJudgeSummary


class JudgeError(ValueError):
    pass


JUDGE_BATCH_SIZE = 10


def _compact_case(case: dict[str, Any]) -> dict[str, Any]:
    """Keep only the evidence needed by the judge.

    The standard evaluator stores the full retrieval trace, while the single
    end-to-end evaluator already prepares this compact shape.  Normalizing at
    this boundary prevents the full trace from being sent to the provider and
    keeps both callers on the same prompt contract.
    """
    retrieval = case.get("retrieved_memories")
    selected_memories = case.get("selected_memories")
    if selected_memories is None and isinstance(retrieval, dict):
        selected_memories = [
            {
                "external_id": item["external_id"],
                "content": item["content"],
                "status": item["status"],
            }
            for item in retrieval.get("items", [])
            if item.get("selected")
        ]

    return {
        "query_id": case["query_id"],
        "query": case["query"],
        "expect_answerable": case["expect_answerable"],
        "selected_memories": selected_memories or [],
        "answer": case.get("answer"),
    }


def _judge_batch(
    provider: StructuredProvider, cases: list[dict[str, Any]], batch_number: int
) -> AIJudgeSummary:
    prompt = (
        "評估以下記憶系統回答。只回傳 answer-judge-summary-v1 JSON，"
        "每個 case 剛好一次，reason 不超過 120 字且不可輸出推理過程。"
        f"這是第 {batch_number} 批，共 {len(cases)} 題。\n"
        f"{json.dumps({'schema_version': 'answer-judge-input-v1', 'cases': cases}, ensure_ascii=False)}"
    )
    result = provider.generate_structured(prompt, AIJudgeSummary)
    expected = [case["query_id"] for case in cases]
    actual = [case.query_id for case in result.cases]
    if len(actual) != len(expected) or set(actual) != set(expected):
        raise JudgeError(
            f"judge batch {batch_number} must return every case exactly once"
        )
    return result


def judge_answers(provider: StructuredProvider, cases: list[dict[str, Any]]) -> dict[str, Any]:
    compact_cases = [_compact_case(case) for case in cases]
    batches = [
        compact_cases[index : index + JUDGE_BATCH_SIZE]
        for index in range(0, len(compact_cases), JUDGE_BATCH_SIZE)
    ]
    if not batches:
        return {
            "status": "PASS",
            "pass_count": 0,
            "fail_count": 0,
            "unsure_count": 0,
            "cases": [],
            "summary": {"batch_count": 0, "case_count": 0, "model_summaries": []},
        }

    results = [
        _judge_batch(provider, batch, batch_number)
        for batch_number, batch in enumerate(batches, start=1)
    ]
    by_id = {
        case.query_id: case
        for result in results
        for case in result.cases
    }
    expected = [case["query_id"] for case in compact_cases]
    if set(by_id) != set(expected) or len(by_id) != len(expected):
        raise JudgeError("judge must return every case exactly once")
    judged_cases = [by_id[query_id] for query_id in expected]
    counts = {
        verdict.lower() + "_count": sum(case.verdict == verdict for case in judged_cases)
        for verdict in ("PASS", "FAIL", "UNSURE")
    }
    return {
        "status": "PASS",
        **counts,
        "cases": [case.model_dump() for case in judged_cases],
        "summary": {
            "batch_count": len(results),
            "case_count": len(judged_cases),
            "model_summaries": [result.summary for result in results],
        },
    }
