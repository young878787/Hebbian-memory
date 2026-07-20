from pathlib import Path
from types import SimpleNamespace

from hela_mem_zh_mvp import single_evaluation
from hela_mem_zh_mvp.cli import build_parser


def test_single_e2e_cli_accepts_batch_and_one_case_overrides() -> None:
    batch = build_parser().parse_args(["run"])
    assert batch.query is None
    assert batch.query_limit == 30

    one_case = build_parser().parse_args(
        [
            "run",
            "--input",
            "input.jsonl",
            "--query",
            "問題",
            "--query-id",
            "case-1",
            "--query-limit",
            "7",
        ]
    )
    assert one_case.command == "run"
    assert one_case.input == Path("input.jsonl")
    assert one_case.query == "問題"
    assert one_case.query_id == "case-1"
    assert one_case.query_limit == 7


def test_single_e2e_runs_batch_and_persists_artifacts(monkeypatch, tmp_path: Path) -> None:
    calls: list[str] = []
    monkeypatch.setattr(single_evaluation, "RESULTS_DIRECTORY", tmp_path)
    monkeypatch.setattr(single_evaluation, "SUMMARY_PATH", tmp_path / "root-summary.json")
    monkeypatch.setattr(single_evaluation, "load_input_messages", lambda path: [object()])

    queries = [
        SimpleNamespace(
            query_id=f"case-{number}",
            query=f"問題 {number}",
            category="test",
            scope=SimpleNamespace(value="general"),
            expect_answerable=True,
        )
        for number in (1, 2)
    ]
    monkeypatch.setattr(
        single_evaluation,
        "_evaluation_queries",
        lambda query, query_id, query_limit: queries,
    )

    def fake_purge(*args, **kwargs):  # noqa: ANN002, ANN003
        calls.append("purge")
        return {"before": {"memories": 2, "edges": 3}, "removed": True, "namespace_exists_after": False}

    def fake_ingest(*args, **kwargs):  # noqa: ANN002, ANN003
        calls.append("ingest")
        assert kwargs["input_path"] == Path("input.jsonl")
        return {"messages": 1, "created": 1, "merged": 0}

    def fake_resolver_state(*args, **kwargs):  # noqa: ANN002, ANN003
        calls.append("resolver")
        return {"decision_actions": {"CREATE": 1}, "candidate_statuses": {"resolved": 1}}

    class FakeResult:
        items = []

        def as_dict(self):
            return {
                "items": [
                    {
                        "external_id": "mem-1",
                        "content": "記憶",
                        "status": "active",
                        "selected": True,
                    }
                ]
            }

    class FakeRetriever:
        def __init__(self, *args, **kwargs):  # noqa: ANN002, ANN003
            pass

        def retrieve(self, *args, **kwargs):  # noqa: ANN002, ANN003
            calls.append("retrieve")
            return FakeResult()

    def fake_answer(*args, **kwargs):  # noqa: ANN002, ANN003
        calls.append("answer")
        return SimpleNamespace(
            model_dump=lambda: {
                "schema_version": "memory-answer-v1",
                "answerable": True,
                "answer": "有。",
                "citations": ["mem-1"],
            }
        )

    def fake_judge(*args, **kwargs):  # noqa: ANN002, ANN003
        calls.append("judge")
        assert len(args[1]) == 2
        return {
            "status": "PASS",
            "pass_count": 2,
            "fail_count": 0,
            "unsure_count": 0,
            "cases": [
                {"query_id": f"case-{number}", "verdict": "PASS", "reason": "正確"}
                for number in (1, 2)
            ],
            "summary": {},
        }

    monkeypatch.setattr(single_evaluation, "_purge_namespace", fake_purge)
    monkeypatch.setattr(single_evaluation, "ingest", fake_ingest)
    monkeypatch.setattr(single_evaluation, "_resolver_state", fake_resolver_state)
    monkeypatch.setattr(
        single_evaluation,
        "get_namespace",
        lambda *args, **kwargs: SimpleNamespace(id="namespace-id"),
    )
    monkeypatch.setattr(single_evaluation, "Retriever", FakeRetriever)
    monkeypatch.setattr(single_evaluation, "answer_query", fake_answer)
    monkeypatch.setattr(single_evaluation, "judge_answers", fake_judge)
    monkeypatch.setattr(single_evaluation.time, "sleep", lambda seconds: None)

    summary = single_evaluation.run_single_e2e_evaluation(
        SimpleNamespace(),
        SimpleNamespace(),
        SimpleNamespace(),
        SimpleNamespace(evaluation=SimpleNamespace(answer_request_interval_seconds=0)),
        extractor_model="test-model",
        input_path=Path("input.jsonl"),
        query_limit=2,
    )

    assert calls == ["purge", "ingest", "resolver", "retrieve", "answer", "retrieve", "answer", "judge", "purge"]
    assert summary["status"] == "PASS"
    assert summary["quality_status"] == "PASS"
    assert summary["questions"] == {
        "requested": 2,
        "completed": 2,
        "answers_returned": 2,
        "provider_errors": 0,
    }
    assert summary["legacy_namespace_cleanup"]["before"]["edges"] == 3
    assert summary["temporary_namespace_cleanup"]["namespace_exists_after"] is False
    assert [item["judge_verdict"] for item in summary["final_qa"]] == ["PASS", "PASS"]
    assert {path.name for path in tmp_path.iterdir()} == {
        "root-summary.json",
        "summary.json",
        "retrieval.json",
        "answers.json",
        "judge_input.json",
    }
