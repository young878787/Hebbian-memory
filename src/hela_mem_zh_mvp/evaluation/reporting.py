"""Shared summary and artifact reporting contract for evaluation workflows."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

SUMMARY_PATH = Path("results/summary.json")


def _answer_text(answer: object) -> str | None:
    if not isinstance(answer, dict):
        return None
    value = answer.get("answer")
    return value if isinstance(value, str) else None


def _correct_answer_text(reference_answer: object) -> str | None:
    if not isinstance(reference_answer, dict):
        return None
    facts = reference_answer.get("facts")
    if not isinstance(facts, list):
        return None
    contents = [
        fact["content"]
        for fact in facts
        if isinstance(fact, dict) and isinstance(fact.get("content"), str)
    ]
    return "\n".join(contents) if contents else None


def build_question_results(
    records: list[dict[str, Any]], ai_judge: dict[str, Any]
) -> list[dict[str, Any]]:
    """Build compact, scan-friendly per-question QA rows for the root summary."""
    verdicts = {case["query_id"]: case for case in ai_judge.get("cases", [])}
    rows: list[dict[str, Any]] = []
    for record in records:
        answer = record.get("answer")
        reference_answer = record.get("reference_answer")
        verdict = verdicts.get(record["query_id"], {})
        rows.append(
            {
                "query_id": record["query_id"],
                "question": record.get("query"),
                "ai_answer": _answer_text(answer),
                "correct_answer": _correct_answer_text(reference_answer),
                "expected_answerable": (
                    reference_answer.get("answerable")
                    if isinstance(reference_answer, dict)
                    else None
                ),
                "judge": (
                    {key: value for key, value in verdict.items() if key != "query_id"}
                    if verdict
                    else None
                ),
                "citations": (
                    answer.get("citations", []) if isinstance(answer, dict) else []
                ),
                "error": record.get("answer_error", record.get("error")),
            }
        )
    return rows


def build_qa_section(
    records: list[dict[str, Any]], ai_judge: dict[str, Any]
) -> dict[str, Any]:
    """Merge judge metadata and per-question QA rows into one readable section."""
    cases = ai_judge.get("cases", [])
    calculated_counts = Counter(
        case.get("verdict")
        for case in cases
        if isinstance(case, dict) and case.get("verdict") in {"PASS", "FAIL", "UNSURE"}
    )
    verdict_counts = {
        key: ai_judge.get(f"{key.lower()}_count", calculated_counts.get(key, 0))
        for key in ("PASS", "FAIL", "UNSURE")
    }
    section: dict[str, Any] = {
        "status": ai_judge.get("status"),
        "verdict_counts": verdict_counts,
        "summary": ai_judge.get("summary", {}),
        "questions": build_question_results(records, ai_judge),
    }
    if ai_judge.get("error"):
        section["error"] = ai_judge["error"]
    return section


def write_root_summary(
    payload: dict[str, Any],
    *,
    results_directory: Path,
    summary_path: Path = SUMMARY_PATH,
) -> None:
    """Write the canonical root summary and remove the obsolete duplicate."""
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    pipeline_summary_path = results_directory / "summary.json"
    if pipeline_summary_path != summary_path:
        pipeline_summary_path.unlink(missing_ok=True)
