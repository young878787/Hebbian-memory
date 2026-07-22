from pathlib import Path
from types import SimpleNamespace

from hela_mem_zh_mvp.cli import build_parser
from hela_mem_zh_mvp.evaluation import single_e2e as single_evaluation
from hela_mem_zh_mvp.evaluation import standard as standard_evaluation
from hela_mem_zh_mvp.evaluation.reporting import build_qa_section, build_question_results
from hela_mem_zh_mvp.retrieval.answerer import AnswerError


def test_single_e2e_cli_accepts_batch_and_one_case_overrides() -> None:
    batch = build_parser().parse_args(["run"])
    assert batch.query is None
    assert batch.query_limit == 60

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
    (tmp_path / "summary.json").write_text("obsolete duplicate", encoding="utf-8")
    monkeypatch.setattr(single_evaluation, "load_input_messages", lambda path: [object()])

    queries = [
        SimpleNamespace(
            query_id=f"case-{number}",
            query=f"問題 {number}",
            category="test",
            suite="baseline",
            complexity="basic",
            architecture_targets=[],
            required_hops=0,
            scope=SimpleNamespace(value="general"),
            expect_answerable=True,
            reference_message_ids=[],
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
        return {
            "before": {"memories": 2, "edges": 3},
            "removed": True,
            "namespace_exists_after": False,
        }

    def fake_ingest(*args, **kwargs):  # noqa: ANN002, ANN003
        calls.append("ingest")
        assert kwargs["input_path"] == Path("input.jsonl")
        return {"messages": 1, "created": 1, "merged": 0, "coverage_pass": True}

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

    assert calls == [
        "purge",
        "ingest",
        "resolver",
        "retrieve",
        "answer",
        "retrieve",
        "answer",
        "judge",
        "purge",
    ]
    assert summary["status"] == "PASS"
    assert summary["quality_status"] == "PASS"
    assert summary["questions"] == {
        "requested": 2,
        "completed": 2,
        "answers_returned": 2,
        "provider_errors": 0,
        "answer_contract_errors": 0,
    }
    assert summary["coverage"] == {
        "by_suite": {"baseline": 2, "architecture_v1": 0},
        "by_complexity": {
            "basic": 2,
            "intermediate": 0,
            "advanced": 0,
            "adversarial": 0,
        },
        "multi_hop_queries": 0,
    }
    assert summary["legacy_namespace_cleanup"]["before"]["edges"] == 3
    assert summary["temporary_namespace_cleanup"]["namespace_exists_after"] is False
    assert summary["qa"]["verdict_counts"] == {"PASS": 2, "FAIL": 0, "UNSURE": 0}
    assert [item["judge"]["verdict"] for item in summary["qa"]["questions"]] == [
        "PASS",
        "PASS",
    ]
    assert summary["qa"]["questions"][0]["question"] == "問題 1"
    assert summary["qa"]["questions"][0]["ai_answer"] == "有。"
    assert summary["qa"]["questions"][0]["correct_answer"] is None
    assert {path.name for path in tmp_path.iterdir()} == {
        "root-summary.json",
        "retrieval.json",
        "answers.json",
        "judge_input.json",
    }


def test_single_e2e_keeps_citation_failure_in_judge_batch(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(single_evaluation, "RESULTS_DIRECTORY", tmp_path)
    monkeypatch.setattr(single_evaluation, "SUMMARY_PATH", tmp_path / "root-summary.json")
    monkeypatch.setattr(single_evaluation, "load_input_messages", lambda path: [object()])
    query = SimpleNamespace(
        query_id="citation-failure",
        query="問題",
        category="test",
        suite="baseline",
        complexity="basic",
        architecture_targets=[],
        required_hops=0,
        scope=SimpleNamespace(value="general"),
        expect_answerable=True,
        reference_message_ids=[],
    )
    monkeypatch.setattr(single_evaluation, "_evaluation_queries", lambda *args: [query])
    monkeypatch.setattr(
        single_evaluation,
        "_purge_namespace",
        lambda *args, **kwargs: {"before": {}, "removed": True, "namespace_exists_after": False},
    )
    monkeypatch.setattr(
        single_evaluation,
        "ingest",
        lambda *args, **kwargs: {"messages": 1, "created": 1, "merged": 0, "coverage_pass": True},
    )
    monkeypatch.setattr(
        single_evaluation,
        "_resolver_state",
        lambda *args, **kwargs: {"decision_actions": {"CREATE": 1}, "candidate_statuses": {}},
    )
    monkeypatch.setattr(
        single_evaluation,
        "get_namespace",
        lambda *args, **kwargs: SimpleNamespace(id="namespace-id"),
    )

    class FakeResult:
        items = []

        def as_dict(self):
            return {
                "items": [
                    {
                        "external_id": "selected-memory",
                        "content": "記憶",
                        "status": "active",
                        "selected": True,
                    }
                ]
            }

    class FakeRetriever:
        def __init__(self, *args, **kwargs):  # noqa: ANN002, ARG002
            pass

        def retrieve(self, *args, **kwargs):  # noqa: ANN002, ARG002
            return FakeResult()

    monkeypatch.setattr(single_evaluation, "Retriever", FakeRetriever)
    partial_answer = {
        "schema_version": "memory-answer-v1",
        "answerable": True,
        "answer": "有。",
        "citations": ["selected-memory", "outside-memory"],
    }

    def fake_answer(*args, **kwargs):  # noqa: ANN002, ARG002
        raise AnswerError(
            "answer cites a memory that was not selected",
            answer=SimpleNamespace(model_dump=lambda: partial_answer),
            invalid_citations=["outside-memory"],
        )

    def fake_judge(*args, **kwargs):  # noqa: ANN002, ARG002
        assert len(args[1]) == 1
        assert args[1][0]["answer"] == partial_answer
        return {
            "status": "PASS",
            "pass_count": 1,
            "fail_count": 0,
            "unsure_count": 0,
            "cases": [{"query_id": "citation-failure", "verdict": "PASS", "reason": "可檢視"}],
            "summary": {},
        }

    monkeypatch.setattr(single_evaluation, "answer_query", fake_answer)
    monkeypatch.setattr(single_evaluation, "judge_answers", fake_judge)

    summary = single_evaluation.run_single_e2e_evaluation(
        SimpleNamespace(),
        SimpleNamespace(),
        SimpleNamespace(),
        SimpleNamespace(evaluation=SimpleNamespace(answer_request_interval_seconds=0)),
        extractor_model="test-model",
        input_path=Path("input.jsonl"),
        query_limit=1,
    )

    assert summary["status"] == "FAIL"
    assert summary["checks"]["all_citations_selected"] is False
    assert summary["questions"] == {
        "requested": 1,
        "completed": 1,
        "answers_returned": 1,
        "provider_errors": 0,
        "answer_contract_errors": 1,
    }


def test_question_results_are_compact_and_include_answer_comparison() -> None:
    rows = build_question_results(
        [
            {
                "query_id": "case-1",
                "query": "問題",
                "answer": {"answer": "AI 回答", "citations": ["mem-1"]},
                "reference_answer": {
                    "answerable": True,
                    "source_message_ids": ["msg-1", "msg-2"],
                    "facts": [
                        {"content": "正確答案一"},
                        {"content": "正確答案二"},
                    ],
                },
            }
        ],
        {"cases": [{"query_id": "case-1", "verdict": "PASS", "reason": "正確"}]},
    )

    assert rows == [
        {
            "query_id": "case-1",
            "question": "問題",
            "ai_answer": "AI 回答",
            "correct_answer": "正確答案一\n正確答案二",
            "expected_answerable": True,
            "judge": {"verdict": "PASS", "reason": "正確"},
            "citations": ["mem-1"],
            "error": None,
        }
    ]


def test_qa_section_merges_judge_summary_and_question_rows() -> None:
    section = build_qa_section(
        [],
        {
            "status": "PASS",
            "pass_count": 1,
            "fail_count": 0,
            "unsure_count": 0,
            "cases": [],
            "summary": {"overall": "正確"},
        },
    )

    assert section == {
        "status": "PASS",
        "verdict_counts": {"PASS": 1, "FAIL": 0, "UNSURE": 0},
        "summary": {"overall": "正確"},
        "questions": [],
    }


def test_standard_summary_also_converges_to_root_path(monkeypatch, tmp_path: Path) -> None:
    pipeline_directory = tmp_path / "pipeline"
    pipeline_directory.mkdir()
    (pipeline_directory / "summary.json").write_text("obsolete duplicate", encoding="utf-8")
    root_summary = tmp_path / "summary.json"
    monkeypatch.setattr(standard_evaluation, "RESULTS_DIRECTORY", pipeline_directory)
    monkeypatch.setattr(standard_evaluation, "SUMMARY_PATH", root_summary)

    standard_evaluation._write_summary({"status": "PASS"})

    assert root_summary.exists()
    assert not (pipeline_directory / "summary.json").exists()
