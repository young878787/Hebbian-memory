import json

from hela_mem_zh_mvp.evaluation.contracts import AIJudgeCase, AIJudgeSummary
from hela_mem_zh_mvp.evaluation.judge import JUDGE_BATCH_SIZE, judge_answers


class _FakeJudgeProvider:
    def __init__(self) -> None:
        self.prompts: list[str] = []

    def generate_structured(self, prompt: str, response_model: type) -> AIJudgeSummary:  # noqa: ARG002
        self.prompts.append(prompt)
        payload = json.loads(prompt.split("\n", 1)[1])
        cases = [
            AIJudgeCase(
                query_id=case["query_id"],
                verdict="PASS",
                correctness=2,
                groundedness=2,
                association_completeness=2,
                contradiction_correctness=2,
                reason="回答符合提供的記憶。",
            )
            for case in payload["cases"]
        ]
        return AIJudgeSummary(
            schema_version="answer-judge-summary-v1",
            cases=cases,
            summary={"batch_case_count": len(cases)},
        )


def test_judge_batches_large_evaluation_and_compacts_retrieval_trace() -> None:
    provider = _FakeJudgeProvider()
    cases = [
        {
            "query_id": f"case-{index}",
            "query": f"問題 {index}",
            "expect_answerable": True,
            "retrieved_memories": {
                "items": [
                    {
                        "external_id": f"mem-{index}",
                        "content": "記憶內容",
                        "status": "active",
                        "selected": True,
                        "scores": {"semantic": 0.9},
                    }
                ],
                "large_trace": "不應傳給 judge",
            },
            "answer": {"answerable": True, "answer": "有。", "citations": [f"mem-{index}"]},
            "deterministic_checks": {"must_include": True},
        }
        for index in range(JUDGE_BATCH_SIZE * 2 + 1)
    ]

    result = judge_answers(provider, cases)

    assert len(provider.prompts) == 3
    assert all(len(json.loads(prompt.split("\n", 1)[1])["cases"]) <= JUDGE_BATCH_SIZE for prompt in provider.prompts)
    assert all("large_trace" not in prompt and "deterministic_checks" not in prompt for prompt in provider.prompts)
    assert result["status"] == "PASS"
    assert result["pass_count"] == len(cases)
    assert [case["query_id"] for case in result["cases"]] == [case["query_id"] for case in cases]
    assert result["summary"]["batch_count"] == 3


def test_judge_sends_primary_source_as_correctness_oracle() -> None:
    provider = _FakeJudgeProvider()
    cases = [
        {
            "query_id": "gpu-decision",
            "query": "最後有沒有買 RTX 3090？",
            "expect_answerable": True,
            "reference_conversations": [
                {
                    "message_id": "msg-002",
                    "session_id": "fixture-gpu-01",
                    "role": "user",
                    "content": "最後我沒有買 RTX 3090。",
                    "occurred_at": "2026-01-15T12:00:00+08:00",
                }
            ],
            "selected_memories": [
                {
                    "external_id": "incorrect-memory",
                    "content": "使用者買了 RTX 3090。",
                    "status": "active",
                }
            ],
            "answer": {"answerable": True, "answer": "有買。", "citations": ["incorrect-memory"]},
        }
    ]

    judge_answers(provider, cases)

    assert "reference_answer" in provider.prompts[0]
    submitted_case = json.loads(provider.prompts[0].split("\n", 1)[1])["cases"][0]
    assert submitted_case["reference_conversations"] == cases[0]["reference_conversations"]
    assert submitted_case["reference_answer"]["facts"] == cases[0]["reference_conversations"]
    assert submitted_case["selected_memories"][0]["content"] == "使用者買了 RTX 3090。"
