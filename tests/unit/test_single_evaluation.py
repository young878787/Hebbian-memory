from pathlib import Path
from types import SimpleNamespace

from hela_mem_zh_mvp import single_evaluation
from hela_mem_zh_mvp.cli import build_parser


def test_single_e2e_cli_accepts_one_case_overrides() -> None:
    args = build_parser().parse_args(
        [
            "single-e2e-evaluate",
            "--input",
            "input.jsonl",
            "--query",
            "問題",
            "--query-id",
            "case-1",
        ]
    )

    assert args.command == "single-e2e-evaluate"
    assert args.input == Path("input.jsonl")
    assert args.query == "問題"
    assert args.query_id == "case-1"


def test_single_e2e_runs_all_stages_and_persists_one_artifact(monkeypatch, tmp_path: Path) -> None:
    calls: list[str] = []
    monkeypatch.setattr(single_evaluation, "RESULTS_DIRECTORY", tmp_path)

    def fake_reset(*args, **kwargs):  # noqa: ANN002, ANN003
        calls.append("reset")

    def fake_ingest(*args, **kwargs):  # noqa: ANN002, ANN003
        calls.append("ingest")
        assert kwargs["input_path"] == Path("input.jsonl")
        return {"messages": 1, "created": 1, "merged": 0}

    def fake_resolver_state(*args, **kwargs):  # noqa: ANN002, ANN003
        calls.append("resolver")
        return {"decision_actions": {"CREATE": 1}, "candidate_statuses": {"resolved": 1}}

    def fake_ask(*args, **kwargs):  # noqa: ANN002, ANN003
        calls.append("ask")
        assert kwargs["learn"] is False
        return {
            "retrieval": {
                "items": [
                    {"external_id": "mem-1", "selected": True},
                    {"external_id": "mem-2", "selected": False},
                ]
            },
            "answer": {
                "schema_version": "memory-answer-v1",
                "answerable": True,
                "answer": "有。",
                "citations": ["mem-1"],
            },
        }

    def fake_judge(*args, **kwargs):  # noqa: ANN002, ANN003
        calls.append("judge")
        assert kwargs == {}
        assert args[1][0]["query_id"] == "case-1"
        return {"status": "PASS", "pass_count": 1, "cases": [], "summary": {}}

    monkeypatch.setattr(single_evaluation, "ingest", fake_ingest)
    monkeypatch.setattr(single_evaluation, "_reset_single_e2e_namespace", fake_reset)
    monkeypatch.setattr(single_evaluation, "_resolver_state", fake_resolver_state)
    monkeypatch.setattr(single_evaluation, "ask", fake_ask)
    monkeypatch.setattr(single_evaluation, "judge_answers", fake_judge)

    summary = single_evaluation.run_single_e2e_evaluation(
        SimpleNamespace(),
        SimpleNamespace(),
        SimpleNamespace(),
        SimpleNamespace(),
        extractor_model="test-model",
        input_path=Path("input.jsonl"),
        query="問題",
        query_id="case-1",
    )

    assert calls == ["reset", "ingest", "resolver", "ask", "judge"]
    assert summary["status"] == "PASS"
    assert summary["checks"] == {
        "resolver_decision_persisted": True,
        "retrieval_selected_memory": True,
        "answer_citations_selected": True,
        "ai_judge_contract": True,
    }
    assert (tmp_path / "summary.json").is_file()
